"""
api/server.py
FastAPI 서버 — 대시보드(프론트엔드)가 호출하는 감사 결과 API.

실행:
  uvicorn AutoAudit.app.api.server:app --reload --port 8000

엔드포인트:
  GET /api/health
  GET /api/runs                              run 목록
  GET /api/runs/{run_id}/summary            CP5 집계 요약
  GET /api/runs/{run_id}/evaluations        평가 목록 (필터 지원)
  GET /api/runs/{run_id}/evaluations/{id}   단일 평가 상세 (Evidence)
  GET /api/runs/{run_id}/trends             메트릭 추이 (run 누적)

데이터 접근은 DataAccess 파사드(SQLite 우선 + JSON 폴백)에 위임.
응답은 schemas.py 모델로 타입 고정 → OpenAPI → TS 자동 생성.
"""
from __future__ import annotations

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from AutoAudit.app.api.data_access import DataAccess
from AutoAudit.app.api.schemas import (
    AgreementResult,
    AgreementSample,
    AuditConversationSubmit,
    AuditRunResult,
    AuditSummaryResponse,
    ConversationDetail,
    ConversationInfo,
    CredentialSubmit,
    EvaluationResponse,
    HealthResponse,
    JudgeModel,
    KbDocumentSubmit,
    KbStatus,
    ReviewResult,
    ReviewSubmit,
    RunEvalConfig,
    RunEvalResult,
    RunInfo,
    TenantInfo,
    TenantSettings,
    TrendsResponse,
)

app = FastAPI(title="CallBot AutoAudit API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 운영 시 프론트 도메인으로 제한
    allow_methods=["*"],
    allow_headers=["*"],
)

# 교체 가능한 데이터 접근 (테스트는 server.data = DataAccess(...) 주입)
data = DataAccess()


def _resolve(run_id: str) -> str:
    return (data.latest_run_id() or "") if run_id == "latest" else run_id


@app.get("/api/health", response_model=HealthResponse)
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/tenants", response_model=list[TenantInfo])
def list_tenants() -> list:
    """가입자 목록 (conversations 테이블에서 tenant 집계)."""
    tenants = data.list_tenants()
    if tenants:
        return tenants
    # 폴백: 시드 전이면 단일 데모 tenant
    return [{"tenant_id": "acme", "name": "Acme Telecom (데모)",
             "conversation_count": 0, "pending_review_count": 0}]


@app.get("/api/judges", response_model=list[JudgeModel])
def list_judges() -> list:
    """선택 가능한 Judge LLM 목록 (API 키 등록 여부 반영)."""
    import os
    mock = os.environ.get("AUTOAUDIT_MOCK") == "1"
    return [
        {"provider": "anthropic", "model": "claude-sonnet-4-5", "label": "Anthropic Claude",
         "available": mock or bool(os.environ.get("ANTHROPIC_API_KEY")),
         "note": "" if (mock or os.environ.get("ANTHROPIC_API_KEY")) else "키 미등록"},
        {"provider": "openai", "model": "gpt-4o", "label": "OpenAI GPT-4o",
         "available": mock or bool(os.environ.get("OPENAI_API_KEY")),
         "note": "" if (mock or os.environ.get("OPENAI_API_KEY")) else "키 미등록"},
        {"provider": "gemini", "model": "gemini-2.5-pro", "label": "Google Gemini",
         "available": mock or bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
         "note": "" if (mock or os.environ.get("GEMINI_API_KEY")) else "키 미등록"},
        {"provider": "azure", "model": "gpt-4o", "label": "Azure OpenAI",
         "available": mock or bool(os.environ.get("AZURE_OPENAI_API_KEY")),
         "note": "" if (mock or os.environ.get("AZURE_OPENAI_API_KEY")) else "키 미등록"},
        {"provider": "mock", "model": "mock", "label": "Mock (개발용)",
         "available": True, "note": "비용 $0"},
    ]


@app.post("/api/t/{tenant}/runs", response_model=RunEvalResult)
def create_run(tenant: str, config: RunEvalConfig) -> dict:
    """평가 실행. (M3) mock 모드에서는 구성을 EvalRun으로 등록하고 요약 반환."""
    import os
    import uuid
    if os.environ.get("AUTOAUDIT_MOCK") != "1":
        # prod 실행은 후속(M3.5)에서 파이프라인 연결. 지금은 구성만 검증.
        raise HTTPException(status_code=501, detail="실 LLM 실행은 아직 미연결 (mock 모드만 지원)")
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    convos = data.list_conversations(tenant)
    n = sum(c.get("turn_count", 0) for c in convos) if "turn" in config.levels else 0
    return {
        "run_id": run_id, "tenant_id": tenant, "status": "completed",
        "judges": config.judges, "levels": config.levels, "methods": config.methods,
        "total_evaluations": n,
        "message": f"[mock] 구성 검증 완료 — {len(convos)}개 대화 대상, Judge {', '.join(config.judges)}",
    }


@app.get("/api/t/{tenant}/conversations", response_model=list[ConversationInfo])
def list_conversations(tenant: str) -> list:
    """가입자의 대화 세션 목록 (싱글턴/멀티턴)."""
    return data.list_conversations(tenant)


@app.get("/api/t/{tenant}/evaluations", response_model=list[EvaluationResponse])
def explore_evaluations(
    tenant: str,
    level: str | None = None,
    metric: str | None = None,
    review_status: str | None = None,
    low_confidence_only: bool = False,
    below_metric: str | None = None,
    below_threshold: float | None = None,
) -> list:
    """tenant 평가 탐색 (필터)."""
    return data.explore_evaluations(
        tenant, level=level, metric=metric, review_status=review_status,
        low_confidence_only=low_confidence_only,
        below_metric=below_metric, below_threshold=below_threshold,
    )


@app.get("/api/t/{tenant}/trends", response_model=TrendsResponse)
def tenant_trends(
    tenant: str,
    group_by: str = "run",
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """tenant 메트릭 추이 (group_by=day|run, 일자 범위 필터)."""
    return data.tenant_trends(tenant, group_by=group_by, date_from=date_from, date_to=date_to)


@app.get("/api/t/{tenant}/agreement", response_model=AgreementResult)
def tenant_agreement(tenant: str) -> dict:
    """휴먼 vs 자동 일치도."""
    return data.agreement(tenant)


@app.get("/api/t/{tenant}/agreement/{metric}", response_model=list[AgreementSample])
def tenant_agreement_samples(tenant: str, metric: str) -> list:
    """특정 메트릭의 휴먼·자동 표본 상세 (드릴다운)."""
    return data.agreement_samples(tenant, metric)


@app.get("/api/t/{tenant}/kb", response_model=KbStatus)
def tenant_kb(tenant: str) -> dict:
    """tenant 지식베이스 현황 + 검색 커버리지 갭 + 구축 문서."""
    return data.kb_status(tenant)


@app.post("/api/t/{tenant}/kb/documents", response_model=KbStatus)
def add_kb_document(tenant: str, body: KbDocumentSubmit) -> dict:
    """고객사 지식 문서 추가 (제목+내용 → 청킹 후 저장)."""
    try:
        return data.kb_add_document(tenant, body.title, body.content, body.source_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/t/{tenant}/kb/documents/{doc_id}", response_model=KbStatus)
def delete_kb_document(tenant: str, doc_id: str) -> dict:
    """구축 KB 문서 삭제."""
    return data.kb_delete_document(tenant, doc_id)


def _decode_bytes(data: bytes) -> str:
    for enc in ("utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _parse_conversation_upload(filename: str, data: bytes):
    """업로드 대화 파일 → (CallLog, ground_truths). json은 정답 포함 가능."""
    import tempfile
    from pathlib import Path

    from AutoAudit.app.cp1_preprocessing.parser import CallLogParser
    from AutoAudit.app.cp1_preprocessing.transcript import parse_transcript

    ext = Path(filename or "").suffix.lower()
    text = _decode_bytes(data)
    if ext == ".json":
        return parse_transcript(text, Path(filename).stem)
    if ext in (".txt", ".csv"):
        with tempfile.NamedTemporaryFile("w", suffix=ext, delete=False, encoding="utf-8") as tf:
            tf.write(text)
            tmp = tf.name
        try:
            call_log = CallLogParser().parse_file(tmp)
        finally:
            Path(tmp).unlink(missing_ok=True)
        if call_log and call_log.turns:
            return call_log, []
    # 폴백: transcript 라인 파서
    return parse_transcript(text, Path(filename or "manual").stem)


@app.post("/api/t/{tenant}/audit-conversation", response_model=AuditRunResult)
async def audit_conversation(tenant: str, body: AuditConversationSubmit) -> dict:
    """대화 직접 입력(transcript/JSON)을 고객사 KB 근거로 품질 검증."""
    from AutoAudit.app.cp1_preprocessing.transcript import parse_transcript
    call_log, gts = parse_transcript(body.text, body.conversation_id or None)
    if not call_log.turns:
        raise HTTPException(status_code=400, detail="대화 내용을 인식하지 못했습니다. '고객:'/'콜봇:' 형식 또는 JSON을 확인하세요.")
    try:
        return await data.audit_conversation(
            tenant, call_log, body.ground_truths or gts,
            body.conversation_id or None, body.enable,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/t/{tenant}/audit-conversation/upload", response_model=AuditRunResult)
async def audit_conversation_upload(
    tenant: str,
    file: UploadFile = File(...),
    conversation_id: str = Form(""),
    enable: str = Form(""),
) -> dict:
    """대화 파일(txt/json/csv)을 고객사 KB 근거로 품질 검증."""
    raw = await file.read()
    call_log, gts = _parse_conversation_upload(file.filename or "conversation", raw)
    if not call_log or not call_log.turns:
        raise HTTPException(status_code=400, detail="대화 파일에서 턴을 추출하지 못했습니다.")
    enable_list = [x.strip() for x in enable.split(",") if x.strip()]
    try:
        return await data.audit_conversation(
            tenant, call_log, gts, conversation_id or None, enable_list,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/t/{tenant}/kb/documents/upload", response_model=KbStatus)
async def upload_kb_documents(
    tenant: str,
    files: list[UploadFile] = File(...),
    source_type: str = Form(""),
) -> dict:
    """파일 업로드로 고객사 지식 추가 (txt/md/csv/json/html/pdf/docx/xlsx 등).

    파일에서 텍스트를 추출 → 청킹 후 저장. 여러 파일 동시 업로드 가능.
    모든 파일이 실패하면 400, 일부라도 성공하면 최신 KB 현황 반환.
    """
    result: dict | None = None
    errors: list[str] = []
    for f in files:
        raw = await f.read()
        try:
            result = data.kb_upload_document(
                tenant, f.filename or "upload", raw, source_type=source_type,
            )
        except ValueError as exc:
            errors.append(f"{f.filename}: {exc}")
    if result is None:
        raise HTTPException(status_code=400, detail="; ".join(errors) or "업로드 실패")
    return result


@app.get("/api/t/{tenant}/settings", response_model=TenantSettings)
def get_settings(tenant: str) -> dict:
    """tenant 설정 조회."""
    return data.get_settings(tenant)


@app.put("/api/t/{tenant}/settings", response_model=TenantSettings)
def put_settings(tenant: str, settings: TenantSettings) -> dict:
    """tenant 설정 저장."""
    return data.save_settings(tenant, settings.model_dump(exclude={"tenant_id"}))


@app.put("/api/t/{tenant}/credentials/{provider}", response_model=TenantSettings)
def put_credential(tenant: str, provider: str, body: CredentialSubmit) -> dict:
    """provider 자격증명 등록 — 평문 키는 마스킹만 보관(평문 미반환)."""
    try:
        return data.save_credential(tenant, provider, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/t/{tenant}/credentials/{provider}", response_model=TenantSettings)
def delete_credential(tenant: str, provider: str) -> dict:
    """수동 등록 자격증명 삭제."""
    return data.delete_credential(tenant, provider)


@app.get("/api/t/{tenant}/review-queue", response_model=list[EvaluationResponse])
def review_queue(tenant: str) -> list:
    """검수 대기 평가 (저신뢰·미검수 우선순위)."""
    return data.review_queue(tenant)


@app.get("/api/evaluations/{eval_id}", response_model=EvaluationResponse)
def get_evaluation_single(eval_id: str) -> dict:
    """단일 평가 상세 (검수 워크스페이스용, run 무관)."""
    rec = data.get_evaluation_any(eval_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"evaluation not found: {eval_id}")
    return rec


@app.post("/api/review/{eval_id}", response_model=ReviewResult)
def submit_review(eval_id: str, body: ReviewSubmit) -> dict:
    """휴먼 재평가 확정 — Final Score 갱신 + 골든셋 적재."""
    ok = data.record_review(
        eval_id, status=body.status, human_scores=body.human_scores,
        labels=body.labels, comment=body.comment, reviewer=body.reviewer,
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"evaluation not found: {eval_id}")
    return {"eval_id": eval_id, "ok": True, "review_status": body.status}


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str) -> dict:
    """세션 상세 — 대화 타임라인 + 턴/세션 평가."""
    conv = data.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail=f"conversation not found: {conversation_id}")
    return conv


@app.get("/api/runs", response_model=list[RunInfo])
def list_runs() -> list:
    return data.list_runs()


@app.get("/api/runs/{run_id}/summary", response_model=AuditSummaryResponse)
def get_summary(run_id: str) -> dict:
    summary = data.get_summary(_resolve(run_id))
    if summary is None:
        raise HTTPException(status_code=404, detail=f"summary not found: {run_id}")
    return summary


@app.get("/api/runs/{run_id}/evaluations", response_model=list[EvaluationResponse])
def get_evaluations(
    run_id: str,
    call_id: str | None = None,
    subscriber_id: str | None = None,
    flagged_only: bool = False,
    low_confidence_only: bool = False,
    below_metric: str | None = None,
    below_threshold: float | None = Query(None, ge=0.0, le=1.0),
) -> list:
    return data.get_evaluations(
        _resolve(run_id),
        call_id=call_id,
        subscriber_id=subscriber_id,
        flagged_only=flagged_only,
        low_confidence_only=low_confidence_only,
        below_metric=below_metric,
        below_threshold=below_threshold,
    )


@app.get("/api/runs/{run_id}/evaluations/{eval_id}", response_model=EvaluationResponse)
def get_evaluation(run_id: str, eval_id: str) -> dict:
    rec = data.get_evaluation(_resolve(run_id), eval_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"evaluation not found: {eval_id}")
    return rec


@app.get("/api/runs/{run_id}/trends", response_model=TrendsResponse)
def get_trends(run_id: str) -> dict:
    """전체 run의 메트릭 평균 추이 (회귀 감지용)"""
    return data.trends()
