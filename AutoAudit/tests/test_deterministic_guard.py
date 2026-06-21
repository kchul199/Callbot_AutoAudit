"""
tests/test_deterministic_guard.py
#6 결정적 가드 확장 — 부정극성(가능↔불가능) + 엔티티/식별자 충돌.
"""
from AutoAudit.app.cp4_evaluator.numeric_guard import (
    apply_guard,
    find_entity_conflicts,
    find_negation_conflicts,
)


# ----------------------------------------------------------------
# 부정극성
# ----------------------------------------------------------------

def test_negation_polarity_conflict():
    """답변 '불가능' vs 컨텍스트 '가능' (공유 키워드 해지) → 충돌."""
    conflicts = find_negation_conflicts(
        answer="해지는 불가능합니다.",
        contexts_text="해지는 위약금 없이 가능합니다.",
    )
    assert any(c.startswith("neg:") for c in conflicts)
    assert any("해지" in c for c in conflicts)


def test_negation_same_polarity_no_conflict():
    """동일 극성(둘 다 긍정)은 충돌 없음."""
    conflicts = find_negation_conflicts(
        answer="해지는 가능합니다.",
        contexts_text="해지는 언제든 가능합니다.",
    )
    assert conflicts == []


def test_negation_requires_shared_keyword():
    """극성은 반대지만 공유 키워드가 없으면 충돌 아님 (보수적)."""
    conflicts = find_negation_conflicts(
        answer="환불은 불가능합니다.",
        contexts_text="배송은 무료로 제공됩니다.",
    )
    assert conflicts == []


# ----------------------------------------------------------------
# 엔티티/식별자
# ----------------------------------------------------------------

def test_entity_clause_conflict():
    """답변의 조항번호가 컨텍스트에 없으면 충돌."""
    conflicts = find_entity_conflicts(
        answer="제15조에 따라 처리됩니다.",
        contexts_text="제3조에 따라 처리됩니다.",
    )
    assert any(c.startswith("ent:") and "15" in c for c in conflicts)


def test_entity_present_no_conflict():
    conflicts = find_entity_conflicts(
        answer="제3조 제2항에 따릅니다.",
        contexts_text="본 약관 제3조 제2항은 해지를 규정합니다.",
    )
    assert conflicts == []


def test_entity_code_conflict():
    conflicts = find_entity_conflicts(
        answer="상품코드 AB1234 로 신청하세요.",
        contexts_text="상품코드 AB9999 가 유효합니다.",
    )
    assert any("AB1234" in c for c in conflicts)


# ----------------------------------------------------------------
# apply_guard 통합 (하위 호환 + 신규 플래그)
# ----------------------------------------------------------------

def test_apply_guard_numeric_only_backward_compatible():
    """신규 플래그 미지정 시 = 기존 수치 전용 동작 (충돌 토큰 그대로)."""
    guarded, conflicts = apply_guard(0.9, "월 35000원", "월 30000원입니다")
    assert guarded < 0.9
    assert conflicts == ["35000원"]            # 접두사 없음 (하위 호환)


def test_apply_guard_combined_penalty():
    """수치+부정극성+엔티티 동시 충돌 → 각 페널티 합산."""
    guarded, conflicts = apply_guard(
        1.0,
        answer="제15조에 따라 해지는 불가능하며 위약금은 50000원입니다.",
        contexts_text="제3조에 따라 해지는 가능하며 위약금은 10000원입니다.",
        penalty_per_conflict=0.3, negation_penalty=0.4, entity_penalty=0.3,
        check_negation=True, check_entities=True,
    )
    kinds = {c.split(":")[0] if ":" in c else "num" for c in conflicts}
    assert "neg" in kinds and "ent" in kinds          # 부정극성·엔티티 포착
    assert any(c == "50000원" for c in conflicts)      # 수치 충돌(접두사 없음)
    assert guarded < 1.0


def test_apply_guard_negation_off_by_flag():
    """check_negation=False면 부정극성 충돌 미적용."""
    _, conflicts = apply_guard(
        1.0, "해지는 불가능합니다", "해지는 가능합니다",
        check_negation=False, check_entities=False,
    )
    assert conflicts == []
