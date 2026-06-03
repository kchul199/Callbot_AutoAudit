"""
core/tracer.py
파이프라인 중간 결과를 data/results/ 에 JSON으로 기록하는 Tracer
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from AutoAudit.app.core.logger import get_logger

logger = get_logger(__name__)


class Tracer:
    """
    CP별 결과를 Trace 파일로 저장.

    사용 예:
        tracer = Tracer(run_id="run_20240601", results_dir="data/results")
        tracer.save("cp1_output", {"call_id": "C001", ...})
    """

    def __init__(self, run_id: str, results_dir: str = "data/results") -> None:
        self.run_id = run_id
        self.base_dir = Path(results_dir) / run_id
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Tracer initialized → {self.base_dir}")

    def save(self, name: str, data: Any) -> Path:
        """
        data를 JSON 파일로 저장.
        Pydantic 모델, dict, list 모두 수용.
        """
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        file_path = self.base_dir / f"{name}_{timestamp}.json"

        if hasattr(data, "model_dump"):
            serializable = data.model_dump(mode="json")
        elif isinstance(data, list):
            serializable = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in data
            ]
        else:
            serializable = data

        with file_path.open("w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2, default=str)

        logger.info(f"Trace saved → {file_path}")
        return file_path

    def load(self, file_path: str) -> Any:
        """저장된 Trace 파일 로드"""
        path = Path(file_path)
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
