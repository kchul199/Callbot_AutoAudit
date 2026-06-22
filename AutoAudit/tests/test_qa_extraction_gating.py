"""
tests/test_qa_extraction_gating.py
#2 QA 추출 품질 — 질문 가치 게이팅 + 다중 의도 분리.
"""
from AutoAudit.app.core.types import CallLog, ConversationTurn, TurnRole
from AutoAudit.app.cp4_evaluator.qa_builder import QAPairBuilder


def make_log(turns: list[tuple[str, str]], call_id: str = "C001") -> CallLog:
    return CallLog(
        call_id=call_id,
        subscriber_id="SUB_01",
        turns=[
            ConversationTurn(turn_id=i, role=TurnRole(r), content=c)
            for i, (r, c) in enumerate(turns)
        ],
    )


# ----------------------------------------------------------------
# 질문 가치 게이팅
# ----------------------------------------------------------------

def test_gate_excludes_backchannel():
    """순수 백채널 사용자 발화는 평가 대상에서 제외."""
    log = make_log([
        ("user", "네 알겠어요"),                       # 백채널
        ("bot", "추가로 궁금하신 점 있으실까요? 안내드리겠습니다."),
    ])
    pairs = QAPairBuilder().extract_pairs(log)
    assert len(pairs) == 0


def test_gate_keeps_real_question():
    """정상 질문은 통과 + extraction_method 기록."""
    log = make_log([
        ("user", "요금제 변경 방법 알려주세요"),
        ("bot", "고객센터 앱에서 직접 변경 가능합니다."),
    ])
    pairs = QAPairBuilder().extract_pairs(log)
    assert len(pairs) == 1
    assert pairs[0].extraction_method in ("gated", "heuristic")
    assert pairs[0].intent_idx == 0


def test_gate_keeps_long_statement_without_marker():
    """의문 신호가 없어도 충분히 긴 문제 제기는 고관용으로 통과."""
    log = make_log([
        ("user", "지난달에 데이터 요금이 평소보다 훨씬 많이 청구되었습니다"),
        ("bot", "사용 내역을 확인해 드리겠습니다."),
    ])
    pairs = QAPairBuilder().extract_pairs(log)
    assert len(pairs) == 1


def test_gate_can_be_disabled():
    """게이팅 비활성 시 백채널도 추출 (하위 호환)."""
    builder = QAPairBuilder()
    builder.worthiness_gate = False
    builder.multi_intent_split = False
    log = make_log([
        ("user", "네 알겠어요"),
        ("bot", "추가로 궁금하신 점 있으실까요 안내드리겠습니다."),
    ])
    pairs = builder.extract_pairs(log)
    assert len(pairs) == 1
    assert pairs[0].extraction_method == "heuristic"


# ----------------------------------------------------------------
# 다중 의도 분리
# ----------------------------------------------------------------

def test_split_two_intents():
    """한 턴에 독립 의도 2개 → 2개 QAPair (같은 답변·turn_index, intent_idx 구분)."""
    log = make_log([
        ("user", "요금제 변경은 어떻게 하나요? 그리고 해지 위약금은 얼마인가요?"),
        ("bot", "앱에서 변경 가능하고, 위약금은 약정 잔여기간에 비례합니다."),
    ])
    pairs = QAPairBuilder().extract_pairs(log)
    assert len(pairs) == 2
    assert {p.intent_idx for p in pairs} == {0, 1}
    assert all(p.turn_index == pairs[0].turn_index for p in pairs)
    assert all(p.extraction_method == "split" for p in pairs)
    assert any("변경" in p.question for p in pairs)
    assert any("위약금" in p.question for p in pairs)


def test_single_intent_not_split():
    """접속어/종결부호 없는 단일 질문은 분리하지 않음."""
    log = make_log([
        ("user", "본인 확인은 어떻게 하나요"),
        ("bot", "주민번호 또는 비밀번호로 확인 가능합니다."),
    ])
    pairs = QAPairBuilder().extract_pairs(log)
    assert len(pairs) == 1
    assert pairs[0].intent_idx == 0


def test_split_disabled_keeps_single():
    """분리 비활성 시 복수 의도라도 1개 유지."""
    builder = QAPairBuilder()
    builder.multi_intent_split = False
    log = make_log([
        ("user", "요금제 변경은 어떻게 하나요? 그리고 해지 위약금은 얼마인가요?"),
        ("bot", "앱에서 변경 가능하고, 위약금은 약정 잔여기간에 비례합니다."),
    ])
    pairs = builder.extract_pairs(log)
    assert len(pairs) == 1
