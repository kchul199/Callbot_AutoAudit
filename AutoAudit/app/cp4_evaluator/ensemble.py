"""
cp4_evaluator/ensemble.py
다중 Judge 앙상블 + 불일치 에스컬레이션.

여러 provider로 같은 메트릭을 평가 → 일치하면 집계, 불일치(표준편차 큼)하면
상위 모델로 메타 판정. 대부분의 호출은 기본 모델, 분쟁만 에스컬레이션 →
비용 대비 신뢰도 극대화.

self-enhancement bias 완화: 생성 모델과 다른 모델을 평가자로 섞는다.
"""
from __future__ import annotations

import json
import statistics

from AutoAudit.app.core.async_utils import gather_with_concurrency
from AutoAudit.app.core.llm_client import CostTracker, LLMProvider, create_provider
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import MetricScore
from AutoAudit.app.cp4_evaluator.options import EnsembleOptions

logger = get_logger(__name__)


class EnsembleJudge:
    def __init__(self, options: EnsembleOptions, cost_tracker: CostTracker | None = None) -> None:
        self.opts = options
        self._providers: dict[str, LLMProvider] = {}
        self._cost = cost_tracker or CostTracker()
        for name in options.providers:
            try:
                self._providers[name] = self._make_provider(name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"앙상블 provider '{name}' 초기화 실패 — 제외: {exc}")

    def _make_provider(self, name: str) -> LLMProvider:
        # provider 이름별 생성. create_provider는 settings의 provider를 보므로
        # 여기서는 임시로 환경에 맞는 provider를 만든다.
        from AutoAudit.app.core.llm_client import (
            AnthropicProvider,
            GoogleProvider,
            OpenAIProvider,
        )
        if name == "openai":
            return OpenAIProvider(self._cost)
        if name == "azure":
            return OpenAIProvider(self._cost, use_azure=True)
        if name == "anthropic":
            return AnthropicProvider(self._cost)
        if name in ("google", "gemini"):
            return GoogleProvider(self._cost)
        if name == "mock":
            from AutoAudit.app.core.mock_provider import MockProvider
            return MockProvider(self._cost)
        raise ValueError(f"지원하지 않는 ensemble provider: {name}")

    @property
    def available(self) -> bool:
        return len(self._providers) >= 2

    async def score(self, metric: str, prompt: str, system: str) -> MetricScore:
        """모든 provider 평가 → 일치/불일치 판정 → 집계 or 에스컬레이션"""
        if not self.available:
            logger.warning("앙상블 provider 부족(<2) — 단일 평가로 폴백")
            single = list(self._providers.values())[0] if self._providers else create_provider(self._cost)
            raw = await single.complete(prompt, system=system, temperature=0.0, json_mode=True)
            s = self._parse(raw)
            return MetricScore(metric=metric, score=s[0], reasoning=s[1], method="single")

        # 각 provider 병렬 평가
        names = list(self._providers.keys())
        coros = [
            self._providers[n].complete(prompt, system=system, temperature=0.0, json_mode=True)
            for n in names
        ]
        raws = await gather_with_concurrency(coros, concurrency=len(names))
        per_provider: dict[str, float] = {}
        reasonings: dict[str, str] = {}
        for n, raw in zip(names, raws, strict=False):
            score, reasoning = self._parse(raw)
            per_provider[n] = score
            reasonings[n] = reasoning

        scores = list(per_provider.values())
        std = statistics.pstdev(scores) if len(scores) > 1 else 0.0
        disagree = std > self.opts.disagreement_threshold

        # 불일치 → 에스컬레이션
        escalated = False
        if disagree and (self.opts.aggregation == "escalate" or self.opts.escalate_provider):
            esc_name = self.opts.escalate_provider or names[0]
            try:
                esc_provider = self._providers.get(esc_name) or self._make_provider(esc_name)
                esc_prompt = self._build_escalation_prompt(prompt, per_provider, reasonings)
                raw = await esc_provider.complete(esc_prompt, system=system, temperature=0.0, json_mode=True)
                final_score, final_reason = self._parse(raw)
                escalated = True
                logger.info(f"[ensemble] '{metric}' 불일치(std={std:.3f}) → {esc_name} 메타판정 {final_score:.2f}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"에스컬레이션 실패, 집계로 폴백: {exc}")
                final_score = self._aggregate(scores)
                final_reason = f"앙상블 집계 (에스컬레이션 실패): {std:.3f}"
        else:
            final_score = self._aggregate(scores)
            final_reason = (
                f"앙상블 {self.opts.aggregation} (provider {len(scores)}개, std={std:.3f})"
            )

        return MetricScore(
            metric=metric,
            score=round(final_score, 4),
            reasoning=final_reason,
            method="ensemble",
            ensemble_scores=per_provider,
            escalated=escalated,
            confidence=round(max(0.0, 1.0 - std / 0.5), 4),
            is_low_confidence=disagree,
            sample_scores=scores,
        )

    def _aggregate(self, scores: list[float]) -> float:
        if self.opts.aggregation == "median":
            return statistics.median(scores)
        return statistics.mean(scores)

    @staticmethod
    def _build_escalation_prompt(prompt: str, per_provider: dict, reasonings: dict) -> str:
        opinions = "\n".join(
            f"- 평가자 {n}: {per_provider[n]:.2f} ({reasonings.get(n, '')[:120]})"
            for n in per_provider
        )
        return (
            prompt
            + "\n\n[참고: 다른 평가자들의 의견 — 의견이 갈립니다. 최종 심판으로 판정하세요]\n"
            + opinions
        )

    @staticmethod
    def _parse(raw: str) -> tuple[float, str]:
        try:
            d = json.loads(raw)
            return float(d.get("score", 0.0)), d.get("reasoning", "")
        except (json.JSONDecodeError, ValueError, AttributeError):
            return 0.0, "파싱 실패"
