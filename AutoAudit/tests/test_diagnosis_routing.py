"""
tests/test_diagnosis_routing.py
검색vs생성 진단 + 라우팅(active sampling) + PPI 분류기 검증
"""
import pytest

from AutoAudit.app.core.types import (
    EvaluationRecord,
    MetricScore,
    QAPair,
    RetrievalResult,
    RetrievedContext,
)
from AutoAudit.app.cp4_evaluator.diagnosis import RetrievalGenerationDiagnoser
from AutoAudit.app.cp4_evaluator.options import DiagnosisOptions, RoutingOptions
from AutoAudit.app.cp4_evaluator.ppi_classifier import HeuristicClassifier
from AutoAudit.app.cp4_evaluator.routing import EvaluationRouter


def _rec(eval_id, faith, recall, low_conf=False):
    return EvaluationRecord(
        eval_id=eval_id, call_id="C1", query="q", generated_answer="a",
        retrieval_result=RetrievalResult(query="q", contexts=[]),
        scores=[
            MetricScore(metric="faithfulness", score=faith, reasoning="",
                        is_low_confidence=low_conf, confidence=0.4 if low_conf else 1.0),
            MetricScore(metric="context_recall", score=recall, reasoning=""),
        ],
    )


# ---- 진단 2x2 ----

@pytest.mark.parametrize("faith,recall,expected", [
    (0.9, 0.9, "healthy"),
    (0.9, 0.4, "retrieval_failure"),
    (0.5, 0.9, "generation_hallucination"),
    (0.5, 0.4, "both"),
])
def test_diagnosis_matrix(faith, recall, expected):
    d = RetrievalGenerationDiagnoser(DiagnosisOptions())
    v = d.diagnose(_rec("e", faith, recall))
    assert v.category == expected
    assert v.recommendation


def test_diagnose_all_distribution():
    d = RetrievalGenerationDiagnoser(DiagnosisOptions())
    recs = [_rec("e1", 0.9, 0.9), _rec("e2", 0.9, 0.4), _rec("e3", 0.5, 0.5)]
    dist = d.diagnose_all(recs)
    assert dist["healthy"] == 1
    assert dist["retrieval_failure"] == 1
    assert dist["both"] == 1
    assert all(r.diagnosis is not None for r in recs)


# ---- 라우팅 / active sampling ----

def test_router_selects_uncertain():
    router = EvaluationRouter(RoutingOptions(active_sampling_quota=1))
    recs = [_rec("e0", 0.9, 0.9, low_conf=True), _rec("e1", 0.95, 0.95), _rec("e2", 0.92, 0.93)]
    selected = router.select_for_human_review(recs)
    assert len(selected) == 1
    assert selected[0].eval_id == "e0"
    assert recs[0].needs_human_review is True


def test_router_needs_escalation():
    router = EvaluationRouter(RoutingOptions())
    assert router.needs_escalation(_rec("e", 0.9, 0.9, low_conf=True)) is True
    assert router.needs_escalation(_rec("e", 0.9, 0.9, low_conf=False)) is False


# ---- PPI 분류기 ----

def test_heuristic_classifier_overlap():
    clf = HeuristicClassifier()
    pair = QAPair(
        qa_id="q", call_id="c", subscriber_id="s",
        question="요금제 변경 방법", bot_answer="요금제는 앱에서 변경 가능합니다",
        turn_index=0,
        retrieval_result=RetrievalResult(
            query="요금제 변경 방법",
            contexts=[RetrievedContext(chunk_id="c1", content="요금제 변경은 앱에서 가능합니다",
                                       score=0.9, source_call_id="c")],
        ),
    )
    assert 0.0 < clf.faithfulness(pair) <= 1.0
    assert 0.0 < clf.context_recall(pair) <= 1.0


def test_heuristic_no_retrieval():
    clf = HeuristicClassifier()
    pair = QAPair(qa_id="q", call_id="c", subscriber_id="s",
                  question="q", bot_answer="a", turn_index=0)
    assert clf.faithfulness(pair) == 0.0
