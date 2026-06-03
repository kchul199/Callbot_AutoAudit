"""
tests/test_cp4_judge.py
CP4 LLM-as-a-Judge 점수 파싱 / 구조 검증 (LLM 호출 없이 Mock 기반)
"""
import json
from unittest.mock import AsyncMock

import pytest

from AutoAudit.app.core.types import MetricScore, RetrievalResult, RetrievedContext
from AutoAudit.app.cp4_evaluator.judge import LLMJudge


class FakeProvider:
    """LLMProvider 프로토콜을 만족하는 테스트용 가짜 provider"""

    def __init__(self, score: float = 0.9, reasoning: str = "컨텍스트와 일치합니다."):
        self._payload = json.dumps(
            {"score": score, "reasoning": reasoning, "grounding_chunks": []}
        )
        self.complete = AsyncMock(return_value=self._payload)

    async def embed(self, text: str):
        return [0.0] * 8


@pytest.fixture
def sample_retrieval_result() -> RetrievalResult:
    return RetrievalResult(
        query="요금제 변경 방법은?",
        contexts=[
            RetrievedContext(
                chunk_id="chunk_001",
                content="요금제 변경은 고객센터 앱에서 직접 변경하거나 대리점 방문을 통해 가능합니다.",
                score=0.9,
                source_call_id="C001",
            )
        ],
    )


def test_parse_score_valid():
    """유효한 JSON → MetricScore 파싱"""
    raw = json.dumps({
        "score": 0.85,
        "reasoning": "답변이 컨텍스트에 충실합니다.",
        "grounding_chunks": ["chunk_001"]
    })
    result = LLMJudge._parse_score(raw, "faithfulness")
    assert isinstance(result, MetricScore)
    assert result.score == pytest.approx(0.85)
    assert result.metric == "faithfulness"
    assert "chunk_001" in result.grounding_chunks


def test_parse_score_invalid_json():
    """JSON 파싱 실패 시 score=0.0 반환 (파이프라인 중단 방지)"""
    result = LLMJudge._parse_score("INVALID JSON", "faithfulness")
    assert result.score == 0.0
    assert "Parse error" in result.reasoning


def test_format_contexts(sample_retrieval_result):
    """컨텍스트 포맷팅 — chunk_id 포함 확인"""
    text = LLMJudge._format_contexts(sample_retrieval_result)
    assert "chunk_001" in text
    assert "요금제 변경" in text


def test_format_contexts_empty():
    """컨텍스트 없을 때 fallback 문자열"""
    empty = RetrievalResult(query="test", contexts=[])
    text = LLMJudge._format_contexts(empty)
    assert "없음" in text


@pytest.mark.asyncio
async def test_evaluate_returns_record(sample_retrieval_result):
    """FakeProvider로 evaluate() 호출 구조 검증"""
    judge = LLMJudge(provider=FakeProvider(score=0.9))
    record = await judge.evaluate(
        call_id="C001",
        query="요금제 변경 방법은?",
        generated_answer="고객센터 앱에서 변경 가능합니다.",
        retrieval_result=sample_retrieval_result,
    )

    assert record.call_id == "C001"
    assert len(record.scores) == len(judge.metrics)
    for score in record.scores:
        assert 0.0 <= score.score <= 1.0


@pytest.mark.asyncio
async def test_multi_sample_aggregation(sample_retrieval_result):
    """다중 샘플 → 중앙값/신뢰도 산출 검증 (모든 샘플 동일 → 고신뢰)"""
    judge = LLMJudge(provider=FakeProvider(score=0.8))
    # claim 분해는 별도 테스트 → 여기선 다중샘플 집계 경로만 검증
    judge.use_claim_faithfulness = False
    record = await judge.evaluate(
        call_id="C002",
        query="질문?",
        generated_answer="답변",
        retrieval_result=sample_retrieval_result,
    )
    for score in record.scores:
        assert score.score == pytest.approx(0.8)
        assert score.confidence == pytest.approx(1.0)   # 분산 0
        assert score.is_low_confidence is False
        assert len(score.sample_scores) == judge.n_samples


def test_evaluate_pair_requires_retrieval():
    """retrieval_result 없는 QAPair는 평가 거부 (동기 래퍼로 플러그인 우회)"""
    import asyncio

    from AutoAudit.app.core.types import QAPair

    judge = LLMJudge(provider=FakeProvider())
    pair = QAPair(
        qa_id="q1", call_id="C001", subscriber_id="S1",
        question="질문?", bot_answer="답변", turn_index=0,
    )
    with pytest.raises(ValueError):
        asyncio.run(judge.evaluate_pair(pair))
