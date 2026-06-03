"""
tests/test_cp2_chunker.py
CP2 청킹 전략 검증 — Parent-Child 구조 무결성
"""
import pytest

from AutoAudit.app.core.types import CallLog, ChunkLevel, ConversationTurn, TurnRole
from AutoAudit.app.cp2_knowledge_base.chunker import ParentChildChunker


@pytest.fixture
def sample_call_log() -> CallLog:
    turns = [
        ConversationTurn(turn_id=i, role=TurnRole.USER if i % 2 == 0 else TurnRole.BOT,
                         content=f"이것은 테스트 대화 턴 {i}입니다. 충분한 길이를 위해 내용을 추가합니다.")
        for i in range(10)
    ]
    return CallLog(call_id="C_TEST", subscriber_id="SUB_TEST", turns=turns)


@pytest.fixture
def chunker() -> ParentChildChunker:
    return ParentChildChunker()


def test_chunk_produces_parent_and_child(chunker, sample_call_log):
    """Parent + Child 청크가 모두 생성되는지 확인"""
    chunks = chunker.chunk(sample_call_log)
    parents = [c for c in chunks if c.level == ChunkLevel.PARENT]
    children = [c for c in chunks if c.level == ChunkLevel.CHILD]
    assert len(parents) >= 1
    assert len(children) >= 1


def test_child_references_parent(chunker, sample_call_log):
    """모든 Child 청크가 유효한 Parent ID를 참조하는지 확인"""
    chunks = chunker.chunk(sample_call_log)
    parent_ids = {c.chunk_id for c in chunks if c.level == ChunkLevel.PARENT}
    for child in [c for c in chunks if c.level == ChunkLevel.CHILD]:
        assert child.parent_chunk_id in parent_ids


def test_chunk_source_call_id(chunker, sample_call_log):
    """모든 청크가 올바른 source_call_id를 갖는지 확인"""
    chunks = chunker.chunk(sample_call_log)
    for chunk in chunks:
        assert chunk.source_call_id == "C_TEST"


def test_chunk_has_positive_token_count(chunker, sample_call_log):
    """모든 청크의 token_count > 0"""
    chunks = chunker.chunk(sample_call_log)
    for chunk in chunks:
        assert chunk.token_count > 0


def test_chunk_ids_are_unique(chunker, sample_call_log):
    """청크 ID 중복 없음"""
    chunks = chunker.chunk(sample_call_log)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
