"""
api/repository.py
data/results/ 의 Trace 파일을 읽어 API에 제공하는 읽기 전용 저장소.

저장 구조:
  data/results/<run_id>/cp5_audit_summary_<ts>.json
  data/results/<run_id>/cp4_eval_records_<ts>.json
각 단계는 타임스탬프가 붙으므로 run별 '최신' 파일을 선택한다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from AutoAudit.app.core.config import get as cfg_get


class ResultsRepository:
    def __init__(self, results_dir: str | None = None) -> None:
        self.results_dir = Path(results_dir or cfg_get("paths.results", default="data/results"))

    # ---- run 목록 ----

    def list_runs(self) -> list[dict[str, Any]]:
        """run_id 목록 (summary 메타 포함, 최신순)"""
        runs: list[dict[str, Any]] = []
        if not self.results_dir.exists():
            return runs
        for run_dir in self.results_dir.iterdir():
            if not run_dir.is_dir():
                continue
            # reports/ 등 보조 디렉토리 제외 — 평가/요약 trace가 있는 run만
            if run_dir.name in {"reports", "_checkpoints"}:
                continue
            summary = self._load_latest(run_dir, "cp5_audit_summary")
            evals = self._load_latest(run_dir, "cp4_eval_records")
            if summary is None and evals is None:
                continue  # trace 없는 디렉토리는 run 아님
            runs.append(
                {
                    "run_id": run_dir.name,
                    "generated_at": (summary or {}).get("generated_at"),
                    "total_calls": (summary or {}).get("total_calls", 0),
                    "total_evaluations": (summary or {}).get("total_evaluations", 0),
                    "flagged_count": len((summary or {}).get("flagged_call_ids", [])),
                    "has_summary": summary is not None,
                }
            )
        runs.sort(key=lambda r: r.get("generated_at") or "", reverse=True)
        return runs

    def latest_run_id(self) -> str | None:
        runs = self.list_runs()
        return runs[0]["run_id"] if runs else None

    # ---- summary ----

    def get_summary(self, run_id: str) -> dict[str, Any] | None:
        return self._load_latest(self.results_dir / run_id, "cp5_audit_summary")

    # ---- evaluations ----

    def get_evaluations(
        self,
        run_id: str,
        metric: str | None = None,
        call_id: str | None = None,
        subscriber_id: str | None = None,
        flagged_only: bool = False,
        low_confidence_only: bool = False,
        below_metric: str | None = None,
        below_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        records = self._load_latest(self.results_dir / run_id, "cp4_eval_records") or []
        if not isinstance(records, list):
            return []

        flagged = set()
        if flagged_only:
            summary = self.get_summary(run_id) or {}
            flagged = set(summary.get("flagged_call_ids", []))

        out = []
        for r in records:
            if call_id and r.get("call_id") != call_id:
                continue
            if subscriber_id and r.get("subscriber_id") != subscriber_id:
                continue
            if flagged_only and r.get("call_id") not in flagged:
                continue
            if low_confidence_only and not any(
                s.get("is_low_confidence") for s in r.get("scores", [])
            ):
                continue
            if below_metric is not None and below_threshold is not None:
                ms = next((s for s in r.get("scores", []) if s["metric"] == below_metric), None)
                if not ms or ms["score"] >= below_threshold:
                    continue
            if metric:
                # 해당 메트릭 점수만 노출하도록 축약
                ms = next((s for s in r.get("scores", []) if s["metric"] == metric), None)
                r = {**r, "focus_metric": ms}
            out.append(r)
        return out

    def get_evaluation(self, run_id: str, eval_id: str) -> dict[str, Any] | None:
        records = self._load_latest(self.results_dir / run_id, "cp4_eval_records") or []
        return next((r for r in records if r.get("eval_id") == eval_id), None)

    # ---- 내부 ----

    def _load_latest(self, run_dir: Path, prefix: str) -> Any | None:
        if not run_dir.exists():
            return None
        candidates = sorted(run_dir.glob(f"{prefix}_*.json"))
        if not candidates:
            return None
        with candidates[-1].open("r", encoding="utf-8") as f:
            return json.load(f)
