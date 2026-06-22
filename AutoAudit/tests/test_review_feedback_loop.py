"""
tests/test_review_feedback_loop.py
#3 Human Review → 골든셋 환류 + 오류기반 few-shot 앵커.
"""
import json

from AutoAudit.app.core.types import (
    EvalLevel,
    EvaluationRecord,
    MetricScore,
    RetrievalResult,
    RetrievedContext,
)
from AutoAudit.app.cp4_evaluator.golden_store import GoldenStore
from AutoAudit.app.cp4_evaluator.options import CalibrationOptions
from AutoAudit.app.cp4_evaluator.calibration import JudgeCalibrator


# ----------------------------------------------------------------
# GoldenStore
# ----------------------------------------------------------------

def test_append_review_writes_golden_and_anchor(tmp_path):
    gp, ap = tmp_path / "golden.jsonl", tmp_path / "anchor.jsonl"
    store = GoldenStore(str(gp), str(ap), disagreement_threshold=0.3)
    g, a = store.append_review(
        qa_id="q1", query="요금 얼마?", answer="월 3만원",
        auto_scores={"faithfulness": 0.9, "answer_relevance": 0.8},
        human_scores={"faithfulness": 0.4, "answer_relevance": 0.78},
    )
    assert g == 2          # 두 메트릭 골든 행
    assert a == 1          # faithfulness 만 |0.9-0.4|=0.5 ≥ 0.3 → 앵커
    golden = [json.loads(x) for x in gp.read_text(encoding="utf-8").splitlines()]
    assert {r["metric"] for r in golden} == {"faithfulness", "answer_relevance"}
    assert any(r["metric"] == "faithfulness" and r["human_score"] == 0.4 for r in golden)
    anchors = [json.loads(x) for x in ap.read_text(encoding="utf-8").splitlines()]
    assert anchors[0]["metric"] == "faithfulness" and anchors[0]["disagreement"] == 0.5


def test_append_is_cumulative(tmp_path):
    gp = tmp_path / "golden.jsonl"
    store = GoldenStore(str(gp), str(tmp_path / "a.jsonl"))
    store.append_review("q1", "q", "a", {"faithfulness": 0.9}, {"faithfulness": 0.5})
    store.append_review("q2", "q", "a", {"faithfulness": 0.8}, {"faithfulness": 0.6})
    assert store.golden_count() == 2


def test_load_anchors_orders_by_disagreement_and_limit(tmp_path):
    ap = tmp_path / "anchor.jsonl"
    store = GoldenStore(str(tmp_path / "g.jsonl"), str(ap))
    store.append_review("q1", "큰불일치", "a", {"faithfulness": 0.95}, {"faithfulness": 0.30})
    store.append_review("q2", "작은불일치", "a", {"faithfulness": 0.70}, {"faithfulness": 0.40})
    top = store.load_anchors("faithfulness", limit=1)
    assert len(top) == 1
    assert top[0]["query"] == "큰불일치"   # 불일치 큰 사례 우선
    assert store.load_anchors("answer_relevance") == []  # 다른 메트릭은 없음


def test_no_anchor_when_agreement(tmp_path):
    gp, ap = tmp_path / "g.jsonl", tmp_path / "a.jsonl"
    store = GoldenStore(str(gp), str(ap), disagreement_threshold=0.3)
    g, a = store.append_review("q1", "q", "a", {"faithfulness": 0.82}, {"faithfulness": 0.80})
    assert g == 1 and a == 0
    assert not ap.exists()  # 앵커 파일 미생성


# ----------------------------------------------------------------
# 동적 few-shot 앵커 주입 (JudgeCalibrator)
# ----------------------------------------------------------------

def test_dynamic_anchor_block_injected(tmp_path):
    ap = tmp_path / "anchor.jsonl"
    GoldenStore(str(tmp_path / "g.jsonl"), str(ap)).append_review(
        "q1", "해지 위약금 얼마?", "위약금 없습니다",
        {"faithfulness": 0.9}, {"faithfulness": 0.2},
    )
    opts = CalibrationOptions(enabled=True, use_dynamic_anchors=True, anchor_pool_path=str(ap))
    cal = JudgeCalibrator(provider=None, options=opts)
    decorated = cal.decorate_prompt("원본 프롬프트", metric="faithfulness")
    assert "사람 검수 보정 예시" in decorated
    assert "위약금" in decorated
    assert "원본 프롬프트" in decorated


def test_no_dynamic_anchor_when_absent(tmp_path):
    opts = CalibrationOptions(enabled=True, use_dynamic_anchors=True,
                              anchor_pool_path=str(tmp_path / "missing.jsonl"))
    cal = JudgeCalibrator(provider=None, options=opts)
    decorated = cal.decorate_prompt("원본", metric="faithfulness")
    assert "사람 검수 보정 예시" not in decorated
    assert "원본" in decorated


# ----------------------------------------------------------------
# data_access.record_review 환류 통합
# ----------------------------------------------------------------

def _rec(eval_id="e1", qa_id="qa1"):
    return EvaluationRecord(
        eval_id=eval_id, qa_id=qa_id, call_id="C1",
        tenant_id="acme", conversation_id="C1", subscriber_id="S1",
        level=EvalLevel.TURN, turn_index=0,
        query="요금 얼마?", generated_answer="월 3만원입니다",
        retrieval_result=RetrievalResult(
            query="요금 얼마?",
            contexts=[RetrievedContext(chunk_id="c1", content="월 5만원", score=0.9, source_call_id="C1")],
        ),
        scores=[MetricScore(metric="faithfulness", score=0.9, reasoning="r")],
    )


def test_record_review_feeds_golden(tmp_path, monkeypatch):
    from AutoAudit.app.api.data_access import DataAccess
    from AutoAudit.app.core.store import ResultStore
    import AutoAudit.app.cp4_evaluator.options as opts_mod

    # 환류 기본 경로를 tmp로 고정 (사용자 실제 data/ 미오염)
    gp, ap = str(tmp_path / "golden.jsonl"), str(tmp_path / "anchor.jsonl")
    orig = opts_mod.FeedbackLoopOptions

    def _patched(**kw):
        kw.setdefault("golden_set_path", gp)
        kw.setdefault("anchor_pool_path", ap)
        return orig(**kw)

    monkeypatch.setattr(opts_mod, "FeedbackLoopOptions", _patched)

    store = ResultStore(db_path=str(tmp_path / "fb.db"))
    store.upsert_evaluations("run1", [_rec()])
    data = DataAccess(store=store)

    ok = data.record_review(
        "e1", status="overridden",
        human_scores={"faithfulness": 0.3}, labels=["환각"], comment="", reviewer="me",
    )
    assert ok is True
    golden = [json.loads(x) for x in open(gp, encoding="utf-8")]
    assert any(r["qa_id"] == "qa1" and r["metric"] == "faithfulness" and r["human_score"] == 0.3
               for r in golden)
    # |0.9-0.3| ≥ 0.3 → 앵커 적재
    anchors = [json.loads(x) for x in open(ap, encoding="utf-8")]
    assert anchors and anchors[0]["metric"] == "faithfulness"


def test_record_review_approved_uses_auto_scores(tmp_path, monkeypatch):
    from AutoAudit.app.api.data_access import DataAccess
    from AutoAudit.app.core.store import ResultStore
    import AutoAudit.app.cp4_evaluator.options as opts_mod

    gp, ap = str(tmp_path / "golden.jsonl"), str(tmp_path / "anchor.jsonl")
    orig = opts_mod.FeedbackLoopOptions
    monkeypatch.setattr(opts_mod, "FeedbackLoopOptions",
                        lambda **kw: orig(golden_set_path=gp, anchor_pool_path=ap, **kw))

    store = ResultStore(db_path=str(tmp_path / "fb2.db"))
    store.upsert_evaluations("run1", [_rec(eval_id="e2", qa_id="qa2")])
    data = DataAccess(store=store)

    # 승인(점수 미입력) → 자동 점수를 사람 점수로 채택해 골든 적재
    ok = data.record_review("e2", status="approved", human_scores={})
    assert ok is True
    golden = [json.loads(x) for x in open(gp, encoding="utf-8")]
    assert any(r["qa_id"] == "qa2" and r["human_score"] == 0.9 for r in golden)
    # 동의(불일치 0) → 앵커 없음
    assert not __import__("os").path.exists(ap)
