"""
tests/test_cp4_qa_builder.py
QA 쌍 추출 로직 검증 — CP1 대화 → 평가 단위 변환
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


def test_extract_basic_pair():
    """User→Bot 쌍 1개 추출"""
    log = make_log([
        ("user", "요금제를 변경하고 싶어요"),
        ("bot", "어떤 요금제로 변경을 원하시나요? 안내해 드리겠습니다."),
    ])
    pairs = QAPairBuilder().extract_pairs(log)
    assert len(pairs) == 1
    assert pairs[0].question == "요금제를 변경하고 싶어요"
    assert "변경" in pairs[0].bot_answer
    assert pairs[0].call_id == "C001"


def test_merge_consecutive_turns():
    """연속 User/Bot 발화 병합"""
    log = make_log([
        ("user", "안녕하세요 질문이 있습니다"),
        ("user", "데이터 요금이 많이 나왔어요"),
        ("bot", "데이터 사용 내역을 확인해 드리겠습니다."),
        ("bot", "지난달 대비 50% 증가했습니다."),
    ])
    pairs = QAPairBuilder().extract_pairs(log)
    assert len(pairs) == 1
    assert "데이터 요금" in pairs[0].question
    assert "50%" in pairs[0].bot_answer


def test_skip_trivial_answer():
    """짧은 상투어 답변은 제외"""
    log = make_log([
        ("user", "통화 연결해 주세요 부탁합니다"),
        ("bot", "네, 잠시만요"),  # trivial + short
    ])
    pairs = QAPairBuilder().extract_pairs(log)
    assert len(pairs) == 0


def test_multiple_pairs():
    """여러 QA 쌍 추출"""
    log = make_log([
        ("user", "요금제 변경 방법 알려주세요"),
        ("bot", "고객센터 앱에서 직접 변경 가능합니다."),
        ("user", "본인 확인은 어떻게 하나요"),
        ("bot", "주민번호 또는 비밀번호로 확인 가능합니다."),
    ])
    pairs = QAPairBuilder().extract_pairs(log)
    assert len(pairs) == 2


def test_extract_from_logs():
    """여러 콜 로그 일괄 추출"""
    logs = [
        make_log([("user", "질문 하나입니다 길게", ), ("bot", "답변 하나입니다 충분히 길게")], call_id="C001"),
        make_log([("user", "질문 둘입니다 길게"), ("bot", "답변 둘입니다 충분히 길게")], call_id="C002"),
    ]
    pairs = QAPairBuilder().extract_from_logs(logs)
    assert len(pairs) == 2
    assert {p.call_id for p in pairs} == {"C001", "C002"}
