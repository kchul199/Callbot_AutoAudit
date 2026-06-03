"""
tests/test_meta_domain.py
메타평가 통계 + 도메인 메트릭(PII/일관성) 검증
"""
import json

import pytest

from AutoAudit.app.core.types import (
    CallLog,
    ConversationTurn,
    EvaluationRecord,
    MetricScore,
    RetrievalResult,
    TurnRole,
)
from AutoAudit.app.cp4_evaluator.domain_metrics import DomainMetricsEvaluator
from AutoAudit.app.cp4_evaluator.meta_eval import MetaEvaluator
from AutoAudit.app.cp4_evaluator.options import DomainMetricsOptions, MetaEvalOptions

# ---- 메타평가 통계 ----

def test_spearman_perfect():
    m = MetaEvaluator(MetaEvalOptions())
    assert m._spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert m._spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_kappa_perfect_agreement():
    m = MetaEvaluator(MetaEvalOptions())
    assert m._cohen_kappa([0.0, 0.5, 1.0, 0.25], [0.0, 0.5, 1.0, 0.25]) == pytest.approx(1.0)


def test_meta_eval_with_golden(tmp_path):
    golden = tmp_path / "golden.jsonl"
    golden.write_text(
        "\n".join([
            json.dumps({"qa_id": "q1", "metric": "faithfulness", "human_score": 0.9}),
            json.dumps({"qa_id": "q2", "metric": "faithfulness", "human_score": 0.5}),
        ]),
        encoding="utf-8",
    )
    opts = MetaEvalOptions(golden_set_path=str(golden))
    recs = [
        EvaluationRecord(eval_id="e1", qa_id="q1", call_id="c", query="q", generated_answer="a",
                         retrieval_result=RetrievalResult(query="q", contexts=[]),
                         scores=[MetricScore(metric="faithfulness", score=0.85, reasoning="")]),
        EvaluationRecord(eval_id="e2", qa_id="q2", call_id="c", query="q", generated_answer="a",
                         retrieval_result=RetrievalResult(query="q", contexts=[]),
                         scores=[MetricScore(metric="faithfulness", score=0.55, reasoning="")]),
    ]
    result = MetaEvaluator(opts).evaluate(recs)
    assert result["available"] is True
    assert result["n"] == 2
    assert "mae" in result["overall"]


def test_meta_eval_no_golden():
    result = MetaEvaluator(MetaEvalOptions(golden_set_path="/nonexistent.jsonl")).evaluate([])
    assert result["available"] is False


# ---- 도메인: PII ----

def test_pii_rrn():
    d = DomainMetricsEvaluator(provider=None, options=DomainMetricsOptions())
    assert "주민등록번호" in d.detect_pii("주민번호 901201-1234567 확인")


def test_pii_phone():
    d = DomainMetricsEvaluator(provider=None, options=DomainMetricsOptions())
    assert "전화번호" in d.detect_pii("연락처 010-1234-5678")


def test_pii_clean():
    d = DomainMetricsEvaluator(provider=None, options=DomainMetricsOptions())
    assert d.detect_pii("요금제 변경을 도와드리겠습니다") == []


# ---- 도메인: 멀티턴 일관성 (mock) ----

class _FakeProvider:
    def __init__(self, payload):
        self._payload = payload

    async def complete(self, prompt, **kwargs):
        return self._payload

    async def embed(self, text):
        return [0.0] * 8


@pytest.mark.asyncio
async def test_multiturn_consistency_short():
    """봇 답변 1개 → 평가 불필요, score=1.0"""
    d = DomainMetricsEvaluator(_FakeProvider("{}"), DomainMetricsOptions())
    log = CallLog(call_id="c", subscriber_id="s", turns=[
        ConversationTurn(turn_id=0, role=TurnRole.USER, content="질문입니다"),
        ConversationTurn(turn_id=1, role=TurnRole.BOT, content="답변입니다"),
    ])
    score = await d.multiturn_consistency(log)
    assert score.score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_multiturn_consistency_conflict():
    payload = json.dumps({"consistent": False, "score": 0.3, "reasoning": "모순",
                          "conflicts": ["턴2 vs 턴4"]})
    d = DomainMetricsEvaluator(_FakeProvider(payload), DomainMetricsOptions())
    log = CallLog(call_id="c", subscriber_id="s", turns=[
        ConversationTurn(turn_id=0, role=TurnRole.BOT, content="요금은 5만원입니다"),
        ConversationTurn(turn_id=1, role=TurnRole.BOT, content="요금은 7만원입니다"),
    ])
    score = await d.multiturn_consistency(log)
    assert score.score == pytest.approx(0.3)
    assert score.metric == "multiturn_consistency"


@pytest.mark.asyncio
async def test_safety_pii_immediate_fail():
    """PII 노출 → LLM 호출 없이 즉시 0점"""
    d = DomainMetricsEvaluator(_FakeProvider("{}"), DomainMetricsOptions())
    score = await d.safety_compliance("고객님 주민번호 901201-1234567 맞으신가요")
    assert score.score == 0.0
    assert "PII" in score.reasoning
