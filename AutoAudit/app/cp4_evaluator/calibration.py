"""
cp4_evaluator/calibration.py
Judge 편향 보정 — position/verbosity/leniency bias 완화 + G-Eval 스코어링.

핵심:
  - anchor 예시로 척도 고정 (leniency drift 방지)
  - length-normalization 지시문 (verbosity bias 완화)
  - G-Eval: 점수 분포의 기댓값 사용 (단일 정수보다 분산↓, 인간 상관↑)
    provider가 logprob 미지원이면 다중 샘플 기댓값으로 graceful degrade
"""
from __future__ import annotations

import json
import statistics

from AutoAudit.app.core.llm_client import LLMProvider
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import MetricScore
from AutoAudit.app.cp4_evaluator.options import CalibrationOptions

logger = get_logger(__name__)

# 척도 고정용 앵커 (leniency drift 방지)
_ANCHOR_BLOCK = """
[평가 척도 기준 — 반드시 준수]
- 1.0: 모든 면에서 완벽, 근거 완전 일치
- 0.75: 대체로 정확, 사소한 누락/모호함
- 0.5: 부분적으로 맞으나 중요한 결함 존재
- 0.25: 대부분 부정확하거나 근거 빈약
- 0.0: 완전히 부정확/무관/환각
"""

_LENGTH_NORM = (
    "\n[주의] 답변의 길이나 표현의 화려함이 아닌 '내용의 정확성과 근거'만으로 평가하세요. "
    "길다고 높은 점수를 주지 마세요."
)

# G-Eval: 이산 점수 후보 (확률 가중 기댓값 계산용)
_SCORE_BINS = [0.0, 0.25, 0.5, 0.75, 1.0]


class JudgeCalibrator:
    def __init__(self, provider: LLMProvider, options: CalibrationOptions) -> None:
        self.provider = provider
        self.opts = options

    def decorate_prompt(self, prompt: str) -> str:
        """앵커/길이정규화 지시문을 프롬프트에 삽입"""
        out = prompt
        if self.opts.use_anchors:
            out = _ANCHOR_BLOCK + "\n" + out
        if self.opts.length_normalize:
            out = out + _LENGTH_NORM
        return out

    async def score(self, metric: str, prompt: str, system: str) -> MetricScore:
        """
        보정된 단일 메트릭 점수.
        g_eval_logprobs=True 면 분포 기댓값, 아니면 다중 샘플 기댓값.
        """
        decorated = self.decorate_prompt(prompt)

        if self.opts.g_eval_logprobs:
            score = await self._g_eval_logprob(decorated, system)
            if score is not None:
                return MetricScore(
                    metric=metric, score=round(score, 4),
                    reasoning="G-Eval logprob 기대점수", method="g_eval",
                )
            # 미지원 → fallback

        # G-Eval 다중 샘플 기댓값 (logprob 대체)
        samples = []
        for _ in range(3):
            raw = await self.provider.complete(
                decorated, system=system, temperature=0.6, json_mode=True
            )
            parsed = self._parse(raw)
            if parsed is not None:
                samples.append(parsed)
        if not samples:
            return MetricScore(metric=metric, score=0.0,
                               reasoning="보정 평가 파싱 실패", method="g_eval")
        mean = statistics.mean(s[0] for s in samples)
        return MetricScore(
            metric=metric, score=round(mean, 4),
            reasoning=samples[0][1], method="g_eval",
            sample_scores=[s[0] for s in samples],
        )

    async def _g_eval_logprob(self, prompt: str, system: str) -> float | None:
        """
        provider.complete_with_logprobs 지원 시 점수 토큰 분포의 기댓값 산출.
        미지원이면 None 반환 → 호출측에서 fallback.
        """
        fn = getattr(self.provider, "complete_with_logprobs", None)
        if fn is None:
            return None
        try:
            ge_prompt = (
                prompt
                + "\n\n0, 0.25, 0.5, 0.75, 1 중 하나의 숫자만 한 글자로 답하세요."
            )
            token_probs = await fn(ge_prompt, system=system, max_tokens=4)
            # token_probs: list[(token, prob)]
            num, denom = 0.0, 0.0
            for tok, prob in token_probs:
                try:
                    v = float(tok.strip())
                except ValueError:
                    continue
                if v in _SCORE_BINS:
                    num += v * prob
                    denom += prob
            return num / denom if denom > 0 else None
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"G-Eval logprob 실패, fallback: {exc}")
            return None

    @staticmethod
    def _parse(raw: str) -> tuple[float, str] | None:
        try:
            d = json.loads(raw)
            return float(d.get("score", 0.0)), d.get("reasoning", "")
        except (json.JSONDecodeError, ValueError, AttributeError):
            return None
