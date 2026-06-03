"""
tests/test_ensemble.py
다중 Judge 앙상블 + 불일치 에스컬레이션 검증
(provider를 직접 주입해 네트워크 없이 분기 검증)
"""
import json

import pytest

from AutoAudit.app.cp4_evaluator.ensemble import EnsembleJudge
from AutoAudit.app.cp4_evaluator.options import EnsembleOptions


class StubProvider:
    def __init__(self, score, reasoning="r"):
        self._payload = json.dumps({"score": score, "reasoning": reasoning})
        self.calls = 0

    async def complete(self, prompt, **kwargs):
        self.calls += 1
        return self._payload

    async def embed(self, text):
        return [0.0] * 8


def _judge(providers: dict, **opt_kwargs) -> EnsembleJudge:
    """provider init을 우회하고 stub 주입"""
    opts = EnsembleOptions(providers=list(providers), **opt_kwargs)
    j = EnsembleJudge.__new__(EnsembleJudge)
    j.opts = opts
    j._providers = providers
    from AutoAudit.app.core.llm_client import CostTracker
    j._cost = CostTracker()
    return j


@pytest.mark.asyncio
async def test_agreement_mean_aggregation():
    """두 provider 점수 근접 → 평균 집계, 에스컬레이션 없음"""
    j = _judge({"openai": StubProvider(0.8), "anthropic": StubProvider(0.82)},
               disagreement_threshold=0.25, aggregation="mean")
    score = await j.score("faithfulness", "프롬프트", "시스템")
    assert score.method == "ensemble"
    assert score.score == pytest.approx(0.81)
    assert score.escalated is False
    assert score.is_low_confidence is False
    assert set(score.ensemble_scores) == {"openai", "anthropic"}


@pytest.mark.asyncio
async def test_disagreement_flags_low_confidence():
    """점수 크게 갈림 → low_confidence, 에스컬레이션 비활성(mean)이면 집계"""
    j = _judge({"openai": StubProvider(0.2), "anthropic": StubProvider(0.95)},
               disagreement_threshold=0.25, aggregation="mean")
    score = await j.score("faithfulness", "프롬프트", "시스템")
    assert score.is_low_confidence is True
    assert score.escalated is False
    assert score.score == pytest.approx(0.575)


@pytest.mark.asyncio
async def test_disagreement_triggers_escalation():
    """불일치 + aggregation='escalate' → 심판 provider가 최종 점수 결정"""
    judge_provider = StubProvider(0.2)
    referee = StubProvider(0.9)
    j = _judge({"openai": judge_provider, "anthropic": StubProvider(0.95)},
               disagreement_threshold=0.25, aggregation="escalate",
               escalate_provider="referee")
    j._providers["referee"] = referee
    score = await j.score("faithfulness", "프롬프트", "시스템")
    assert score.escalated is True
    assert score.score == pytest.approx(0.9)   # 심판 점수 채택


@pytest.mark.asyncio
async def test_median_aggregation():
    j = _judge(
        {"a": StubProvider(0.1), "b": StubProvider(0.5), "c": StubProvider(0.9)},
        disagreement_threshold=1.0, aggregation="median",
    )
    score = await j.score("faithfulness", "프롬프트", "시스템")
    assert score.score == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_single_provider_fallback():
    """provider 1개 → available=False, 단일 평가 폴백"""
    j = _judge({"openai": StubProvider(0.77)})
    assert j.available is False
    score = await j.score("faithfulness", "프롬프트", "시스템")
    assert score.method == "single"
    assert score.score == pytest.approx(0.77)
