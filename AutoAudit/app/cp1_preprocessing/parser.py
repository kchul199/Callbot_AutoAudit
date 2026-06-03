"""
cp1_preprocessing/parser.py
콜봇 로그(.txt / .json / .csv) → CallLog 표준 스키마 변환
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import CallLog, ConversationTurn, TurnRole

logger = get_logger(__name__)


class CallLogParser:
    """
    지원 포맷: txt | json | csv
    Data Integrity 원칙:
      - 메타데이터(call_id, subscriber_id, timestamp) 유실 시 경고 + 기본값 대체
      - min_turn_length 미만 콘텐츠 필터링
    """

    def __init__(self) -> None:
        self.min_turn_length: int = cfg_get("cp1.min_turn_length", default=5)

    # ----------------------------------------------------------
    # 진입점
    # ----------------------------------------------------------

    def parse_file(self, file_path: str | Path) -> CallLog | None:
        path = Path(file_path)
        suffix = path.suffix.lstrip(".").lower()
        supported = cfg_get("cp1.supported_formats", default=["txt", "json", "csv"])

        if suffix not in supported:
            logger.warning(f"Unsupported format: {suffix} — {path}")
            return None

        logger.info(f"Parsing [{suffix.upper()}] {path.name}")
        try:
            if suffix == "json":
                return self._parse_json(path)
            elif suffix == "csv":
                return self._parse_csv(path)
            else:
                return self._parse_txt(path)
        except Exception as exc:
            logger.error(f"Parse failed for {path}: {exc}")
            return None

    def parse_directory(self, directory: str | Path) -> list[CallLog]:
        logs: list[CallLog] = []
        for fp in Path(directory).rglob("*"):
            if fp.is_file():
                log = self.parse_file(fp)
                if log:
                    logs.append(log)
        logger.info(f"Parsed {len(logs)} call logs from {directory}")
        return logs

    # ----------------------------------------------------------
    # 포맷별 파서
    # ----------------------------------------------------------

    def _parse_json(self, path: Path) -> CallLog:
        """
        기대 JSON 구조:
        {
          "call_id": "...", "subscriber_id": "...",
          "started_at": "ISO8601", "ended_at": "ISO8601",
          "turns": [{"role": "user|bot", "content": "...", "timestamp": "..."}]
        }
        """
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        turns = [
            ConversationTurn(
                turn_id=i,
                role=TurnRole(t.get("role", "user")),
                content=t.get("content", "").strip(),
                timestamp=self._safe_dt(t.get("timestamp")),
            )
            for i, t in enumerate(raw.get("turns", []))
            if len(t.get("content", "")) >= self.min_turn_length
        ]

        return CallLog(
            call_id=raw.get("call_id", path.stem),
            subscriber_id=raw.get("subscriber_id", "unknown"),
            started_at=self._safe_dt(raw.get("started_at")),
            ended_at=self._safe_dt(raw.get("ended_at")),
            turns=turns,
            metadata=raw.get("metadata", {}),
        )

    def _parse_csv(self, path: Path) -> CallLog:
        """
        기대 CSV 헤더: call_id, subscriber_id, timestamp, role, content
        """
        turns: list[ConversationTurn] = []
        call_id = path.stem
        subscriber_id = "unknown"

        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if i == 0:
                    call_id = row.get("call_id", path.stem)
                    subscriber_id = row.get("subscriber_id", "unknown")

                content = row.get("content", "").strip()
                if len(content) < self.min_turn_length:
                    continue

                turns.append(
                    ConversationTurn(
                        turn_id=i,
                        role=TurnRole(row.get("role", "user")),
                        content=content,
                        timestamp=self._safe_dt(row.get("timestamp")),
                    )
                )

        return CallLog(
            call_id=call_id,
            subscriber_id=subscriber_id,
            turns=turns,
        )

    def _parse_txt(self, path: Path) -> CallLog:
        """
        기대 TXT 형식:
        [CALL_ID: C001] [SUBSCRIBER: SUB_01]
        [2024-01-01 09:00:00] USER: 안녕하세요
        [2024-01-01 09:00:05] BOT: 안녕하세요, 무엇을 도와드릴까요?
        """
        import re

        lines = path.read_text(encoding="utf-8").splitlines()
        call_id = path.stem
        subscriber_id = "unknown"
        turns: list[ConversationTurn] = []

        header_pattern = re.compile(
            r"\[CALL_ID:\s*(.+?)\].*\[SUBSCRIBER:\s*(.+?)\]", re.IGNORECASE
        )
        turn_pattern = re.compile(
            r"\[(.+?)\]\s*(USER|BOT|SYSTEM):\s*(.+)", re.IGNORECASE
        )

        for line in lines:
            hm = header_pattern.match(line.strip())
            if hm:
                call_id = hm.group(1).strip()
                subscriber_id = hm.group(2).strip()
                continue

            tm = turn_pattern.match(line.strip())
            if tm:
                content = tm.group(3).strip()
                if len(content) < self.min_turn_length:
                    continue
                turns.append(
                    ConversationTurn(
                        turn_id=len(turns),
                        role=TurnRole(tm.group(2).lower()),
                        content=content,
                        timestamp=self._safe_dt(tm.group(1)),
                    )
                )

        return CallLog(call_id=call_id, subscriber_id=subscriber_id, turns=turns)

    # ----------------------------------------------------------
    # 유틸
    # ----------------------------------------------------------

    @staticmethod
    def _safe_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
