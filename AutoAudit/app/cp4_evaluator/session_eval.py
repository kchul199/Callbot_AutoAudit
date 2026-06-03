"""
cp4_evaluator/session_eval.py
세션(대화 전체) 레벨 평가 — 멀티턴 대응.

메트릭:
  - resolution        : 고객 목표 달성/해결 여부 (LLM)
  - multiturn_consistency : 턴 간 모순 없음 (기존 domain_metrics 재사용)
  - efficiency        : 불필요한 턴 반복 없이 간결히 해결했는가 (LLM)
  - escalation        : 상담원 연결이 필요했는데 못 했는가 (LLM, 위험 신호)
  - gt_similarity     : Ground Truth가 있으면 최종 답변과 의미 유사도 (옵션)

싱글턴(턴 1개)이면 resolution/efficiency만 의미 있고 일관성은 1.0 처리.
"""
from __future__ import annotations

import json

from AutoAudit.app.core.llm_client import LLMProvider
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import (
    CallLog,
    MetricScore,
    SessionEvaluation,
    TurnRole,
)

logger = get_logger(__name__)

_SYSTEM = "당신은 콜봇 대화 품질 평가 전문가입니다. 반드시 유효한 JSON만 반환하세요."

_SESSION_PROMPT = """
아래는 한 콜봇 통화의 전체 대화입니다. 대화 전체를 평가하세요.

[대화]
{dialogue}

다음을 JSON으로 판정하세요:
- resolved: 고객의 목표/문의가 최종적으로 해결되었는가 (true/false)
- resolution_score: 해결 정도 0.0~1.0
- efficiency_score: 불필요한 반복 없이 간결히 해결했는가 0.0~1.0
- escalation_needed: 상담원(사람) 연결이 필요한 상황이었는가 (true/false)
- reasoning: 위 판정 근거 (한국어)

출력 형식:
{{"resolved": true/false, "resolution_score": 0.0~1.0, "efficiency_score": 0.0~1.0,
  "escalation_needed": true/false, "reasoning": "..."}}
"""


class SessionEvaluator:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def evaluate(
        self,
        call_log: CallLog,
        ground_truth: str | None = None,
    ) -> SessionEvaluation:
        scores: list[MetricScore] = []

        # 1) LLM 세션 판정 (resolution / efficiency / escalation)
        dialogue = self._format_dialogue(call_log)
        verdict = await self._judge_session(dialogue)
        resolved = verdict.get("resolved")
        escalation = verdict.get("escalation_needed")
        scores.append(MetricScore(
            metric="resolution",
            score=float(verdict.get("resolution_score", 1.0 if resolved else 0.0)),
            reasoning=verdict.get("reasoning", ""), method="session",
        ))
        scores.append(MetricScore(
            metric="efficiency",
            score=float(verdict.get("efficiency_score", 1.0)),
            reasoning="대화 효율성", method="session",
        ))
        # escalation은 위험 신호 → 필요했으면 낮은 점수(안전성 관점)
        scores.append(MetricScore(
            metric="escalation_handling",
            score=0.0 if escalation else 1.0,
            reasoning="상담원 연결 필요" if escalation else "콜봇 내 해결",
            method="session",
        ))

        # 2) 멀티턴 일관성 (기존 domain_metrics 재사용)
        from AutoAudit.app.cp4_evaluator.domain_metrics import DomainMetricsEvaluator
        from AutoAudit.app.cp4_evaluator.options import DomainMetricsOptions
        dm = DomainMetricsEvaluator(self.provider, DomainMetricsOptions())
        scores.append(await dm.multiturn_consistency(call_log))

        # 3) Ground Truth 유사도 (있으면)
        if ground_truth:
            scores.append(await self._gt_similarity(call_log, ground_truth))

        return SessionEvaluation(
            conversation_id=call_log.call_id,
            tenant_id=call_log.metadata.get("tenant_id", "default"),
            scores=scores,
            resolved=resolved,
            escalation_needed=escalation,
            judge_provider=type(self.provider).__name__.replace("Provider", "").lower(),
        )

    # ----------------------------------------------------------

    async def _judge_session(self, dialogue: str) -> dict:
        raw = await self.provider.complete(
            _SESSION_PROMPT.format(dialogue=dialogue),
            system=_SYSTEM, temperature=0.0, json_mode=True,
        )
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, AttributeError):
            return {}

    async def _gt_similarity(self, call_log: CallLog, ground_truth: str) -> MetricScore:
        """최종 봇 답변 vs Ground Truth 임베딩 코사인 유사도"""
        last_bot = ""
        for t in reversed(call_log.turns):
            if t.role == TurnRole.BOT:
                last_bot = t.content
                break
        if not last_bot:
            return MetricScore(metric="gt_similarity", score=0.0,
                               reasoning="봇 답변 없음", method="session")
        try:
            a = await self.provider.embed(last_bot)
            b = await self.provider.embed(ground_truth)
            sim = self._cosine(a, b)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"GT 유사도 계산 실패: {exc}")
            sim = 0.0
        return MetricScore(
            metric="gt_similarity", score=round(max(0.0, sim), 4),
            reasoning="정답 대비 의미 유사도", method="session",
        )

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        import math
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)

    @staticmethod
    def _format_dialogue(call_log: CallLog) -> str:
        lines = []
        for t in call_log.turns:
            who = {"user": "고객", "bot": "콜봇", "system": "시스템"}.get(t.role.value, t.role.value)
            lines.append(f"{who}: {t.content}")
        return "\n".join(lines)
