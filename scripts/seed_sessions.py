"""
scripts/seed_sessions.py
M2 화면용 세션 구조 mock 데이터 시드.

다양한 가입자(tenant) × 싱글턴/멀티턴 대화 × 턴/세션 레벨 평가 ×
검수상태(미검수/확정/수정)를 SQLite(ResultStore)에 직접 적재한다.

실행:
  python scripts/seed_sessions.py            # 기본 시드
  python scripts/seed_sessions.py --reset    # 기존 DB 비우고 시드
"""
from __future__ import annotations

import argparse
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from AutoAudit.app.core.store import ResultStore  # noqa: E402
from AutoAudit.app.core.types import (  # noqa: E402
    AggregatedMetric,
    AuditSummary,
    DiagnosisVerdict,
    EvalLevel,
    EvaluationRecord,
    MetricScore,
    Nugget,
    RetrievalResult,
    RetrievedContext,
    ReviewStatus,
)

random.seed(42)

TENANTS = [
    ("acme", "Acme Telecom"),
    ("globex", "Globex 보험"),
    ("initech", "Initech 커머스"),
]

# 가입자별 도메인 대화 시나리오 (싱글턴 + 멀티턴 혼합)
SCENARIOS = {
    "acme": [
        {
            "multiturn": True,
            "subscriber": "SUB_0042",
            "turns": [
                ("user", "요금제를 변경하고 싶은데요."),
                ("bot", "안녕하세요! 어떤 요금제로 변경을 원하시나요?"),
                ("user", "데이터 무제한으로요."),
                ("bot", "5G 프리미엄 요금제는 월 69,000원에 데이터 무제한을 제공합니다."),
                ("user", "본인 확인은 어떻게 하나요?"),
                ("bot", "주민번호 또는 비밀번호 4자리로 확인 가능합니다. 또한 대리점에서 지문 인증도 됩니다."),
            ],
            "contexts": [
                "5G 프리미엄 요금제는 월 69,000원, 데이터 무제한입니다.",
                "본인 확인은 주민등록번호 또는 고객 비밀번호 4자리로 가능합니다.",
            ],
            "answer": "주민번호 또는 비밀번호 4자리로 확인 가능합니다. 또한 대리점에서 지문 인증도 됩니다.",
            "question": "본인 확인은 어떻게 하나요?",
            "scores": {"faithfulness": 0.3, "answer_relevance": 1.0, "context_precision": 0.9, "context_recall": 0.98},
            "diag": "generation_hallucination",
            "review": "pending",
        },
        {
            "multiturn": False,
            "subscriber": "SUB_0107",
            "turns": [
                ("user", "데이터 무제한 요금제 얼마예요?"),
                ("bot", "5G 프리미엄 요금제가 월 69,000원에 데이터 무제한입니다."),
            ],
            "contexts": ["5G 프리미엄 요금제는 월 69,000원, 데이터 무제한입니다."],
            "answer": "5G 프리미엄 요금제가 월 69,000원에 데이터 무제한입니다.",
            "question": "데이터 무제한 요금제 얼마예요?",
            "scores": {"faithfulness": 0.95, "answer_relevance": 1.0, "context_precision": 0.95, "context_recall": 1.0},
            "diag": "healthy",
            "review": "approved",
        },
        {
            "multiturn": True,
            "subscriber": "SUB_0233",
            "turns": [
                ("user", "해지하면 위약금 있나요?"),
                ("bot", "약정 기간에 따라 다릅니다. 가입일을 알려주시겠어요?"),
                ("user", "작년 3월이요."),
                ("bot", "죄송합니다, 정확한 위약금은 확인이 어렵습니다. 상담원에게 연결해 드릴까요?"),
            ],
            "contexts": ["위약금은 약정 잔여 기간에 비례하여 부과됩니다."],
            "answer": "죄송합니다, 정확한 위약금은 확인이 어렵습니다. 상담원에게 연결해 드릴까요?",
            "question": "해지하면 위약금 있나요?",
            "scores": {"faithfulness": 0.5, "answer_relevance": 0.6, "context_precision": 0.4, "context_recall": 0.68},
            "diag": "retrieval_failure",
            "review": "overridden",
            "escalation": True,
        },
    ],
    "globex": [
        {
            "multiturn": True,
            "subscriber": "GX_5521",
            "turns": [
                ("user", "실손보험 청구하려면 뭐가 필요해요?"),
                ("bot", "진단서와 영수증, 신분증 사본이 필요합니다."),
                ("user", "온라인으로도 되나요?"),
                ("bot", "네, 모바일 앱이나 홈페이지에서 서류를 첨부해 청구하실 수 있습니다."),
            ],
            "contexts": [
                "실손보험 청구 시 진단서·영수증·신분증 사본이 필요합니다.",
                "청구는 모바일 앱 또는 홈페이지에서 온라인 접수 가능합니다.",
            ],
            "answer": "네, 모바일 앱이나 홈페이지에서 서류를 첨부해 청구하실 수 있습니다.",
            "question": "온라인으로도 되나요?",
            "scores": {"faithfulness": 1.0, "answer_relevance": 1.0, "context_precision": 0.9, "context_recall": 0.95},
            "diag": "healthy",
            "review": "approved",
        },
        {
            "multiturn": False,
            "subscriber": "GX_6390",
            "turns": [
                ("user", "보험금 지급까지 며칠 걸려요?"),
                ("bot", "서류 접수 후 영업일 기준 3일 이내 지급됩니다."),
            ],
            "contexts": ["보험금은 서류 완비 후 영업일 3일 이내 지급됩니다."],
            "answer": "서류 접수 후 영업일 기준 3일 이내 지급됩니다.",
            "question": "보험금 지급까지 며칠 걸려요?",
            "scores": {"faithfulness": 0.92, "answer_relevance": 0.95, "context_precision": 0.88, "context_recall": 0.9},
            "diag": "healthy",
            "review": "pending",
        },
    ],
    "initech": [
        {
            "multiturn": True,
            "subscriber": "IT_9001",
            "turns": [
                ("user", "주문 취소하고 싶어요."),
                ("bot", "주문번호를 알려주시겠어요?"),
                ("user", "20260601-ABC."),
                ("bot", "해당 주문은 이미 배송이 시작되어 취소가 불가합니다. 반품으로 진행해 주세요."),
            ],
            "contexts": ["배송 시작 전 주문만 취소 가능하며, 이후에는 반품 절차를 따릅니다."],
            "answer": "해당 주문은 이미 배송이 시작되어 취소가 불가합니다. 반품으로 진행해 주세요.",
            "question": "주문 취소하고 싶어요.",
            "scores": {"faithfulness": 0.88, "answer_relevance": 0.9, "context_precision": 0.85, "context_recall": 0.8},
            "diag": "healthy",
            "review": "pending",
        },
        {
            "multiturn": False,
            "subscriber": "IT_9145",
            "turns": [
                ("user", "무료배송 기준이 얼마예요?"),
                ("bot", "3만원 이상 구매 시 무료배송입니다. 도서산간은 추가 배송비가 있습니다."),
            ],
            "contexts": ["3만원 이상 주문 시 무료배송, 도서산간 지역은 추가 비용 발생."],
            "answer": "3만원 이상 구매 시 무료배송입니다. 도서산간은 추가 배송비가 있습니다.",
            "question": "무료배송 기준이 얼마예요?",
            "scores": {"faithfulness": 1.0, "answer_relevance": 1.0, "context_precision": 1.0, "context_recall": 1.0},
            "diag": "healthy",
            "review": "pending",
        },
    ],
}

_DIAG_REC = {
    "healthy": "정상 — 조치 불필요",
    "retrieval_failure": "검색 개선 필요: 청킹/임베딩/HyDE·BM25 가중치 점검",
    "generation_hallucination": "생성 개선 필요: 프롬프트 grounding 강화",
    "both": "검색·생성 동시 결함",
}


def _retrieval(question: str, contexts: list[str], call_id: str) -> RetrievalResult:
    ctxs = [
        RetrievedContext(
            chunk_id=uuid.uuid4().hex[:8], content=c,
            score=round(0.95 - i * 0.2, 2), dense_score=round(0.6 - i * 0.1, 2),
            bm25_score=round(0.3 - i * 0.05, 2), source_call_id=call_id,
        )
        for i, c in enumerate(contexts)
    ]
    return RetrievalResult(query=question, hyde_query=f"{question} (HyDE 확장)", contexts=ctxs)


def build_records(run_id: str) -> tuple[list[EvaluationRecord], AuditSummary, list[dict]]:
    records: list[EvaluationRecord] = []
    convo_rows: list[dict] = []   # 대화 원문(turns) 저장용
    now = datetime.now(UTC)

    for tid, _name in TENANTS:
        for si, sc in enumerate(SCENARIOS[tid]):
            conv_id = f"{tid.upper()}-{1000 + si}"
            ts = now - timedelta(hours=random.randint(1, 72))
            convo_rows.append({
                "conversation_id": conv_id, "tenant_id": tid,
                "subscriber_id": sc["subscriber"],
                "turns": [{"role": r, "content": c} for r, c in sc["turns"]],
                "started_at": ts.isoformat(),
            })
            rr = _retrieval(sc["question"], sc["contexts"], conv_id)
            review = ReviewStatus(sc["review"])
            human_scores = {}
            reviewer = None
            label: list[str] = []
            comment = ""
            if review == ReviewStatus.OVERRIDDEN:
                human_scores = {"faithfulness": round(sc["scores"]["faithfulness"] - 0.2, 2)}
                reviewer = "qa_kim"
                label = ["검색실패"]
                comment = "검색이 위약금 규정을 못 가져옴. 검색 개선 필요."
            elif review == ReviewStatus.APPROVED:
                reviewer = "qa_lee"

            # 턴 레벨 (대상 답변)
            turn_idx = len(sc["turns"]) - 1
            scores = [
                MetricScore(
                    metric=m, score=v, reasoning=f"{m} 자동 평가",
                    confidence=round(random.uniform(0.4, 0.95), 2),
                    is_low_confidence=v < 0.6,
                    claims=[] , method="claim_nli" if m == "faithfulness" else "single",
                )
                for m, v in sc["scores"].items()
            ]
            nuggets = [
                Nugget(text=sc["contexts"][0][:20], found_in_context=True),
                Nugget(text="추가 정보", found_in_context=sc["scores"]["context_recall"] > 0.8),
            ]
            diag = DiagnosisVerdict(
                category=sc["diag"],
                recall_ok=sc["scores"]["context_recall"] >= 0.7,
                faithfulness_ok=sc["scores"]["faithfulness"] >= 0.8,
                recommendation=_DIAG_REC[sc["diag"]],
            )
            records.append(EvaluationRecord(
                eval_id=uuid.uuid4().hex, qa_id=f"{conv_id}-t{turn_idx}",
                call_id=conv_id, tenant_id=tid, conversation_id=conv_id,
                subscriber_id=sc["subscriber"], level=EvalLevel.TURN, turn_index=turn_idx,
                eval_run_id=run_id, query=sc["question"], generated_answer=sc["answer"],
                retrieval_result=rr, scores=scores, nuggets=nuggets, diagnosis=diag,
                evaluated_at=ts, judge_provider="anthropic", judge_model="claude-sonnet-4-5",
                review_status=review, human_scores=human_scores, human_label=label,
                human_comment=comment, reviewer=reviewer,
                reviewed_at=ts if reviewer else None,
                needs_human_review=review == ReviewStatus.PENDING,
            ))

            # 세션 레벨 (멀티턴만)
            if sc["multiturn"]:
                resolved = sc["diag"] == "healthy"
                escalation = sc.get("escalation", False)
                sess_scores = [
                    MetricScore(metric="resolution", score=0.9 if resolved else 0.4,
                                reasoning="목표 달성 여부", method="session"),
                    MetricScore(metric="multiturn_consistency",
                                score=0.6 if sc["diag"] == "generation_hallucination" else 0.95,
                                reasoning="턴 간 일관성", method="session"),
                    MetricScore(metric="efficiency", score=round(random.uniform(0.7, 0.95), 2),
                                reasoning="대화 효율성", method="session"),
                    MetricScore(metric="escalation_handling", score=0.0 if escalation else 1.0,
                                reasoning="상담원 연결 필요" if escalation else "콜봇 내 해결",
                                method="session"),
                ]
                records.append(EvaluationRecord(
                    eval_id=uuid.uuid4().hex, qa_id=f"{conv_id}-session",
                    call_id=conv_id, tenant_id=tid, conversation_id=conv_id,
                    subscriber_id=sc["subscriber"], level=EvalLevel.SESSION, turn_index=None,
                    eval_run_id=run_id, query="(세션 전체)",
                    generated_answer=f"멀티턴 {len(sc['turns'])//2}문답",
                    retrieval_result=RetrievalResult(query="(세션)"),
                    scores=sess_scores, evaluated_at=ts,
                    judge_provider="anthropic", judge_model="claude-sonnet-4-5",
                    review_status=ReviewStatus.PENDING, needs_human_review=True,
                ))

    # 집계 summary
    turn_recs = [r for r in records if r.level == EvalLevel.TURN]
    metric_vals: dict[str, list[float]] = {}
    for r in turn_recs:
        for s in r.scores:
            metric_vals.setdefault(s.metric, []).append(s.score)
    import statistics
    metrics = []
    for m, vals in metric_vals.items():
        below = [v for v in vals if v < 0.8]
        metrics.append(AggregatedMetric(
            metric=m, mean=statistics.mean(vals), median=statistics.median(vals),
            p10=min(vals), p90=max(vals), below_sla_count=len(below),
            total_count=len(vals), sla_pass_rate=1 - len(below) / len(vals),
        ))
    flagged = sorted({r.call_id for r in turn_recs
                      if any(s.score < 0.8 for s in r.scores)})
    summary = AuditSummary(
        run_id=run_id, total_calls=len({r.conversation_id for r in turn_recs}),
        total_evaluations=len(turn_recs), metrics=metrics, flagged_call_ids=flagged,
        diagnosis_distribution=_diag_dist(turn_recs),
    )
    return records, summary, convo_rows


def _diag_dist(recs: list[EvaluationRecord]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for r in recs:
        if r.diagnosis:
            dist[r.diagnosis.category] = dist.get(r.diagnosis.category, 0) + 1
    return dist


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="기존 DB 삭제 후 시드")
    args = ap.parse_args()

    db_path = _ROOT / "data" / "results" / "autoaudit.db"
    if args.reset and db_path.exists():
        db_path.unlink()
        for suffix in ("-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                p.unlink()
        print(f"기존 DB 삭제: {db_path}")

    store = ResultStore(db_path=str(db_path))
    run_id = f"seed_{datetime.now(UTC).strftime('%Y%m%d')}"
    records, summary, convo_rows = build_records(run_id)
    store.upsert_evaluations(run_id, records)
    store.upsert_summary(summary)
    for cr in convo_rows:
        store.upsert_conversation(**cr)

    turns = sum(1 for r in records if r.level == EvalLevel.TURN)
    sessions = sum(1 for r in records if r.level == EvalLevel.SESSION)
    print(f"✅ 시드 완료 (run={run_id})")
    print(f"   tenants: {len(TENANTS)} · 턴 평가: {turns} · 세션 평가: {sessions}")
    for tid, name in TENANTS:
        convos = store.list_conversations(tenant_id=tid)
        multi = sum(1 for c in convos if c["is_multiturn"])
        print(f"   - {name}({tid}): 대화 {len(convos)}건 (멀티턴 {multi})")


if __name__ == "__main__":
    main()
