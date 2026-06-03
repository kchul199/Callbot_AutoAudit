"""
cp3_retrieval/retriever.py
HyDE + Multi-Query + BM25 Hybrid + Cross-Encoder Reranking
"""
from __future__ import annotations

from typing import Any

from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.llm_client import LLMProvider
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import RetrievalResult, RetrievedContext
from AutoAudit.app.cp2_knowledge_base.indexer import KnowledgeBaseIndexer

logger = get_logger(__name__)


class HybridRetriever:
    """
    Recall 최적화: HyDE → Multi-Query → Dense + BM25 Hybrid → Cross-Encoder Rerank
    """

    def __init__(self, indexer: KnowledgeBaseIndexer, provider: LLMProvider | None = None) -> None:
        self.indexer = indexer
        self.provider: LLMProvider = provider or indexer.provider

        self.top_k: int = cfg_get("cp3.top_k", default=20)
        self.top_n: int = cfg_get("cp3.top_n", default=5)
        self.hyde_enabled: bool = cfg_get("cp3.hyde_enabled", default=True)
        self.multi_query_count: int = cfg_get("cp3.multi_query_count", default=3)
        self.bm25_weight: float = cfg_get("cp3.bm25_weight", default=0.3)
        self.dense_weight: float = cfg_get("cp3.dense_weight", default=0.7)
        self.reranker_model: str = cfg_get(
            "cp3.reranker_model", default="cross-encoder/ms-marco-MiniLM-L-6-v2"
        )

        self.rrf_k: int = cfg_get("cp3.rrf_k", default=60)  # RRF 상수

        # mock 모드(로컬 개발) → sentence-transformers/rank-bm25 없이 동작
        import os
        self.mock_mode: bool = (
            os.environ.get("AUTOAUDIT_MOCK") == "1"
            or type(self.provider).__name__ == "MockProvider"
        )

        self._reranker: Any | None = None
        self._bm25: Any | None = None
        self._bm25_ids: list[str] = []
        self._bm25_docs: list[str] = []
        self._bm25_meta: list[dict] = []

    @property
    def reranker(self) -> Any:
        if self._reranker is None:
            from sentence_transformers import CrossEncoder  # 지연 import

            logger.info(f"Loading reranker: {self.reranker_model}")
            self._reranker = CrossEncoder(self.reranker_model)
        return self._reranker

    def _ensure_bm25(self) -> None:
        """ChromaDB 전체 child 문서로 BM25 희소 인덱스 구축 (1회)"""
        if self._bm25 is not None:
            return

        data = self.indexer.collection.get(include=["documents", "metadatas"])
        self._bm25_ids = data.get("ids", []) or []
        self._bm25_docs = data.get("documents", []) or []
        self._bm25_meta = data.get("metadatas", []) or []
        tokenized = [self._tokenize(d) for d in self._bm25_docs]

        if self.mock_mode:
            # rank-bm25 없이 stdlib TF 기반 경량 점수기
            self._bm25 = _SimpleBM25(tokenized)
        else:
            from rank_bm25 import BM25Okapi  # 지연 import
            self._bm25 = BM25Okapi(tokenized) if tokenized else None
        logger.info(f"BM25 index built over {len(self._bm25_docs)} docs (mock={self.mock_mode})")

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """간단 토크나이저 (한국어: 공백 + 2-gram 보강)"""
        words = text.lower().split()
        bigrams = [text[i : i + 2] for i in range(len(text) - 1) if text[i : i + 2].strip()]
        return words + bigrams

    # ----------------------------------------------------------
    # 진입점
    # ----------------------------------------------------------

    async def retrieve(self, query: str) -> RetrievalResult:
        """단일 질의 → RetrievalResult"""
        # 1. HyDE: 가상 답변 생성
        hyde_query: str | None = None
        if self.hyde_enabled:
            hyde_query = await self._generate_hyde(query)
            logger.debug(f"HyDE query: {hyde_query[:80]}...")

        # 2. Multi-Query: 다각도 질의 확장
        sub_queries = await self._generate_sub_queries(query)

        # 3. 모든 질의 수집
        all_queries = [query]
        if hyde_query:
            all_queries.append(hyde_query)
        all_queries.extend(sub_queries)

        # 4. Dense + BM25 Hybrid 검색 → 가중 RRF 융합
        ctx_store: dict[str, RetrievedContext] = {}   # chunk_id → context
        fused: dict[str, float] = {}                  # chunk_id → 융합 점수

        for q in all_queries:
            dense = await self._dense_search(q)
            sparse = self._bm25_search(q)

            for ctx in dense:
                ctx_store.setdefault(ctx.chunk_id, ctx)
            for ctx in sparse:
                if ctx.chunk_id in ctx_store:
                    ctx_store[ctx.chunk_id].bm25_score = ctx.bm25_score
                else:
                    ctx_store[ctx.chunk_id] = ctx

            # 랭크 기반 RRF (점수 스케일 차이에 강건)
            self._accumulate_rrf(fused, dense, self.dense_weight)
            self._accumulate_rrf(fused, sparse, self.bm25_weight)

        # 융합 점수 상위 top_k만 reranker로 전달
        ranked_ids = sorted(fused, key=fused.get, reverse=True)[: self.top_k]
        candidates = [ctx_store[cid] for cid in ranked_ids]
        for cid in ranked_ids:
            ctx_store[cid].score = fused[cid]  # 융합 점수 임시 기록
        logger.info(f"Hybrid candidates before reranking: {len(candidates)}")

        # 5. Cross-Encoder Reranking
        reranked = self._rerank(query, candidates)

        return RetrievalResult(
            query=query,
            hyde_query=hyde_query,
            sub_queries=sub_queries,
            contexts=reranked[: self.top_n],
        )

    def _accumulate_rrf(
        self, fused: dict, ranked: list[RetrievedContext], weight: float
    ) -> None:
        """가중 Reciprocal Rank Fusion: weight / (k + rank)"""
        for rank, ctx in enumerate(ranked):
            fused[ctx.chunk_id] = fused.get(ctx.chunk_id, 0.0) + weight / (self.rrf_k + rank)

    # ----------------------------------------------------------
    # HyDE
    # ----------------------------------------------------------

    async def _generate_hyde(self, query: str) -> str:
        """질의에 대한 가상(hypothetical) 이상적 답변 생성"""
        prompt = (
            "다음 질문에 대해 콜봇 FAQ/매뉴얼에서 발췌한 것처럼 이상적인 답변을 생성하세요. "
            "실제 문서가 있다고 가정하고, 구체적이고 사실적으로 작성하세요.\n\n"
            f"질문: {query}\n\n답변:"
        )
        return await self.provider.complete(prompt, temperature=0.3, max_tokens=300)

    # ----------------------------------------------------------
    # Multi-Query
    # ----------------------------------------------------------

    async def _generate_sub_queries(self, query: str) -> list[str]:
        """다각도 질의 변형 생성"""
        prompt = (
            f"다음 질문을 검색 성능 향상을 위해 {self.multi_query_count}가지 다른 표현으로 재작성하세요. "
            "각 질문은 줄바꿈으로 구분하세요.\n\n"
            f"원본 질문: {query}\n\n재작성된 질문들:"
        )
        raw = await self.provider.complete(prompt, temperature=0.5, max_tokens=200)
        return [line.strip().lstrip("0123456789.-) ") for line in raw.splitlines() if line.strip()]

    # ----------------------------------------------------------
    # Dense 검색
    # ----------------------------------------------------------

    async def _dense_search(self, query: str) -> list[RetrievedContext]:
        embedding = await self.indexer.embed_query(query)
        results = self.indexer.collection.query(
            query_embeddings=[embedding],
            n_results=self.top_k,
            include=["documents", "metadatas", "distances"],
        )

        contexts: list[RetrievedContext] = []
        for i, (doc, meta, dist) in enumerate(
            zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
                strict=False,
            )
        ):
            dense_score = 1.0 - float(dist)  # cosine distance → similarity
            contexts.append(
                RetrievedContext(
                    chunk_id=results["ids"][0][i],
                    content=doc,
                    score=dense_score,
                    dense_score=dense_score,
                    source_call_id=meta.get("source_call_id", ""),
                    metadata=meta,
                )
            )
        return contexts

    # ----------------------------------------------------------
    # BM25 희소 검색
    # ----------------------------------------------------------

    def _bm25_search(self, query: str) -> list[RetrievedContext]:
        """BM25 상위 top_k 반환 (빈 인덱스면 빈 리스트)"""
        self._ensure_bm25()
        if not self._bm25:
            return []

        scores = self._bm25.get_scores(self._tokenize(query))
        # numpy 없이 정렬 (mock 모드 호환)
        top_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[: self.top_k]
        contexts: list[RetrievedContext] = []
        for i in top_idx:
            if scores[i] <= 0:
                continue
            meta = self._bm25_meta[i] if i < len(self._bm25_meta) else {}
            contexts.append(
                RetrievedContext(
                    chunk_id=self._bm25_ids[i],
                    content=self._bm25_docs[i],
                    score=float(scores[i]),
                    bm25_score=float(scores[i]),
                    source_call_id=meta.get("source_call_id", ""),
                    metadata=meta,
                )
            )
        return contexts

    # ----------------------------------------------------------
    # Cross-Encoder Reranking
    # ----------------------------------------------------------

    def _rerank(self, query: str, candidates: list[RetrievedContext]) -> list[RetrievedContext]:
        if not candidates:
            return []
        # mock 모드: Cross-Encoder 없이 토큰 중첩 점수로 재정렬
        if self.mock_mode:
            q_tokens = set(self._tokenize(query))
            for ctx in candidates:
                c_tokens = set(self._tokenize(ctx.content))
                overlap = len(q_tokens & c_tokens) / (len(q_tokens) or 1)
                ctx.score = overlap
            return sorted(candidates, key=lambda x: x.score, reverse=True)
        pairs = [(query, c.content) for c in candidates]
        scores = self.reranker.predict(pairs)
        for ctx, score in zip(candidates, scores, strict=False):
            ctx.score = float(score)
        return sorted(candidates, key=lambda x: x.score, reverse=True)


# ============================================================
# 경량 BM25 (mock 모드 — rank-bm25/numpy 미설치 시 폴백)
# ============================================================

class _SimpleBM25:
    """stdlib만 사용하는 최소 BM25 근사 (TF-IDF 가중)."""

    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        import math
        self.k1, self.b = k1, b
        self.corpus = corpus_tokens
        self.n = len(corpus_tokens)
        self.avgdl = (sum(len(d) for d in corpus_tokens) / self.n) if self.n else 0.0
        # document frequency
        self.df: dict[str, int] = {}
        for doc in corpus_tokens:
            for term in set(doc):
                self.df[term] = self.df.get(term, 0) + 1
        self.idf = {
            t: math.log(1 + (self.n - f + 0.5) / (f + 0.5)) for t, f in self.df.items()
        }

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        scores = []
        for doc in self.corpus:
            dl = len(doc) or 1
            tf: dict[str, int] = {}
            for t in doc:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            for q in query_tokens:
                if q not in tf:
                    continue
                idf = self.idf.get(q, 0.0)
                num = tf[q] * (self.k1 + 1)
                den = tf[q] + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                s += idf * num / den
            scores.append(s)
        return scores
