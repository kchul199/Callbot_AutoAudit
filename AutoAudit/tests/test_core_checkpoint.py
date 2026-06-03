"""
tests/test_core_checkpoint.py
체크포인트 저장소 — resume 멱등성 검증
"""
from AutoAudit.app.core.checkpoint import CheckpointStore
from AutoAudit.app.core.types import EvaluationRecord, RetrievalResult


def test_stage_save_and_load(tmp_path):
    store = CheckpointStore("run_x", str(tmp_path))
    assert store.has_stage("cp3") is False
    store.save_stage("cp3", [{"qa_id": "q1"}, {"qa_id": "q2"}])
    assert store.has_stage("cp3") is True
    loaded = store.load_stage("cp3")
    assert len(loaded) == 2
    assert loaded[0]["qa_id"] == "q1"


def test_stage_save_pydantic(tmp_path):
    store = CheckpointStore("run_y", str(tmp_path))
    rec = EvaluationRecord(
        eval_id="e1", qa_id="q1", call_id="C1", query="q", generated_answer="a",
        retrieval_result=RetrievalResult(query="q", contexts=[]),
    )
    store.save_stage("cp4", [rec])
    loaded = store.load_stage("cp4")
    assert loaded[0]["eval_id"] == "e1"


def test_item_incremental_append(tmp_path):
    store = CheckpointStore("run_z", str(tmp_path))
    assert store.load_completed_ids("cp4", id_field="qa_id") == set()

    for qid in ["q1", "q2", "q3"]:
        rec = EvaluationRecord(
            eval_id=f"e_{qid}", qa_id=qid, call_id="C1", query="q", generated_answer="a",
            retrieval_result=RetrievalResult(query="q", contexts=[]),
        )
        store.append_item("cp4", rec)

    done = store.load_completed_ids("cp4", id_field="qa_id")
    assert done == {"q1", "q2", "q3"}
    assert len(store.load_items("cp4")) == 3


def test_resume_skips_completed(tmp_path):
    """완료 항목 로드 후 잔여만 식별"""
    store = CheckpointStore("run_w", str(tmp_path))
    rec = EvaluationRecord(
        eval_id="e1", qa_id="q1", call_id="C1", query="q", generated_answer="a",
        retrieval_result=RetrievalResult(query="q", contexts=[]),
    )
    store.append_item("cp4", rec)

    all_qa_ids = ["q1", "q2", "q3"]
    done = store.load_completed_ids("cp4", id_field="qa_id")
    pending = [q for q in all_qa_ids if q not in done]
    assert pending == ["q2", "q3"]
