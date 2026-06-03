"""
tests/test_cp5_aggregator.py
CP5 집계 로직 검증 — SLA 판정 + 통계 정확성
"""
from datetime import UTC, datetime

import pytest

from AutoAudit.app.core.types import (
    EvaluationRecord,
    MetricScore,
    RetrievalResult,
)
from AutoAudit.app.cp5_aggregator.aggregator import ResultAggregator


def make_record(call_id: str, faithfulness: float, answer_relevance: float) -> EvaluationRecord:
    return EvaluationRecord(
        eval_id=f"eval_{call_id}",
        call_id=call_id,
        query="테스트 질문",
        generated_answer="테스트 답변",
        retrieval_result=RetrievalResult(query="테스트 질문", contexts=[]),
        scores=[
            MetricScore(metric="faithfulness", score=faithfulness, reasoning=""),
            MetricScore(metric="answer_relevance", score=answer_relevance, reasoning=""),
        ],
        evaluated_at=datetime.now(UTC),
    )


@pytest.fixture
def aggregator() -> ResultAggregator:
    return ResultAggregator()


def test_aggregate_basic(aggregator):
    """기본 집계 — 메트릭 수 확인"""
    records = [
        make_record("C001", 0.9, 0.85),
        make_record("C002", 0.7, 0.65),
        make_record("C003", 0.95, 0.9),
    ]
    summary = aggregator.aggregate(records, run_id="test_run")
    assert summary.total_evaluations == 3
    assert summary.total_calls == 3
    assert len(summary.metrics) == 2


def test_aggregate_sla_flagging(aggregator):
    """SLA 미달 콜 플래깅 — faithfulness < 0.8"""
    records = [
        make_record("C001", 0.9, 0.9),   # 통과
        make_record("C002", 0.6, 0.9),   # faithfulness 미달 → 플래깅
        make_record("C003", 0.85, 0.5),  # answer_relevance 미달 → 플래깅
    ]
    summary = aggregator.aggregate(records)
    assert "C002" in summary.flagged_call_ids
    assert "C003" in summary.flagged_call_ids
    assert "C001" not in summary.flagged_call_ids


def test_aggregate_mean_calculation(aggregator):
    """평균 계산 정확성"""
    records = [
        make_record("C001", 0.8, 0.8),
        make_record("C002", 0.6, 0.6),
    ]
    summary = aggregator.aggregate(records)
    faith_metric = next(m for m in summary.metrics if m.metric == "faithfulness")
    assert faith_metric.mean == pytest.approx(0.7, abs=1e-6)


def test_aggregate_empty(aggregator):
    """빈 레코드 → 빈 summary (파이프라인 중단 없음)"""
    summary = aggregator.aggregate([])
    assert summary.total_evaluations == 0
    assert summary.total_calls == 0
    assert summary.flagged_call_ids == []


def test_sla_pass_rate(aggregator):
    """SLA 통과율 계산"""
    records = [
        make_record("C001", 0.9, 0.9),
        make_record("C002", 0.9, 0.9),
        make_record("C003", 0.5, 0.5),  # 미달
    ]
    summary = aggregator.aggregate(records)
    faith_metric = next(m for m in summary.metrics if m.metric == "faithfulness")
    assert faith_metric.sla_pass_rate == pytest.approx(2 / 3, abs=1e-6)
