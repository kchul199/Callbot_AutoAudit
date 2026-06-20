"""
cp6_reporter/regression.py
run 간 메트릭 회귀 감지 — 배포 전후 품질 비교.

이전 run 대비 메트릭 평균이 임계 이상 하락하면 회귀로 판정.
CP6 리포트/알림에서 사용.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import AuditSummary

logger = get_logger(__name__)


@dataclass
class MetricDelta:
    metric: str
    current: float
    previous: float
    delta: float          # current - previous (음수 = 하락)
    is_regression: bool


@dataclass
class RegressionReport:
    has_regression: bool
    deltas: list[MetricDelta] = field(default_factory=list)
    threshold: float = 0.05

    def summary_text(self) -> str:
        lines = []
        for d in self.deltas:
            arrow = "▼" if d.delta < 0 else "▲"
            flag = " 🚨 회귀" if d.is_regression else ""
            lines.append(
                f"{arrow} {d.metric}: {d.previous:.3f} → {d.current:.3f} "
                f"({d.delta:+.3f}){flag}"
            )
        return "\n".join(lines)


def _means(summary: AuditSummary | dict) -> dict[str, float]:
    metrics = summary.metrics if isinstance(summary, AuditSummary) else summary.get("metrics", [])
    out: dict[str, float] = {}
    for m in metrics:
        if isinstance(m, dict):
            out[m["metric"]] = m["mean"]
        else:
            out[m.metric] = m.mean
    return out


def detect_regression(
    current: AuditSummary | dict,
    previous: AuditSummary | dict | None,
    threshold: float | None = None,
) -> RegressionReport:
    """
    current vs previous 메트릭 평균 비교.
    하락폭이 threshold를 초과하면 회귀로 판정.
    previous가 없으면 (첫 실행) 회귀 없음.
    """
    thr = threshold if threshold is not None else cfg_get("cp6.regression_threshold", default=0.05)

    if previous is None:
        logger.info("이전 run 없음 — 회귀 비교 생략")
        return RegressionReport(has_regression=False, threshold=thr)

    cur_means = _means(current)
    prev_means = _means(previous)

    deltas: list[MetricDelta] = []
    has_reg = False
    for metric, cur in cur_means.items():
        if metric not in prev_means:
            continue
        prev = prev_means[metric]
        delta = cur - prev
        is_reg = delta < -thr
        has_reg = has_reg or is_reg
        deltas.append(MetricDelta(metric, cur, prev, delta, is_reg))

    report = RegressionReport(has_regression=has_reg, deltas=deltas, threshold=thr)
    if has_reg:
        logger.warning(f"품질 회귀 감지!\n{report.summary_text()}")
    else:
        logger.info("회귀 없음 (이전 run 대비 안정)")
    return report


@dataclass
class MetaEvalDrift:
    """#7 감사기 정확도(메타평가) 회귀 — 직전 run 대비 Judge↔인간 일치도 하락."""
    has_drift: bool
    current_rho: float | None
    previous_rho: float | None
    delta: float | None        # current - previous (음수 = 일치도 하락)
    threshold: float

    def summary_text(self) -> str:
        if self.current_rho is None or self.previous_rho is None:
            return "메타평가 추세 비교 불가 (골든셋 매칭 부족)"
        arrow = "▼" if (self.delta or 0) < 0 else "▲"
        flag = " 🚨 정확도 회귀" if self.has_drift else ""
        return (
            f"{arrow} Judge↔인간 ρ: {self.previous_rho:.3f} → {self.current_rho:.3f} "
            f"({self.delta:+.3f}){flag}"
        )


def _overall_rho(meta_eval: dict | None) -> float | None:
    if not meta_eval or not meta_eval.get("available"):
        return None
    overall = meta_eval.get("overall") or {}
    rho = overall.get("spearman")
    return float(rho) if rho is not None else None


def detect_meta_eval_drift(
    current: AuditSummary | dict,
    previous: AuditSummary | dict | None,
    rho_threshold: float = 0.7,
) -> MetaEvalDrift:
    """#7 현재 run의 메타평가 ρ 가 직전 run 대비 임계 미만으로 하락했는지 판정.

    - 직전 run이 없거나 한쪽 ρ 가 없으면 회귀 없음(비교 불가).
    - 하락(delta<0)이면서 현재 ρ < rho_threshold 일 때만 회귀로 본다
      (절대 수준이 충분히 높으면 소폭 하락은 경보하지 않음 → 거짓 알람 감소).
    """
    cur_meta = current.meta_eval if isinstance(current, AuditSummary) else current.get("meta_eval")
    prev_meta = (
        None if previous is None
        else previous.meta_eval if isinstance(previous, AuditSummary) else previous.get("meta_eval")
    )
    cur_rho = _overall_rho(cur_meta)
    prev_rho = _overall_rho(prev_meta)

    if cur_rho is None or prev_rho is None:
        return MetaEvalDrift(False, cur_rho, prev_rho, None, rho_threshold)

    delta = cur_rho - prev_rho
    has_drift = delta < 0 and cur_rho < rho_threshold
    drift = MetaEvalDrift(has_drift, cur_rho, prev_rho, delta, rho_threshold)
    if has_drift:
        logger.warning(f"감사기 정확도 회귀 감지!\n{drift.summary_text()}")
    else:
        logger.info(f"메타평가 추세 안정 — {drift.summary_text()}")
    return drift


def detect_regression_significant(
    current_scores: dict[str, list[float]],
    previous_scores: dict[str, list[float]],
    alpha: float = 0.05,
) -> RegressionReport:
    """
    원시 점수 기반 통계적 유의성 회귀 감지 (순열검정).
    고정 임계값 대신 '유의하게 하락'한 메트릭만 회귀로 판정 → 거짓 알람 감소.
    """
    from AutoAudit.app.cp5_aggregator.statistics_ext import paired_permutation_test

    deltas: list[MetricDelta] = []
    has_reg = False
    for metric, cur in current_scores.items():
        prev = previous_scores.get(metric)
        if not prev or not cur:
            continue
        sig = paired_permutation_test(cur, prev, alpha=alpha)
        is_reg = sig.significant and sig.delta < 0
        has_reg = has_reg or is_reg
        cur_mean = sum(cur) / len(cur)
        prev_mean = sum(prev) / len(prev)
        deltas.append(MetricDelta(metric, cur_mean, prev_mean, sig.delta, is_reg))
        if is_reg:
            logger.warning(
                f"[유의한 회귀] {metric}: {sig.delta:+.3f} "
                f"(p={sig.p_value:.3f}, CI[{sig.ci_low:.3f}, {sig.ci_high:.3f}])"
            )

    report = RegressionReport(has_regression=has_reg, deltas=deltas, threshold=alpha)
    if not has_reg:
        logger.info("통계적으로 유의한 회귀 없음")
    return report
