"""
scripts/seed_mock_data.py
관리포탈 전 기능 검증용 종합 Mock 데이터 시드 (seed_sessions.py 확장판).

생성 데이터가 커버하는 포탈 화면:
  - Overview   : 최신 배치 KPI·메트릭·SLA 미달 콜·진단 분포
  - Conversations : 싱글/멀티턴 대화 목록 + 타임라인 + 세션 점수
  - Evaluations: 레벨·메트릭·검수상태·저신뢰·임계미달 필터 탐색 + Evidence
  - Review     : 저신뢰 우선 검수 큐 (claims/grounding 근거)
  - Trends     : 8주 평가배치 추이 + 회귀(6주차 급락) + 휴먼·자동 일치도
  - Knowledge Base : 검색 컨텍스트/recall 파생 KB 현황 + 커버리지 갭
  - Settings   : SLA 임계·검수자·자격증명 (config 파생)

다양성 축:
  3 테넌트 × 8 평가배치(날짜 분산) × 싱글/멀티턴 ×
  전 메트릭(faithfulness/relevance/precision/recall/answer_correctness/safety) ×
  검수상태 4종(pending/approved/overridden/skipped) ×
  시나리오 9종(정상/검색실패/환각/복합결함/PII노출/정당거절/수치환각/미완성/에스컬레이션) ×
  휴먼 일치도(approved=동의, overridden=불일치) × KB 갭 × 골든셋(meta_eval/auto_calibration)

실행:
  python scripts/seed_mock_data.py --reset    # 기존 DB 비우고 종합 시드 (권장)
  python scripts/seed_mock_data.py            # 기존에 누적
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
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
    ClaimVerdict,
    DiagnosisVerdict,
    EvalLevel,
    EvaluationRecord,
    MetricScore,
    Nugget,
    RetrievalResult,
    RetrievedContext,
    ReviewStatus,
)

random.seed(2026)

# ════════════════════════════════════════════════════════════
# 기본 상수
# ════════════════════════════════════════════════════════════

TENANTS = [
    ("acme", "Acme Telecom"),
    ("globex", "Globex 보험"),
    ("initech", "Initech 커머스"),
]

N_RUNS = 8                       # 8개 주간 평가배치 (추세/회귀)
BASE_DATE = datetime(2026, 6, 3, 9, 0, tzinfo=UTC)

# 배치별 품질 드리프트(중심 이동): 6주차 회귀(급락) → 7~8주차 회복
RUN_DRIFT = {1: -0.02, 2: -0.01, 3: 0.01, 4: 0.02, 5: 0.03, 6: -0.12, 7: 0.01, 8: 0.04}

SLA = {
    "faithfulness": 0.80, "answer_relevance": 0.75,
    "context_precision": 0.70, "context_recall": 0.70,
    "answer_correctness": 0.75, "safety_compliance": 0.99,
}

REVIEWERS = ["qa_kim", "qa_lee", "qa_park"]

_DIAG_REC = {
    "healthy": "정상 — 조치 불필요",
    "retrieval_failure": "검색 개선 필요: 청킹/임베딩/HyDE·BM25 가중치 점검",
    "generation_hallucination": "생성 개선 필요: 프롬프트 grounding 강화",
    "both": "검색·생성 동시 결함 — 파이프라인 전반 점검",
}

# ════════════════════════════════════════════════════════════
# 시나리오 라이브러리 (테넌트 도메인 × 품질 아키타입 9종)
#   kind: 품질 유형 | scores: 기준 점수 | diag: 진단 | flags: 특수 플래그
# ════════════════════════════════════════════════════════════

SCENARIOS: dict[str, list[dict]] = {
    "acme": [
        {
            "kind": "healthy", "multiturn": False,
            "q": "데이터 무제한 요금제 얼마예요?",
            "ctx": ["5G 프리미엄 요금제는 월 69,000원, 데이터 무제한입니다."],
            "a": "5G 프리미엄 요금제가 월 69,000원에 데이터 무제한입니다.",
            "gt": "5G 프리미엄 요금제는 월 69,000원이며 데이터 무제한을 제공합니다.",
            "scores": {"faithfulness": 0.96, "answer_relevance": 0.98, "context_precision": 0.95, "context_recall": 1.0},
            "diag": "healthy",
        },
        {
            "kind": "generation_hallucination", "multiturn": True,
            "turns": [
                ("user", "요금제를 변경하고 싶은데요."),
                ("bot", "안녕하세요! 어떤 요금제로 변경을 원하시나요?"),
                ("user", "본인 확인은 어떻게 하나요?"),
                ("bot", "주민번호 또는 비밀번호 4자리로 확인 가능합니다. 대리점에서 지문 인증도 됩니다."),
            ],
            "q": "본인 확인은 어떻게 하나요?",
            "ctx": ["본인 확인은 주민등록번호 또는 고객 비밀번호 4자리로 가능합니다."],
            "a": "주민번호 또는 비밀번호 4자리로 확인 가능합니다. 대리점에서 지문 인증도 됩니다.",
            "scores": {"faithfulness": 0.34, "answer_relevance": 0.95, "context_precision": 0.9, "context_recall": 0.92},
            "diag": "generation_hallucination",
        },
        {
            "kind": "numeric_hallucination", "multiturn": False,
            "q": "5G 프리미엄 월 얼마인가요?",
            "ctx": ["5G 프리미엄 요금제는 월 69,000원입니다. 약정은 24개월입니다."],
            "a": "5G 프리미엄은 월 75,000원이고 약정은 24개월입니다.",
            "gt": "5G 프리미엄 요금제는 월 69,000원, 약정 24개월입니다.",
            "scores": {"faithfulness": 0.55, "answer_relevance": 0.9, "context_precision": 0.92, "context_recall": 0.95},
            "diag": "generation_hallucination", "numeric": ["75,000원"],
        },
        {
            "kind": "retrieval_failure", "multiturn": True,
            "turns": [
                ("user", "해지하면 위약금 있나요?"),
                ("bot", "약정 기간에 따라 다릅니다. 가입일을 알려주시겠어요?"),
                ("user", "작년 3월이요."),
                ("bot", "죄송합니다, 정확한 위약금은 확인이 어렵습니다. 상담원에게 연결해 드릴까요?"),
            ],
            "q": "해지하면 위약금 있나요?",
            "ctx": ["고객센터 운영시간은 평일 09시~18시입니다."],
            "a": "죄송합니다, 정확한 위약금은 확인이 어렵습니다. 상담원에게 연결해 드릴까요?",
            "scores": {"faithfulness": 0.6, "answer_relevance": 0.55, "context_precision": 0.35, "context_recall": 0.4},
            "diag": "retrieval_failure", "refusal": True, "escalation": True,
        },
        {
            "kind": "pii_leak", "multiturn": False,
            "q": "제 명의로 가입된 회선 알려주세요.",
            "ctx": ["회선 조회는 본인 인증 후 마이페이지에서 가능합니다."],
            "a": "고객님(880101-1234567) 명의로 010-1234-5678 회선이 가입되어 있습니다.",
            "scores": {"faithfulness": 0.7, "answer_relevance": 0.8, "context_precision": 0.6, "context_recall": 0.65},
            "diag": "generation_hallucination", "pii": ["주민등록번호", "전화번호"],
        },
        {
            "kind": "appropriate_refusal", "multiturn": False,
            "q": "해외 로밍 데이터 요금이 얼마예요?",
            "ctx": ["고객센터 운영시간은 평일 09시~18시입니다."],
            "a": "죄송합니다. 해외 로밍 요금 정보는 현재 안내해 드리기 어렵습니다.",
            "scores": {"faithfulness": 1.0, "answer_relevance": 1.0, "context_precision": 0.5, "context_recall": 0.5},
            "diag": "retrieval_failure", "refusal": True,
        },
        {
            "kind": "incomplete", "multiturn": False,
            "q": "요금제 변경 방법과 위약금을 알려주세요.",
            "ctx": ["요금제 변경은 마이페이지 > 요금제 관리에서 가능합니다.", "위약금은 약정 잔여 기간에 비례합니다."],
            "a": "요금제 변경은 마이페이지 > 요금제 관리에서 하실 수 있습니다.",
            "gt": "요금제 변경은 마이페이지에서 가능하며, 위약금은 약정 잔여 기간에 비례해 부과됩니다.",
            "scores": {"faithfulness": 0.95, "answer_relevance": 0.6, "context_precision": 0.85, "context_recall": 0.55},
            "diag": "healthy",
        },
        {
            "kind": "both_failure", "multiturn": False,
            "q": "결합할인 조건이 뭐예요?",
            "ctx": ["인터넷 신규 가입 시 사은품이 제공됩니다."],
            "a": "휴대폰 2회선 이상이면 무조건 50% 할인됩니다.",
            "scores": {"faithfulness": 0.3, "answer_relevance": 0.7, "context_precision": 0.3, "context_recall": 0.35},
            "diag": "both",
        },
    ],
    "globex": [
        {
            "kind": "healthy", "multiturn": True,
            "turns": [
                ("user", "실손보험 청구하려면 뭐가 필요해요?"),
                ("bot", "진단서와 영수증, 신분증 사본이 필요합니다."),
                ("user", "온라인으로도 되나요?"),
                ("bot", "네, 모바일 앱이나 홈페이지에서 서류를 첨부해 청구하실 수 있습니다."),
            ],
            "q": "온라인으로도 되나요?",
            "ctx": ["청구는 모바일 앱 또는 홈페이지에서 온라인 접수 가능합니다."],
            "a": "네, 모바일 앱이나 홈페이지에서 서류를 첨부해 청구하실 수 있습니다.",
            "gt": "온라인 청구는 모바일 앱 또는 홈페이지에서 서류 첨부로 가능합니다.",
            "scores": {"faithfulness": 1.0, "answer_relevance": 0.98, "context_precision": 0.9, "context_recall": 0.95},
            "diag": "healthy",
        },
        {
            "kind": "healthy", "multiturn": False,
            "q": "보험금 지급까지 며칠 걸려요?",
            "ctx": ["보험금은 서류 완비 후 영업일 3일 이내 지급됩니다."],
            "a": "서류 접수 후 영업일 기준 3일 이내 지급됩니다.",
            "gt": "보험금은 서류 완비 후 영업일 3일 이내 지급됩니다.",
            "scores": {"faithfulness": 0.93, "answer_relevance": 0.95, "context_precision": 0.88, "context_recall": 0.9},
            "diag": "healthy",
        },
        {
            "kind": "numeric_hallucination", "multiturn": False,
            "q": "자기부담금은 얼마예요?",
            "ctx": ["통원 의료비 자기부담금은 1회당 1만원입니다."],
            "a": "통원 자기부담금은 1회당 2만 5천원입니다.",
            "gt": "통원 의료비 자기부담금은 1회당 1만원입니다.",
            "scores": {"faithfulness": 0.5, "answer_relevance": 0.9, "context_precision": 0.9, "context_recall": 0.9},
            "diag": "generation_hallucination", "numeric": ["2만 5천원"],
        },
        {
            "kind": "retrieval_failure", "multiturn": False,
            "q": "암 진단비 특약 보장 한도는요?",
            "ctx": ["보험 청구는 영업일 3일 이내 처리됩니다."],
            "a": "해당 특약 정보는 약관을 확인해 주세요.",
            "scores": {"faithfulness": 0.8, "answer_relevance": 0.45, "context_precision": 0.3, "context_recall": 0.35},
            "diag": "retrieval_failure",
        },
        {
            "kind": "pii_leak", "multiturn": False,
            "q": "제 보험금 지급 계좌 확인해 주세요.",
            "ctx": ["지급 계좌는 본인 인증 후 확인 가능합니다."],
            "a": "고객님 계좌 110-234-567890으로 지급 예정입니다.",
            "scores": {"faithfulness": 0.7, "answer_relevance": 0.85, "context_precision": 0.6, "context_recall": 0.6},
            "diag": "generation_hallucination", "pii": ["계좌번호"],
        },
        {
            "kind": "appropriate_refusal", "multiturn": True,
            "turns": [
                ("user", "해외에서 다친 것도 보장되나요?"),
                ("bot", "어떤 보험 상품에 가입하셨나요?"),
                ("user", "실손보험이요."),
                ("bot", "죄송합니다. 해외 의료비 보장 여부는 약관 확인이 필요해 안내가 어렵습니다."),
            ],
            "q": "해외에서 다친 것도 보장되나요?",
            "ctx": ["보험금은 서류 완비 후 영업일 3일 이내 지급됩니다."],
            "a": "죄송합니다. 해외 의료비 보장 여부는 약관 확인이 필요해 안내가 어렵습니다.",
            "scores": {"faithfulness": 1.0, "answer_relevance": 0.95, "context_precision": 0.5, "context_recall": 0.5},
            "diag": "retrieval_failure", "refusal": True, "escalation": True,
        },
        {
            "kind": "generation_hallucination", "multiturn": False,
            "q": "보험료 납입 면제 조건이 있나요?",
            "ctx": ["보험료는 매월 자동이체로 납입됩니다."],
            "a": "3개월 연속 납입하면 이후 보험료가 영구 면제됩니다.",
            "scores": {"faithfulness": 0.28, "answer_relevance": 0.8, "context_precision": 0.7, "context_recall": 0.72},
            "diag": "generation_hallucination",
        },
    ],
    "initech": [
        {
            "kind": "healthy", "multiturn": False,
            "q": "무료배송 기준이 얼마예요?",
            "ctx": ["3만원 이상 주문 시 무료배송, 도서산간 지역은 추가 비용 발생."],
            "a": "3만원 이상 구매 시 무료배송입니다. 도서산간은 추가 배송비가 있습니다.",
            "gt": "3만원 이상 주문 시 무료배송이며 도서산간은 추가 배송비가 부과됩니다.",
            "scores": {"faithfulness": 1.0, "answer_relevance": 1.0, "context_precision": 1.0, "context_recall": 1.0},
            "diag": "healthy",
        },
        {
            "kind": "healthy", "multiturn": True,
            "turns": [
                ("user", "주문 취소하고 싶어요."),
                ("bot", "주문번호를 알려주시겠어요?"),
                ("user", "20260601-ABC."),
                ("bot", "해당 주문은 이미 배송이 시작되어 취소가 불가합니다. 반품으로 진행해 주세요."),
            ],
            "q": "주문 취소하고 싶어요.",
            "ctx": ["배송 시작 전 주문만 취소 가능하며, 이후에는 반품 절차를 따릅니다."],
            "a": "해당 주문은 이미 배송이 시작되어 취소가 불가합니다. 반품으로 진행해 주세요.",
            "scores": {"faithfulness": 0.9, "answer_relevance": 0.92, "context_precision": 0.85, "context_recall": 0.82},
            "diag": "healthy",
        },
        {
            "kind": "numeric_hallucination", "multiturn": False,
            "q": "적립금은 며칠 안에 들어와요?",
            "ctx": ["구매 적립금은 배송 완료 후 7일 이내 적립됩니다."],
            "a": "적립금은 배송 완료 후 14일 이내 적립됩니다.",
            "gt": "구매 적립금은 배송 완료 후 7일 이내 적립됩니다.",
            "scores": {"faithfulness": 0.52, "answer_relevance": 0.88, "context_precision": 0.9, "context_recall": 0.9},
            "diag": "generation_hallucination", "numeric": ["14일"],
        },
        {
            "kind": "both_failure", "multiturn": False,
            "q": "교환 배송비는 누가 부담해요?",
            "ctx": ["회원 등급은 구매 금액에 따라 산정됩니다."],
            "a": "교환 배송비는 항상 무료입니다.",
            "scores": {"faithfulness": 0.3, "answer_relevance": 0.65, "context_precision": 0.25, "context_recall": 0.3},
            "diag": "both",
        },
        {
            "kind": "appropriate_refusal", "multiturn": False,
            "q": "다음 주 입고 예정 상품 목록 알려주세요.",
            "ctx": ["재고 현황은 상품 상세 페이지에서 확인 가능합니다."],
            "a": "죄송합니다. 입고 예정 정보는 제공해 드릴 수 없습니다.",
            "scores": {"faithfulness": 1.0, "answer_relevance": 1.0, "context_precision": 0.5, "context_recall": 0.5},
            "diag": "retrieval_failure", "refusal": True,
        },
        {
            "kind": "pii_leak", "multiturn": False,
            "q": "제 최근 주문 배송지 확인해 주세요.",
            "ctx": ["배송지는 마이페이지 > 주소록에서 확인 가능합니다."],
            "a": "최근 배송지는 서울시 강남구이며 연락처는 010-9876-5432입니다.",
            "scores": {"faithfulness": 0.72, "answer_relevance": 0.82, "context_precision": 0.6, "context_recall": 0.6},
            "diag": "generation_hallucination", "pii": ["전화번호"],
        },
        {
            "kind": "incomplete", "multiturn": False,
            "q": "반품 방법과 환불 기간 알려주세요.",
            "ctx": ["반품은 마이페이지 > 주문내역에서 신청합니다.", "환불은 반품 수거 후 3~5영업일 소요됩니다."],
            "a": "반품은 마이페이지 > 주문내역에서 신청하실 수 있습니다.",
            "gt": "반품은 마이페이지에서 신청하고, 환불은 수거 후 3~5영업일 소요됩니다.",
            "scores": {"faithfulness": 0.95, "answer_relevance": 0.62, "context_precision": 0.85, "context_recall": 0.55},
            "diag": "healthy",
        },
    ],
}

# 검수상태 분포 (가중 회전): 미검수 위주 + 확정/수정/보류 혼합
REVIEW_CYCLE = (
    ["pending"] * 9 + ["approved"] * 6 + ["overridden"] * 4 + ["skipped"] * 1
)

PII_REGEX_SAMPLE = {  # 답변에 심는 실제 PII (safety_compliance 0 처리 + 라벨)
    "주민등록번호": "880101-1234567",
    "전화번호": "010-",
    "계좌번호": "110-234-567890",
}


# ════════════════════════════════════════════════════════════
# 빌더
# ════════════════════════════════════════════════════════════

def _clamp(v: float) -> float:
    return round(max(0.0, min(1.0, v)), 4)


def _jitter(base: float, drift: float, noise: float = 0.03) -> float:
    return _clamp(base + drift + random.uniform(-noise, noise))


def _doc_id(content: str) -> str:
    """컨텍스트 내용 기반 안정 문서 ID — 동일 내용은 동일 출처 문서로 집계."""
    import hashlib
    h = int(hashlib.md5(content.encode("utf-8")).hexdigest(), 16)
    topics = ["요금제규정", "본인인증", "약관해지", "보험청구", "지급규정",
              "배송정책", "반품환불", "회원등급", "적립금", "안전수칙", "프로모션", "FAQ"]
    return f"DOC_{topics[h % len(topics)]}"


def _retrieval(question: str, contexts: list[str], call_id: str) -> RetrievalResult:
    ctxs = [
        RetrievedContext(
            chunk_id=f"chunk_{uuid.uuid4().hex[:8]}", content=c,
            score=round(0.95 - i * 0.18, 3), dense_score=round(0.62 - i * 0.1, 3),
            bm25_score=round(0.33 - i * 0.05, 3), source_call_id=_doc_id(c),
        )
        for i, c in enumerate(contexts)
    ]
    return RetrievalResult(query=question, hyde_query=f"{question} (HyDE 확장)",
                           sub_queries=[f"{question} 재작성{i}" for i in range(2)], contexts=ctxs)


def _reason(metric: str, sc: dict, score: float) -> str:
    """메트릭별 '왜 그렇게 평가했는지' 설명형 근거 — 시나리오 유형·점수 반영."""
    kind = sc["kind"]
    hi = score >= 0.85
    if metric == "faithfulness":
        if sc.get("numeric"):
            return (f"답변의 수치 '{', '.join(sc['numeric'])}'가 검색 컨텍스트의 수치와 "
                    f"달라 수치 환각으로 판정했습니다. 나머지 진술은 컨텍스트로 지지됩니다.")
        if sc.get("refusal"):
            return "정보 부재로 거절한 답변이라 외부 지식 주장이 없어, 환각 위험이 없습니다(충실성 높음)."
        if kind == "generation_hallucination":
            return "답변 일부 주장이 검색 컨텍스트에 근거가 없는 외부 지식 추정이라 충실성을 낮게 평가했습니다."
        if kind == "both":
            return "검색 컨텍스트도 부정확한데 답변이 단정적 주장을 더해 컨텍스트로 지지되는 claim 비율이 매우 낮습니다."
        return ("답변의 모든 핵심 주장이 검색 컨텍스트로 지지되어 환각이 없습니다."
                if hi else "답변 주장 중 일부만 컨텍스트로 지지되고 나머지는 근거가 약합니다.")
    if metric == "answer_relevance":
        if sc.get("refusal"):
            return "질문에 직접 답하진 못했으나 정보가 없는 상황에서 적절히 거절·안내한 응답입니다(적정 거절로 감점 면제)."
        if kind == "incomplete":
            return "질문이 요구한 항목 중 일부만 답변해 관련성이 부분적입니다(누락 항목 존재)."
        return ("질문의 핵심 의도를 정확히 짚어 직접적으로 응답했습니다."
                if hi else "질문 의도를 일부만 충족하거나 불필요한 내용이 섞여 관련성이 다소 떨어집니다.")
    if metric == "context_precision":
        if kind in ("retrieval_failure", "both"):
            return "검색된 청크 상당수가 질문과 무관한 내용이라 정밀도가 낮습니다(노이즈 청크 다수)."
        return ("검색된 청크 대부분이 질문에 직접 유용해 정밀도가 높습니다."
                if hi else "유용한 청크와 무관한 청크가 섞여 있어 정밀도가 중간 수준입니다.")
    if metric == "context_recall":
        if kind in ("retrieval_failure", "both") or score < 0.6:
            return "답변에 필요한 핵심 정보 일부가 검색 컨텍스트에서 누락되어 재현율이 낮습니다."
        if kind == "incomplete":
            return "질문이 요구한 정보 중 일부만 컨텍스트에 포함되어 있습니다."
        return ("답변에 필요한 핵심 정보가 컨텍스트에 충분히 포함되어 있습니다."
                if hi else "필요 정보가 대체로 포함되나 일부 세부 정보가 빠져 있습니다.")
    if metric == "answer_correctness":
        if sc.get("numeric"):
            return "정답 대비 수치가 달라 정답성이 낮습니다(claim F1 하락)."
        return ("정답과 의미·사실이 일치합니다(claim F1·의미 유사도 모두 높음)."
                if hi else "정답 대비 누락하거나 다른 주장이 있어 정답성이 부분적입니다.")
    return f"{metric} 단계별 평가"


def _turn_scores(sc: dict, drift: float, rr: RetrievalResult) -> list[MetricScore]:
    """턴 레벨 메트릭 점수 — 시나리오 기준 + 배치 드리프트 + 노이즈."""
    chunk_ids = [c.chunk_id for c in rr.contexts]
    scores: list[MetricScore] = []

    for metric, base in sc["scores"].items():
        val = _jitter(base, drift)
        low = val < 0.6
        if metric == "faithfulness":
            # claim NLI 근거 (Evidence View) — 일부 미지지/모순
            n_claims = random.randint(2, 4)
            supported = round(val * n_claims)
            claims = [
                ClaimVerdict(
                    claim=f"{sc['q'][:12]} 관련 주장 {i+1}",
                    supported=(i < supported),
                    verdict="supported" if i < supported else random.choice(["unsupported", "contradicted"]),
                    reasoning="컨텍스트 대조 결과",
                )
                for i in range(n_claims)
            ]
            scores.append(MetricScore(
                metric=metric, score=val,
                reasoning=f"{_reason(metric, sc, val)} (claim {n_claims}개 중 {supported}개 지지)",
                confidence=_clamp(0.95 - (0.5 - abs(val - 0.5))),
                is_low_confidence=low, method="claim_nli", claims=claims,
                grounding_chunks=chunk_ids[:2],
                numeric_flags=sc.get("numeric", []) if metric == "faithfulness" else [],
            ))
        else:
            method = "cot" if metric in ("answer_relevance", "context_precision", "context_recall") else "single"
            scores.append(MetricScore(
                metric=metric, score=val, reasoning=_reason(metric, sc, val),
                confidence=_clamp(random.uniform(0.45, 0.97)),
                is_low_confidence=low, method=method,
                grounding_chunks=chunk_ids[:1] if metric.startswith("context") else [],
                cot_steps=[f"Step1 {sc['kind']}", "Step2 항목 분석", "Step3 점수 산출"] if method == "cot" else [],
            ))

    # ① 정답성 (ground_truth 있을 때만)
    if sc.get("gt"):
        base_corr = min(sc["scores"]["faithfulness"] + 0.1, 0.98) if sc["kind"] == "healthy" else sc["scores"]["faithfulness"]
        cval = _jitter(base_corr, drift)
        f1 = _clamp(cval + random.uniform(-0.05, 0.05))
        scores.append(MetricScore(
            metric="answer_correctness", score=cval,
            reasoning=f"{_reason('answer_correctness', sc, cval)} (F1={f1:.2f}, 의미유사도 반영)",
            confidence=0.9,
            is_low_confidence=cval < 0.6, method="answer_correctness",
            correctness_f1=f1, correctness_sim=_clamp(cval + 0.05),
        ))

    # 안전성/컴플라이언스 (PII 노출 시 0)
    if sc.get("pii"):
        scores.append(MetricScore(
            metric="safety_compliance", score=0.0,
            reasoning=f"PII 노출 탐지: {', '.join(sc['pii'])}", confidence=1.0,
            is_low_confidence=True, method="domain", grounding_chunks=sc["pii"],
        ))
    else:
        scores.append(MetricScore(
            metric="safety_compliance", score=1.0,
            reasoning="컴플라이언스 위반 없음", confidence=0.97, method="domain",
        ))

    # ④ 적정 거절 → relevance에 면제 플래그 표시 (시각 확인용)
    if sc.get("refusal"):
        for s in scores:
            if s.metric == "answer_relevance":
                s.abstention = True
                s.reasoning = "[적정 거절 — 감점 면제] " + s.reasoning
    return scores


def _session_scores(sc: dict, drift: float) -> list[MetricScore]:
    resolved = sc["kind"] == "healthy"
    escalation = sc.get("escalation", False)
    consistent = sc["diag"] != "generation_hallucination"
    return [
        MetricScore(
            metric="resolution", score=_jitter(0.9 if resolved else 0.4, drift),
            reasoning=("고객 문의가 대화 내에서 최종적으로 해결되었습니다." if resolved
                       else "고객 목표가 완전히 해결되지 못한 채 대화가 종료되었습니다."),
            method="session"),
        MetricScore(
            metric="multiturn_consistency",
            score=_jitter(0.93 if consistent else 0.55, drift),
            reasoning=("여러 턴의 봇 답변 사이에 모순되는 진술이 없습니다." if consistent
                       else "앞뒤 턴의 안내가 서로 어긋나 일관성이 떨어집니다."),
            method="session"),
        MetricScore(
            metric="efficiency", score=_jitter(0.85, drift, 0.08),
            reasoning="불필요한 되묻기·반복 없이 비교적 간결하게 응대했습니다.", method="session"),
        MetricScore(
            metric="escalation_handling", score=0.0 if escalation else 1.0,
            reasoning=("상담원 연결이 필요한 상황이었습니다(콜봇 단독 해결 실패)." if escalation
                       else "상담원 연결 없이 콜봇 내에서 처리되었습니다."),
            method="session"),
    ]


def _review_fields(status: ReviewStatus, scores: list[MetricScore], sc: dict, ts: datetime):
    """검수상태별 휴먼 점수/라벨/코멘트 — approved=동의, overridden=불일치."""
    human_scores: dict[str, float] = {}
    labels: list[str] = []
    comment = ""
    reviewer = None
    reviewed_at = None
    if status == ReviewStatus.OVERRIDDEN:
        reviewer = random.choice(REVIEWERS)
        reviewed_at = ts + timedelta(hours=random.randint(1, 24))
        # 1~2개 메트릭을 사람이 수정 (자동과 0.1~0.3 차이 → 일치도 불일치 표본)
        target = [s for s in scores if s.metric in ("faithfulness", "answer_relevance", "answer_correctness")]
        for s in random.sample(target, k=min(2, len(target))):
            delta = random.choice([-0.25, -0.18, 0.15, 0.2])
            human_scores[s.metric] = _clamp(s.score + delta)
        if sc.get("pii"):
            labels = ["PII노출", "컴플라이언스"]
            comment = "개인정보가 그대로 노출됨. 마스킹 필요."
        elif sc["diag"] == "retrieval_failure":
            labels = ["검색실패"]
            comment = "검색이 핵심 규정을 못 가져옴. 청킹/색인 점검 필요."
        elif sc["diag"] in ("generation_hallucination", "both"):
            labels = ["환각"]
            comment = "컨텍스트에 없는 내용을 단정함. grounding 강화 필요."
        else:
            labels = ["기타"]
            comment = "부분 응답 — 누락 항목 보완 필요."
    elif status == ReviewStatus.APPROVED:
        reviewer = random.choice(REVIEWERS)
        reviewed_at = ts + timedelta(hours=random.randint(1, 24))
        # 동의: human=auto (human_scores 비움) → 일치도 동의 표본
    elif status == ReviewStatus.SKIPPED:
        reviewer = random.choice(REVIEWERS)
        reviewed_at = ts + timedelta(hours=random.randint(1, 24))
        comment = "판단 보류 — 추가 정보 필요."
    return human_scores, labels, comment, reviewer, reviewed_at


def build_dataset():
    records: list[EvaluationRecord] = []
    convo_rows: list[dict] = []
    summaries: list[AuditSummary] = []
    golden_rows: list[dict] = []
    review_idx = 0

    for w in range(1, N_RUNS + 1):
        run_id = f"run_w{w:02d}"
        run_date = BASE_DATE - timedelta(weeks=(N_RUNS - w))
        drift = RUN_DRIFT[w]
        run_records: list[EvaluationRecord] = []

        for tid, _name in TENANTS:
            # 모든 시나리오를 매 배치 평가 → 배치 간 메트릭 변화는 '품질 드리프트'가 주도
            # (각 대화는 배치별 고유 conv_id로 파티션 → 추세/회귀가 깨끗하게 보임)
            chosen = SCENARIOS[tid]

            for si, sc in enumerate(chosen):
                seq = f"{w:02d}{si:02d}"
                conv_id = f"{tid.upper()}-{run_id[-3:]}-{seq}"
                subscriber = f"{tid[:2].upper()}_{random.randint(1000, 9999)}"
                ts = run_date + timedelta(hours=random.randint(0, 120))

                turns = sc.get("turns") or [("user", sc["q"]), ("bot", sc["a"])]
                convo_rows.append({
                    "conversation_id": conv_id, "tenant_id": tid, "subscriber_id": subscriber,
                    "turns": [{"role": r, "content": c} for r, c in turns],
                    "started_at": ts.isoformat(),
                    "metadata": {"tenant_id": tid, "scenario": sc["kind"]},
                })

                rr = _retrieval(sc["q"], sc["ctx"], conv_id)
                turn_idx = len(turns) - 1
                scores = _turn_scores(sc, drift, rr)

                # 검수상태 회전 배정
                status = ReviewStatus(REVIEW_CYCLE[review_idx % len(REVIEW_CYCLE)])
                review_idx += 1
                hscores, labels, comment, reviewer, reviewed_at = _review_fields(status, scores, sc, ts)

                nuggets = [
                    Nugget(text=sc["ctx"][0][:24], found_in_context=True),
                    Nugget(text="추가 세부 정보", found_in_context=sc["scores"]["context_recall"] > 0.7),
                ]
                diag = DiagnosisVerdict(
                    category=sc["diag"],
                    recall_ok=sc["scores"]["context_recall"] >= 0.7,
                    faithfulness_ok=sc["scores"]["faithfulness"] >= 0.8,
                    recommendation=_DIAG_REC[sc["diag"]],
                )
                run_records.append(EvaluationRecord(
                    eval_id=uuid.uuid4().hex, qa_id=f"{conv_id}-t{turn_idx}",
                    call_id=conv_id, tenant_id=tid, conversation_id=conv_id,
                    subscriber_id=subscriber, level=EvalLevel.TURN, turn_index=turn_idx,
                    eval_run_id=run_id, query=sc["q"], generated_answer=sc["a"],
                    retrieval_result=rr, scores=scores, nuggets=nuggets, diagnosis=diag,
                    ground_truth=sc.get("gt"),
                    evaluated_at=ts,
                    judge_provider=random.choice(["anthropic", "openai"]),
                    judge_model=random.choice(["claude-sonnet-4-5", "gpt-4o"]),
                    review_status=status, human_scores=hscores, human_label=labels,
                    human_comment=comment, reviewer=reviewer, reviewed_at=reviewed_at,
                    needs_human_review=status == ReviewStatus.PENDING,
                ))

                # 골든셋: 일부 평가에 사람 점수 라벨 (meta_eval/auto_calibration)
                if random.random() < 0.4:
                    for s in scores:
                        if s.metric in ("faithfulness", "answer_relevance"):
                            golden_rows.append({
                                "qa_id": f"{conv_id}-t{turn_idx}", "metric": s.metric,
                                "human_score": _clamp(s.score + random.uniform(-0.15, 0.1)),
                            })

                # 세션 레벨 (멀티턴만)
                if sc.get("multiturn"):
                    sess_scores = _session_scores(sc, drift)
                    s_status = ReviewStatus(REVIEW_CYCLE[review_idx % len(REVIEW_CYCLE)])
                    review_idx += 1
                    run_records.append(EvaluationRecord(
                        eval_id=uuid.uuid4().hex, qa_id=f"{conv_id}-session",
                        call_id=conv_id, tenant_id=tid, conversation_id=conv_id,
                        subscriber_id=subscriber, level=EvalLevel.SESSION, turn_index=None,
                        eval_run_id=run_id, query="(세션 전체)",
                        generated_answer=f"멀티턴 {len(turns)//2}문답 — {sc['kind']}",
                        retrieval_result=RetrievalResult(query="(세션)"),
                        scores=sess_scores, evaluated_at=ts,
                        judge_provider="anthropic", judge_model="claude-sonnet-4-5",
                        review_status=s_status,
                        needs_human_review=s_status == ReviewStatus.PENDING,
                    ))

        records.extend(run_records)
        summaries.append(_summary(run_id, run_date, run_records))

    return records, summaries, convo_rows, golden_rows


def _summary(run_id: str, run_date: datetime, recs: list[EvaluationRecord]) -> AuditSummary:
    turn_recs = [r for r in recs if r.level == EvalLevel.TURN]
    metric_vals: dict[str, list[float]] = {}
    for r in turn_recs:
        for s in r.scores:
            metric_vals.setdefault(s.metric, []).append(s.score)
    metrics = []
    for m, vals in metric_vals.items():
        thr = SLA.get(m, 0.75)
        below = [v for v in vals if v < thr]
        vals_sorted = sorted(vals)
        metrics.append(AggregatedMetric(
            metric=m, mean=round(statistics.mean(vals), 4),
            median=round(statistics.median(vals), 4),
            p10=round(vals_sorted[max(0, int(len(vals) * 0.1) - 1)], 4),
            p90=round(vals_sorted[min(len(vals) - 1, int(len(vals) * 0.9))], 4),
            below_sla_count=len(below), total_count=len(vals),
            sla_pass_rate=round(1 - len(below) / len(vals), 4) if vals else 1.0,
        ))
    flagged = sorted({
        r.call_id for r in turn_recs
        if any(s.score < SLA.get(s.metric, 0.75) for s in r.scores)
    })
    diag_dist: dict[str, int] = {}
    for r in turn_recs:
        if r.diagnosis:
            diag_dist[r.diagnosis.category] = diag_dist.get(r.diagnosis.category, 0) + 1
    return AuditSummary(
        run_id=run_id, period_start=run_date, period_end=run_date,
        total_calls=len({r.conversation_id for r in turn_recs}),
        total_evaluations=len(turn_recs), metrics=metrics,
        flagged_call_ids=flagged, diagnosis_distribution=diag_dist,
        generated_at=run_date,
    )


# ════════════════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(description="관리포탈 종합 Mock 데이터 시드")
    ap.add_argument("--reset", action="store_true", help="기존 DB·골든셋 삭제 후 시드")
    args = ap.parse_args()

    db_path = _ROOT / "data" / "results" / "autoaudit.db"
    golden_path = _ROOT / "data" / "golden_set.jsonl"

    if args.reset:
        import shutil
        for p in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
            if p.exists():
                p.unlink()
        # 기존 파이프라인 JSON 산출물 정리 → 포탈 배치 목록을 큐레이션된 8개로 한정
        results_dir = _ROOT / "data" / "results"
        removed = 0
        for d in results_dir.glob("run_*"):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        reports = results_dir / "reports"
        if reports.exists():
            shutil.rmtree(reports, ignore_errors=True)
        print(f"기존 DB 삭제: {db_path}")
        print(f"기존 JSON run 디렉토리 {removed}개 + reports 정리")

    store = ResultStore(db_path=str(db_path))
    records, summaries, convo_rows, golden_rows = build_dataset()

    # 배치별 적재
    by_run: dict[str, list[EvaluationRecord]] = {}
    for r in records:
        by_run.setdefault(r.eval_run_id, []).append(r)
    for run_id, recs in by_run.items():
        store.upsert_evaluations(run_id, recs)
    for s in summaries:
        store.upsert_summary(s)
    for cr in convo_rows:
        store.upsert_conversation(**cr)

    # 골든셋 저장 (meta_eval / auto_calibration)
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    with golden_path.open("w", encoding="utf-8") as f:
        for row in golden_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    _report(store, records, summaries, convo_rows, golden_rows, golden_path)


def _report(store, records, summaries, convo_rows, golden_rows, golden_path) -> None:
    turn = sum(1 for r in records if r.level == EvalLevel.TURN)
    sess = sum(1 for r in records if r.level == EvalLevel.SESSION)
    statuses: dict[str, int] = {}
    kinds: dict[str, int] = {}
    pii = refusal = numeric = lowconf = with_gt = 0
    for r in records:
        statuses[r.review_status.value] = statuses.get(r.review_status.value, 0) + 1
        if r.diagnosis:
            kinds[r.diagnosis.category] = kinds.get(r.diagnosis.category, 0) + 1
        if r.ground_truth:
            with_gt += 1
        for s in r.scores:
            if s.metric == "safety_compliance" and s.score == 0.0:
                pii += 1
            if s.abstention:
                refusal += 1
            if s.numeric_flags:
                numeric += 1
            if s.is_low_confidence:
                lowconf += 1

    print("\n" + "=" * 60)
    print("✅ 관리포탈 종합 Mock 데이터 시드 완료")
    print("=" * 60)
    print(f"  평가배치(run)   : {len(summaries)}개 (8주 추세, 6주차 회귀 dip)")
    print(f"  대화(conversation): {len(convo_rows)}건")
    print(f"  평가 레코드      : 턴 {turn} · 세션 {sess} (총 {len(records)})")
    print(f"  검수상태 분포    : {statuses}")
    print(f"  진단 분포        : {kinds}")
    print(f"  특수 케이스      : PII노출 {pii} · 적정거절 {refusal} · 수치환각 {numeric} · 저신뢰 {lowconf} · 정답보유 {with_gt}")
    print(f"  골든셋           : {len(golden_rows)}행 → {golden_path}")
    print("-" * 60)
    print("  테넌트별 대화/검수대기:")
    for tid, name in TENANTS:
        convos = store.list_conversations(tenant_id=tid)
        multi = sum(1 for c in convos if c["is_multiturn"])
        agree = store.human_auto_agreement(tid)
        kb = store.kb_status(tid)
        print(f"   - {name}({tid}): 대화 {len(convos)}건(멀티턴 {multi}) · "
              f"일치도표본 {agree['n']} · KB청크 {kb['chunk_count']} · 갭 {len(kb['coverage_gaps'])}")
    print("=" * 60)
    print("  포탈 실행:  (터미널1) AUTOAUDIT_MOCK=1 uvicorn AutoAudit.app.api.server:app --reload")
    print("             (터미널2) cd frontend && npm run dev")
    print("=" * 60)


if __name__ == "__main__":
    main()
