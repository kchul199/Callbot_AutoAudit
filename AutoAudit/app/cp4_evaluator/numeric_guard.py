"""
cp4_evaluator/numeric_guard.py
결정적 수치·엔티티 가드(Numeric/Entity Guard) ⑤.

LLM Judge(claim NLI 포함)는 숫자에 약하다. "월 3만원"을 컨텍스트가 말했는데
답변이 "월 3만 5천원"이라 해도 토큰이 비슷해 supported로 통과하기 쉽다.

이 모듈은 답변에서 숫자·금액·기간·날짜·퍼센트를 정규식으로 추출해
컨텍스트 수치 집합과 결정적으로 대조한다. 컨텍스트에 없는 수치를 답변이
주장하면 '수치 환각'으로 보고 faithfulness를 감점한다. LLM 호출 0, 비용 0.
"""
from __future__ import annotations

import re

from AutoAudit.app.core.logger import get_logger

logger = get_logger(__name__)

# 금액/수량/기간/퍼센트: 숫자(+ 한국어 만/천 단위) + 단위
_UNIT = r"(?:원|만\s*원|천\s*원|만원|개월|개|일|월|년|주|시간|분|초|%|퍼센트|건|회|명|GB|MB|기가|메가|배|위|등급|km|kg)"
_NUM = r"\d[\d,]*(?:\.\d+)?"
_NUMERIC_RE = re.compile(rf"{_NUM}\s*{_UNIT}?")
# 날짜/기간 표현
_DATE_RE = re.compile(r"\d{1,4}\s*[./년-]\s*\d{1,2}(?:\s*[./월-]\s*\d{1,2})?\s*일?")


def _normalize(token: str) -> str:
    """공백 제거 + 콤마 제거 + '만원'→'만 원' 통일로 비교 키 생성."""
    t = re.sub(r"\s+", "", token)
    t = t.replace(",", "")
    return t


def extract_numerics(text: str, *, check_dates: bool = True) -> set[str]:
    """텍스트에서 수치·단위·날짜 표현을 정규화 집합으로 추출."""
    found: set[str] = set()
    for m in _NUMERIC_RE.finditer(text or ""):
        tok = m.group().strip()
        # 단위 없는 한 자리 순번(예: '1.')는 잡지 않도록 최소 길이 필터
        digits = re.sub(r"[^\d]", "", tok)
        if not digits:
            continue
        found.add(_normalize(tok))
    if check_dates:
        for m in _DATE_RE.finditer(text or ""):
            found.add(_normalize(m.group()))
    return found


def find_conflicts(answer: str, contexts_text: str, *, check_dates: bool = True) -> list[str]:
    """답변 수치 중 컨텍스트에 (정규화 기준) 존재하지 않는 것들 = 수치 환각 후보."""
    ans_nums = extract_numerics(answer, check_dates=check_dates)
    ctx_nums = extract_numerics(contexts_text, check_dates=check_dates)
    if not ans_nums:
        return []
    # 컨텍스트 정규화 키들을 부분일치까지 허용 (예: '35000원' ⊂ '월35000원')
    ctx_join = "".join(ctx_nums)
    conflicts = []
    for token in sorted(ans_nums):
        digits = re.sub(r"[^\d]", "", token)
        # 숫자 부분이 컨텍스트 어디에도 없으면 충돌
        if token in ctx_nums:
            continue
        if digits and digits in ctx_join:
            continue
        conflicts.append(token)
    return conflicts


# ══════════════════════════════════════════════════════════════
# #6 부정극성 가드 — "가능 ↔ 불가능" 류 반전 환각 포착
# ══════════════════════════════════════════════════════════════

# 부정(negative) 극성 마커 — POS 마커보다 먼저 검사해야 함('불가능'⊃'가능', '지원하지 않'⊃'지원')
_NEG_MARKERS = (
    "불가능", "불가", "안 됩니다", "안됩니다", "안 돼요", "안돼요", "안 된다", "안된다",
    "없습니다", "없어요", "없음", "지원하지 않", "지원되지 않", "제공하지 않", "제공되지 않",
    "되지 않", "할 수 없", "불허", "미포함", "포함되지 않", "유료", "제한됩니다", "제한",
)
_POS_MARKERS = (
    "가능합니다", "가능", "됩니다", "돼요", "된다", "있습니다", "있어요", "있음",
    "지원합니다", "지원", "제공합니다", "제공", "포함", "무료", "허용", "가입 가능", "신청 가능",
)
_STOPWORDS = {
    "그리고", "그러나", "하지만", "또한", "경우", "관련", "대해", "통해", "위해", "에서",
    "입니다", "합니다", "고객", "안내", "확인", "문의",
}
_KO_TOKEN = re.compile(r"[가-힣]{2,}")
_CLAUSE_SPLIT = re.compile(r"[.!?。\n,]")


def _polarity(text: str) -> int:
    """절의 극성: -1(부정) / +1(긍정) / 0(중립). 부정 마커를 우선 검사."""
    if any(m in text for m in _NEG_MARKERS):
        return -1
    if any(m in text for m in _POS_MARKERS):
        return 1
    return 0


def _content_keys(text: str) -> set[str]:
    """극성/불용어를 제외한 내용어 토큰(명사류, 2자 이상)."""
    keys = set(_KO_TOKEN.findall(text))
    drop = set(_STOPWORDS)
    for m in _NEG_MARKERS + _POS_MARKERS:
        for t in _KO_TOKEN.findall(m):
            drop.add(t)
    return {k for k in keys if k not in drop}


def find_negation_conflicts(answer: str, contexts_text: str) -> list[str]:
    """답변 절의 극성이 동일 키워드에 대한 컨텍스트 절의 극성과 반대이면 충돌.

    예) 답변 "해지는 불가능합니다" vs 컨텍스트 "해지는 가능합니다" → neg:해지
    보수적: 반대 극성 + 공유 내용어가 모두 성립할 때만 충돌로 본다.
    """
    ctx_clauses = [c.strip() for c in _CLAUSE_SPLIT.split(contexts_text or "") if c.strip()]
    ctx_pol = [(_polarity(c), _content_keys(c)) for c in ctx_clauses]
    seen: set[str] = set()
    conflicts: list[str] = []
    for clause in _CLAUSE_SPLIT.split(answer or ""):
        clause = clause.strip()
        if not clause:
            continue
        pol = _polarity(clause)
        if pol == 0:
            continue
        keys = _content_keys(clause)
        if not keys:
            continue
        for cpol, ckeys in ctx_pol:
            if cpol == -pol:
                shared = keys & ckeys
                if shared:
                    key = sorted(shared)[0]
                    if key not in seen:
                        seen.add(key)
                        conflicts.append(f"neg:{key}")
                    break
    return conflicts


# ══════════════════════════════════════════════════════════════
# #6 엔티티/식별자 가드 — 약관 조항·코드·고유명사 불일치 포착
# ══════════════════════════════════════════════════════════════

_CLAUSE_REF = re.compile(r"제?\s*\d+\s*조(?:\s*제?\s*\d+\s*항)?|\d+\s*항")
_CODE_REF = re.compile(r"[A-Za-z]{2,}[-_]?\d{2,}")
_QUOTED = re.compile(r"['\"“”‘’]([^'\"“”‘’]{2,20})['\"“”‘’]")


def _entity_tokens(text: str) -> set[str]:
    out: set[str] = set()
    for rx in (_CLAUSE_REF, _CODE_REF):
        out |= {re.sub(r"\s+", "", m.group()) for m in rx.finditer(text or "")}
    out |= {m.group(1).strip() for m in _QUOTED.finditer(text or "")}
    return {e for e in out if e}


def find_entity_conflicts(answer: str, contexts_text: str) -> list[str]:
    """답변의 조항번호·코드·따옴표 고유명사가 컨텍스트에 없으면 충돌(ent:...)."""
    ctx = re.sub(r"\s+", "", contexts_text or "")
    conflicts: list[str] = []
    seen: set[str] = set()
    for ent in sorted(_entity_tokens(answer)):
        key = re.sub(r"\s+", "", ent)
        if key and key not in ctx and ent not in (contexts_text or "") and key not in seen:
            seen.add(key)
            conflicts.append(f"ent:{ent}")
    return conflicts


def apply_guard(
    score: float,
    answer: str,
    contexts_text: str,
    *,
    penalty_per_conflict: float = 0.3,
    check_dates: bool = True,
    check_negation: bool = False,
    check_entities: bool = False,
    negation_penalty: float = 0.4,
    entity_penalty: float = 0.3,
) -> tuple[float, list[str]]:
    """
    faithfulness 점수에 결정적 가드 적용 (수치 + 선택적 부정극성/엔티티).
    반환: (보정 점수, 충돌 목록). 충돌이 없으면 원점수 그대로.

    수치 충돌은 (기존 호환) 토큰 그대로, 부정/엔티티 충돌은 'neg:'/'ent:' 접두사로 구분.
    """
    num_conflicts = find_conflicts(answer, contexts_text, check_dates=check_dates)
    neg_conflicts = find_negation_conflicts(answer, contexts_text) if check_negation else []
    ent_conflicts = find_entity_conflicts(answer, contexts_text) if check_entities else []

    all_conflicts = num_conflicts + neg_conflicts + ent_conflicts
    if not all_conflicts:
        return score, []

    penalty = (
        penalty_per_conflict * len(num_conflicts)
        + negation_penalty * len(neg_conflicts)
        + entity_penalty * len(ent_conflicts)
    )
    guarded = max(0.0, round(score - penalty, 4))
    logger.warning(
        f"[numeric_guard] 결정적 충돌 {len(all_conflicts)}건 — {all_conflicts} "
        f"→ faithfulness {score:.3f}→{guarded:.3f}"
    )
    return guarded, all_conflicts
