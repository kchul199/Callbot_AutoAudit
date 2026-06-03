"""
tests/test_store_m0.py
M0 SQLite 확장 — tenant/conversation/level/human 재평가 검증
"""
import pytest

from AutoAudit.app.core.store import ResultStore
from AutoAudit.app.core.types import (
    EvalLevel,
    EvaluationRecord,
    MetricScore,
    RetrievalResult,
    RetrievedContext,
)


@pytest.fixture
def store(tmp_path) -> ResultStore:
    return ResultStore(db_path=str(tmp_path / "m0.db"))


def _rec(eval_id, conv, tenant="acme", turn=0, faith=0.5, conf=0.4):
    return EvaluationRecord(
        eval_id=eval_id, qa_id=f"qa_{eval_id}", call_id=conv,
        tenant_id=tenant, conversation_id=conv, subscriber_id="S1",
        level=EvalLevel.TURN, turn_index=turn,
        query="질문?", generated_answer="답변",
        retrieval_result=RetrievalResult(
            query="질문?",
            contexts=[RetrievedContext(chunk_id="c1", content="ctx", score=0.9, source_call_id=conv)],
        ),
        scores=[MetricScore(metric="faithfulness", score=faith, reasoning="r", confidence=conf)],
    )


def test_tenant_column_persisted(store):
    store.upsert_evaluations("run1", [_rec("e1", "C1", tenant="acme")])
    rec = store.get_evaluation("run1", "e1")
    assert rec["tenant_id"] == "acme"
    assert rec["conversation_id"] == "C1"
    assert rec["level"] == "turn"
    assert rec["review_status"] == "pending"


def test_list_conversations_groups_turns(store):
    # 대화 원문 저장 (멀티턴 C1 = bot 2회, 싱글턴 C2 = bot 1회)
    store.upsert_conversation("C1", "acme", "S1", turns=[
        {"role": "user", "content": "q1"}, {"role": "bot", "content": "a1"},
        {"role": "user", "content": "q2"}, {"role": "bot", "content": "a2"},
    ])
    store.upsert_conversation("C2", "acme", "S2", turns=[
        {"role": "user", "content": "q"}, {"role": "bot", "content": "a"},
    ])
    store.upsert_evaluations("run1", [
        _rec("e1", "C1", turn=0), _rec("e2", "C1", turn=1),
        _rec("e3", "C2", turn=0),
    ])
    convos = store.list_conversations(tenant_id="acme")
    by_id = {c["conversation_id"]: c for c in convos}
    assert by_id["C1"]["is_multiturn"] is True
    assert by_id["C2"]["is_multiturn"] is False
    assert by_id["C1"]["pending_review"] == 2   # 턴 평가 2건 미검수


def test_get_conversation_with_turns(store):
    store.upsert_conversation("C1", "acme", "S1", turns=[
        {"role": "user", "content": "요금 알려줘"}, {"role": "bot", "content": "6만원입니다"},
    ])
    store.upsert_evaluations("run1", [_rec("e1", "C1", turn=1)])
    d = store.get_conversation("C1")
    assert d is not None
    assert len(d["turns"]) == 2
    assert d["turns"][0]["role"] == "user"
    assert len(d["turn_evaluations"]) == 1


def test_list_tenants(store):
    store.upsert_conversation("C1", "acme", "S1", turns=[{"role": "bot", "content": "a"}])
    store.upsert_conversation("C2", "globex", "S2", turns=[{"role": "bot", "content": "b"}])
    store.upsert_evaluations("run1", [_rec("e1", "C1", tenant="acme")])
    tenants = {t["tenant_id"]: t for t in store.list_tenants()}
    assert set(tenants) == {"acme", "globex"}
    assert tenants["acme"]["conversation_count"] == 1
    assert tenants["acme"]["pending_review_count"] == 1


def test_record_human_review_updates_final(store):
    store.upsert_evaluations("run1", [_rec("e1", "C1", faith=0.3)])
    ok = store.record_human_review(
        "e1", status="overridden", human_scores={"faithfulness": 0.1},
        labels=["환각"], comment="명백한 환각", reviewer="qa_kim",
    )
    assert ok
    rec = store.get_evaluation("run1", "e1")
    assert rec["review_status"] == "overridden"
    assert rec["human_scores"]["faithfulness"] == pytest.approx(0.1)
    assert rec["reviewer"] == "qa_kim"
    # final_score = human 우선
    faith = next(s for s in rec["scores"] if s["metric"] == "faithfulness")
    assert faith["final_score"] == pytest.approx(0.1)
    assert faith["score"] == pytest.approx(0.3)   # 원본 자동점수 보존


def test_review_queue_prioritizes_low_confidence(store):
    store.upsert_evaluations("run1", [
        _rec("e_hi", "C1", conf=0.9),
        _rec("e_lo", "C2", conf=0.2),
        _rec("e_mid", "C3", conf=0.5),
    ])
    q = store.review_queue(tenant_id="acme")
    assert [r["eval_id"] for r in q][0] == "e_lo"   # 최저 신뢰도 우선


def test_review_queue_excludes_reviewed(store):
    store.upsert_evaluations("run1", [_rec("e1", "C1"), _rec("e2", "C2")])
    store.record_human_review("e1", status="approved")
    q = store.review_queue(tenant_id="acme")
    assert {r["eval_id"] for r in q} == {"e2"}      # 확정된 e1 제외
