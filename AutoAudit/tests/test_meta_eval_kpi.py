"""
tests/test_meta_eval_kpi.py
#7 Meta-eval 상시 KPI 검증.

  - MetaEvaluator.evaluate_from_rows: (qa_id, metric, score) 행 → 골든셋 매칭 통계
  - detect_meta_eval_drift: 직전 run 대비 감사기 정확도(ρ) 회귀 감지
  - /api/meta-eval/trends 엔드포인트 wiring
"""
import json

from AutoAudit.app.core.types import AuditSummary
from AutoAudit.app.cp4_evaluator.meta_eval import MetaEvaluator
from AutoAudit.app.cp4_evaluator.options import MetaEvalOptions
from AutoAudit.app.cp6_reporter.regression import detect_meta_eval_drift


# ----------------------------------------------------------------
# evaluate_from_rows
# ----------------------------------------------------------------

def _write_golden(path, rows):
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
    )


def test_evaluate_from_rows_matches_golden(tmp_path):
    golden = tmp_path / "golden.jsonl"
    _write_golden(golden, [
        {"qa_id": "q1", "metric": "faithfulness", "human_score": 0.9},
        {"qa_id": "q2", "metric": "faithfulness", "human_score": 0.4},
        {"qa_id": "q3", "metric": "faithfulness", "human_score": 0.7},
    ])
    ev = MetaEvaluator(MetaEvalOptions(golden_set_path=str(golden)))
    rows = [("q1", "faithfulness", 0.92), ("q2", "faithfulness", 0.45), ("q3", "faithfulness", 0.68)]
    result = ev.evaluate_from_rows(rows)
    assert result["available"] is True
    assert result["n"] == 3
    # judge 와 human 이 같은 순위 → spearman 양의 상관
    assert result["overall"]["spearman"] > 0.9
    assert "faithfulness" in result["per_metric"]


def test_evaluate_from_rows_no_golden(tmp_path):
    ev = MetaEvaluator(MetaEvalOptions(golden_set_path=str(tmp_path / "nope.jsonl")))
    result = ev.evaluate_from_rows([("q1", "faithfulness", 0.9)])
    assert result["available"] is False


def test_evaluate_records_delegates(tmp_path):
    """EvaluationRecord 경로도 동일 결과 (행 변환 위임)."""
    from AutoAudit.app.core.types import EvaluationRecord, MetricScore, RetrievalResult

    golden = tmp_path / "golden.jsonl"
    _write_golden(golden, [{"qa_id": "q1", "metric": "faithfulness", "human_score": 0.8}])
    rec = EvaluationRecord(
        eval_id="e1", qa_id="q1", call_id="C1",
        query="q", generated_answer="a",
        retrieval_result=RetrievalResult(query="q", contexts=[]),
        scores=[MetricScore(metric="faithfulness", score=0.82, reasoning="")],
    )
    result = MetaEvaluator(MetaEvalOptions(golden_set_path=str(golden))).evaluate([rec])
    assert result["available"] is True and result["n"] == 1


# ----------------------------------------------------------------
# detect_meta_eval_drift
# ----------------------------------------------------------------

def _summary_with_rho(rho: float | None) -> AuditSummary:
    meta = (
        {"available": True, "n": 10, "overall": {"spearman": rho, "kappa": 0.5, "mae": 0.1}}
        if rho is not None else {"available": False}
    )
    return AuditSummary(run_id="r", total_calls=1, total_evaluations=1, meta_eval=meta)


def test_drift_detected():
    cur = _summary_with_rho(0.55)   # 임계(0.7) 미만 + 하락
    prev = _summary_with_rho(0.85)
    drift = detect_meta_eval_drift(cur, prev, rho_threshold=0.7)
    assert drift.has_drift is True
    assert drift.delta < 0


def test_drift_stable_when_high():
    cur = _summary_with_rho(0.82)   # 하락했지만 여전히 임계 이상
    prev = _summary_with_rho(0.88)
    drift = detect_meta_eval_drift(cur, prev, rho_threshold=0.7)
    assert drift.has_drift is False


def test_drift_no_previous():
    drift = detect_meta_eval_drift(_summary_with_rho(0.5), None)
    assert drift.has_drift is False


def test_drift_missing_meta():
    cur = _summary_with_rho(None)
    prev = _summary_with_rho(0.9)
    drift = detect_meta_eval_drift(cur, prev)
    assert drift.has_drift is False
    assert drift.current_rho is None


# ----------------------------------------------------------------
# /api/meta-eval/trends 엔드포인트 wiring
# ----------------------------------------------------------------

def test_meta_eval_trends_endpoint(tmp_path):
    from fastapi.testclient import TestClient

    import AutoAudit.app.api.server as server
    from AutoAudit.app.api.data_access import DataAccess
    from AutoAudit.app.api.repository import ResultsRepository
    from AutoAudit.app.core.store import ResultStore

    server.data = DataAccess(
        store=ResultStore(db_path=str(tmp_path / "empty.db")),
        repo=ResultsRepository(str(tmp_path)),
    )
    client = TestClient(server.app)
    r = client.get("/api/meta-eval/trends")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) >= {"available", "points", "metrics"}
    assert isinstance(body["points"], list)
