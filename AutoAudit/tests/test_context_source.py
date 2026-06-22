"""
tests/test_context_source.py
#1 평가 컨텍스트 출처 분리 검증.

봇이 실제로 본 RAG 트레이스(provided_context)가 있으면 faithfulness 는 그것을 근거로,
검색 품질 메트릭(context_recall/precision)은 감사기 재검색 컨텍스트를 근거로 평가하는지 확인.
"""
import json
from unittest.mock import AsyncMock

import pytest

from AutoAudit.app.core.types import (
    CallLog,
    ConversationTurn,
    QAPair,
    RetrievalResult,
    RetrievedContext,
    TurnRole,
)
from AutoAudit.app.cp1_preprocessing.parser import CallLogParser
from AutoAudit.app.cp4_evaluator.judge import LLMJudge
from AutoAudit.app.cp4_evaluator.qa_builder import QAPairBuilder


class FakeProvider:
    def __init__(self, score: float = 0.9):
        self._payload = json.dumps(
            {"score": score, "reasoning": "ok", "grounding_chunks": []}
        )
        self.complete = AsyncMock(return_value=self._payload)

    async def embed(self, text: str):
        return [0.0] * 8


# ----------------------------------------------------------------
# 1) CP1 파서: 봇 턴의 contexts 적재
# ----------------------------------------------------------------

def test_parser_loads_bot_contexts(tmp_path):
    log_path = tmp_path / "call.json"
    log_path.write_text(
        json.dumps({
            "call_id": "C001",
            "subscriber_id": "SUB_01",
            "turns": [
                {"role": "user", "content": "요금제 가격이 얼마인가요"},
                {
                    "role": "bot",
                    "content": "월 3만원입니다.",
                    "contexts": ["요금제 A는 월 3만원입니다."],
                },
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    log = CallLogParser().parse_file(log_path)
    bot_turn = next(t for t in log.turns if t.role == TurnRole.BOT)
    assert bot_turn.contexts == ["요금제 A는 월 3만원입니다."]


# ----------------------------------------------------------------
# 2) qa_builder: provided_context / context_source 구성
# ----------------------------------------------------------------

def _log_with_contexts(bot_contexts: list[str]) -> CallLog:
    return CallLog(
        call_id="C001", subscriber_id="SUB_01",
        turns=[
            ConversationTurn(turn_id=0, role=TurnRole.USER, content="요금제 가격이 얼마인가요"),
            ConversationTurn(turn_id=1, role=TurnRole.BOT, content="월 3만원입니다 안내드립니다.", contexts=bot_contexts),
        ],
    )


def test_qa_builder_bot_trace():
    pairs = QAPairBuilder().extract_pairs(_log_with_contexts(["요금제 A는 월 3만원입니다."]))
    assert len(pairs) == 1
    assert pairs[0].context_source == "bot_trace"
    assert pairs[0].provided_context is not None
    assert "3만원" in pairs[0].provided_context.contexts[0].content


def test_qa_builder_auditor_fallback():
    pairs = QAPairBuilder().extract_pairs(_log_with_contexts([]))
    assert len(pairs) == 1
    assert pairs[0].context_source == "auditor"
    assert pairs[0].provided_context is None


# ----------------------------------------------------------------
# 3) judge: faithfulness=봇 컨텍스트, recall/precision=감사기 컨텍스트
# ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_judge_uses_bot_context_for_faithfulness():
    auditor = RetrievalResult(
        query="요금제 가격?",
        contexts=[RetrievedContext(chunk_id="aud", content="AUDITOR_CTX 월 5만원", score=0.9, source_call_id="C001")],
    )
    bot_trace = RetrievalResult(
        query="요금제 가격?",
        contexts=[RetrievedContext(chunk_id="bot", content="BOTTRACE_CTX 월 3만원", score=1.0, source_call_id="C001")],
    )
    judge = LLMJudge(provider=FakeProvider(score=0.9))
    judge.use_claim_faithfulness = False  # 기본 샘플링 경로로 프롬프트 검사 단순화
    judge.n_samples = 1

    record = await judge.evaluate(
        call_id="C001",
        query="요금제 가격?",
        generated_answer="월 3만원입니다.",
        retrieval_result=auditor,
        provided_context=bot_trace,
    )

    assert record.context_source == "bot_trace"

    # 호출 프롬프트 수집
    prompts = [c.args[0] if c.args else c.kwargs.get("prompt", "") for c in judge.provider.complete.call_args_list]
    joined = "\n".join(prompts)
    # 봇 트레이스 컨텍스트가 faithfulness 평가에 사용됨
    assert any("BOTTRACE_CTX" in p for p in prompts)
    # 감사기 컨텍스트는 검색 품질 메트릭에 사용됨
    assert "AUDITOR_CTX" in joined


@pytest.mark.asyncio
async def test_judge_auditor_when_no_bot_trace():
    auditor = RetrievalResult(
        query="q?",
        contexts=[RetrievedContext(chunk_id="aud", content="AUDITOR_CTX 내용", score=0.9, source_call_id="C001")],
    )
    judge = LLMJudge(provider=FakeProvider(score=0.9))
    judge.use_claim_faithfulness = False
    judge.n_samples = 1
    record = await judge.evaluate(
        call_id="C001", query="q?", generated_answer="답변", retrieval_result=auditor,
    )
    assert record.context_source == "auditor"


@pytest.mark.asyncio
async def test_evaluate_pair_propagates_context_source():
    bot_trace = RetrievalResult(
        query="q?",
        contexts=[RetrievedContext(chunk_id="bot", content="BOTTRACE_CTX", score=1.0, source_call_id="C001")],
    )
    auditor = RetrievalResult(
        query="q?",
        contexts=[RetrievedContext(chunk_id="aud", content="AUDITOR_CTX", score=0.9, source_call_id="C001")],
    )
    pair = QAPair(
        qa_id="q1", call_id="C001", subscriber_id="S1",
        question="q?", bot_answer="답변입니다 충분히 길게", turn_index=0,
        retrieval_result=auditor, provided_context=bot_trace, context_source="bot_trace",
    )
    judge = LLMJudge(provider=FakeProvider(score=0.9))
    judge.use_claim_faithfulness = False
    judge.n_samples = 1
    record = await judge.evaluate_pair(pair)
    assert record.context_source == "bot_trace"
    assert record.qa_id == "q1"
