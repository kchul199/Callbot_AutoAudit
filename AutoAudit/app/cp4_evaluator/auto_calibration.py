"""
cp4_evaluator/auto_calibration.py
휴먼 정합 자동 보정 루프(Auto-Calibration) ⑦ — meta_eval의 '측정'을 '교정'으로.

meta_eval은 Judge가 사람과 얼마나 맞는지 측정(spearman/kappa/mae)만 했다.
이 모듈은 한 발 더 나가, 골든셋(judge_score → human_score)으로
메트릭별 보정 함수를 학습해 Judge 점수를 사람 척도로 끌어당긴다.

  - isotonic: 단조 증가 보정 (PAVA, 비모수) — 순서는 유지하되 척도만 교정
  - platt:    로지스틱(시그모이드) 보정 — 경사하강 소량 반복
  - linear:   최소제곱 1차 보정 (y = a·x + b)
  - identity: 보정 안 함 (패스스루)

추가로 SLA 임계를 '사람 합격/불합격(human≥0.5)'을 가장 잘 가르는 지점으로
자동 탐색(F1 최대화)한다. numpy/scipy 미의존(stdlib).

골든셋 포맷 (JSONL): {"qa_id": "...", "metric": "faithfulness", "human_score": 0.75}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import EvaluationRecord

if TYPE_CHECKING:
    from AutoAudit.app.cp4_evaluator.options import AutoCalibrationOptions

logger = get_logger(__name__)


class _IsotonicMap:
    """PAVA(Pool Adjacent Violators)로 학습한 단조 증가 보정 + 선형보간 적용."""

    def __init__(self, xs: list[float], ys: list[float]) -> None:
        self.xs = xs  # 정렬된 입력(judge)
        self.ys = ys  # 단조 보정된 출력(human)

    @classmethod
    def fit(cls, pairs: list[tuple[float, float]]) -> _IsotonicMap:
        # x(judge) 오름차순 정렬, 동일 x는 y 평균
        pts: dict[float, list[float]] = {}
        for x, y in pairs:
            pts.setdefault(x, []).append(y)
        xs = sorted(pts)
        ys = [sum(pts[x]) / len(pts[x]) for x in xs]
        w = [float(len(pts[x])) for x in xs]

        # PAVA: 인접 위반(감소) 블록을 가중 평균으로 병합
        vals = list(ys)
        wts = list(w)
        i = 0
        while i < len(vals) - 1:
            if vals[i] > vals[i + 1] + 1e-12:
                new_w = wts[i] + wts[i + 1]
                new_v = (vals[i] * wts[i] + vals[i + 1] * wts[i + 1]) / new_w
                vals[i] = new_v
                wts[i] = new_w
                del vals[i + 1]
                del wts[i + 1]
                del xs[i + 1]
                if i > 0:
                    i -= 1
            else:
                i += 1
        return cls(xs, vals)

    def apply(self, x: float) -> float:
        xs, ys = self.xs, self.ys
        if not xs:
            return x
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        # 선형 보간
        for i in range(len(xs) - 1):
            if xs[i] <= x <= xs[i + 1]:
                if xs[i + 1] == xs[i]:
                    return ys[i]
                t = (x - xs[i]) / (xs[i + 1] - xs[i])
                return ys[i] + t * (ys[i + 1] - ys[i])
        return ys[-1]


class _LinearMap:
    """최소제곱 1차 보정 y = a·x + b (0~1 클램프)."""

    def __init__(self, a: float, b: float) -> None:
        self.a, self.b = a, b

    @classmethod
    def fit(cls, pairs: list[tuple[float, float]]) -> _LinearMap:
        n = len(pairs)
        sx = sum(p[0] for p in pairs)
        sy = sum(p[1] for p in pairs)
        sxx = sum(p[0] * p[0] for p in pairs)
        sxy = sum(p[0] * p[1] for p in pairs)
        denom = n * sxx - sx * sx
        if abs(denom) < 1e-12:
            return cls(1.0, 0.0)
        a = (n * sxy - sx * sy) / denom
        b = (sy - a * sx) / n
        return cls(a, b)

    def apply(self, x: float) -> float:
        return max(0.0, min(1.0, self.a * x + self.b))


class _PlattMap:
    """로지스틱 보정 sigmoid(a·x + b) — 경사하강 소량 반복."""

    def __init__(self, a: float, b: float) -> None:
        self.a, self.b = a, b

    @classmethod
    def fit(cls, pairs: list[tuple[float, float]], iters: int = 300, lr: float = 0.5) -> _PlattMap:
        import math
        a, b = 1.0, 0.0
        n = len(pairs)
        for _ in range(iters):
            ga = gb = 0.0
            for x, y in pairs:
                z = a * x + b
                p = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))
                err = p - y
                ga += err * x
                gb += err
            a -= lr * ga / n
            b -= lr * gb / n
        return cls(a, b)

    def apply(self, x: float) -> float:
        import math
        z = self.a * x + self.b
        return 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))


class AutoCalibrator:
    """골든셋으로 메트릭별 보정맵 학습 + 점수 보정 + SLA 임계 자동튜닝."""

    def __init__(self, options: AutoCalibrationOptions) -> None:
        self.opts = options
        self.maps: dict[str, object] = {}          # metric → map
        self.tuned_sla: dict[str, float] = {}      # metric → 자동 임계
        self.report_data: dict = {"available": False}

    # ----------------------------------------------------------
    # 골든셋 로드
    # ----------------------------------------------------------

    def _load_golden(self) -> dict[tuple[str, str], float]:
        path = Path(self.opts.golden_set_path)
        golden: dict[tuple[str, str], float] = {}
        if not path.exists():
            logger.warning(f"[auto_calibration] 골든셋 없음: {path}")
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
        return golden

    # ----------------------------------------------------------
    # 학습
    # ----------------------------------------------------------

    def fit(self, records: list[EvaluationRecord]) -> bool:
        """records(judge)와 골든(human)을 매칭해 메트릭별 보정맵 학습."""
        golden = self._load_golden()
        if not golden:
            self.report_data = {"available": False, "reason": "골든셋 없음"}
            return False

        per_metric: dict[str, list[tuple[float, float]]] = {}
        for rec in records:
            for ms in rec.scores:
                key = (rec.qa_id or "", ms.metric)
                if key in golden:
                    per_metric.setdefault(ms.metric, []).append((ms.score, golden[key]))

        if not per_metric:
            self.report_data = {"available": False, "reason": "골든셋과 매칭된 평가 없음"}
            return False

        fitted: dict[str, int] = {}
        for metric, pairs in per_metric.items():
            if len(pairs) < self.opts.min_samples:
                logger.info(f"[auto_calibration] {metric}: 샘플 {len(pairs)} < {self.opts.min_samples} → 보정 생략")
                continue
            self.maps[metric] = self._fit_one(pairs)
            fitted[metric] = len(pairs)
            if self.opts.auto_tune_sla:
                self.tuned_sla[metric] = self._tune_threshold(pairs)

        self.report_data = {
            "available": bool(fitted),
            "method": self.opts.method,
            "fitted_metrics": fitted,
            "tuned_sla": dict(self.tuned_sla),
        }
        if fitted:
            logger.info(
                f"[auto_calibration] 보정맵 학습 — method={self.opts.method}, "
                f"metrics={fitted}, tuned_sla={self.tuned_sla}"
            )
        return bool(fitted)

    def _fit_one(self, pairs: list[tuple[float, float]]) -> object:
        method = self.opts.method
        if method == "isotonic":
            return _IsotonicMap.fit(pairs)
        if method == "platt":
            return _PlattMap.fit(pairs)
        if method == "linear":
            return _LinearMap.fit(pairs)
        return None  # identity

    # ----------------------------------------------------------
    # 적용
    # ----------------------------------------------------------

    def apply_to_records(self, records: list[EvaluationRecord]) -> int:
        """학습된 보정맵을 모든 점수에 적용 (원점수는 calibrated_from에 보존)."""
        if not self.maps:
            return 0
        n = 0
        for rec in records:
            for ms in rec.scores:
                m = self.maps.get(ms.metric)
                if m is None:
                    continue
                calibrated = round(max(0.0, min(1.0, m.apply(ms.score))), 4)  # type: ignore[attr-defined]
                if abs(calibrated - ms.score) > 1e-9:
                    ms.calibrated_from = ms.score
                    ms.score = calibrated
                    n += 1
        if n:
            logger.info(f"[auto_calibration] {n}개 점수 보정 적용")
        return n

    # ----------------------------------------------------------
    # SLA 임계 자동 튜닝 — 사람 합격/불합격 F1 최대화
    # ----------------------------------------------------------

    @staticmethod
    def _tune_threshold(pairs: list[tuple[float, float]], human_pass: float = 0.5) -> float:
        """judge 점수 임계를 사람 합격(human≥human_pass) 예측 F1이 최대가 되게 탐색."""
        labels = [1 if h >= human_pass else 0 for _, h in pairs]
        if all(labels) or not any(labels):
            return 0.5  # 한 클래스뿐이면 의미 없음
        best_t, best_f1 = 0.5, -1.0
        for i in range(1, 100):
            t = i / 100
            tp = fp = fn = 0
            for (j_score, _), lab in zip(pairs, labels, strict=False):
                pred = 1 if j_score >= t else 0
                if pred and lab:
                    tp += 1
                elif pred and not lab:
                    fp += 1
                elif not pred and lab:
                    fn += 1
            denom = 2 * tp + fp + fn
            f1 = (2 * tp / denom) if denom > 0 else 0.0
            if f1 > best_f1:
                best_f1, best_t = f1, t
        return round(best_t, 3)

    def report(self) -> dict:
        return self.report_data
