"""
tests/test_cp1_parser.py
CP1 전처리 회귀 테스트 — Data Integrity 검증
"""
import json
from pathlib import Path

import pytest

from AutoAudit.app.core.types import TurnRole
from AutoAudit.app.cp1_preprocessing.parser import CallLogParser


@pytest.fixture
def parser() -> CallLogParser:
    return CallLogParser()


# ============================================================
# JSON 파서
# ============================================================

def test_parse_json_basic(parser: CallLogParser, tmp_path: Path) -> None:
    """표준 JSON 로그 파싱 — call_id/subscriber_id/turns 무결성"""
    data = {
        "call_id": "C001",
        "subscriber_id": "SUB_01",
        "started_at": "2024-01-01T09:00:00",
        "turns": [
            {"role": "user", "content": "요금제 변경하고 싶어요", "timestamp": "2024-01-01T09:00:01"},
            {"role": "bot", "content": "네, 어떤 요금제로 변경을 원하시나요?", "timestamp": "2024-01-01T09:00:05"},
        ],
    }
    p = tmp_path / "test.json"
    p.write_text(json.dumps(data, ensure_ascii=False))

    log = parser.parse_file(p)

    assert log is not None
    assert log.call_id == "C001"
    assert log.subscriber_id == "SUB_01"
    assert len(log.turns) == 2
    assert log.turns[0].role == TurnRole.USER
    assert log.turns[1].role == TurnRole.BOT


def test_parse_json_min_length_filter(parser: CallLogParser, tmp_path: Path) -> None:
    """min_turn_length 미만 턴 필터링"""
    data = {
        "call_id": "C002",
        "subscriber_id": "SUB_02",
        "turns": [
            {"role": "user", "content": "네"},          # 2자 → 필터됨
            {"role": "bot", "content": "안녕하세요, 무엇을 도와드릴까요?"},
        ],
    }
    p = tmp_path / "short.json"
    p.write_text(json.dumps(data, ensure_ascii=False))

    log = parser.parse_file(p)
    assert log is not None
    assert len(log.turns) == 1  # 짧은 턴 제외


def test_parse_json_missing_metadata_defaults(parser: CallLogParser, tmp_path: Path) -> None:
    """call_id / subscriber_id 누락 시 기본값 대체"""
    data = {"turns": [{"role": "user", "content": "인터넷이 안 됩니다"}]}
    p = tmp_path / "no_meta.json"
    p.write_text(json.dumps(data))

    log = parser.parse_file(p)
    assert log is not None
    assert log.call_id == "no_meta"       # 파일명 사용
    assert log.subscriber_id == "unknown"


# ============================================================
# CSV 파서
# ============================================================

def test_parse_csv_basic(parser: CallLogParser, tmp_path: Path) -> None:
    csv_content = (
        "call_id,subscriber_id,timestamp,role,content\n"
        "C003,SUB_03,2024-01-01T10:00:00,user,데이터 요금이 너무 많이 나왔어요\n"
        "C003,SUB_03,2024-01-01T10:00:05,bot,데이터 사용 내역을 확인해 드리겠습니다\n"
    )
    p = tmp_path / "test.csv"
    p.write_text(csv_content)

    log = parser.parse_file(p)
    assert log is not None
    assert log.call_id == "C003"
    assert len(log.turns) == 2


# ============================================================
# TXT 파서
# ============================================================

def test_parse_txt_basic(parser: CallLogParser, tmp_path: Path) -> None:
    txt_content = (
        "[CALL_ID: C004] [SUBSCRIBER: SUB_04]\n"
        "[2024-01-01 11:00:00] USER: 유심 교체는 어디서 하나요?\n"
        "[2024-01-01 11:00:05] BOT: 가까운 대리점에서 교체하실 수 있습니다.\n"
    )
    p = tmp_path / "test.txt"
    p.write_text(txt_content)

    log = parser.parse_file(p)
    assert log is not None
    assert log.call_id == "C004"
    assert log.subscriber_id == "SUB_04"
    assert len(log.turns) == 2


# ============================================================
# 비지원 포맷
# ============================================================

def test_unsupported_format_returns_none(parser: CallLogParser, tmp_path: Path) -> None:
    p = tmp_path / "test.xml"
    p.write_text("<root/>")
    log = parser.parse_file(p)
    assert log is None


# ============================================================
# 디렉토리 파싱
# ============================================================

def test_parse_directory(parser: CallLogParser, tmp_path: Path) -> None:
    for i in range(3):
        data = {
            "call_id": f"C{i:03d}",
            "subscriber_id": f"SUB_{i:02d}",
            "turns": [{"role": "user", "content": f"테스트 질문 {i} 입니다"}],
        }
        (tmp_path / f"log_{i}.json").write_text(json.dumps(data))

    logs = parser.parse_directory(tmp_path)
    assert len(logs) == 3
