"""
tests/test_calibration.py
Judge 편향 보정 + G-Eval 검증
"""
import json

import pytest

from AutoAudit.app.cp4_evaluator.calibration import JudgeCalibrator
from AutoAudit.app.cp4_evaluator.options import CalibrationOptions


class FixedProvider:
    """항상 같은 score를 반환하는 가짜 provider"""

    def __init__(self, score=0.8, reasoning="ok"):
        self._payload = json.dumps({"score": score, "reasoning": reasoning})
        self.calls = 0

    async def complete(self, prompt, **kwargs):
        self.calls += 1
        return self._payload

    async def embed(self, text):
        return [0.0] * 8


class LogprobProvider(FixedProvider):
    """complete_with_logprobs 지원 provider (G-Eval 경로)"""

    async def complete_with_logprobs(self, prompt, **kwargs):
        # "1"(=1.0) 80%, "0.5" 20% → 기대값 0.9
        return [("1", 0.8), ("0.5", 0.2)]


def test_decorate_prompt_adds_anchor_and_length():
    opts = CalibrationOptions(use_anchors=True, length_normalize=True)
    cal = JudgeCalibrator(FixedProvider(), opts)
    decorated = cal.decorate_prompt("원본 프롬프트")
    assert "평가 척도 기준" in decorated   # anchor block
    assert "길이" in decorated             # length-norm 지시문
    assert "원본 프롬프트" in decorated


def test_decorate_prompt_toggles_off():
    opts = CalibrationOptions(use_anchors=False, length_normalize=False)
    cal = JudgeCalibrator(FixedProvider(), opts)
    decorated = cal.decorate_prompt("원본")
    assert decorated == "원본"


@pytest.mark.asyncio
async def test_g_eval_multisample_mean():
    """logprob 미지원 → 다중 샘플 기댓값 (3회 호출, 동일 점수 → 평균=0.8)"""
    provider = FixedProvider(score=0.8)
    cal = JudgeCalibrator(provider, CalibrationOptions(g_eval_logprobs=False))
    score = await cal.score("faithfulness", "프롬프트", "시스템")
    assert score.score == pytest.approx(0.8)
    assert score.method == "g_eval"
    assert provider.calls == 3            # 다중 샘플
    assert len(score.sample_scores) == 3


@pytest.mark.asyncio
async def test_g_eval_logprob_expectation():
    """logprob 지원 → 분포 기대값 (1*0.8 + 0.5*0.2 = 0.9)"""
    cal = JudgeCalibrator(LogprobProvider(), CalibrationOptions(g_eval_logprobs=True))
    score = await cal.score("faithfulness", "프롬프트", "시스템")
    assert score.score == pytest.approx(0.9, abs=1e-6)
    assert score.method == "g_eval"


@pytest.mark.asyncio
async def test_g_eval_logprob_fallback_when_unsupported():
    """logprob 요청했으나 provider 미지원 → 다중샘플 fallback"""
    provider = FixedProvider(score=0.7)
    cal = JudgeCalibrator(provider, CalibrationOptions(g_eval_logprobs=True))
    score = await cal.score("faithfulness", "프롬프트", "시스템")
    assert score.score == pytest.approx(0.7)
    assert provider.calls == 3            # fallback 경로 진입
