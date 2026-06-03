"""
cp6_reporter/notifier.py
Slack Webhook 알림 — 회귀/SLA 미달 발생 시 발송.
(이메일 등 채널은 동일 인터페이스로 확장)
"""
from __future__ import annotations

import json
import os
import urllib.request

from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.logger import get_logger

logger = get_logger(__name__)


class SlackNotifier:
    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = (
            webhook_url
            or cfg_get("cp6.slack_webhook_url", default="")
            or os.environ.get("SLACK_WEBHOOK_URL", "")
        )

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def send(self, text: str) -> bool:
        if not self.enabled:
            logger.info("Slack 알림 비활성 (webhook 미설정) — 발송 생략")
            return False
        try:
            payload = json.dumps({"text": text}).encode("utf-8")
            req = urllib.request.Request(
                self.webhook_url, data=payload, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok = resp.status == 200
            logger.info(f"Slack 알림 발송 {'성공' if ok else '실패'}")
            return ok
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Slack 알림 오류: {exc}")
            return False
