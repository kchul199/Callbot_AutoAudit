"""
cp4_evaluator/meta_eval.py
인간 골든셋 대비 Judge 메타평가 — "평가자를 평가".

소량(50~100건)의 인간 라벨과 Judge 점수의 상관도(Spearman ρ),
일치도(Cohen's κ, 이산화), 오차(MAE)를 산출하여
Judge/프롬프트/모델 변경의 회귀를 검증한다.

골든셋 포맷 (JSONL, 한 줄당):
  {"qa_id": "...", "metric": "faithfulness", "human_score": 0.75}

stdlib만 사용 (numpy/scipy 미의존) → 경량.
"""
from __future__ import annotations

import json
from pathlib import Path

from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import EvaluationRecord
from AutoAudit.app.cp4_evaluator.options import MetaEvalOptions

logger = get_logger(__name__)


class MetaEvaluator:
    def __init__(self, options: MetaEvalOptions) -> None:
        self.opts = options

    def load_golden(self) -> dict[tuple[str, str], float]:
        """(qa_id, metric) → human_score"""
        path = Path(self.opts.golden_set_path)
        golden: dict[tuple[str, str], float] = {}
        if not path.exists():
            logger.warning(f"골든셋 없음: {path}")
            return golden
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    golden[(d["qa_id"], d["metric"])] = float(d["human_score"])
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
        logger.info(f"골든셋 {len(golden)}건 로드")
        return golden

    def evaluate(self, records: list[EvaluationRecord]) -> dict:
        """Judge 점수 vs 인간 점수 상관/일치/오차 (EvaluationRecord 입력)"""
        rows = [
            (rec.qa_id or "", ms.metric, ms.score)
            for rec in records
            for ms in rec.scores
        ]
        result = self.evaluate_from_rows(rows)
        if result.get("available"):
            self._log(result)
        return result

    def evaluate_from_rows(self, rows: list[tuple[str, str, float]]) -> dict:
        """(qa_id, metric, judge_score) 행 목록으로 메타평가.

        저장된 평가 dict(트렌드 API)·EvaluationRecord 양쪽에서 재사용하는 공통 경로.
        """
        golden = self.load_golden()
        if not golden:
            return {"available": False, "reason": "골든셋 없음"}

        pairs: list[tuple[float, float]] = []  # (judge, human)
        per_metric: dict[str, list[tuple[float, float]]] = {}
        for qa_id, metric, score in rows:
            key = (qa_id or "", metric)
            if key in golden:
                pair = (float(score), golden[key])
                pairs.append(pair)
                per_metric.setdefault(metric, []).append(pair)

        if not pairs:
            return {"available": False, "reason": "골든셋과 매칭된 평가 없음"}

        result = {"available": True, "n": len(pairs), "overall": self._stats(pairs)}
        result["per_metric"] = {m: self._stats(p) for m, p in per_metric.items()}
        return result

    def _stats(self, pairs: list[tuple[float, float]]) -> dict:
        judge = [p[0] for p in pairs]
        human = [p[1] for p in pairs]
        out: dict[str, float] = {"n": len(pairs)}
        if "mae" in self.opts.metrics:
            out["mae"] = sum(abs(a - b) for a, b in pairs) / len(pairs)
        if "spearman" in self.opts.metrics:
            out["spearman"] = self._spearman(judge, human)
        if "kappa" in self.opts.metrics:
            out["kappa"] = self._cohen_kappa(judge, human)
        return out

    # ----------------------------------------------------------
    # 통계 (stdlib 구현)
    # ----------------------------------------------------------

    @staticmethod
    def _rank(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(values):
            j = i
            while j + 1 < len(values) and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1  # 1-based 평균 순위 (동점 처리)
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks

    def _spearman(self, a: list[float], b: list[float]) -> float:
        if len(a) < 2:
            return 0.0
        ra, rb = self._rank(a), self._rank(b)
        n = len(a)
        mean_ra = sum(ra) / n
        mean_rb = sum(rb) / n
        cov = sum((ra[i] - mean_ra) * (rb[i] - mean_rb) for i in range(n))
        va = sum((r - mean_ra) ** 2 for r in ra) ** 0.5
        vb = sum((r - mean_rb) ** 2 for r in rb) ** 0.5
        return cov / (va * vb) if va > 0 and vb > 0 else 0.0

    @staticmethod
    def _bin(score: float) -> int:
        """0~1 점수를 5단계 라벨로 이산화"""
        return min(4, int(round(score * 4)))

    def _cohen_kappa(self, a: list[float], b: list[float]) -> float:
        la = [self._bin(x) for x in a]
        lb = [self._bin(x) for x in b]
        n = len(la)
        if n == 0:
            return 0.0
        po = sum(1 for i in range(n) if la[i] == lb[i]) / n
        # 기대 일치도
        labels = set(la) | set(lb)
        pe = 0.0
        for lab in labels:
            pa = la.count(lab) / n
            pb = lb.count(lab) / n
            pe += pa * pb
        return (po - pe) / (1 - pe) if pe < 1 else 1.0

    def _log(self, result: dict) -> None:
        o = result["overall"]
        parts = [f"n={result['n']}"]
        for k in ("spearman", "kappa", "mae"):
            if k in o:
                parts.append(f"{k}={o[k]:.3f}")
        logger.info(f"[meta-eval] Judge vs 인간 — {' | '.join(parts)}")
