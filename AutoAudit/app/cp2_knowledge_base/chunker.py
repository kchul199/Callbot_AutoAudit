"""
cp2_knowledge_base/chunker.py
Parent-Child 청킹 전략 구현
"""
from __future__ import annotations

import uuid

from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import CallLog, Chunk, ChunkLevel

logger = get_logger(__name__)


class ParentChildChunker:
    """
    Parent-Child 청킹:
      - Parent: 큰 의미 단위 (세션/주제 블록) → 컨텍스트 보존
      - Child : 작은 검색 단위 → Precision 향상
    검색 시 Child 단위로 매칭 → Parent로 컨텍스트 확장
    """

    def __init__(self) -> None:
        self.parent_size: int = cfg_get("cp2.parent_chunk_size", default=1000)
        self.child_size: int = cfg_get("cp2.child_chunk_size", default=200)
        self.overlap: int = cfg_get("cp2.chunk_overlap", default=50)

    def chunk(self, call_log: CallLog) -> list[Chunk]:
        """CallLog → Parent + Child Chunk 목록 반환"""
        full_text = self._build_full_text(call_log)
        parents = self._split_text(full_text, self.parent_size, self.overlap)
        chunks: list[Chunk] = []

        for p_text in parents:
            parent_id = str(uuid.uuid4())
            chunks.append(
                Chunk(
                    chunk_id=parent_id,
                    source_call_id=call_log.call_id,
                    level=ChunkLevel.PARENT,
                    content=p_text,
                    token_count=self._approx_tokens(p_text),
                    metadata={"subscriber_id": call_log.subscriber_id},
                )
            )

            children = self._split_text(p_text, self.child_size, self.overlap)
            for c_text in children:
                chunks.append(
                    Chunk(
                        chunk_id=str(uuid.uuid4()),
                        source_call_id=call_log.call_id,
                        level=ChunkLevel.CHILD,
                        parent_chunk_id=parent_id,
                        content=c_text,
                        token_count=self._approx_tokens(c_text),
                        metadata={"subscriber_id": call_log.subscriber_id},
                    )
                )

        logger.info(
            f"[{call_log.call_id}] Chunked → "
            f"{sum(1 for c in chunks if c.level == ChunkLevel.PARENT)} parents, "
            f"{sum(1 for c in chunks if c.level == ChunkLevel.CHILD)} children"
        )
        return chunks

    # ----------------------------------------------------------
    # 내부 유틸
    # ----------------------------------------------------------

    @staticmethod
    def _build_full_text(call_log: CallLog) -> str:
        """대화 턴을 하나의 텍스트 블록으로 조합"""
        parts = []
        for turn in call_log.turns:
            prefix = f"[{turn.role.upper()}]"
            parts.append(f"{prefix} {turn.content}")
        return "\n".join(parts)

    @staticmethod
    def _split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
        """단순 토큰 기반 슬라이딩 윈도우 분할 (단어 단위)"""
        words = text.split()
        chunks: list[str] = []
        start = 0
        while start < len(words):
            end = min(start + chunk_size, len(words))
            chunks.append(" ".join(words[start:end]))
            if end >= len(words):
                break
            start += chunk_size - overlap
        return chunks

    @staticmethod
    def _approx_tokens(text: str) -> int:
        """단어 수 기반 근사 토큰 수"""
        return len(text.split())
