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


def test_kb_document_build(tmp_path):
    """고객사 지식 구축 — KB 문서 추가/청킹/목록/삭제."""
    from fastapi.testclient import TestClient

    import AutoAudit.app.api.server as server
    from AutoAudit.app.api.data_access import DataAccess
    from AutoAudit.app.core.store import ResultStore

    server.data = DataAccess(store=ResultStore(db_path=str(tmp_path / "kb.db")))
    client = TestClient(server.app)

    # 초기: 구축 문서 0
    assert client.get("/api/t/acme/kb").json()["built_document_count"] == 0

    # 추가 → 청킹되어 카운트 반영
    long_text = "5G 프리미엄 요금제는 월 69000원입니다. " * 30
    r = client.post("/api/t/acme/kb/documents",
                    json={"title": "요금제 정책", "content": long_text, "source_type": "정책"})
    assert r.status_code == 200
    kb = r.json()
    assert kb["built_document_count"] == 1
    doc = kb["built_documents"][0]
    assert doc["title"] == "요금제 정책" and doc["source_type"] == "정책"
    assert doc["chunk_count"] >= 1 and doc["char_count"] == len(long_text.strip())
    assert kb["built_chunk_count"] == doc["chunk_count"]

    # 빈 제목/내용 거부
    assert client.post("/api/t/acme/kb/documents", json={"title": "", "content": "x"}).status_code == 400
    assert client.post("/api/t/acme/kb/documents", json={"title": "x", "content": ""}).status_code == 400

    # 테넌트 격리: globex에는 없음
    assert client.get("/api/t/globex/kb").json()["built_document_count"] == 0

    # 삭제 → 0 복귀
    r = client.delete(f"/api/t/acme/kb/documents/{doc['doc_id']}")
    assert r.json()["built_document_count"] == 0


def test_kb_file_upload(tmp_path):
    """고객사 지식 파일 업로드 — 다양한 포맷 추출 + 미지원/빈파일 거부."""
    import io

    from fastapi.testclient import TestClient

    import AutoAudit.app.api.server as server
    from AutoAudit.app.api.data_access import DataAccess
    from AutoAudit.app.core.store import ResultStore

    server.data = DataAccess(store=ResultStore(db_path=str(tmp_path / "kb.db")))
    client = TestClient(server.app)
    url = "/api/t/acme/kb/documents/upload"

    # txt → source_type 자동 감지 "텍스트"
    r = client.post(url, files={"files": ("요금.txt", "5G는 월 69000원. " * 20, "text/plain")})
    assert r.status_code == 200
    kb = r.json()
    assert kb["built_document_count"] == 1
    assert kb["built_documents"][0]["source_type"] == "텍스트"
    assert kb["built_documents"][0]["title"] == "요금"  # 확장자 제거된 stem

    # csv + json 다중 업로드
    r = client.post(url, files=[
        ("files", ("faq.csv", "Q,A\n요금?,69000", "text/csv")),
        ("files", ("plan.json", '{"price": 69000}', "application/json")),
    ])
    assert r.status_code == 200 and r.json()["built_document_count"] == 3

    # docx 추출 (python-docx 설치 시)
    import docx
    b = io.BytesIO()
    d = docx.Document()
    d.add_paragraph("약관 본문입니다.")
    d.save(b)
    r = client.post(url, files={"files": ("약관.docx", b.getvalue(), "application/octet-stream")})
    assert r.status_code == 200
    assert any(x["source_type"] == "Word" for x in r.json()["built_documents"])

    # 미지원 포맷 → 400
    assert client.post(url, files={"files": ("a.exe", b"MZ", "application/octet-stream")}).status_code == 400
    # 빈 파일 → 400
    assert client.post(url, files={"files": ("empty.txt", "", "text/plain")}).status_code == 400


def test_file_loader_formats():
    """file_loader.extract_text — 텍스트/HTML/CSV/JSON 포맷별 추출."""
    from AutoAudit.app.cp2_knowledge_base.file_loader import SUPPORTED_EXTENSIONS, extract_text

    assert ".pdf" in SUPPORTED_EXTENSIONS and ".docx" in SUPPORTED_EXTENSIONS
    txt, st = extract_text("a.txt", "안녕하세요".encode())
    assert txt == "안녕하세요" and st == "텍스트"
    # HTML 태그 제거 + script 제외
    html = b"<html><body><h1>title</h1><p>content</p><script>bad()</script></body></html>"
    txt, st = extract_text("a.html", html)
    assert "title" in txt and "content" in txt and "bad" not in txt and st == "HTML"
    # CSV → 행 구분
    txt, _ = extract_text("a.csv", b"a,b\n1,2")
    assert "a | b" in txt and "1 | 2" in txt
    # 미지원 확장자
    import pytest as _pytest
    with _pytest.raises(ValueError):
        extract_text("a.exe", b"data")
