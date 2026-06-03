"""
core/logger.py
전 파이프라인 공용 구조화 로거
"""
from __future__ import annotations

import logging
import sys


def get_logger(name: str, level: str | None = None) -> logging.Logger:
    """
    표준 포맷 로거 반환.
    level이 없으면 settings.yaml의 log_level 사용.
    """
    from AutoAudit.app.core.config import get as cfg_get

    resolved_level = level or cfg_get("project.log_level", default="INFO")
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # 중복 핸들러 방지

    logger.setLevel(resolved_level.upper())
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(resolved_level.upper())
    fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    return logger
