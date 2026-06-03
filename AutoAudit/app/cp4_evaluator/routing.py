"""
cp4_evaluator/routing.py
불확실성 기반 selective evaluation + active sampling 큐.

- selective: 신뢰도 낮은 평가만 추가 샘플/에스컬레이션 (동일 예산, 신뢰도↑)
- active sampling: 불확실성 높은 + SLA 경계 근처 항목을 인간검수 큐로 선정
  (인간 라벨 → meta_eval 골든셋으로 환류하는 선순환)
"""
from __future__ import annotations

from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import EvaluationRecord
from AutoAudit.app.cp4_evaluator.options import RoutingOptions

logger = get_logger(__name__)


class EvaluationRouter:
    def __init__(self, options: RoutingOptions) -> None:
        self.opts = options
        self.sla: dict[str, float] = cfg_get("cp5.sla_thresholds", default={})

    def needs_escalation(self, record: EvaluationRecord) -> bool:
        """추가 샘플/상위모델 평가가 필요한가 (low confidence)"""
        if not self.opts.escalate_on_low_confidence:
            return False
        return any(s.is_low_confidence for s in record.scores)

    def select_for_human_review(
        self, records: list[EvaluationRecord]
    ) -> list[EvaluationRecord]:
        """
        active sampling: 불확실성 + SLA 경계 근접도로 우선순위 → 상위 quota개 선정.
        선정된 레코드에 needs_human_review=True 표시.
        """
        scored = [(self._priority(r), r) for r in records]
        scored.sort(key=lambda x: x[0], reverse=True)
        quota = min(self.opts.active_sampling_quota, len(scored))
        selected = [r for _, r in scored[:quota]]
        for r in selected:
            r.needs_human_review = True
        logger.info(f"[routing] 인간검수 큐 {len(selected)}건 선정 (quota={quota})")
        return selected

    def _priority(self, record: EvaluationRecord) -> float:
        """우선순위 점수: 불확실성 + SLA 경계 근접도"""
        uncertainty = 0.0
        boundary = 0.0
        for s in record.scores:
            # 불확실성: 낮은 confidence + 분산
            uncertainty += (1.0 - s.confidence)
            if s.is_low_confidence:
                uncertainty += 0.5
            # SLA 경계 근접도: 임계값 ±0.1 이내면 가중
            thr = self.sla.get(s.metric)
            if thr is not None:
                dist = abs(s.score - thr)
                if dist < 0.1:
                    boundary += (0.1 - dist) * 10  # 0~1
        n = max(1, len(record.scores))
        return uncertainty / n + boundary / n
