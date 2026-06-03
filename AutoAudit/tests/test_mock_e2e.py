"""
tests/test_mock_e2e.py
로컬 mock 모드 — MockProvider / InMemoryVectorStore / CP1~CP5 end-to-end 검증.
API 키·chromadb·sentence-transformers 없이 전체 파이프라인이 도는지 확인.
"""
import asyncio

import pytest

from AutoAudit.app.core.mock_provider import (
    InMemoryVectorStore,
    MockProvider,
    deterministic_embedding,
)

# ---- 결정적 임베딩 ----

def test_embedding_deterministic():
    a = deterministic_embedding("요금제 변경")
    b = deterministic_embedding("요금제 변경")
    assert a == b
    assert len(a) == 64
    # L2 정규화 확인
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6


def test_embedding_differs_by_text():
    assert deterministic_embedding("요금제") != deterministic_embedding("인터넷")


# ---- In-Memory 벡터 스토어 ----

def test_inmemory_store_upsert_and_query():
    store = InMemoryVectorStore()
    store.upsert(
        ids=["c1", "c2"],
        embeddings=[deterministic_embedding("요금제 변경 방법"),
                    deterministic_embedding("인터넷 속도 문제")],
        documents=["요금제 변경은 앱에서", "인터넷 속도 측정"],
        metadatas=[{"source_call_id": "C1"}, {"source_call_id": "C2"}],
    )
    res = store.query([deterministic_embedding("요금제 변경")], n_results=2)
    assert res["ids"][0][0] == "c1"          # 가장 유사한 문서가 1위
    assert len(res["documents"][0]) == 2
    assert "distances" in res


def test_inmemory_store_get():
    store = InMemoryVectorStore()
    store.add(ids=["x"], embeddings=[[0.0] * 64], documents=["doc"], metadatas=[{}])
    data = store.get()
    assert data["ids"] == ["x"]


# ---- MockProvider 응답 분기 ----

@pytest.mark.asyncio
async def test_mock_metric_score_json():
    p = MockProvider()
    raw = await p.complete("[질문]\nq\n[답변]\n요금제 변경\n[컨텍스트]\n요금제 변경 안내",
                           json_mode=True)
    import json
    d = json.loads(raw)
    assert 0.0 <= d["score"] <= 1.0


@pytest.mark.asyncio
async def test_mock_claims_and_verdicts():
    import json
    p = MockProvider()
    claims_raw = await p.complete("원자적 주장(claim)을 분해\n[답변]\n요금은 5만원이다. 무제한이다.")
    assert "claims" in json.loads(claims_raw)


# ---- 전체 파이프라인 E2E (mock) ----

def test_full_pipeline_mock(tmp_path, monkeypatch):
    """CP1→CP2→CP3→CP4→CP5 를 mock으로 완주 (네트워크 0)"""
    monkeypatch.setenv("AUTOAUDIT_MOCK", "1")

    # 샘플 콜 로그 작성
    import json

    from AutoAudit.app.core.mock_provider import MockProvider
    from AutoAudit.app.cp1_preprocessing.parser import CallLogParser
    from AutoAudit.app.cp2_knowledge_base.chunker import ParentChildChunker
    from AutoAudit.app.cp2_knowledge_base.indexer import KnowledgeBaseIndexer
    from AutoAudit.app.cp3_retrieval.retriever import HybridRetriever
    from AutoAudit.app.cp4_evaluator.judge import LLMJudge
    from AutoAudit.app.cp4_evaluator.options import EvaluationOptions
    from AutoAudit.app.cp4_evaluator.qa_builder import QAPairBuilder
    from AutoAudit.app.cp5_aggregator.aggregator import ResultAggregator
    sample = {
        "call_id": "C001", "subscriber_id": "S1",
        "turns": [
            {"role": "user", "content": "요금제 변경 방법 알려주세요"},
            {"role": "bot", "content": "요금제 변경은 고객센터 앱에서 직접 가능합니다."},
        ],
    }
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "c001.json").write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

    async def _run():
        provider = MockProvider()
        # CP1
        logs = CallLogParser().parse_directory(raw_dir)
        assert logs
        # CP2
        chunker = ParentChildChunker()
        chunks = []
        for log in logs:
            chunks.extend(chunker.chunk(log))
        indexer = KnowledgeBaseIndexer(reindex=True, provider=provider)
        assert isinstance(indexer._collection, InMemoryVectorStore)
        await indexer.index_chunks(chunks)
        # CP3
        retriever = HybridRetriever(indexer, provider=provider)
        assert retriever.mock_mode is True
        builder = QAPairBuilder(retriever=retriever)
        qa_pairs = await builder.build(logs)
        assert qa_pairs and qa_pairs[0].retrieval_result is not None
        # CP4
        judge = LLMJudge(provider=provider, options=EvaluationOptions())
        records = await judge.evaluate_pairs(qa_pairs)
        assert records and all(r.scores for r in records)
        # CP5
        summary = ResultAggregator().aggregate(records, run_id="mock_run",
                                                options=EvaluationOptions())
        assert summary.total_evaluations == len(records)
        return summary

    summary = asyncio.run(_run())
    assert summary.total_calls == 1
    assert len(summary.metrics) >= 1
