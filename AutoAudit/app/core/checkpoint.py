"""
core/checkpoint.py
파이프라인 재개(resume)를 위한 체크포인트 저장소.

설계:
  - 단계(stage) 단위: 고정 파일명으로 저장 → resume 시 존재하면 재계산 생략
  - 항목(item) 단위: CP4처럼 비싼 단계는 qa_id별 평가 결과를 누적 저장 →
    중단 후 재개 시 이미 평가된 항목은 건너뜀 (멱등성)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from AutoAudit.app.core.logger import get_logger

logger = get_logger(__name__)


class CheckpointStore:
    def __init__(self, run_id: str, results_dir: str = "data/results") -> None:
        self.run_id = run_id
        self.base = Path(results_dir) / run_id / "_checkpoints"
        self.base.mkdir(parents=True, exist_ok=True)

    # ---- 단계 단위 ----

    def _stage_path(self, stage: str) -> Path:
        return self.base / f"{stage}.json"

    def has_stage(self, stage: str) -> bool:
        return self._stage_path(stage).exists()

    def save_stage(self, stage: str, data: Any) -> None:
        payload = self._serialize(data)
        with self._stage_path(stage).open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"[checkpoint] stage '{stage}' saved ({self.run_id})")

    def load_stage(self, stage: str) -> Any:
        with self._stage_path(stage).open("r", encoding="utf-8") as f:
            return json.load(f)

    # ---- 항목 단위 (CP4 incremental) ----

    def _items_path(self, stage: str) -> Path:
        return self.base / f"{stage}_items.jsonl"

    def load_completed_ids(self, stage: str, id_field: str = "eval_id") -> set[str]:
        path = self._items_path(stage)
        if not path.exists():
            return set()
        done: set[str] = set()
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    done.add(json.loads(line)[id_field])
                except (json.JSONDecodeError, KeyError):
                    continue
        return done

    def load_items(self, stage: str) -> list[dict]:
        path = self._items_path(stage)
        if not path.exists():
            return []
        items = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))
        return items

    def append_item(self, stage: str, item: Any) -> None:
        payload = self._serialize(item)
        with self._items_path(stage).open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    # ---- 직렬화 헬퍼 ----

    @staticmethod
    def _serialize(data: Any) -> Any:
        if hasattr(data, "model_dump"):
            return data.model_dump(mode="json")
        if isinstance(data, list):
            return [
                d.model_dump(mode="json") if hasattr(d, "model_dump") else d
                for d in data
            ]
        return data
