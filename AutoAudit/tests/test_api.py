"""
tests/test_api.py
FastAPI 엔드포인트 + ResultsRepository 검증 (fixture로 가짜 결과 트리 구성)
"""
import json

import pytest

from AutoAudit.app.api.repository import ResultsRepository


@pytest.fixture
def results_tree(tmp_path):
    """가짜 data/results/<run_id>/ 트리 구성"""
    run_dir = tmp_path / "run_abc"
    run_dir.mkdir(parents=True)

    summary = {
        "run_id": "run_abc",
        "generated_at": "2024-06-01T00:00:00",
        "total_calls": 2,
        "total_evaluations": 3,
        "flagged_call_ids": ["C002"],
        "metrics": [
            {"metric": "faithfulness", "mean": 0.8, "median": 0.8, "p10": 0.6, "p90": 0.95,
             "below_sla_count": 1, "total_count": 3, "sla_pass_rate": 0.66},
        ],
    }
    (run_dir / "cp5_audit_summary_20240601_000000.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )

    evals = [
        {"eval_id": "e1", "qa_id": "q1", "call_id": "C001", "subscriber_id": "S1",
         "query": "q", "generated_answer": "a",
         "scores": [{"metric": "faithfulness", "score": 0.9, "reasoning": "ok",
                     "is_low_confidence": False, "grounding_chunks": []}]},
        {"eval_id": "e2", "qa_id": "q2", "call_id": "C002", "subscriber_id": "S2",
         "query": "q", "generated_answer": "a",
         "scores": [{"metric": "faithfulness", "score": 0.5, "reasoning": "bad",
                     "is_low_confidence": True, "grounding_chunks": []}]},
    ]
    (run_dir / "cp4_eval_records_20240601_000000.json").write_text(
        json.dumps(evals), encoding="utf-8"
    )
    return str(tmp_path)


def test_list_runs(results_tree):
    repo = ResultsRepository(results_tree)
    runs = repo.list_runs()
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run_abc"
    assert runs[0]["flagged_count"] == 1


def test_get_summary(results_tree):
    repo = ResultsRepository(results_tree)
    s = repo.get_summary("run_abc")
    assert s["total_calls"] == 2


def test_get_evaluations_all(results_tree):
    repo = ResultsRepository(results_tree)
    assert len(repo.get_evaluations("run_abc")) == 2


def test_filter_by_call_id(results_tree):
    repo = ResultsRepository(results_tree)
    res = repo.get_evaluations("run_abc", call_id="C002")
    assert len(res) == 1 and res[0]["eval_id"] == "e2"


def test_filter_flagged_only(results_tree):
    repo = ResultsRepository(results_tree)
    res = repo.get_evaluations("run_abc", flagged_only=True)
    assert {r["call_id"] for r in res} == {"C002"}


def test_filter_low_confidence(results_tree):
    repo = ResultsRepository(results_tree)
    res = repo.get_evaluations("run_abc", low_confidence_only=True)
    assert len(res) == 1 and res[0]["eval_id"] == "e2"


def test_filter_below_threshold(results_tree):
    repo = ResultsRepository(results_tree)
    res = repo.get_evaluations("run_abc", below_metric="faithfulness", below_threshold=0.8)
    assert len(res) == 1 and res[0]["eval_id"] == "e2"


def test_get_single_evaluation(results_tree):
    repo = ResultsRepository(results_tree)
    assert repo.get_evaluation("run_abc", "e1")["qa_id"] == "q1"
    assert repo.get_evaluation("run_abc", "nope") is None


# ---- FastAPI 통합 (TestClient) ----

def test_api_endpoints(results_tree, tmp_path):
    from fastapi.testclient import TestClient

    import AutoAudit.app.api.server as server
    from AutoAudit.app.api.data_access import DataAccess
    from AutoAudit.app.core.store import ResultStore

    # JSON 폴백만으로 동작 검증 (DB는 빈 격리 인스턴스)
    isolated_store = ResultStore(db_path=str(tmp_path / "empty.db"))
    server.data = DataAccess(store=isolated_store, repo=ResultsRepository(results_tree))
    client = TestClient(server.app)

    assert client.get("/api/health").json() == {"status": "ok"}

    runs = client.get("/api/runs").json()
    assert runs[0]["run_id"] == "run_abc"

    summary = client.get("/api/runs/run_abc/summary").json()
    assert summary["total_calls"] == 2

    # latest alias
    summary2 = client.get("/api/runs/latest/summary").json()
    assert summary2["run_id"] == "run_abc"

    evals = client.get("/api/runs/run_abc/evaluations?flagged_only=true").json()
    assert len(evals) == 1

    detail = client.get("/api/runs/run_abc/evaluations/e1").json()
    assert detail["qa_id"] == "q1"

    assert client.get("/api/runs/run_abc/evaluations/missing").status_code == 404


def test_credential_endpoints(tmp_path, monkeypatch):
    """Judge 자격증명 등록/조회/삭제 + 마스킹(평문 미유출) 검증."""
    from fastapi.testclient import TestClient

    import AutoAudit.app.api.server as server
    from AutoAudit.app.api.data_access import DataAccess
    from AutoAudit.app.core.store import ResultStore

    # provider 환경변수 격리 → 결정적 (env 키가 있으면 source=env 가 됨)
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
              "GOOGLE_API_KEY", "AZURE_OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    isolated = ResultStore(db_path=str(tmp_path / "empty.db"))
    server.data = DataAccess(store=isolated, repo=ResultsRepository(str(tmp_path)))
    client = TestClient(server.app)

    # 초기: 전부 미등록 + credential_details 존재
    s = client.get("/api/t/acme/settings").json()
    assert s["judge_credentials"]["anthropic"] is False
    assert s["credential_details"]["anthropic"]["registered"] is False

    # 등록 → 수동 source + 마스킹 키, 평문 미유출
    r = client.put("/api/t/acme/credentials/anthropic", json={"api_key": "sk-ant-SECRET-1234"})
    assert r.status_code == 200
    d = r.json()["credential_details"]["anthropic"]
    assert d["registered"] is True and d["source"] == "manual"
    assert d["masked_key"].endswith("1234")
    assert "SECRET" not in r.text and "sk-ant-SECRET-1234" not in r.text

    # azure: 추가 필드(endpoint/version/deployment) 영속
    r = client.put("/api/t/acme/credentials/azure", json={
        "api_key": "azkey-9999", "endpoint": "https://x.openai.azure.com",
        "api_version": "2024-06-01", "deployment": "gpt-4o",
    })
    az = r.json()["credential_details"]["azure"]
    assert az["endpoint"] == "https://x.openai.azure.com"
    assert az["deployment"] == "gpt-4o" and az["api_version"] == "2024-06-01"

    # 빈 키 거부
    assert client.put("/api/t/acme/credentials/anthropic", json={"api_key": ""}).status_code == 400

    # 삭제 → 미등록 복귀
    r = client.delete("/api/t/acme/credentials/anthropic")
    assert r.json()["credential_details"]["anthropic"]["registered"] is False
