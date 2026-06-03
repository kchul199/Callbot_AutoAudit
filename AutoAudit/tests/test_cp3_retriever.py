"""
tests/test_cp3_retriever.py
CP3 Hybrid 융합(RRF) + 토크나이저 순수 로직 검증
(chromadb/rank-bm25 미설치 환경에서도 동작하도록 순수 메서드만 테스트)
"""
from AutoAudit.app.core.types import RetrievedContext
from AutoAudit.app.cp3_retrieval.retriever import HybridRetriever


def _make_retriever() -> HybridRetriever:
    """__init__(외부 의존성) 우회용 경량 인스턴스"""
    r = object.__new__(HybridRetriever)
    r.rrf_k = 60
    r.dense_weight = 0.7
    r.bm25_weight = 0.3
    return r


def _ctx(cid: str) -> RetrievedContext:
    return RetrievedContext(chunk_id=cid, content="x", score=0.0, source_call_id="C1")


def test_tokenize_includes_words_and_bigrams():
    tokens = HybridRetriever._tokenize("요금제 변경")
    assert "요금제" in tokens          # 단어
    assert "요금" in tokens            # 2-gram
    assert any(len(t) == 2 for t in tokens)


def test_rrf_accumulation_basic():
    r = _make_retriever()
    fused: dict[str, float] = {}
    dense = [_ctx("a"), _ctx("b"), _ctx("c")]
    r._accumulate_rrf(fused, dense, r.dense_weight)
    # rank 0 점수 > rank 1 > rank 2
    assert fused["a"] > fused["b"] > fused["c"]
    assert fused["a"] == 0.7 / 60


def test_rrf_fusion_combines_dense_and_sparse():
    r = _make_retriever()
    fused: dict[str, float] = {}
    dense = [_ctx("a"), _ctx("b")]      # dense는 a를 1위로
    sparse = [_ctx("b"), _ctx("a")]     # bm25는 b를 1위로
    r._accumulate_rrf(fused, dense, r.dense_weight)
    r._accumulate_rrf(fused, sparse, r.bm25_weight)
    # 양쪽에 등장한 a, b 모두 점수 누적됨
    assert "a" in fused and "b" in fused
    # dense_weight=0.7 우세 → dense 1위 a가 b보다 높아야 함
    assert fused["a"] > fused["b"]


def test_rrf_rank_decay():
    r = _make_retriever()
    fused: dict[str, float] = {}
    ranked = [_ctx(f"c{i}") for i in range(5)]
    r._accumulate_rrf(fused, ranked, 1.0)
    scores = [fused[f"c{i}"] for i in range(5)]
    assert scores == sorted(scores, reverse=True)  # 단조 감소
