"""
core/types.py
파이프라인 전체에서 공유하는 Pydantic 데이터 모델 (타입 규약)
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    """타임존 인식 UTC now (datetime.utcnow() deprecated 대체)."""
    return datetime.now(UTC)


# ============================================================
# CP1 — 전처리 스키마
# ============================================================

class TurnRole(str, Enum):
    USER = "user"
    BOT = "bot"
    SYSTEM = "system"


class ConversationTurn(BaseModel):
    """단일 대화 턴"""
    turn_id: int
    role: TurnRole
    content: str
    timestamp: datetime | None = None
    # #1 봇 턴이 답변 생성 시 실제로 본 RAG 컨텍스트(있으면). faithfulness 평가의 근거 출처.
    contexts: list[str] = Field(default_factory=list)


class CallLog(BaseModel):
    """가입자 콜 로그 표준 스키마"""
    call_id: str
    subscriber_id: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    turns: list[ConversationTurn] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ============================================================
# CP2 — 지식 베이스 청크 스키마
# ============================================================

class ChunkLevel(str, Enum):
    PARENT = "parent"
    CHILD = "child"


class Chunk(BaseModel):
    """Parent-Child 청킹 단위"""
    chunk_id: str
    source_call_id: str
    level: ChunkLevel
    parent_chunk_id: str | None = None  # child 청크인 경우 부모 ID
    content: str
    token_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)


# ============================================================
# CP3 — 검색 결과 스키마
# ============================================================

class RetrievedContext(BaseModel):
    """단일 검색 결과"""
    chunk_id: str
    content: str
    score: float                  # Reranker 이후 최종 점수
    bm25_score: float | None = None
    dense_score: float | None = None
    source_call_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    """한 질의에 대한 전체 검색 결과"""
    query: str
    hyde_query: str | None = None
    sub_queries: list[str] = Field(default_factory=list)
    contexts: list[RetrievedContext] = Field(default_factory=list)
    retrieved_at: datetime = Field(default_factory=utcnow)


# ============================================================
# CP3.5 — 평가 대상 QA 쌍 (CP1 대화 → CP4 평가 브릿지)
# ============================================================

class QAPair(BaseModel):
    """
    콜봇 대화에서 추출한 (질문, 봇 답변) 쌍 = 턴 단위 평가 대상.
    retrieval_result는 CP3 검색 후 채워진다.
    """
    qa_id: str
    call_id: str                   # = conversation_id (호환 위해 유지)
    subscriber_id: str
    tenant_id: str = "default"     # 멀티테넌시 (단일 DB + tenant_id)
    conversation_id: str | None = None  # 명시적 세션 ID (없으면 call_id 사용)
    question: str                  # User 발화
    bot_answer: str                # 평가 대상 Bot 답변
    turn_index: int                # 원본 대화 내 위치 (추적용)
    retrieval_result: RetrievalResult | None = None      # 감사기 재검색 컨텍스트 (검색 품질 평가용)
    provided_context: RetrievalResult | None = None      # 봇이 실제 답변 시 본 RAG 트레이스 (있으면 충실도 평가에 우선)
    context_source: str = "auditor"   # faithfulness 근거 출처: "bot_trace"(봇 실제) | "auditor"(감사기 재검색)
    ground_truth: str | None = None   # 정답 답변(있으면 유사도 비교 평가)
    history: list[str] = Field(default_factory=list)  # 직전 대화 맥락 ("고객: ...", "콜봇: ...")


# ============================================================
# CP4 — 평가 스키마
# ============================================================

class ClaimVerdict(BaseModel):
    """RAGAS 스타일 claim(주장) 단위 검증 결과"""
    claim: str                     # 답변에서 추출한 단일 주장
    supported: bool                # 컨텍스트로 지지되는가
    verdict: str                   # "supported" | "unsupported" | "contradicted"
    reasoning: str = ""            # 판정 근거


class MetricScore(BaseModel):
    """단일 메트릭 점수 + 근거 + 신뢰도"""
    metric: str
    score: float                   # 0.0 ~ 1.0 (다중 샘플 집계값)
    reasoning: str                 # LLM Judge 근거 (Evidence-based)
    grounding_chunks: list[str] = Field(default_factory=list)  # 근거로 사용된 chunk_id 목록
    confidence: float = 1.0        # 샘플 분산 기반 신뢰도 (1=완전일치)
    sample_scores: list[float] = Field(default_factory=list)   # 개별 샘플 점수
    is_low_confidence: bool = False  # SLA 판정 시 별도 처리 플래그
    # --- 고급 기법 메타데이터 (옵션 활성 시 채워짐) ---
    claims: list[ClaimVerdict] = Field(default_factory=list)   # faithfulness claim 분해 결과
    method: str = "single"         # single | multi_sample | g_eval | ensemble | nugget | claim_nli | ppi_classifier | domain | cot | cot_reverse | answer_correctness | abstention_exempt
    ensemble_scores: dict[str, float] = Field(default_factory=dict)  # provider별 점수
    escalated: bool = False         # 불일치/저신뢰로 재평가됨
    ci_low: float | None = None     # 점수 신뢰구간 하한
    ci_high: float | None = None
    # --- CoT (Chain-of-Thought) 메타데이터 ---
    cot_steps: list[str] = Field(default_factory=list)  # CoT 단계별 추론 결과
    # --- 역방향 검증 (Reverse Verification) 메타데이터 ---
    forward_score: float | None = None   # 순방향 점수 (답변→컨텍스트)
    reverse_score: float | None = None   # 역방향 점수 (컨텍스트→답변 or 답변→질문)
    consistency_score: float | None = None  # |forward - reverse| (낮을수록 일관성 높음)
    # --- 정답성 (Answer Correctness) 메타데이터 ① ---
    correctness_f1: float | None = None      # 정답 대비 claim F1
    correctness_sim: float | None = None     # 정답 대비 의미 유사도
    # --- 적정 거절 (Appropriate Abstention) ④ ---
    abstention: bool = False         # 정당한 거절/모름으로 판정되어 감점 면제됨
    # --- 결정적 수치·엔티티 가드 (Numeric Guard) ⑤ ---
    numeric_flags: list[str] = Field(default_factory=list)  # 컨텍스트와 불일치한 수치/엔티티
    # --- 휴먼 정합 자동 보정 (Auto-Calibration) ⑦ ---
    calibrated_from: float | None = None  # 자동 보정 전 원점수 (보정 적용 시만)


class Nugget(BaseModel):
    """질문에 답하기 위해 필요한 핵심 정보 조각 (TREC RAG nugget)"""
    text: str
    found_in_context: bool = False
    matched_chunk_id: str | None = None


class DiagnosisVerdict(BaseModel):
    """검색 vs 생성 책임 진단 결과 (2x2)"""
    category: str          # retrieval_failure | generation_hallucination | both | healthy
    recall_ok: bool
    faithfulness_ok: bool
    recommendation: str = ""


class ReviewStatus(str, Enum):
    PENDING = "pending"        # 미검수
    APPROVED = "approved"      # 자동 점수에 동의 확정
    OVERRIDDEN = "overridden"  # 사람이 점수 수정 확정
    SKIPPED = "skipped"        # 보류


class EvalLevel(str, Enum):
    RETRIEVAL = "retrieval"    # 검색 컨텍스트 품질
    TURN = "turn"              # 답변 1건 품질
    SESSION = "session"        # 대화 전체 품질


class EvaluationRecord(BaseModel):
    """단일 평가 결과 (턴 또는 세션 또는 검색 레벨)"""
    eval_id: str
    qa_id: str | None = None     # 원본 QAPair 추적용 (resume 키)
    call_id: str
    # --- 멀티테넌시 / 세션 구조 ---
    tenant_id: str = "default"
    conversation_id: str | None = None
    subscriber_id: str | None = None
    level: EvalLevel = EvalLevel.TURN          # retrieval / turn / session
    turn_index: int | None = None
    eval_run_id: str | None = None             # 소속 평가배치
    # --- 평가 내용 ---
    query: str
    generated_answer: str
    retrieval_result: RetrievalResult
    context_source: str = "auditor"   # faithfulness 근거 출처: "bot_trace" | "auditor"
    scores: list[MetricScore] = Field(default_factory=list)
    nuggets: list[Nugget] = Field(default_factory=list)        # nugget recall 활성 시
    diagnosis: DiagnosisVerdict | None = None                  # 진단 활성 시
    ground_truth: str | None = None                            # 정답(있으면 유사도)
    # --- Judge ---
    evaluated_at: datetime = Field(default_factory=utcnow)
    judge_provider: str = "openai"
    judge_model: str = "gpt-4o"
    # --- 휴먼 재평가 ---
    needs_human_review: bool = False
    review_status: ReviewStatus = ReviewStatus.PENDING
    human_scores: dict[str, float] = Field(default_factory=dict)  # metric → 사람 점수
    human_label: list[str] = Field(default_factory=list)          # 환각/검색실패 등
    human_comment: str = ""
    reviewer: str | None = None
    reviewed_at: datetime | None = None

    def final_scores(self) -> dict[str, float]:
        """Final = 사람 확정값 우선, 없으면 자동 점수"""
        out = {ms.metric: ms.score for ms in self.scores}
        out.update(self.human_scores)   # 사람이 수정한 메트릭만 덮어씀
        return out


# ============================================================
# CP5 — 집계 스키마
# ============================================================

class AggregatedMetric(BaseModel):
    """집계된 메트릭 통계"""
    metric: str
    mean: float
    median: float
    p10: float
    p90: float
    below_sla_count: int
    total_count: int
    sla_pass_rate: float
    ci_low: float | None = None     # 부트스트랩 신뢰구간 (statistics 옵션)
    ci_high: float | None = None


class AuditSummary(BaseModel):
    """전체 감사 요약 (CP5 출력)"""
    run_id: str
    period_start: datetime | None = None
    period_end: datetime | None = None
    total_calls: int
    total_evaluations: int
    metrics: list[AggregatedMetric] = Field(default_factory=list)
    flagged_call_ids: list[str] = Field(default_factory=list)  # SLA 미달 콜
    diagnosis_distribution: dict[str, int] = Field(default_factory=dict)  # 진단 분포
    human_review_queue: list[str] = Field(default_factory=list)  # active sampling eval_id
    meta_eval: dict = Field(default_factory=dict)               # Judge 메타평가 결과
    generated_at: datetime = Field(default_factory=utcnow)


# ============================================================
# 대화(세션) & 평가배치 엔티티
# ============================================================

class Conversation(BaseModel):
    """한 최종사용자와의 대화 세션 (싱글턴 또는 멀티턴)"""
    conversation_id: str
    tenant_id: str = "default"
    subscriber_id: str
    is_multiturn: bool = False
    turn_count: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionEvaluation(BaseModel):
    """세션(대화 전체) 레벨 평가 결과"""
    conversation_id: str
    tenant_id: str = "default"
    scores: list[MetricScore] = Field(default_factory=list)  # resolution/consistency/efficiency 등
    resolved: bool | None = None          # 목표 달성/해결 여부
    escalation_needed: bool | None = None  # 상담원 연결 필요 감지
    evaluated_at: datetime = Field(default_factory=utcnow)
    judge_provider: str = "openai"
    judge_model: str = "gpt-4o"


class EvalRun(BaseModel):
    """평가배치 — 어떤 구성(Judge·레벨·옵션)으로 평가했는지 재현용"""
    run_id: str
    tenant_id: str = "default"
    config: dict[str, Any] = Field(default_factory=dict)  # judge/levels/metrics/options
    status: str = "completed"   # queued | running | completed | failed
    total_evaluations: int = 0
    created_at: datetime = Field(default_factory=utcnow)


# ============================================================
# 공통 — 파이프라인 실행 컨텍스트
# ============================================================

class PipelineContext(BaseModel):
    """run_pipeline.py 에서 CP 간 공유하는 실행 컨텍스트"""
    run_id: str
    config_path: str = "config/settings.yaml"
    until_cp: str | None = None   # "cp3" 등 중간 종료 지점
    reindex: bool = False
    results_dir: str = "data/results"
    started_at: datetime = Field(default_factory=utcnow)
