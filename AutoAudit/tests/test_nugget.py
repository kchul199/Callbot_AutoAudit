"""
tests/test_nugget.py
Nugget 기반 context recall 검증 (GT-free)
"""
import json

import pytest

from AutoAudit.app.core.types import RetrievalResult, RetrievedContext
from AutoAudit.app.cp4_evaluator.nugget import NuggetRecallEvaluator
from AutoAudit.app.cp4_evaluator.options import NuggetOptions


class ScriptedProvider:
    """추출/매칭 단계를 프롬프트로 분기하는 가짜 provider"""

    def __init__(self, nuggets, matches):
        self._nuggets = nuggets
        self._matches = matches

    async def complete(self, prompt, **kwargs):
        if "핵심 정보 조각(nugget)을 추출" in prompt:
            return json.dumps({"nuggets": self._nuggets})
        return json.dumps({"matches": self._matches})

    async def embed(self, text):
        return [0.0] * 8


def _retrieval():
    return RetrievalResult(
        query="요금제 변경 방법",
        contexts=[RetrievedContext(chunk_id="c1", content="요금제는 앱에서 변경 가능",
                                   score=0.9, source_call_id="C1")],
    )


@pytest.mark.asyncio
async def test_full_recall():
    nuggets = ["앱에서 변경 가능", "본인확인 필요"]
    matches = [
        {"nugget": "앱에서 변경 가능", "found": True, "chunk_hint": "앱"},
        {"nugget": "본인확인 필요", "found": True, "chunk_hint": "확인"},
    ]
    ev = NuggetRecallEvaluator(ScriptedProvider(nuggets, matches), NuggetOptions())
    score, ns = await ev.evaluate("질문", "답변", _retrieval())
    assert score.metric == "context_recall"
    assert score.score == pytest.approx(1.0)
    assert len(ns) == 2 and all(n.found_in_context for n in ns)


@pytest.mark.asyncio
async def test_partial_recall():
    nuggets = ["A", "B", "C", "D"]
    matches = [
        {"nugget": "A", "found": True, "chunk_hint": ""},
        {"nugget": "B", "found": False, "chunk_hint": ""},
        {"nugget": "C", "found": True, "chunk_hint": ""},
        {"nugget": "D", "found": False, "chunk_hint": ""},
    ]
    ev = NuggetRecallEvaluator(ScriptedProvider(nuggets, matches), NuggetOptions())
    score, ns = await ev.evaluate("질문", "답변", _retrieval())
    assert score.score == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_no_nuggets_returns_neutral():
    ev = NuggetRecallEvaluator(ScriptedProvider([], []), NuggetOptions())
    score, ns = await ev.evaluate("질문", "답변", _retrieval())
    assert score.score == pytest.approx(1.0)
    assert ns == []


@pytest.mark.asyncio
async def test_missing_match_treated_not_found():
    """매칭 응답에 누락된 nugget → found=False 보수 처리"""
    nuggets = ["A", "B"]
    matches = [{"nugget": "A", "found": True, "chunk_hint": ""}]  # B 누락
    ev = NuggetRecallEvaluator(ScriptedProvider(nuggets, matches), NuggetOptions())
    score, ns = await ev.evaluate("질문", "답변", _retrieval())
    assert score.score == pytest.approx(0.5)
    b = next(n for n in ns if n.text == "B")
    assert b.found_in_context is False


@pytest.mark.asyncio
async def test_max_nuggets_capped():
    nuggets = [f"n{i}" for i in range(20)]
    ev = NuggetRecallEvaluator(ScriptedProvider(nuggets, []), NuggetOptions(max_nuggets=5))
    extracted = await ev._extract("q", "a")
    assert len(extracted) == 5


@pytest.mark.asyncio
async def test_malformed_json_graceful():
    class BadProvider:
        async def complete(self, prompt, **kwargs):
            return "NOT JSON"
        async def embed(self, text):
            return [0.0] * 8

    ev = NuggetRecallEvaluator(BadProvider(), NuggetOptions())
    score, ns = await ev.evaluate("질문", "답변", _retrieval())
    # 추출 실패 → nugget 없음 → 중립 1.0
    assert score.score == pytest.approx(1.0)
