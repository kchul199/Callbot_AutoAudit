"""
cp2_knowledge_base/indexer.py
ChromaDB 인덱싱 — text-embedding-3-large 기반
"""
from __future__ import annotations

from AutoAudit.app.core.async_utils import gather_with_concurrency
from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.llm_client import LLMProvider, create_provider
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import Chunk, ChunkLevel

logger = get_logger(__name__)


class KnowledgeBaseIndexer:
    """
    Child 청크만 벡터 DB에 인덱싱 (검색 Precision 최적화).
    Parent 청크는 메타데이터로 연결 → 컨텍스트 확장 시 사용.

    임베딩은 LLMProvider를 통해 수행 (provider 교체 가능).
    """

    def __init__(self, reindex: bool = False, provider: LLMProvider | None = None) -> None:
        self.collection_name: str = cfg_get("cp2.collection_name", default="callbot_kb")
        self.db_path: str = cfg_get("paths.chroma_db", default="data/chroma_db")
        self.reindex = reindex

        self.provider: LLMProvider = provider or create_provider()

        # mock 모드(로컬 개발) → chromadb 없이 In-Memory 스토어 사용
        import os
        is_mock = (
            os.environ.get("AUTOAUDIT_MOCK") == "1"
            or cfg_get("llm.provider", default="openai").lower() == "mock"
            or type(self.provider).__name__ == "MockProvider"
        )
        if is_mock:
            from AutoAudit.app.core.mock_provider import InMemoryVectorStore
            self._chroma = None
            self._collection = InMemoryVectorStore()
            logger.info("Using In-Memory vector store (mock mode)")
        else:
            import chromadb  # 지연 import (테스트/경량 사용 시 미설치 허용)
            self._chroma = chromadb.PersistentClient(path=self.db_path)
            self._collection = self._get_or_create_collection()

    def _get_or_create_collection(self):
        if self.reindex:
            try:
                self._chroma.delete_collection(self.collection_name)
                logger.info(f"Existing collection '{self.collection_name}' deleted (--reindex).")
            except Exception:
                pass

        return self._chroma.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    # ----------------------------------------------------------
    # 진입점
    # ----------------------------------------------------------

    async def index_chunks(self, chunks: list[Chunk]) -> int:
        """Child 청크만 필터 후 임베딩 + 인덱싱"""
        child_chunks = [c for c in chunks if c.level == ChunkLevel.CHILD]
        if not child_chunks:
            logger.warning("No child chunks to index.")
            return 0

        logger.info(f"Embedding {len(child_chunks)} child chunks...")
        embeddings = await self._embed_batch(child_chunks)

        ids = [c.chunk_id for c in child_chunks]
        documents = [c.content for c in child_chunks]
        metadatas = [
            {
                **c.metadata,
                "source_call_id": c.source_call_id,
                "parent_chunk_id": c.parent_chunk_id or "",
                "token_count": c.token_count,
            }
            for c in child_chunks
        ]

        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        logger.info(f"Indexed {len(child_chunks)} chunks into '{self.collection_name}'.")
        return len(child_chunks)

    # ----------------------------------------------------------
    # 임베딩
    # ----------------------------------------------------------

    async def _embed_batch(self, chunks: list[Chunk]) -> list[list[float]]:
        """청크 목록을 배치 임베딩 (concurrency 제한)"""
        coros = [self.provider.embed(c.content) for c in chunks]
        return await gather_with_concurrency(coros, concurrency=10)

    async def embed_query(self, text: str) -> list[float]:
        """단일 질의 임베딩 (CP3에서 호출)"""
        return await self.provider.embed(text)

    @property
    def collection(self):
        return self._collection
