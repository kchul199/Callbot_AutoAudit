"""
cp4_evaluator/diagnosis.py
검색 vs 생성 책임 진단 (2x2 매트릭스).

faithfulness(생성 충실도)와 context_recall(검색 충분성)을 교차하여
"어느 레이어를 고쳐야 하는가"를 자동 분류 → 운영자 액션 명확화.

                  recall 높음            recall 낮음
faith 높음   ✅ healthy             🔍 retrieval_failure(검색이 못 가져옴)
faith 낮음   🧠 generation_hallu.   ⚠️ both (검색+생성 모두 문제)
"""
from __future__ import annotations

from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import DiagnosisVerdict, EvaluationRecord
from AutoAudit.app.cp4_evaluator.options import DiagnosisOptions

logger = get_logger(__name__)

_RECOMMENDATIONS = {
    "healthy": "정상 — 조치 불필요",
    "retrieval_failure": "검색 개선 필요: 청킹/임베딩/HyDE·BM25 가중치 점검",
    "generation_hallucination": "생성 개선 필요: 프롬프트 grounding 강화, 답변 제약 추가",
    "both": "검색·생성 동시 결함: 우선 검색(CP2/CP3)부터 수정 후 재평가",
}


class RetrievalGenerationDiagnoser:
    def __init__(self, options: DiagnosisOptions) -> None:
        self.opts = options

    def diagnose(self, record: EvaluationRecord) -> DiagnosisVerdict:
        faith = self._score(record, "faithfulness")
        recall = self._score(record, "context_recall")

        faith_ok = faith is None or faith >= self.opts.faithfulness_threshold
        recall_ok = recall is None or recall >= self.opts.recall_threshold

        if faith_ok and recall_ok:
            category = "healthy"
        elif faith_ok and not recall_ok:
            category = "retrieval_failure"
        elif not faith_ok and recall_ok:
            category = "generation_hallucination"
        else:
            category = "both"

        return DiagnosisVerdict(
            category=category,
            recall_ok=recall_ok,
            faithfulness_ok=faith_ok,
            recommendation=_RECOMMENDATIONS[category],
        )

    def diagnose_all(self, records: list[EvaluationRecord]) -> dict[str, int]:
        """전체 진단 + 카테고리 분포 반환 (집계/리포트용)"""
        dist: dict[str, int] = {
            "healthy": 0, "retrieval_failure": 0,
            "generation_hallucination": 0, "both": 0,
        }
        for rec in records:
            v = self.diagnose(rec)
            rec.diagnosis = v
            dist[v.category] += 1
        logger.info(f"[diagnosis] 분포: {dist}")
        return dist

    @staticmethod
    def _score(record: EvaluationRecord, metric: str) -> float | None:
        ms = next((s for s in record.scores if s.metric == metric), None)
        return ms.score if ms else None
