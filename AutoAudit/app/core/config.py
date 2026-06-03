"""
core/config.py
settings.yaml 로더 — 전 CP에서 공유
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


@lru_cache(maxsize=1)
def load_config(config_path: str = "config/settings.yaml") -> dict[str, Any]:
    """settings.yaml을 읽어 dict로 반환 (캐시 적용)"""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get(key_path: str, config_path: str = "config/settings.yaml", default: Any = None) -> Any:
    """
    점(.) 구분자로 중첩 키 접근.
    예) get("cp3.top_k") → 20
    """
    cfg = load_config(config_path)
    keys = key_path.split(".")
    val = cfg
    for k in keys:
        if not isinstance(val, dict):
            return default
        val = val.get(k, default)
    return val
