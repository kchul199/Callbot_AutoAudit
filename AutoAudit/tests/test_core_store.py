"""
tests/test_core_store.py
SQLite ResultStore — 적재/쿼리/필터/집계 검증
"""
import pytest

from AutoAudit.app.core.store import ResultStore
from AutoAudit.app.core.types import (
    AggregatedMetric,
    AuditSummary,
    EvaluationRecord,
    MetricScore,
    RetrievalResult,
    RetrievedContext,
)


@pytest.fixture
def store(tmp_path) -> ResultStore:
    return ResultStore(db_path=str(tmp_path / "test.db"))


def _rec(eval_id, call_id, faith, low_conf=False, sub="S1") -> EvaluationRecord:
    return EvaluationRecord(
        eval_id=eval_id, qa_id=f"qa_{eval_id}", call_id=call_id, subscriber_id=sub,
        query="질문?", generated_answer="답변",
        retrieval_result=RetrievalResult(
            query="질문?",
            contexts=[RetrievedContext(chunk_id="c1", content="ctx", score=0.9, source_call_id=call_id)],
        ),
        scores=[
            MetricScore(metric="faithfulness", score=faith, reasoning="r", is_low_confidence=low_conf),
            MetricScore(metric="answer_relevance", score=0.8, reasoning="r"),
        ],
    )


def _summary(run_id, flagged) -> AuditSummary:
    return AuditSummary(
        run_id=run_id, total_calls=3, total_evaluations=3,
        flagged_call_ids=flagged,
        metrics=[
            AggregatedMetric(metric="faithfulness", mean=0.75, median=0.8, p10=0.5, p90=0.9,
                             below_sla_count=1, total_count=3, sla_pass_rate=0.66),
        ],
    )


def test_upsert_and_get_summary(store):
    store.upsert_summary(_summary("run1", ["C2"]))
    s = store.get_summary("run1")
    assert s["total_calls"] == 3
    assert s["flagged_call_ids"] == ["C2"]
    assert s["metrics"][0]["metric"] == "faithfulness"


def test_upsert_evaluations_and_query(store):
    store.upsert_summary(_summary("run1", ["C2"]))
    store.upsert_evaluations("run1", [
        _rec("e1", "C1", 0.9),
        _rec("e2", "C2", 0.5, low_conf=True),
        _rec("e3", "C3", 0.85),
    ])
    assert len(store.query_evaluations("run1")) == 3


def test_filter_by_call_id(store):
    store.upsert_evaluations("run1", [_rec("e1", "C1", 0.9), _rec("e2", "C2", 0.5)])
    res = store.query_evaluations("run1", call_id="C2")
    assert len(res) == 1 and res[0]["eval_id"] == "e2"


def test_filter_below_threshold(store):
    store.upsert_evaluations("run1", [_rec("e1", "C1", 0.9), _rec("e2", "C2", 0.5)])
    res = store.query_evaluations("run1", below_metric="faithfulness", below_threshold=0.7)
    assert len(res) == 1 and res[0]["eval_id"] == "e2"


def test_filter_low_confidence(store):
    store.upsert_evaluations("run1", [_rec("e1", "C1", 0.9), _rec("e2", "C2", 0.5, low_conf=True)])
    res = store.query_evaluations("run1", low_confidence_only=True)
    assert len(res) == 1 and res[0]["eval_id"] == "e2"


def test_filter_flagged_only(store):
    store.upsert_summary(_summary("run1", ["C2"]))
    store.upsert_evaluations("run1", [_rec("e1", "C1", 0.9), _rec("e2", "C2", 0.5)])
    res = store.query_evaluations("run1", flagged_only=True)
    assert {r["call_id"] for r in res} == {"C2"}


def test_upsert_idempotent(store):
    """동일 eval_id 재적재 → 중복 없이 갱신 (resume 멱등성)"""
    store.upsert_evaluations("run1", [_rec("e1", "C1", 0.5)])
    store.upsert_evaluations("run1", [_rec("e1", "C1", 0.95)])  # 점수 갱신
    res = store.query_evaluations("run1")
    assert len(res) == 1
    faith = next(s for s in res[0]["scores"] if s["metric"] == "faithfulness")
    assert faith["score"] == pytest.approx(0.95)


def test_get_single_evaluation(store):
    store.upsert_evaluations("run1", [_rec("e1", "C1", 0.9)])
    assert store.get_evaluation("run1", "e1")["qa_id"] == "qa_e1"
    assert store.get_evaluation("run1", "missing") is None


def test_trends_aggregation(store):
    store.upsert_summary(_summary("run1", []))
    store.upsert_summary(_summary("run2", []))
    t = store.trends()
    assert len(t["points"]) == 2
    assert "faithfulness" in t["metrics"]


def test_list_runs_order(store):
    store.upsert_summary(_summary("run1", []))
    store.upsert_summary(_summary("run2", []))
    runs = store.list_runs()
    assert {r["run_id"] for r in runs} == {"run1", "run2"}
