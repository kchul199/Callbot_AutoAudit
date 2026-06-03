"""
tests/test_cp4_faithfulness.py
RAGAS claim 단위 NLI faithfulness 검증
"""
import json

import pytest

from AutoAudit.app.cp4_evaluator.faithfulness import FaithfulnessEvaluator


class ScriptedProvider:
    """프롬프트 내용에 따라 claim 추출 / verdict 응답을 분기하는 가짜 provider"""

    def __init__(self, claims: list[str], verdicts: list[dict]):
        self._claims = claims
        self._verdicts = verdicts

    async def complete(self, prompt: str, **kwargs) -> str:
        if "원자적 주장" in prompt:
            return json.dumps({"claims": self._claims})
        return json.dumps({"verdicts": self._verdicts})

    async def embed(self, text: str):
        return [0.0] * 8


@pytest.mark.asyncio
async def test_all_claims_supported():
    claims = ["요금제는 월 6만9천원이다", "데이터 무제한이다"]
    verdicts = [
        {"claim": claims[0], "verdict": "supported", "reasoning": "명시"},
        {"claim": claims[1], "verdict": "supported", "reasoning": "명시"},
    ]
    ev = FaithfulnessEvaluator(ScriptedProvider(claims, verdicts))
    score = await ev.evaluate("답변", "컨텍스트")
    assert score.metric == "faithfulness"
    assert score.score == pytest.approx(1.0)
    assert len(score.claims) == 2
    assert all(c.supported for c in score.claims)


@pytest.mark.asyncio
async def test_partial_support():
    claims = ["주장A", "주장B", "주장C"]
    verdicts = [
        {"claim": "주장A", "verdict": "supported", "reasoning": ""},
        {"claim": "주장B", "verdict": "contradicted", "reasoning": "모순"},
        {"claim": "주장C", "verdict": "unsupported", "reasoning": "정보없음"},
    ]
    ev = FaithfulnessEvaluator(ScriptedProvider(claims, verdicts))
    score = await ev.evaluate("답변", "컨텍스트")
    assert score.score == pytest.approx(1 / 3, abs=1e-3)
    assert len([c for c in score.claims if c.verdict == "contradicted"]) == 1


@pytest.mark.asyncio
async def test_no_claims_is_neutral():
    ev = FaithfulnessEvaluator(ScriptedProvider([], []))
    score = await ev.evaluate("안녕하세요", "컨텍스트")
    assert score.score == pytest.approx(1.0)
    assert score.claims == []


@pytest.mark.asyncio
async def test_missing_verdict_treated_unsupported():
    claims = ["주장A", "주장B"]
    verdicts = [{"claim": "주장A", "verdict": "supported", "reasoning": ""}]
    ev = FaithfulnessEvaluator(ScriptedProvider(claims, verdicts))
    score = await ev.evaluate("답변", "컨텍스트")
    assert score.score == pytest.approx(0.5)
    b = next(c for c in score.claims if c.claim == "주장B")
    assert b.supported is False and b.verdict == "unsupported"
