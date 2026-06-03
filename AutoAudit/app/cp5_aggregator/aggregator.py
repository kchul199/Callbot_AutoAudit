"""
cp5_aggregator/aggregator.py
CP4 평가 결과 집계 → SLA 판정 + AuditSummary 생성
"""
from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING

from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import (
    AggregatedMetric,
    AuditSummary,
    EvaluationRecord,
)

if TYPE_CHECKING:
    from AutoAudit.app.cp4_evaluator.options import EvaluationOptions

logger = get_logger(__name__)


class ResultAggregator:
    """
    CP4 EvaluationRecord 목록 → AuditSummary

    집계 단위: 전체 / call_id / subscriber_id / date
    SLA 미달 콜 자동 플래깅
    """

    def __init__(self) -> None:
        self.sla: dict[str, float] = cfg_get(
            "cp5.sla_thresholds",
            default={
                "faithfulness": 0.8,
                "answer_relevance": 0.75,
                "context_precision": 0.7,
                "context_recall": 0.7,
            },
        )

    # ----------------------------------------------------------
    # 진입점
    # ----------------------------------------------------------

    def aggregate(
        self,
        records: list[EvaluationRecord],
        run_id: str | None = None,
        options: EvaluationOptions | None = None,
    ) -> AuditSummary:
        if not records:
            logger.warning("No evaluation records to aggregate.")
            return AuditSummary(
                run_id=run_id or str(uuid.uuid4()),
                total_calls=0,
                total_evaluations=0,
            )

        run_id = run_id or str(uuid.uuid4())

        if options is None:
            from AutoAudit.app.cp4_evaluator.options import EvaluationOptions
            options = EvaluationOptions.from_config()

        # 메트릭별 점수 수집
        metric_scores: dict[str, list[float]] = defaultdict(list)
        for rec in records:
            for ms in rec.scores:
                metric_scores[ms.metric].append(ms.score)

        # 통계 계산 (옵션 시 부트스트랩 신뢰구간)
        use_stats = options.statistics.enabled
        if use_stats:
            from AutoAudit.app.cp5_aggregator.statistics_ext import bootstrap_ci

        aggregated_metrics: list[AggregatedMetric] = []
        for metric, scores in metric_scores.items():
            sla_threshold = self.sla.get(metric, 0.7)
            below_sla = [s for s in scores if s < sla_threshold]
            ci_low = ci_high = None
            if use_stats:
                ci = bootstrap_ci(
                    scores,
                    n_samples=options.statistics.bootstrap_samples,
                    confidence=options.statistics.confidence_level,
                )
                ci_low, ci_high = round(ci.low, 4), round(ci.high, 4)
            aggregated_metrics.append(
                AggregatedMetric(
                    metric=metric,
                    mean=statistics.mean(scores),
                    median=statistics.median(scores),
                    p10=self._percentile(scores, 10),
                    p90=self._percentile(scores, 90),
                    below_sla_count=len(below_sla),
                    total_count=len(scores),
                    sla_pass_rate=1.0 - len(below_sla) / len(scores),
                    ci_low=ci_low,
                    ci_high=ci_high,
                )
            )

        # SLA 미달 콜 플래깅
        flagged_call_ids = self._flag_calls(records)

        # 기간 추출
        timestamps = [r.evaluated_at for r in records if r.evaluated_at]
        period_start = min(timestamps) if timestamps else None
        period_end = max(timestamps) if timestamps else None

        unique_calls = len({r.call_id for r in records})

        summary = AuditSummary(
            run_id=run_id,
            period_start=period_start,
            period_end=period_end,
            total_calls=unique_calls,
            total_evaluations=len(records),
            metrics=aggregated_metrics,
            flagged_call_ids=flagged_call_ids,
        )

        # 검색 vs 생성 진단 (옵션)
        if options.diagnosis.enabled:
            from AutoAudit.app.cp4_evaluator.diagnosis import RetrievalGenerationDiagnoser
            diagnoser = RetrievalGenerationDiagnoser(options.diagnosis)
            summary.diagnosis_distribution = diagnoser.diagnose_all(records)

        # active sampling 인간검수 큐 (옵션)
        if options.routing.enabled:
            from AutoAudit.app.cp4_evaluator.routing import EvaluationRouter
            router = EvaluationRouter(options.routing)
            selected = router.select_for_human_review(records)
            summary.human_review_queue = [r.eval_id for r in selected]

        # Judge 메타평가 (옵션, 골든셋 필요)
        if options.meta_eval.enabled:
            from AutoAudit.app.cp4_evaluator.meta_eval import MetaEvaluator
            summary.meta_eval = MetaEvaluator(options.meta_eval).evaluate(records)

        self._log_summary(summary)
        return summary

    # ----------------------------------------------------------
    # SLA 플래깅
    # ----------------------------------------------------------

    def _flag_calls(self, records: list[EvaluationRecord]) -> list[str]:
        """
        임의 메트릭에서 SLA 미달 시 해당 call_id 플래깅
        """
        flagged: set[str] = set()
        for rec in records:
            for ms in rec.scores:
                threshold = self.sla.get(ms.metric, 0.7)
                if ms.score < threshold:
                    flagged.add(rec.call_id)
                    break
        return sorted(flagged)

    # ----------------------------------------------------------
    # 유틸
    # ----------------------------------------------------------

    @staticmethod
    def _percentile(data: list[float], pct: int) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        k = (len(sorted_data) - 1) * pct / 100
        lo, hi = int(k), min(int(k) + 1, len(sorted_data) - 1)
        return sorted_data[lo] + (sorted_data[hi] - sorted_data[lo]) * (k - lo)

    def _log_summary(self, summary: AuditSummary) -> None:
        logger.info(
            f"\n{'='*60}\n"
            f"[CP5 Audit Summary] run_id={summary.run_id}\n"
            f"  Total calls      : {summary.total_calls}\n"
            f"  Total evaluations: {summary.total_evaluations}\n"
            f"  Flagged calls    : {len(summary.flagged_call_ids)}\n"
        )
        for m in summary.metrics:
            status = "✅" if m.sla_pass_rate >= 0.9 else "⚠️" if m.sla_pass_rate >= 0.7 else "❌"
            logger.info(
                f"  {status} {m.metric:25s} | "
                f"mean={m.mean:.3f} | p10={m.p10:.3f} | p90={m.p90:.3f} | "
                f"SLA pass={m.sla_pass_rate:.1%}"
            )
        logger.info("="*60)
