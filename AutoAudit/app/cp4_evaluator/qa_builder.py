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
from AutoAudit.app.core.types import (
    CallLog,
    QAPair,
    RetrievalResult,
    RetrievedContext,
    TurnRole,
)
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
        # #2 추출 정확화: 질문 가치 게이팅 + 다중 의도 분리
        self.worthiness_gate: bool = cfg_get("cp4.qa_extraction.worthiness_gate", default=True)
        self.multi_intent_split: bool = cfg_get("cp4.qa_extraction.multi_intent_split", default=True)

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

            # 직후 Bot 발화 블록 수집 (+ #1 봇 실제 RAG 컨텍스트 수집)
            a_parts = []
            a_contexts: list[str] = []
            while i < len(turns) and turns[i].role == TurnRole.BOT:
                a_parts.append(turns[i].content)
                a_contexts.extend(turns[i].contexts)
                i += 1
            answer = " ".join(a_parts).strip()

            if not answer or len(question) < self.min_question_len or len(answer) < self.min_answer_len:
                continue
            if self._is_trivial(answer):
                continue
            # #2 질문 가치 게이팅: 정보 요청/업무 질문이 아니면 평가 제외 (백채널·잡담)
            if self.worthiness_gate and not self._is_question_worthy(question):
                logger.debug(f"[{call_log.call_id}] 질문 가치 게이팅 제외: {question[:30]!r}")
                continue

            provided = self._build_provided_context(question, a_contexts, call_log.call_id)
            history = self._format_history(turns[:q_start])
            # #2 다중 의도 분리: 한 턴에 독립 의도가 여럿이면 각각 별도 평가 단위로
            intents = self._split_intents(question) if self.multi_intent_split else [question]
            method = "split" if len(intents) > 1 else ("gated" if self.worthiness_gate else "heuristic")
            for k, intent in enumerate(intents):
                pairs.append(
                    QAPair(
                        qa_id=str(uuid.uuid4()),
                        call_id=call_log.call_id,
                        subscriber_id=call_log.subscriber_id,
                        question=intent,
                        bot_answer=answer,
                        turn_index=q_turn_index,
                        history=history,
                        provided_context=provided,
                        context_source="bot_trace" if provided else "auditor",
                        intent_idx=k,
                        extraction_method=method,
                    )
                )

        logger.info(f"[{call_log.call_id}] extracted {len(pairs)} QA pairs")
        return pairs

    # ----------------------------------------------------------
    # #2 질문 가치 게이팅 + 다중 의도 분리 (휴리스틱, LLM 호출 0)
    # ----------------------------------------------------------

    # 정보 요청/질문 신호 (의문형 어미·의문사·요청 동사)
    _QUESTION_MARKERS = (
        "?", "까", "까요", "나요", "가요", "ㄴ가요", "은가요", "인가요", "될까", "되나",
        "얼마", "어떻게", "어떤", "어디", "언제", "무엇", "뭐", "뭔", "왜", "몇",
        "알려", "알 수", "가능", "방법", "여부", "주세요", "해줘", "해 줘", "하고 싶", "싶어",
        "문의", "확인", "신청", "변경", "해지", "환불", "조회", "되나요",
        "질문", "문제", "궁금", "요청",
    )
    # 평가 가치 없는 순수 백채널/잡담 (짧을 때만 적용)
    _BACKCHANNEL = {
        "네", "넵", "응", "어", "음", "아", "예", "그래", "그래요", "맞아요", "맞아",
        "알겠어요", "알겠습니다", "감사합니다", "고마워요", "고맙습니다", "안녕", "안녕하세요",
        "ㅇㅋ", "오케이", "ok",
    }

    def _is_question_worthy(self, question: str) -> bool:
        """질문이 정보 요청/업무 질문인지 휴리스틱 판정 (고관용: 애매하면 통과)."""
        q = question.strip()
        compact = q.replace(" ", "").rstrip(".!~ ")
        # 순수 백채널 (짧고 stoplist) → 제외
        if compact and compact.lower() in self._BACKCHANNEL:
            return False
        # 의문/요청 신호가 있으면 통과
        if any(m in q for m in self._QUESTION_MARKERS):
            return True
        # 신호가 없어도 충분히 길면(서술형 문제 제기 가능) 통과 — 고관용
        return len(compact) >= 10

    def _split_intents(self, question: str) -> list[str]:
        """한 질문을 독립 의도들로 보수적 분리 (과분할 방지).

        문장 종결부호(.,!?。) 또는 명시적 접속어(그리고/또한/추가로/그리고요)로만 분리하고,
        분리된 조각이 각각 질문 가치가 있을 때만 별도 의도로 인정한다.
        """
        import re
        # 1) 종결부호 기준 1차 분할
        rough = re.split(r"(?<=[.!?。])\s+", question.strip())
        # 2) 접속어 기준 2차 분할
        segments: list[str] = []
        for chunk in rough:
            segments.extend(re.split(r"\s*(?:그리고요|그리고|그리구|또한|또|추가로)\s+", chunk))
        segments = [s.strip(" .,!?~") for s in segments if s.strip(" .,!?~")]
        # 3) 각 조각이 질문 가치가 있어야 독립 의도로 인정
        worthy = [s for s in segments if self._is_question_worthy(s)]
        # 분리 결과가 2개 이상일 때만 분리, 아니면 원문 유지
        return worthy if len(worthy) >= 2 else [question.strip()]

    @staticmethod
    def _build_provided_context(
        question: str, contexts: list[str], call_id: str
    ) -> RetrievalResult | None:
        """#1 봇 턴이 실제로 본 컨텍스트 문자열들을 RetrievalResult로 변환 (없으면 None)."""
        cleaned = [c.strip() for c in contexts if c and c.strip()]
        if not cleaned:
            return None
        return RetrievalResult(
            query=question,
            contexts=[
                RetrievedContext(
                    chunk_id=f"bot_trace_{k}",
                    content=c,
                    score=1.0,
                    source_call_id=call_id,
                )
                for k, c in enumerate(cleaned)
            ],
        )

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
            # 감사기 재검색은 항상 수행 (검색 품질 평가 = context_recall/precision 용)
            pair.retrieval_result = await self.retriever.retrieve(pair.question)
            # #1 컨텍스트 출처: 봇 실제 RAG 트레이스가 주입돼 있으면 충실도 평가에 우선
            pair.context_source = "bot_trace" if pair.provided_context else "auditor"
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
