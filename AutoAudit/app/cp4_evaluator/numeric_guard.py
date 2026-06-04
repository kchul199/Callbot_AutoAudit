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


def apply_guard(
    score: float,
    answer: str,
    contexts_text: str,
    *,
    penalty_per_conflict: float = 0.3,
    check_dates: bool = True,
) -> tuple[float, list[str]]:
    """
    faithfulness 점수에 수치 가드 적용.
    반환: (보정 점수, 충돌 수치 목록).
    충돌이 없으면 원점수 그대로.
    """
    conflicts = find_conflicts(answer, contexts_text, check_dates=check_dates)
    if not conflicts:
        return score, []
    penalty = penalty_per_conflict * len(conflicts)
    guarded = max(0.0, round(score - penalty, 4))
    logger.warning(
        f"[numeric_guard] 수치 환각 {len(conflicts)}건 — {conflicts} "
        f"→ faithfulness {score:.3f}→{guarded:.3f}"
    )
    return guarded, conflicts
