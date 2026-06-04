"""
cp4_evaluator/qa_builder.py
CP1 대화 로그 → 평가 대상 QAPair 추출 → CP3 검색 결과 부착.

이 모듈이 CP1↔CP3↔CP4를 잇는 핵심 브릿지다.
(기존 run_pipeline.py의 하드코딩된 "(샘플 답변)" 문제 해결)

추출 전략:
  - 콜봇 대화에서 User 발화 → 직후 Bot 응답을 (question, answer) 쌍으로 추출
  - 연속 User/Bot 발화는 병합 (멀티 턴 발화 대응)
  - 의미 없는 인사/확인 응답은 휴리스틱 필터 (선택적)
"""
from __future__ import annotations

import uuid

from AutoAudit.app.core.async_utils import gather_with_concurrency
from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import CallLog, QAPair, TurnRole
from AutoAudit.app.cp3_retrieval.retriever import HybridRetriever

logger = get_logger(__name__)


class QAPairBuilder:
    """대화 로그에서 평가 가능한 QA 쌍을 추출하고 컨텍스트를 검색한다."""

    def __init__(self, retriever: HybridRetriever | None = None) -> None:
        self.retriever = retriever
        self.min_question_len: int = cfg_get("cp4.min_question_len", default=4)
        self.min_answer_len: int = cfg_get("cp4.min_answer_len", default=4)
        # 평가 가치가 낮은 봇 상투어 (선택적 필터)
        self.skip_answer_prefixes: list[str] = cfg_get(
            "cp4.skip_answer_prefixes",
            default=["안녕하세요", "감사합니다", "네,", "잠시만"],
        )

    # ----------------------------------------------------------
    # 1) 대화 → QA 쌍 추출 (검색 전)
    # ----------------------------------------------------------

    def extract_pairs(self, call_log: CallLog) -> list[QAPair]:
        """단일 콜에서 (User질문, Bot답변) 쌍 추출 + 직전 대화 맥락(history) 부착"""
        pairs: list[QAPair] = []
        turns = call_log.turns
        i = 0
        while i < len(turns):
            # User 발화 블록 수집 (연속 병합)
            if turns[i].role != TurnRole.USER:
                i += 1
                continue
            q_start = i  # ③ 대화 맥락: 이 질문 이전 모든 턴이 history
            q_parts = []
            while i < len(turns) and turns[i].role == TurnRole.USER:
                q_parts.append(turns[i].content)
                i += 1
            question = " ".join(q_parts).strip()
            q_turn_index = i - 1

            # 직후 Bot 발화 블록 수집
            a_parts = []
            while i < len(turns) and turns[i].role == TurnRole.BOT:
                a_parts.append(turns[i].content)
                i += 1
            answer = " ".join(a_parts).strip()

            if not answer or len(question) < self.min_question_len or len(answer) < self.min_answer_len:
                continue
            if self._is_trivial(answer):
                continue

            pairs.append(
                QAPair(
                    qa_id=str(uuid.uuid4()),
                    call_id=call_log.call_id,
                    subscriber_id=call_log.subscriber_id,
                    question=question,
                    bot_answer=answer,
                    turn_index=q_turn_index,
                    history=self._format_history(turns[:q_start]),
                )
            )

        logger.info(f"[{call_log.call_id}] extracted {len(pairs)} QA pairs")
        return pairs

    @staticmethod
    def _format_history(prior_turns: list) -> list[str]:
        """직전 턴들을 '역할: 내용' 문자열 목록으로 변환 (③ 대화 맥락 주입용)."""
        role_kr = {"user": "고객", "bot": "콜봇", "system": "시스템"}
        out: list[str] = []
        for t in prior_turns:
            who = role_kr.get(t.role.value, t.role.value)
            content = (t.content or "").strip()
            if content:
                out.append(f"{who}: {content}")
        return out

    def extract_from_logs(self, call_logs: list[CallLog]) -> list[QAPair]:
        pairs: list[QAPair] = []
        for log in call_logs:
            pairs.extend(self.extract_pairs(log))
        logger.info(f"Total QA pairs extracted: {len(pairs)}")
        return pairs

    # ----------------------------------------------------------
    # 2) QA 쌍 → 컨텍스트 검색 부착 (CP3 연동)
    # ----------------------------------------------------------

    async def attach_retrieval(self, pairs: list[QAPair]) -> list[QAPair]:
        """각 QA 쌍의 question으로 CP3 검색을 수행해 컨텍스트를 부착"""
        if self.retriever is None:
            raise ValueError("retriever가 필요합니다 (attach_retrieval).")

        concurrency = cfg_get("cp3.retrieval_concurrency", default=5)

        async def _one(pair: QAPair) -> QAPair:
            pair.retrieval_result = await self.retriever.retrieve(pair.question)
            return pair

        results = await gather_with_concurrency([_one(p) for p in pairs], concurrency=concurrency)
        logger.info(f"Retrieval attached to {len(results)} QA pairs")
        return results

    async def build(self, call_logs: list[CallLog]) -> list[QAPair]:
        """추출 + 검색을 한 번에 (CP4 입력 완성)"""
        pairs = self.extract_from_logs(call_logs)
        return await self.attach_retrieval(pairs)

    # ----------------------------------------------------------
    # 유틸
    # ----------------------------------------------------------

    def _is_trivial(self, answer: str) -> bool:
        stripped = answer.strip()
        return any(stripped.startswith(p) for p in self.skip_answer_prefixes) and len(stripped) < 20
