"""
cp4_evaluator/judge.py
LLM-as-a-Judge 평가 엔진
메트릭: Faithfulness / Answer Relevance / Context Precision / Context Recall

Senior RAG Architect 설계 고려사항 3가지:
  1. Grounding 강제: 평가 프롬프트에 검색된 컨텍스트만 제공 → 환각 원천 차단
  2. Score + Reasoning 동시 추출: 점수만이 아닌 근거(reasoning) 필수 기록
  3. Async 병렬 처리: 메트릭 4개를 동시 평가 → 지연 최소화
"""
from __future__ import annotations

import json
import statistics
import uuid
from typing import TYPE_CHECKING

from AutoAudit.app.core.async_utils import gather_with_concurrency
from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.llm_client import LLMProvider, create_provider
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import (
    EvaluationRecord,
    MetricScore,
    QAPair,
    RetrievalResult,
)

if TYPE_CHECKING:
    from AutoAudit.app.cp4_evaluator.options import EvaluationOptions

logger = get_logger(__name__)

# ── 프롬프트 템플릿 ───────────────────────────────────────────

_FAITHFULNESS_PROMPT = """
당신은 RAG 시스템의 품질 평가 전문가입니다. 아래 규칙을 반드시 따르세요:
- 평가는 오직 제공된 [컨텍스트] 내에서만 수행합니다.
- 컨텍스트 외부 지식 사용 금지 (환각 방지).

[컨텍스트]
{contexts}

[질문]
{query}

[답변]
{answer}

위 답변이 컨텍스트에 얼마나 충실한지 평가하세요.
- 컨텍스트에 없는 내용을 주장하면 점수가 낮아집니다.
- 0.0~1.0 사이 점수와 한국어 근거를 JSON으로 반환하세요.

출력 형식:
{{"score": 0.0~1.0, "reasoning": "근거 설명", "grounding_chunks": ["chunk_id_1", ...]}}
"""

_ANSWER_RELEVANCE_PROMPT = """
[질문]
{query}

[답변]
{answer}

답변이 질문에 얼마나 적절하게 응답하는지 평가하세요.
- 질문의 핵심 의도를 얼마나 충족하는가?
- 0.0~1.0 점수와 한국어 근거를 JSON으로 반환하세요.

출력 형식:
{{"score": 0.0~1.0, "reasoning": "근거 설명", "grounding_chunks": []}}
"""

_CONTEXT_PRECISION_PROMPT = """
[질문]
{query}

[검색된 컨텍스트]
{contexts}

위 컨텍스트 중 질문 답변에 실제로 유용한 비율을 평가하세요.
- 관련 없는 청크가 많으면 점수가 낮아집니다.
- 0.0~1.0 점수와 한국어 근거를 JSON으로 반환하세요.

출력 형식:
{{"score": 0.0~1.0, "reasoning": "근거 설명", "grounding_chunks": []}}
"""

_CONTEXT_RECALL_PROMPT = """
[질문]
{query}

[답변]
{answer}

[검색된 컨텍스트]
{contexts}

답변 생성에 필요한 핵심 정보가 컨텍스트에 얼마나 포함되어 있는지 평가하세요.
- 중요한 정보가 누락되면 점수가 낮아집니다.
- 0.0~1.0 점수와 한국어 근거를 JSON으로 반환하세요.

출력 형식:
{{"score": 0.0~1.0, "reasoning": "근거 설명", "grounding_chunks": []}}
"""

METRIC_PROMPTS = {
    "faithfulness": _FAITHFULNESS_PROMPT,
    "answer_relevance": _ANSWER_RELEVANCE_PROMPT,
    "context_precision": _CONTEXT_PRECISION_PROMPT,
    "context_recall": _CONTEXT_RECALL_PROMPT,
}


class LLMJudge:
    """
    4가지 메트릭을 asyncio로 병렬 평가.
    Evidence-based: 프롬프트에 컨텍스트를 명시적으로 삽입.

    신뢰성 강화:
      - 메트릭당 N회 샘플링 → 중앙값 채택, 분산 기반 신뢰도 산출
      - 분산이 임계 초과 시 is_low_confidence=True (CP5에서 별도 처리)
      - Provider 추상화로 OpenAI/Azure/Anthropic 교체 가능
    """

    _SYSTEM = "당신은 RAG 품질 평가 전문가입니다. 반드시 JSON 객체만 반환하세요."

    def __init__(
        self,
        provider: LLMProvider | None = None,
        options: EvaluationOptions | None = None,
    ) -> None:
        self.provider: LLMProvider = provider or create_provider()
        self.judge_model: str = cfg_get("cp4.judge_model", default="gpt-4o")
        self.temperature: float = cfg_get("cp4.judge_temperature", default=0.0)
        self.metrics: list[str] = cfg_get(
            "cp4.metrics",
            default=["faithfulness", "answer_relevance", "context_precision", "context_recall"],
        )
        self.concurrency: int = cfg_get("cp4.concurrency", default=5)
        # 다중 샘플링 설정
        self.n_samples: int = cfg_get("cp4.n_samples", default=3)
        self.sample_temperature: float = cfg_get("cp4.sample_temperature", default=0.4)
        self.low_confidence_std: float = cfg_get("cp4.low_confidence_std", default=0.2)
        # RAGAS claim 단위 faithfulness 토글
        self.use_claim_faithfulness: bool = cfg_get(
            "cp4.faithfulness.use_claim_decomposition", default=True
        )
        self._faith_eval = None

        # 고급 평가 옵션 (편향보정/앙상블/nugget/라우팅 등)
        from AutoAudit.app.cp4_evaluator.options import EvaluationOptions
        self.options: EvaluationOptions = options or EvaluationOptions.from_config()
        self._calibrator = None
        self._ensemble = None
        self._nugget_eval = None
        self._last_nuggets: list = []

    # ----------------------------------------------------------
    # 진입점
    # ----------------------------------------------------------

    async def evaluate_pair(self, pair: QAPair) -> EvaluationRecord:
        """QAPair → EvaluationRecord (권장 진입점)"""
        if pair.retrieval_result is None:
            raise ValueError(f"QAPair {pair.qa_id}: retrieval_result가 비어있음 (CP3 미수행)")
        record = await self.evaluate(
            call_id=pair.call_id,
            query=pair.question,
            generated_answer=pair.bot_answer,
            retrieval_result=pair.retrieval_result,
        )
        record.qa_id = pair.qa_id
        record.subscriber_id = pair.subscriber_id
        return record

    async def evaluate(
        self,
        call_id: str,
        query: str,
        generated_answer: str,
        retrieval_result: RetrievalResult,
    ) -> EvaluationRecord:
        """단일 QA 쌍 → EvaluationRecord"""
        contexts_text = self._format_contexts(retrieval_result)

        coros = [
            self._evaluate_metric(metric, query, generated_answer, contexts_text, retrieval_result)
            for metric in self.metrics
        ]
        scores = await gather_with_concurrency(coros, concurrency=self.concurrency)

        record = EvaluationRecord(
            eval_id=str(uuid.uuid4()),
            call_id=call_id,
            query=query,
            generated_answer=generated_answer,
            retrieval_result=retrieval_result,
            scores=scores,
            judge_model=self.judge_model,
        )

        # nugget context_recall 결과를 record.nuggets에 부착
        if self.options.nugget.enabled and self._last_nuggets:
            record.nuggets = self._last_nuggets
            self._last_nuggets = []

        return record

    async def evaluate_pairs(self, pairs: list[QAPair]) -> list[EvaluationRecord]:
        """QAPair 목록 일괄 평가 (권장)"""
        coros = [self.evaluate_pair(p) for p in pairs]
        return await gather_with_concurrency(coros, concurrency=self.concurrency)

    async def evaluate_batch(self, records: list[dict]) -> list[EvaluationRecord]:
        """dict 목록 일괄 평가 (하위 호환)"""
        coros = [
            self.evaluate(
                call_id=r["call_id"],
                query=r["query"],
                generated_answer=r["generated_answer"],
                retrieval_result=r["retrieval_result"],
            )
            for r in records
        ]
        return await gather_with_concurrency(coros, concurrency=self.concurrency)

    # ----------------------------------------------------------
    # 단일 메트릭 평가 (N회 샘플링 → 집계)
    # ----------------------------------------------------------

    async def _evaluate_metric(
        self,
        metric: str,
        query: str,
        answer: str,
        contexts_text: str,
        retrieval_result: RetrievalResult | None = None,
    ) -> MetricScore:
        opts = self.options

        # context_recall + nugget 옵션 → nugget 기반 recall
        if metric == "context_recall" and opts.nugget.enabled and retrieval_result is not None:
            if self._nugget_eval is None:
                from AutoAudit.app.cp4_evaluator.nugget import NuggetRecallEvaluator
                self._nugget_eval = NuggetRecallEvaluator(self.provider, opts.nugget)
            score, nuggets = await self._nugget_eval.evaluate(query, answer, retrieval_result)
            self._last_nuggets = nuggets
            return await self._maybe_escalate(score, metric, query, answer, contexts_text)

        # Faithfulness는 RAGAS claim 단위 NLI 분해 사용 (토글)
        if metric == "faithfulness" and self.use_claim_faithfulness:
            if self._faith_eval is None:
                from AutoAudit.app.cp4_evaluator.faithfulness import FaithfulnessEvaluator
                self._faith_eval = FaithfulnessEvaluator(self.provider)
            score = await self._faith_eval.evaluate(answer, contexts_text)
            return await self._maybe_escalate(score, metric, query, answer, contexts_text)

        template = METRIC_PROMPTS.get(metric, "")
        prompt = template.format(query=query, answer=answer, contexts=contexts_text)

        # 앙상블 옵션 → 다중 provider 평가
        if opts.ensemble.enabled:
            if self._ensemble is None:
                from AutoAudit.app.cp4_evaluator.ensemble import EnsembleJudge
                self._ensemble = EnsembleJudge(opts.ensemble)
            return await self._ensemble.score(metric, prompt, self._SYSTEM)

        # 편향보정/G-Eval 옵션
        if opts.calibration.enabled:
            if self._calibrator is None:
                from AutoAudit.app.cp4_evaluator.calibration import JudgeCalibrator
                self._calibrator = JudgeCalibrator(self.provider, opts.calibration)
            score = await self._calibrator.score(metric, prompt, self._SYSTEM)
            return await self._maybe_escalate(score, metric, query, answer, contexts_text)

        # 기본: N회 샘플링 → 집계
        if self.n_samples <= 1:
            samples = [await self._single_call(prompt, metric, self.temperature)]
        else:
            coros = [
                self._single_call(prompt, metric, self.sample_temperature)
                for _ in range(self.n_samples)
            ]
            samples = await gather_with_concurrency(coros, concurrency=self.n_samples)

        score = self._aggregate_samples(metric, samples)
        return await self._maybe_escalate(score, metric, query, answer, contexts_text)

    async def _maybe_escalate(
        self, score: MetricScore, metric: str, query: str, answer: str, contexts_text: str
    ) -> MetricScore:
        """라우팅 옵션: 낮은 신뢰도면 추가 샘플로 재평가 (selective evaluation)"""
        r = self.options.routing
        if not (r.enabled and r.escalate_on_low_confidence and score.is_low_confidence):
            return score
        template = METRIC_PROMPTS.get(metric, "")
        if not template:
            return score
        prompt = template.format(query=query, answer=answer, contexts=contexts_text)
        coros = [
            self._single_call(prompt, metric, self.sample_temperature)
            for _ in range(r.extra_samples_on_low_conf)
        ]
        extra = await gather_with_concurrency(coros, concurrency=r.extra_samples_on_low_conf)
        merged = self._aggregate_samples(metric, extra)
        merged.escalated = True
        merged.reasoning = f"[escalated] {merged.reasoning}"
        return merged

    async def _single_call(self, prompt: str, metric: str, temperature: float) -> MetricScore:
        raw = await self.provider.complete(
            prompt, system=self._SYSTEM, temperature=temperature, json_mode=True,
        )
        return self._parse_score(raw, metric)

    def _aggregate_samples(self, metric: str, samples: list[MetricScore]) -> MetricScore:
        """다중 샘플 → 중앙값 + 신뢰도"""
        valid = [s for s in samples if s.reasoning and not s.reasoning.startswith("Parse error")]
        if not valid:
            valid = samples  # 전부 실패해도 기록은 남김

        sample_scores = [s.score for s in valid]
        median_score = statistics.median(sample_scores)
        std = statistics.pstdev(sample_scores) if len(sample_scores) > 1 else 0.0
        confidence = max(0.0, 1.0 - std / 0.5)  # std 0.5 이상이면 신뢰도 0
        is_low = std > self.low_confidence_std

        # 중앙값에 가장 가까운 샘플의 reasoning 채택
        best = min(valid, key=lambda s: abs(s.score - median_score))

        if is_low:
            logger.warning(f"[{metric}] low confidence — std={std:.3f}, scores={sample_scores}")

        return MetricScore(
            metric=metric,
            score=round(median_score, 4),
            reasoning=best.reasoning,
            grounding_chunks=best.grounding_chunks,
            confidence=round(confidence, 4),
            sample_scores=sample_scores,
            is_low_confidence=is_low,
        )

    # ----------------------------------------------------------
    # 유틸
    # ----------------------------------------------------------

    @staticmethod
    def _format_contexts(retrieval_result: RetrievalResult) -> str:
        parts = []
        for i, ctx in enumerate(retrieval_result.contexts, 1):
            parts.append(f"[{i}] (chunk_id={ctx.chunk_id})\n{ctx.content}")
        return "\n\n".join(parts) if parts else "(검색된 컨텍스트 없음)"

    @staticmethod
    def _parse_score(raw: str, metric: str) -> MetricScore:
        try:
            data = json.loads(raw)
            return MetricScore(
                metric=metric,
                score=float(data.get("score", 0.0)),
                reasoning=data.get("reasoning", ""),
                grounding_chunks=data.get("grounding_chunks", []),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning(f"Score parse failed for [{metric}]: {exc}. Raw: {raw[:100]}")
            return MetricScore(metric=metric, score=0.0, reasoning=f"Parse error: {exc}")
