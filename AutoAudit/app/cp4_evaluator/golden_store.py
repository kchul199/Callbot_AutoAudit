"""
cp4_evaluator/golden_store.py
#3 Human Review → 골든셋 환류 라이터 + 오류기반 few-shot 앵커 풀.

Review에서 확정(승인/수정)된 사람 점수를 골든셋(JSONL)에 누적 append 한다.
이 골든셋은 그대로 meta_eval(#7)·auto_calibration(⑦)의 입력이 되어
'사람 라벨 → 측정·보정·임계튜닝'의 폐루프를 완성한다.

추가로, 자동 점수와 사람 점수가 크게 어긋난 '경계 사례'를 앵커 풀(JSONL)에
적재해 JudgeCalibrator의 동적 few-shot 예시로 재사용한다.

저장은 append-only (라인 추가)만 사용 → 멱등·동시성 안전, 삭제(unlink) 불필요.

골든 라인 : {"qa_id", "metric", "human_score", "auto_score", "ts"}
앵커 라인 : {"metric", "query", "answer", "auto_score", "human_score", "disagreement", "ts"}
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from AutoAudit.app.core.logger import get_logger

logger = get_logger(__name__)


class GoldenStore:
    def __init__(
        self,
        golden_path: str = "data/golden_set.jsonl",
        anchor_path: str = "data/anchor_pool.jsonl",
        disagreement_threshold: float = 0.3,
        anchor_pool_size: int = 12,
    ) -> None:
        self.golden_path = Path(golden_path)
        self.anchor_path = Path(anchor_path)
        self.disagreement_threshold = disagreement_threshold
        self.anchor_pool_size = anchor_pool_size

    # ----------------------------------------------------------
    # 쓰기 (환류)
    # ----------------------------------------------------------

    def append_review(
        self,
        qa_id: str,
        query: str,
        answer: str,
        auto_scores: dict[str, float],
        human_scores: dict[str, float],
    ) -> tuple[int, int]:
        """확정된 사람 점수를 골든셋에 append + 큰 불일치는 앵커 풀에 적재.

        반환: (추가된 골든 행 수, 추가된 앵커 행 수)
        """
        if not qa_id or not human_scores:
            return (0, 0)
        ts = datetime.now(timezone.utc).isoformat()
        golden_rows: list[dict] = []
        anchor_rows: list[dict] = []
        for metric, human in human_scores.items():
            try:
                human = float(human)
            except (TypeError, ValueError):
                continue
            auto = auto_scores.get(metric)
            golden_rows.append({
                "qa_id": qa_id, "metric": metric,
                "human_score": round(human, 4),
                "auto_score": round(float(auto), 4) if auto is not None else None,
                "ts": ts,
            })
            if auto is not None and abs(float(auto) - human) >= self.disagreement_threshold:
                anchor_rows.append({
                    "metric": metric, "query": query, "answer": answer,
                    "auto_score": round(float(auto), 4), "human_score": round(human, 4),
                    "disagreement": round(abs(float(auto) - human), 4), "ts": ts,
                })

        self._append(self.golden_path, golden_rows)
        self._append(self.anchor_path, anchor_rows)
        if golden_rows:
            logger.info(
                f"[golden_store] 환류: 골든 +{len(golden_rows)}행, 앵커 +{len(anchor_rows)}행 "
                f"(qa={qa_id})"
            )
        return (len(golden_rows), len(anchor_rows))

    def golden_count(self) -> int:
        return self._count(self.golden_path)

    # ----------------------------------------------------------
    # 읽기 (few-shot 앵커)
    # ----------------------------------------------------------

    def load_anchors(self, metric: str, limit: int | None = None) -> list[dict]:
        """해당 메트릭의 앵커를 불일치 크기·최신성 우선으로 상위 N개 반환."""
        limit = limit if limit is not None else self.anchor_pool_size
        rows = [r for r in self._read(self.anchor_path) if r.get("metric") == metric]
        # 불일치 큰 순 → 최신 순
        rows.sort(key=lambda r: (r.get("disagreement", 0.0), r.get("ts", "")), reverse=True)
        return rows[:limit]

    # ----------------------------------------------------------
    # 파일 IO (append-only)
    # ----------------------------------------------------------

    @staticmethod
    def _append(path: Path, rows: list[dict]) -> None:
        if not rows:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    @staticmethod
    def _read(path: Path) -> list[dict]:
        if not path.exists():
            return []
        out: list[dict] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    @staticmethod
    def _count(path: Path) -> int:
        if not path.exists():
            return 0
        with path.open(encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
