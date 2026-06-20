"""
cp4_evaluator/judge.py
LLM-as-a-Judge 평가 엔진
메트릭: Faithfulness / Answer Relevance / Context Precision / Context Recall

개선 (v2):
  CoT (Chain-of-Thought) 강제 평가:
    - '근거 먼저, 점수 나중' 구조로 프롬프트 재설계
    - LLM이 점수를 먼저 결정하고 근거를 역으로 생성하는 편향 차단
    - 단계별 추론(cot_steps)을 MetricScore에 기록

  역방향 검증 (Reverse Verification):
    - faithfulness: 순방향(답변→컨텍스트) + 역방향(컨텍스트→답변 생성 가능성)
    - answer_relevance: 순방향(답변→질문 응답) + 역방향(답변→질문 역추론)
    - 두 방향 불일치 시 is_low_confidence=True → 사람 검수 우선 배정

설계 고려사항:
  1. Grounding 강제: 컨텍스트 외 지식 사용 금지 (기존 유지)
  2. CoT = 추가 LLM 호출 없음 (프롬프트 구조만 변경) → 비용 동일
  3. Reverse = 추가 1회 LLM 호출 → 비용 2× (기본 OFF, 고신뢰 프로필에서 ON)
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

# ══════════════════════════════════════════════════════════════
# 기본 프롬프트 (CoT 비활성 시 폴백 — 하위 호환)
# ══════════════════════════════════════════════════════════════

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

# ══════════════════════════════════════════════════════════════
# CoT 프롬프트 — '근거 먼저, 점수 나중' 구조
# ══════════════════════════════════════════════════════════════

_COT_ANSWER_RELEVANCE_PROMPT = """
당신은 RAG 시스템의 답변 적절성 평가 전문가입니다.
반드시 아래 단계 순서대로 분석한 뒤 최종 점수를 산출하세요.
단계를 건너뛰거나 점수를 먼저 결정하면 안 됩니다.

[질문]
{query}

[답변]
{answer}

━━ 분석 단계 (순서대로 수행) ━━

Step 1 — 질문 의도 파악:
  이 질문이 원하는 핵심 정보를 1~2문장으로 명확히 서술하세요.

Step 2 — 답변 항목 나열:
  답변이 실제로 다루고 있는 내용을 항목별로 나열하세요 (최대 5개).

Step 3 — 누락 및 이탈 분석:
  a) 질문 의도 중 답변에서 누락된 항목을 찾으세요.
  b) 질문과 무관한 불필요한 내용이 있으면 지적하세요.

Step 4 — 최종 점수 산출:
  위 분석을 종합하여 0.0~1.0 점수를 결정하세요.
  (1.0=완전히 적절, 0.5=부분 응답, 0.0=무관하거나 완전히 빗나감)

출력 형식 (JSON):
{{
  "step1_intent": "질문 핵심 의도",
  "step2_items": ["답변 항목1", "답변 항목2"],
  "step3_missing": ["누락 항목1"],
  "step3_irrelevant": ["불필요 항목1"],
  "score": 0.0~1.0,
  "reasoning": "최종 판정 근거 (한국어)",
  "grounding_chunks": []
}}
"""

_COT_ANSWER_RELEVANCE_REF_PROMPT = """
당신은 RAG 시스템의 답변 적절성 평가 전문가입니다.
[모범답안]을 기준선으로 삼아, 아래 단계 순서대로 분석한 뒤 점수를 산출하세요.
단계를 건너뛰거나 점수를 먼저 결정하면 안 됩니다.

[질문]
{query}

[모범답안]
{ground_truth}

[답변]
{answer}

━━ 분석 단계 (순서대로 수행) ━━

Step 1 — 질문 의도 파악:
  이 질문이 원하는 핵심 정보를 1~2문장으로 명확히 서술하세요.

Step 2 — 답변 항목 나열:
  답변이 실제로 다루는 내용을 항목별로 나열하세요 (최대 5개).

Step 3 — 모범답안 대비 분석:
  a) 모범답안이 다루는데 답변이 누락한 항목을 찾으세요.
  b) 모범답안과 어긋나거나 질문과 무관한 내용을 지적하세요.

Step 4 — 최종 점수 산출:
  모범답안 대비 답변이 질문 의도를 얼마나 충족하는지 0.0~1.0으로 결정하세요.
  (1.0=모범답안 수준, 0.5=부분 응답, 0.0=무관/빗나감)

출력 형식 (JSON):
{{
  "step1_intent": "질문 핵심 의도",
  "step2_items": ["답변 항목1", "답변 항목2"],
  "step3_missing": ["누락 항목1"],
  "step3_irrelevant": ["불필요/상이 항목1"],
  "score": 0.0~1.0,
  "reasoning": "모범답안 대비 최종 판정 근거 (한국어)",
  "grounding_chunks": []
}}
"""

_COT_CONTEXT_PRECISION_PROMPT = """
당신은 RAG 검색 품질 평가 전문가입니다.
반드시 아래 단계 순서대로 분석한 뒤 최종 점수를 산출하세요.

[질문]
{query}

[검색된 컨텍스트]
{contexts}

━━ 분석 단계 (순서대로 수행) ━━

Step 1 — 각 컨텍스트 청크 유용성 판정:
  각 청크 번호([1], [2], ...)에 대해 이 질문에 답하는 데 유용한지
  "유용" 또는 "불필요"로 판정하고 한 줄 이유를 적으세요.

Step 2 — 비율 계산:
  유용한 청크 수 / 전체 청크 수 = precision

Step 3 — 최종 점수 산출:
  Step 2의 비율을 기반으로 최종 점수를 결정하세요.
  완전히 유용한 청크만 있으면 1.0, 절반이 무관하면 0.5.

출력 형식 (JSON):
{{
  "step1_verdicts": {{"[1]": "유용 — 이유", "[2]": "불필요 — 이유"}},
  "step2_ratio": "유용N / 전체M",
  "useful_chunks": ["[1]", "[3]"],
  "score": 0.0~1.0,
  "reasoning": "최종 판정 근거 (한국어)",
  "grounding_chunks": []
}}
"""

_COT_CONTEXT_RECALL_PROMPT = """
당신은 RAG 검색 품질 평가 전문가입니다.
반드시 아래 단계 순서대로 분석한 뒤 최종 점수를 산출하세요.
평가는 오직 제공된 컨텍스트만을 기준으로 합니다. 외부 지식 사용 금지.

[질문]
{query}

[답변]
{answer}

[검색된 컨텍스트]
{contexts}

━━ 분석 단계 (순서대로 수행) ━━

Step 1 — 필요 정보 목록화:
  이 질문에 완전히 답하기 위해 반드시 필요한 핵심 정보를 3~5개 나열하세요.
  (답변을 보지 말고 질문만 보고 판단하세요)

Step 2 — 컨텍스트 포함 여부 확인:
  Step 1의 각 핵심 정보가 검색된 컨텍스트에 있는지 "있음/없음"으로 판정하세요.

Step 3 — 최종 점수 산출:
  포함된 핵심 정보 수 / 전체 핵심 정보 수를 기반으로 점수를 결정하세요.

출력 형식 (JSON):
{{
  "step1_required": ["필요 정보1", "필요 정보2", "필요 정보3"],
  "step2_coverage": {{"필요 정보1": "있음", "필요 정보2": "없음"}},
  "step3_ratio": "포함N / 전체M",
  "score": 0.0~1.0,
  "reasoning": "최종 판정 근거 (한국어)",
  "grounding_chunks": []
}}
"""

# ══════════════════════════════════════════════════════════════
# 역방향 검증 프롬프트
# ══════════════════════════════════════════════════════════════

_REVERSE_FAITHFULNESS_PROMPT = """
당신은 RAG 시스템의 품질 평가 전문가입니다.
오직 제공된 [컨텍스트]만을 근거로 판단하세요. 외부 지식 사용 금지.

[컨텍스트]
{contexts}

[답변]
{answer}

━━ 역방향 검증 ━━
위 [컨텍스트]만을 보고, 이 [답변]과 같은 내용을 정당하게 생성할 수 있었는지 평가하세요.

Step 1 — 답변 문장 분석:
  답변의 각 주요 문장/주장을 나열하고,
  컨텍스트에서 도출 가능한지 "가능/불가"로 판정하세요.

Step 2 — 도출 불가 항목 확인:
  컨텍스트 없이 외부 지식이 있어야만 쓸 수 있는 내용이 있으면 나열하세요.

Step 3 — 역방향 점수 산출:
  컨텍스트로부터 이 답변을 합리적으로 도출할 수 있는 정도 (0.0~1.0).
  (1.0=완전 도출 가능, 0.0=컨텍스트와 무관)

출력 형식 (JSON):
{{
  "step1_derivability": {{"문장1": "가능", "문장2": "불가 — 이유"}},
  "step2_external_knowledge": ["외부 지식 필요 항목"],
  "score": 0.0~1.0,
  "reasoning": "역방향 판정 근거 (한국어)",
  "grounding_chunks": []
}}
"""

_REVERSE_ANSWER_RELEVANCE_PROMPT = """
당신은 RAG 시스템의 품질 평가 전문가입니다.

[답변]
{answer}

━━ 역방향 검증 ━━
위 [답변]만 보고, 이 답변이 다음 [질문]에 대한 것임을 얼마나 명확히 알 수 있는지 평가하세요.

[질문]
{query}

Step 1 — 답변 주제 파악:
  답변의 핵심 주제와 키워드를 나열하세요.

Step 2 — 질문 역추론:
  답변만 보았을 때 이 답변이 어떤 질문에 대한 것인지 추론하세요.
  실제 질문과 얼마나 일치하는지 평가하세요.

Step 3 — 역방향 점수 산출:
  답변이 해당 질문에 특화되어 있는 정도 (0.0~1.0).
  (1.0=답변만 봐도 질문이 명확히 추론됨, 0.0=어떤 질문인지 불명확)

출력 형식 (JSON):
{{
  "step1_keywords": ["키워드1", "키워드2"],
  "step2_inferred_question": "답변만 보고 추론한 질문",
  "step2_match_level": "높음/중간/낮음",
  "score": 0.0~1.0,
  "reasoning": "역방향 판정 근거 (한국어)",
  "grounding_chunks": []
}}
"""

# 메트릭 → (기본 프롬프트, CoT 프롬프트, 역방향 프롬프트)
METRIC_PROMPTS: dict[str, str] = {
    "faithfulness": _FAITHFULNESS_PROMPT,
    "answer_relevance": _ANSWER_RELEVANCE_PROMPT,
    "context_precision": _CONTEXT_PRECISION_PROMPT,
    "context_recall": _CONTEXT_RECALL_PROMPT,
}

METRIC_COT_PROMPTS: dict[str, str] = {
    # faithfulness는 FaithfulnessEvaluator(claim NLI)가 CoT 역할 수행
    "answer_relevance": _COT_ANSWER_RELEVANCE_PROMPT,
    "context_precision": _COT_CONTEXT_PRECISION_PROMPT,
    "context_recall": _COT_CONTEXT_RECALL_PROMPT,
}

METRIC_REVERSE_PROMPTS: dict[str, str] = {
    "faithfulness": _REVERSE_FAITHFULNESS_PROMPT,
    "answer_relevance": _REVERSE_ANSWER_RELEVANCE_PROMPT,
}


class LLMJudge:
    """
    4가지 메트릭을 asyncio로 병렬 평가.

    개선 기능:
      CoT 강제: answer_relevance / context_precision / context_recall 에 단계별 추론 적용
      역방향 검증: faithfulness / answer_relevance 를 양방향으로 평가해 일관성 검증
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
        self.n_samples: int = cfg_get("cp4.n_samples", default=3)
        self.sample_temperature: float = cfg_get("cp4.sample_temperature", default=0.4)
        self.low_confidence_std: float = cfg_get("cp4.low_confidence_std", default=0.2)
        self.use_claim_faithfulness: bool = cfg_get(
            "cp4.faithfulness.use_claim_decomposition", default=True
        )
        self._faith_eval = None

        from AutoAudit.app.cp4_evaluator.options import EvaluationOptions
        self.options: EvaluationOptions = options or EvaluationOptions.from_config()
        self._calibrator = None
        self._ensemble = None
        self._nugget_eval = None
        self._last_nuggets: list = []
        # 답변 품질 정확도 강화 (① 정답성 / ④ 적정거절)
        self._correctness_eval = None
        self._abstention = None

    # ----------------------------------------------------------
    # 진입점
    # ----------------------------------------------------------

    async def evaluate_pair(self, pair: QAPair) -> EvaluationRecord:
        if pair.retrieval_result is None:
            raise ValueError(f"QAPair {pair.qa_id}: retrieval_result가 비어있음 (CP3 미수행)")
        record = await self.evaluate(
            call_id=pair.call_id,
            query=pair.question,
            generated_answer=pair.bot_answer,
            retrieval_result=pair.retrieval_result,
            ground_truth=pair.ground_truth,
            history=pair.history,
            provided_context=pair.provided_context,
        )
        record.qa_id = pair.qa_id
        record.subscriber_id = pair.subscriber_id
        record.ground_truth = pair.ground_truth
        return record

    async def evaluate(
        self,
        call_id: str,
        query: str,
        generated_answer: str,
        retrieval_result: RetrievalResult,
        ground_truth: str | None = None,
        history: list[str] | None = None,
        provided_context: RetrievalResult | None = None,
    ) -> EvaluationRecord:
        # context_recall / context_precision = 감사기 재검색 컨텍스트 (검색 품질)
        contexts_text = self._format_contexts(retrieval_result)
        # #1 faithfulness = 봇 실제 RAG 트레이스가 있으면 그것, 없으면 감사기 컨텍스트
        context_source = "bot_trace" if provided_context else "auditor"
        faith_contexts_text = (
            self._format_contexts(provided_context) if provided_context else contexts_text
        )

        # ④ 적정 거절: 답변이 거절/모름이면 정당성을 1회만 판정해 메트릭에 공유
        # (거절 정당성은 봇이 실제 본 근거 = faithfulness 컨텍스트 기준으로 판정)
        abstention_info = await self._assess_abstention(query, generated_answer, faith_contexts_text)

        coros = [
            self._evaluate_metric(
                metric, query, generated_answer, contexts_text, retrieval_result,
                ground_truth=ground_truth, history=history, abstention=abstention_info,
                faith_contexts_text=faith_contexts_text,
            )
            for metric in self.metrics
        ]
        # ① 정답성: ground_truth가 있고 correctness 활성 시 추가 메트릭
        if self.options.correctness.enabled and ground_truth:
            coros.append(self._evaluate_correctness(generated_answer, ground_truth))

        scores = await gather_with_concurrency(coros, concurrency=self.concurrency)

        record = EvaluationRecord(
            eval_id=str(uuid.uuid4()),
            call_id=call_id,
            query=query,
            generated_answer=generated_answer,
            retrieval_result=retrieval_result,
            context_source=context_source,
            scores=scores,
            judge_model=self.judge_model,
            ground_truth=ground_truth,
        )

        if self.options.nugget.enabled and self._last_nuggets:
            record.nuggets = self._last_nuggets
            self._last_nuggets = []

        return record

    async def evaluate_pairs(self, pairs: list[QAPair]) -> list[EvaluationRecord]:
        coros = [self.evaluate_pair(p) for p in pairs]
        return await gather_with_concurrency(coros, concurrency=self.concurrency)

    async def evaluate_batch(self, records: list[dict]) -> list[EvaluationRecord]:
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
    # 단일 메트릭 평가 — CoT + 역방향 분기
    # ----------------------------------------------------------

    async def _evaluate_metric(
        self,
        metric: str,
        query: str,
        answer: str,
        contexts_text: str,
        retrieval_result: RetrievalResult | None = None,
        ground_truth: str | None = None,
        history: list[str] | None = None,
        abstention=None,
        faith_contexts_text: str | None = None,
    ) -> MetricScore:
        opts = self.options
        # #1 faithfulness 는 봇 실제 컨텍스트(faith_contexts_text)를, 검색 품질 메트릭은 감사기 컨텍스트를 사용
        fctx = faith_contexts_text if faith_contexts_text is not None else contexts_text
        # 프롬프트용 컨텍스트: faithfulness 만 봇 실제 컨텍스트, 그 외(검색 품질)는 감사기 컨텍스트
        pctx = fctx if metric == "faithfulness" else contexts_text

        # ── ④ 적정 거절 면제: 정당한 거절이면 감점 면제 ──
        if (
            opts.abstention.enabled
            and abstention is not None
            and abstention.is_abstention
            and abstention.appropriate
            and metric in opts.abstention.exempt_metrics
        ):
            return self._exempt_score(metric, abstention.reasoning)

        # ── 특수 경로: nugget 기반 context_recall ──
        if metric == "context_recall" and opts.nugget.enabled and retrieval_result is not None:
            if self._nugget_eval is None:
                from AutoAudit.app.cp4_evaluator.nugget import NuggetRecallEvaluator
                self._nugget_eval = NuggetRecallEvaluator(self.provider, opts.nugget)
            score, nuggets = await self._nugget_eval.evaluate(query, answer, retrieval_result)
            self._last_nuggets = nuggets
            return await self._maybe_escalate(score, metric, query, answer, contexts_text)

        # ── 특수 경로: claim NLI faithfulness (봇 실제 컨텍스트 fctx 기준) ──
        if metric == "faithfulness" and self.use_claim_faithfulness:
            if self._faith_eval is None:
                from AutoAudit.app.cp4_evaluator.faithfulness import FaithfulnessEvaluator
                self._faith_eval = FaithfulnessEvaluator(self.provider)
            forward_score = await self._faith_eval.evaluate(answer, fctx)

            # ⑤ 수치 가드: 결정적 수치 환각 포착 → 감점
            forward_score = self._apply_numeric_guard(forward_score, answer, fctx)

            # 역방향 검증: faithfulness에 reverse 적용
            if opts.reverse.enabled and metric in opts.reverse.metrics:
                return await self._apply_reverse(forward_score, metric, query, answer, fctx)

            return await self._maybe_escalate(forward_score, metric, query, answer, fctx)

        # ── 앙상블 경로 ──
        if opts.ensemble.enabled:
            if self._ensemble is None:
                from AutoAudit.app.cp4_evaluator.ensemble import EnsembleJudge
                self._ensemble = EnsembleJudge(opts.ensemble)
            prompt = self._build_prompt(
                metric, query, answer, pctx, use_cot=False,
                ground_truth=ground_truth, history=history,
            )
            return await self._ensemble.score(metric, prompt, self._SYSTEM)

        # ── 편향 보정 경로 ──
        if opts.calibration.enabled:
            if self._calibrator is None:
                from AutoAudit.app.cp4_evaluator.calibration import JudgeCalibrator
                self._calibrator = JudgeCalibrator(self.provider, opts.calibration)
            prompt = self._build_prompt(
                metric, query, answer, pctx, use_cot=False,
                ground_truth=ground_truth, history=history,
            )
            score = await self._calibrator.score(metric, prompt, self._SYSTEM)
            return await self._maybe_escalate(score, metric, query, answer, pctx)

        # ── 기본 경로: CoT 여부 판단 후 샘플링 ──
        use_cot = opts.cot.enabled and metric in opts.cot.metrics
        prompt = self._build_prompt(
            metric, query, answer, pctx, use_cot=use_cot,
            ground_truth=ground_truth, history=history,
        )

        if self.n_samples <= 1:
            samples = [await self._single_call(prompt, metric, self.temperature, use_cot=use_cot)]
        else:
            coros = [
                self._single_call(prompt, metric, self.sample_temperature, use_cot=use_cot)
                for _ in range(self.n_samples)
            ]
            samples = await gather_with_concurrency(coros, concurrency=self.n_samples)

        forward_score = self._aggregate_samples(metric, samples)
        if use_cot:
            forward_score.method = "cot"

        # 역방향 검증 적용
        if opts.reverse.enabled and metric in opts.reverse.metrics:
            return await self._apply_reverse(forward_score, metric, query, answer, pctx)

        return await self._maybe_escalate(forward_score, metric, query, answer, pctx)

    # ----------------------------------------------------------
    # ① 정답성 / ④ 적정 거절 / ⑤ 수치 가드 헬퍼
    # ----------------------------------------------------------

    async def _evaluate_correctness(self, answer: str, ground_truth: str) -> MetricScore:
        """① ground_truth 대비 정답성(claim F1 + 의미 유사도) 메트릭."""
        if self._correctness_eval is None:
            from AutoAudit.app.cp4_evaluator.correctness import CorrectnessEvaluator
            self._correctness_eval = CorrectnessEvaluator(self.provider, self.options.correctness)
        return await self._correctness_eval.evaluate(answer, ground_truth)

    async def _assess_abstention(self, query: str, answer: str, contexts_text: str):
        """④ 답변이 거절/모름인지 + 정당한지 1회 판정 (메트릭 간 공유)."""
        if not self.options.abstention.enabled:
            return None
        if self._abstention is None:
            from AutoAudit.app.cp4_evaluator.abstention import AbstentionDetector
            self._abstention = AbstentionDetector(self.provider)
        return await self._abstention.assess(query, answer, contexts_text)

    def _exempt_score(self, metric: str, reasoning: str) -> MetricScore:
        """정당한 거절에 대한 감점 면제 점수."""
        return MetricScore(
            metric=metric,
            score=self.options.abstention.exempt_score,
            reasoning=f"[적정 거절 — 감점 면제] {reasoning}",
            method="abstention_exempt",
            abstention=True,
        )

    def _apply_numeric_guard(
        self, score: MetricScore, answer: str, contexts_text: str
    ) -> MetricScore:
        """⑤ faithfulness에 결정적 수치 가드 적용."""
        ng = self.options.numeric_guard
        if not ng.enabled or score.metric != ng.apply_to:
            return score
        from AutoAudit.app.cp4_evaluator.numeric_guard import apply_guard
        guarded, conflicts = apply_guard(
            score.score, answer, contexts_text,
            penalty_per_conflict=ng.penalty_per_conflict, check_dates=ng.check_dates,
        )
        if not conflicts:
            return score
        score.score = guarded
        score.numeric_flags = conflicts
        score.is_low_confidence = True
        score.reasoning = f"[수치 가드 -{len(conflicts)}건: {', '.join(conflicts)}] {score.reasoning}"
        return score

    # ----------------------------------------------------------
    # CoT 프롬프트 선택
    # ----------------------------------------------------------

    def _build_prompt(
        self,
        metric: str,
        query: str,
        answer: str,
        contexts_text: str,
        use_cot: bool,
        ground_truth: str | None = None,
        history: list[str] | None = None,
    ) -> str:
        """CoT/참조기반 여부에 따라 프롬프트 선택 + 대화 맥락 주입.

        ② 참조 기반: answer_relevance에 ground_truth가 있으면 모범답안 대비 채점
        ③ 대화 맥락: 지정 메트릭에 직전 N턴 이력을 프롬프트 앞에 주입
        """
        opts = self.options
        # ② 참조 기반 채점 — answer_relevance + 정답 존재 시
        if (
            metric == "answer_relevance"
            and opts.correctness.reference_guided
            and ground_truth
        ):
            prompt = _COT_ANSWER_RELEVANCE_REF_PROMPT.format(
                query=query, answer=answer, contexts=contexts_text, ground_truth=ground_truth,
            )
        elif use_cot and metric in METRIC_COT_PROMPTS:
            prompt = METRIC_COT_PROMPTS[metric].format(
                query=query, answer=answer, contexts=contexts_text,
            )
        else:
            prompt = METRIC_PROMPTS.get(metric, "").format(
                query=query, answer=answer, contexts=contexts_text,
            )

        # ③ 대화 맥락 주입 — 지정 메트릭에 직전 턴 이력 prepend
        if (
            history
            and opts.context_injection.enabled
            and metric in opts.context_injection.metrics
        ):
            prompt = self._format_history_block(history) + prompt
        return prompt

    def _format_history_block(self, history: list[str]) -> str:
        """직전 대화 맥락 블록 생성 (최근 max_history_turns개)."""
        n = self.options.context_injection.max_history_turns
        recent = history[-n:] if n > 0 else history
        body = "\n".join(recent)
        return (
            "[직전 대화 맥락] (아래 평가 시 이 맥락을 참고해 지시대명사·생략을 해석하세요)\n"
            f"{body}\n\n"
        )

    # ----------------------------------------------------------
    # CoT 파싱 — 단계별 추론 추출
    # ----------------------------------------------------------

    @staticmethod
    def _parse_cot_score(raw: str, metric: str) -> MetricScore:
        """CoT 응답에서 단계별 추론 + 최종 점수 추출"""
        try:
            data = json.loads(raw)
            score = float(data.get("score", 0.0))
            reasoning = data.get("reasoning", "")
            grounding = data.get("grounding_chunks", [])

            # 메트릭별 CoT 단계 추출
            cot_steps: list[str] = []
            if metric == "answer_relevance":
                if intent := data.get("step1_intent"):
                    cot_steps.append(f"[의도] {intent}")
                if items := data.get("step2_items"):
                    cot_steps.append(f"[답변 항목] {', '.join(items)}")
                if missing := data.get("step3_missing"):
                    cot_steps.append(f"[누락] {', '.join(missing)}")
                if irrelevant := data.get("step3_irrelevant"):
                    cot_steps.append(f"[불필요] {', '.join(irrelevant)}")
            elif metric == "context_precision":
                if verdicts := data.get("step1_verdicts"):
                    for chunk, verdict in verdicts.items():
                        cot_steps.append(f"{chunk}: {verdict}")
                if ratio := data.get("step2_ratio"):
                    cot_steps.append(f"[비율] {ratio}")
            elif metric == "context_recall":
                if required := data.get("step1_required"):
                    cot_steps.append(f"[필요 정보] {', '.join(required)}")
                if coverage := data.get("step2_coverage"):
                    for info, status in coverage.items():
                        cot_steps.append(f"  {info}: {status}")
                if ratio := data.get("step3_ratio"):
                    cot_steps.append(f"[커버리지] {ratio}")

            return MetricScore(
                metric=metric,
                score=round(score, 4),
                reasoning=reasoning,
                grounding_chunks=grounding,
                cot_steps=cot_steps,
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning(f"CoT score parse failed for [{metric}]: {exc}. Raw: {raw[:120]}")
            return MetricScore(metric=metric, score=0.0, reasoning=f"Parse error: {exc}")

    # ----------------------------------------------------------
    # 역방향 검증
    # ----------------------------------------------------------

    async def _apply_reverse(
        self,
        forward_score: MetricScore,
        metric: str,
        query: str,
        answer: str,
        contexts_text: str,
    ) -> MetricScore:
        """
        역방향 프롬프트로 독립 평가 후 순방향과 결합.

        결합 공식:
          final = forward × weight_f + reverse × weight_r
          consistency = |forward - reverse|
          is_low_confidence = (consistency > threshold) OR forward.is_low_confidence
        """
        rev_opts = self.options.reverse
        rev_template = METRIC_REVERSE_PROMPTS.get(metric)
        if not rev_template:
            logger.debug(f"[reverse] 메트릭 {metric}에 역방향 프롬프트 없음 — 순방향만 사용")
            return forward_score

        rev_prompt = rev_template.format(
            query=query, answer=answer, contexts=contexts_text
        )
        reverse_raw = await self.provider.complete(
            rev_prompt, system=self._SYSTEM, temperature=self.temperature, json_mode=True
        )
        reverse_parsed = self._parse_score(reverse_raw, metric)

        f_score = forward_score.score
        r_score = reverse_parsed.score
        consistency = abs(f_score - r_score)
        final_score = (
            f_score * rev_opts.weight_forward
            + r_score * rev_opts.weight_reverse
        )
        is_low = (
            consistency > rev_opts.inconsistency_threshold
            or forward_score.is_low_confidence
        )

        if consistency > rev_opts.inconsistency_threshold:
            logger.warning(
                f"[reverse] {metric} 불일치 감지 — "
                f"forward={f_score:.3f}, reverse={r_score:.3f}, "
                f"consistency={consistency:.3f} > threshold={rev_opts.inconsistency_threshold}"
            )

        # 역방향 CoT 단계 추출
        reverse_cot_steps = _extract_reverse_cot_steps(reverse_raw, metric)

        return MetricScore(
            metric=metric,
            score=round(final_score, 4),
            reasoning=(
                f"[순방향 {f_score:.3f}] {forward_score.reasoning} | "
                f"[역방향 {r_score:.3f}] {reverse_parsed.reasoning}"
            ),
            grounding_chunks=forward_score.grounding_chunks,
            confidence=forward_score.confidence,
            sample_scores=forward_score.sample_scores,
            is_low_confidence=is_low,
            claims=forward_score.claims,
            method="cot_reverse" if forward_score.method == "cot" else "reverse",
            cot_steps=forward_score.cot_steps + reverse_cot_steps,
            forward_score=round(f_score, 4),
            reverse_score=round(r_score, 4),
            consistency_score=round(consistency, 4),
        )

    # ----------------------------------------------------------
    # 라우팅 에스컬레이션
    # ----------------------------------------------------------

    async def _maybe_escalate(
        self, score: MetricScore, metric: str, query: str, answer: str, contexts_text: str
    ) -> MetricScore:
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

    # ----------------------------------------------------------
    # 단일 LLM 호출 + 파싱
    # ----------------------------------------------------------

    async def _single_call(
        self, prompt: str, metric: str, temperature: float, use_cot: bool = False
    ) -> MetricScore:
        raw = await self.provider.complete(
            prompt, system=self._SYSTEM, temperature=temperature, json_mode=True,
        )
        if use_cot and metric in METRIC_COT_PROMPTS:
            return self._parse_cot_score(raw, metric)
        return self._parse_score(raw, metric)

    def _aggregate_samples(self, metric: str, samples: list[MetricScore]) -> MetricScore:
        valid = [s for s in samples if s.reasoning and not s.reasoning.startswith("Parse error")]
        if not valid:
            valid = samples

        sample_scores = [s.score for s in valid]
        median_score = statistics.median(sample_scores)
        std = statistics.pstdev(sample_scores) if len(sample_scores) > 1 else 0.0
        confidence = max(0.0, 1.0 - std / 0.5)
        is_low = std > self.low_confidence_std

        best = min(valid, key=lambda s: abs(s.score - median_score))

        if is_low:
            logger.warning(f"[{metric}] low confidence — std={std:.3f}, scores={sample_scores}")

        # CoT steps: 중앙값 샘플의 steps 채택
        best_cot_steps = getattr(best, "cot_steps", [])

        return MetricScore(
            metric=metric,
            score=round(median_score, 4),
            reasoning=best.reasoning,
            grounding_chunks=best.grounding_chunks,
            confidence=round(confidence, 4),
            sample_scores=sample_scores,
            is_low_confidence=is_low,
            cot_steps=best_cot_steps,
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


# ══════════════════════════════════════════════════════════════
# 역방향 CoT 단계 추출 (모듈 레벨 헬퍼)
# ══════════════════════════════════════════════════════════════

def _extract_reverse_cot_steps(raw: str, metric: str) -> list[str]:
    """역방향 응답에서 단계별 추론을 [역방향] 접두사와 함께 추출"""
    try:
        data = json.loads(raw)
        steps: list[str] = []
        if metric == "faithfulness":
            if derivability := data.get("step1_derivability"):
                for sent, verdict in derivability.items():
                    steps.append(f"[역방향] {sent}: {verdict}")
            if ext_knowledge := data.get("step2_external_knowledge"):
                if ext_knowledge:
                    steps.append(f"[역방향 외부지식] {', '.join(ext_knowledge)}")
        elif metric == "answer_relevance":
            if keywords := data.get("step1_keywords"):
                steps.append(f"[역방향 키워드] {', '.join(keywords)}")
            if inferred := data.get("step2_inferred_question"):
                steps.append(f"[역방향 추론 질문] {inferred}")
            if match := data.get("step2_match_level"):
                steps.append(f"[역방향 일치도] {match}")
        return steps
    except (json.JSONDecodeError, AttributeError):
        return []
