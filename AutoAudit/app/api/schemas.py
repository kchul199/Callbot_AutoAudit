"""
api/schemas.py
API 응답 스키마 (Pydantic) — OpenAPI 문서 + TS 타입 자동 생성의 원천.

저장 백엔드(dict)와 프론트엔드(TS) 사이의 계약을 명시적으로 고정한다.
이 모델들이 곧 frontend/src/types.gen.ts 가 된다.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class JudgeModel(BaseModel):
    """선택 가능한 Judge LLM"""
    provider: str            # openai | anthropic | gemini | azure | mock
    model: str
    label: str
    available: bool = True   # API 키 등록 여부
    note: str = ""


class RunEvalConfig(BaseModel):
    """평가 실행 구성 (POST 본문)"""
    judges: list[str] = Field(default_factory=lambda: ["mock"])   # provider 목록
    ensemble: bool = False
    levels: list[str] = Field(default_factory=lambda: ["turn"])    # retrieval/turn/session
    metrics: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)               # 활성 방법론 옵션
    target: str = "all"      # all | unreviewed | 특정 범위
    temperature: float = 0.0


class RunEvalResult(BaseModel):
    """평가 실행 결과 요약"""
    run_id: str
    tenant_id: str
    status: str               # completed | running | failed
    judges: list[str] = Field(default_factory=list)
    levels: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    total_evaluations: int = 0
    message: str = ""


class ReviewSubmit(BaseModel):
    """휴먼 재평가 확정 (POST 본문)"""
    status: str                                       # approved | overridden | skipped
    human_scores: dict[str, float] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)
    comment: str = ""
    reviewer: str | None = None


class ReviewResult(BaseModel):
    eval_id: str
    ok: bool
    review_status: str


class AgreementResult(BaseModel):
    """휴먼 vs 자동 일치도"""
    available: bool = False
    overall_agreement: float = 0.0
    n: int = 0
    per_metric: dict[str, dict[str, float]] = Field(default_factory=dict)


class AgreementSample(BaseModel):
    """일치도 표본 상세 (메트릭 드릴다운)"""
    eval_id: str
    conversation_id: str | None = None
    query: str = ""
    review_status: str = ""
    reviewer: str | None = None
    auto_score: float = 0.0
    human_score: float = 0.0
    delta: float = 0.0
    agree: bool = True


class TenantInfo(BaseModel):
    tenant_id: str
    name: str
    conversation_count: int = 0
    pending_review_count: int = 0


class ConversationInfo(BaseModel):
    """세션 목록 항목"""
    conversation_id: str
    tenant_id: str = "default"
    subscriber_id: str | None = None
    is_multiturn: bool = False
    turn_count: int = 0
    pending_review: int = 0
    started_at: str | None = None
    session_scores: dict[str, float] = Field(default_factory=dict)


class ConversationTurnView(BaseModel):
    role: str
    content: str


class ConversationDetail(BaseModel):
    """세션 상세 — 대화 원문 + 턴/세션 평가"""
    conversation_id: str
    tenant_id: str = "default"
    subscriber_id: str | None = None
    is_multiturn: bool = False
    started_at: str | None = None
    turns: list[ConversationTurnView] = Field(default_factory=list)
    turn_evaluations: list[dict] = Field(default_factory=list)
    session_evaluation: dict | None = None


class RunInfo(BaseModel):
    run_id: str
    generated_at: str | None = None
    total_calls: int = 0
    total_evaluations: int = 0
    flagged_count: int = 0
    has_summary: bool = True


class MetricSummary(BaseModel):
    metric: str
    mean: float
    median: float
    p10: float
    p90: float
    below_sla_count: int
    total_count: int
    sla_pass_rate: float


class AuditSummaryResponse(BaseModel):
    run_id: str
    generated_at: str | None = None
    total_calls: int
    total_evaluations: int
    flagged_call_ids: list[str] = Field(default_factory=list)
    metrics: list[MetricSummary] = Field(default_factory=list)


class ClaimVerdict(BaseModel):
    """RAGAS 스타일 claim 단위 검증 결과"""
    claim: str
    supported: bool
    verdict: str               # "supported" | "unsupported" | "contradicted"
    reasoning: str = ""


class MetricScoreResponse(BaseModel):
    metric: str
    score: float
    reasoning: str = ""
    grounding_chunks: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    sample_scores: list[float] = Field(default_factory=list)
    is_low_confidence: bool = False
    claims: list[ClaimVerdict] = Field(default_factory=list)  # faithfulness 분해 결과
    human_score: float | None = None    # 휴먼 재평가 점수
    final_score: float | None = None    # human 우선 최종 점수


class RetrievedContextResponse(BaseModel):
    chunk_id: str
    content: str
    score: float
    bm25_score: float | None = None
    dense_score: float | None = None
    source_call_id: str = ""


class RetrievalResultResponse(BaseModel):
    query: str
    hyde_query: str | None = None
    sub_queries: list[str] = Field(default_factory=list)
    contexts: list[RetrievedContextResponse] = Field(default_factory=list)


class EvaluationResponse(BaseModel):
    eval_id: str
    qa_id: str | None = None
    call_id: str
    tenant_id: str = "default"
    conversation_id: str | None = None
    subscriber_id: str | None = None
    level: str = "turn"
    turn_index: int | None = None
    query: str
    generated_answer: str
    judge_provider: str = "openai"
    judge_model: str = "gpt-4o"
    evaluated_at: str | None = None
    retrieval_result: RetrievalResultResponse | None = None
    scores: list[MetricScoreResponse] = Field(default_factory=list)
    # 휴먼 재평가
    review_status: str = "pending"
    human_scores: dict[str, float] = Field(default_factory=dict)
    human_label: list[str] = Field(default_factory=list)
    human_comment: str = ""
    reviewer: str | None = None


class TrendPoint(BaseModel):
    run_id: str | None = None
    day: str | None = None
    label: str | None = None
    generated_at: str | None = None
    n: int | None = None
    # 메트릭별 평균은 동적 키 → model_config extra allow
    model_config = {"extra": "allow"}


class TrendsResponse(BaseModel):
    points: list[TrendPoint] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)


# ============================================================
# M6 — Knowledge Base & Settings
# ============================================================

class KbGap(BaseModel):
    """검색 커버리지 갭 — 검색 품질이 낮았던 질의"""
    query: str
    conversation_id: str | None = None
    context_recall: float = 0.0


class KbStatus(BaseModel):
    """tenant 지식베이스 현황"""
    tenant_id: str
    document_count: int = 0
    chunk_count: int = 0
    source_types: list[str] = Field(default_factory=list)
    last_indexed_at: str | None = None
    avg_context_recall: float = 0.0
    coverage_gaps: list[KbGap] = Field(default_factory=list)


class CredentialInfo(BaseModel):
    """provider 자격증명 등록 상태 (평문 키는 절대 반환하지 않음 — 마스킹만)."""
    provider: str
    registered: bool = False
    source: str = "none"            # env | manual | mock | none
    masked_key: str = ""            # 예: ••••••••abcd (마지막 4자리만)
    base_url: str = ""              # openai/anthropic 호환 게이트웨이 (선택)
    endpoint: str = ""              # azure 엔드포인트
    api_version: str = ""           # azure API 버전
    deployment: str = ""            # azure 배포명
    updated_at: str | None = None


class CredentialSubmit(BaseModel):
    """provider 자격증명 등록 (PUT 본문) — 평문 키는 저장 시 마스킹 처리."""
    api_key: str = ""
    base_url: str = ""
    endpoint: str = ""
    api_version: str = ""
    deployment: str = ""


class TenantSettings(BaseModel):
    """tenant 설정 — SLA 임계값·옵션 프로필·Judge·알림"""
    tenant_id: str
    sla_thresholds: dict[str, float] = Field(default_factory=dict)
    eval_profile: str = "기본"                # 빠른 점검 | 고신뢰 | 검색 진단 | 안전성 감사
    default_judge: str = "anthropic"
    judge_credentials: dict[str, bool] = Field(default_factory=dict)  # provider → 등록 여부
    credential_details: dict[str, CredentialInfo] = Field(default_factory=dict)  # provider → 상세
    slack_webhook: str = ""
    notify_on_regression: bool = True
    reviewers: list[str] = Field(default_factory=list)
