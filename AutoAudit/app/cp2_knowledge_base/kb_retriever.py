"""
cp2_knowledge_base/kb_retriever.py
고객사 구축 KB(`kb_documents`) → 검색기(HybridRetriever) 빌드.

대화 품질 검증 시 "고객사 지식 문서"를 검색 근거로 삼기 위한 연결 고리.
kb_documents의 원문을 CP2 child 청크로 분할 → 임베딩 → 인덱싱 → HybridRetriever 반환.

설계:
  - 기존 ParentChildChunker(_split_text) · KnowledgeBaseIndexer · HybridRetriever 재사용
  - mock 모드: KnowledgeBaseIndexer가 InMemoryVectorStore를 생성 → 테넌트별 격리(인스턴스 단위)
  - 평가 시점에 온디맨드로 빌드 (SQLite kb_documents가 단일 진실원천)
  - 운영(ChromaDB): 테넌트별 컬렉션 분리 권장 (collection_name=f"kb_{tenant}")
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import Chunk, ChunkLevel
from AutoAudit.app.cp2_knowledge_base.chunker import ParentChildChunker
from AutoAudit.app.cp2_knowledge_base.indexer import KnowledgeBaseIndexer
from AutoAudit.app.cp3_retrieval.retriever import HybridRetriever

if TYPE_CHECKING:
    from AutoAudit.app.core.llm_client import LLMProvider
    from AutoAudit.app.core.store import ResultStore

logger = get_logger(__name__)


def _docs_to_chunks(docs: list[dict]) -> list[Chunk]:
    """KB 문서 원문 → CP2 child 청크 목록 (검색 인덱싱 단위)."""
    child_size = cfg_get("cp2.child_chunk_size", default=200)
    overlap = cfg_get("cp2.chunk_overlap", default=50)
    chunks: list[Chunk] = []
    for d in docs:
        content = (d.get("content") or "").strip()
        if not content:
            continue
        # 출처 표기: "유형:제목" → Evidence View에서 어떤 KB 문서인지 식별
        source = f"{d.get('source_type', '문서')}:{d.get('title', d.get('doc_id', 'KB'))}"
        for piece in ParentChildChunker._split_text(content, child_size, overlap):
            chunks.append(
                Chunk(
                    chunk_id=str(uuid.uuid4()),
                    source_call_id=source,
                    level=ChunkLevel.CHILD,
                    content=piece,
                    token_count=len(piece.split()),
                    metadata={"doc_id": d.get("doc_id"), "title": d.get("title"),
                              "source_type": d.get("source_type")},
                )
            )
    return chunks


async def build_kb_retriever(
    tenant_id: str, store: ResultStore, provider: LLMProvider,
) -> HybridRetriever | None:
    """
    테넌트 구축 KB로 HybridRetriever를 빌드. KB 문서가 없으면 None.

    검증 파이프라인이 이 검색기로 각 질문에 대한 근거 컨텍스트를 가져온다.
    """
    docs = store.kb_documents_for_index(tenant_id)
    if not docs:
        logger.warning(f"[kb_retriever] tenant={tenant_id} 구축 KB 문서 없음")
        return None

    chunks = _docs_to_chunks(docs)
    if not chunks:
        return None

    indexer = KnowledgeBaseIndexer(provider=provider)  # mock → InMemoryVectorStore (테넌트 격리)
    await indexer.index_chunks(chunks)
    logger.info(
        f"[kb_retriever] tenant={tenant_id} — 문서 {len(docs)}개 → child 청크 {len(chunks)}개 인덱싱"
    )
    return HybridRetriever(indexer, provider=provider)
