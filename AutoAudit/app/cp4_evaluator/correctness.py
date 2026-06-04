"""
cp4_evaluator/correctness.py
정답성(Answer Correctness) 평가 — ground_truth(정답) 대비 ① + ②.

faithfulness는 "검색된 컨텍스트에 충실한가"만 본다. 컨텍스트 자체가 틀렸거나
봇이 정답과 다른 말을 해도 컨텍스트에만 맞으면 통과한다.
이 모듈은 "정답과 비교해 실제로 맞는가"를 직접 검증하는 유일한 축이다.

방식 (RAGAS answer_correctness 계열):
  1. 답변/정답을 원자적 진술로 분해(또는 LLM이 직접 TP/FP/FN 분류)
  2. TP = 정답·답변 모두에 있는 옳은 진술
     FP = 답변에만 있는(틀렸거나 불필요) 진술
     FN = 정답에만 있는(누락) 진술
  3. claim F1 = 2·TP / (2·TP + FP + FN)
  4. 의미 유사도 = 답변·정답 임베딩 코사인
  5. score = F1 × weight_f1 + similarity × weight_similarity
"""
from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING

from AutoAudit.app.core.llm_client import LLMProvider
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import MetricScore

if TYPE_CHECKING:
    from AutoAudit.app.cp4_evaluator.options import CorrectnessOptions

logger = get_logger(__name__)

_SYSTEM = "당신은 RAG 답변의 정답성 평가 전문가입니다. 반드시 유효한 JSON만 반환하세요."

_ALIGN_PROMPT = """
당신은 RAG 답변의 정답성(correctness)을 평가하는 전문가입니다.
[정답]과 [답변]의 진술을 비교해 아래 세 분류로 나누세요. (정답성(correctness) 분류)

- TP: 정답과 답변 모두에 있는, 사실상 동일한 옳은 진술
- FP: 답변에는 있지만 정답에는 없는(틀렸거나 불필요한) 진술
- FN: 정답에는 있지만 답변이 누락한 진술

[정답]
{ground_truth}

[답변]
{answer}

출력 형식 (JSON):
{{"tp": ["일치 진술", ...], "fp": ["오류/불필요 진술", ...], "fn": ["누락 진술", ...], "reasoning": "근거(한국어)"}}
"""


class CorrectnessEvaluator:
    """정답 대비 claim F1 + 의미 유사도 = answer_correctness."""

    def __init__(self, provider: LLMProvider, options: CorrectnessOptions) -> None:
        self.provider = provider
        self.opts = options

    async def evaluate(self, answer: str, ground_truth: str) -> MetricScore:
        if not ground_truth or not ground_truth.strip():
            return MetricScore(
                metric=self.opts.metric_name, score=0.0,
                reasoning="정답(ground_truth) 없음 — 정답성 평가 불가",
                method="answer_correctness",
            )

        # 1) TP/FP/FN 분류 (LLM 1회)
        raw = await self.provider.complete(
            _ALIGN_PROMPT.format(ground_truth=ground_truth, answer=answer),
            system=_SYSTEM, temperature=0.0, json_mode=True,
        )
        tp, fp, fn, reasoning = self._parse_align(raw)
        f1 = self._f1(len(tp), len(fp), len(fn))

        # 2) 의미 유사도 (임베딩 코사인)
        sim = await self._similarity(answer, ground_truth)

        # 3) 가중 결합
        score = f1 * self.opts.weight_f1 + sim * self.opts.weight_similarity
        score = round(max(0.0, min(1.0, score)), 4)

        return MetricScore(
            metric=self.opts.metric_name,
            score=score,
            reasoning=(
                f"정답성 F1={f1:.3f} (TP {len(tp)}, FP {len(fp)}, FN {len(fn)}), "
                f"의미유사도={sim:.3f} | {reasoning}"
            ),
            method="answer_correctness",
            correctness_f1=round(f1, 4),
            correctness_sim=round(sim, 4),
            # 누락(FN)·오류(FP) 진술을 grounding_chunks에 기록 → Evidence View 직결
            grounding_chunks=[f"FN:{s}" for s in fn] + [f"FP:{s}" for s in fp],
            is_low_confidence=(len(fp) + len(fn) > len(tp)),
        )

    # ----------------------------------------------------------

    @staticmethod
    def _f1(tp: int, fp: int, fn: int) -> float:
        denom = 2 * tp + fp + fn
        return (2 * tp / denom) if denom > 0 else 1.0  # 진술 없음 → 모순 없음 → 1.0

    async def _similarity(self, answer: str, ground_truth: str) -> float:
        try:
            a = await self.provider.embed(answer)
            b = await self.provider.embed(ground_truth)
            return max(0.0, self._cosine(a, b))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"정답성 유사도 계산 실패: {exc}")
            return 0.0

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)

    @staticmethod
    def _parse_align(raw: str) -> tuple[list[str], list[str], list[str], str]:
        try:
            d = json.loads(raw)
            tp = [s for s in d.get("tp", []) if isinstance(s, str)]
            fp = [s for s in d.get("fp", []) if isinstance(s, str)]
            fn = [s for s in d.get("fn", []) if isinstance(s, str)]
            return tp, fp, fn, d.get("reasoning", "")
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.warning(f"정답성 분류 파싱 실패: {exc}. Raw: {raw[:100]}")
            return [], [], [], f"Parse error: {exc}"
