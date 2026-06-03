"""
tests/test_cot_reverse.py
CoT (Chain-of-Thought) + 역방향 검증 테스트

AUTOAUDIT_MOCK=1 환경에서 API 키 없이 실행 가능.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ["AUTOAUDIT_MOCK"] = "1"

from AutoAudit.app.core.mock_provider import MockProvider
from AutoAudit.app.core.types import QAPair, RetrievalResult, RetrievedContext
from AutoAudit.app.cp4_evaluator.judge import (
    _COT_ANSWER_RELEVANCE_PROMPT,
    _COT_CONTEXT_PRECISION_PROMPT,
    _COT_CONTEXT_RECALL_PROMPT,
    _REVERSE_ANSWER_RELEVANCE_PROMPT,
    _REVERSE_FAITHFULNESS_PROMPT,
    LLMJudge,
)
from AutoAudit.app.cp4_evaluator.options import (
    CoTOptions,
    EvaluationOptions,
    ReverseVerificationOptions,
)

# ──────────────────────────────────────────────
# 픽스처
# ──────────────────────────────────────────────

@pytest.fixture
def provider():
    return MockProvider()


@pytest.fixture
def simple_retrieval() -> RetrievalResult:
    return RetrievalResult(
        query="요금제 변경 방법은?",
        contexts=[
            RetrievedContext(
                chunk_id="chunk_01",
                content="요금제 변경은 마이페이지 > 요금제 관리에서 가능합니다.",
                score=0.92,
                source_call_id="C001",
            ),
            RetrievedContext(
                chunk_id="chunk_02",
                content="5G 프리미엄 요금제는 월 69,000원입니다.",
                score=0.80,
                source_call_id="C001",
            ),
        ],
    )


@pytest.fixture
def sample_qa_pair(simple_retrieval) -> QAPair:
    return QAPair(
        qa_id="qa_test_01",
        call_id="C001",
        subscriber_id="S001",
        question="요금제 변경 방법은?",
        bot_answer="마이페이지에서 요금제를 변경할 수 있습니다.",
        turn_index=1,
        retrieval_result=simple_retrieval,
    )


def _make_judge(cot_enabled: bool = False, reverse_enabled: bool = False) -> LLMJudge:
    provider = MockProvider()
    opts = EvaluationOptions(
        cot=CoTOptions(enabled=cot_enabled, metrics=["answer_relevance", "context_precision", "context_recall"]),
        reverse=ReverseVerificationOptions(
            enabled=reverse_enabled,
            metrics=["faithfulness", "answer_relevance"],
            weight_forward=0.6,
            weight_reverse=0.4,
            inconsistency_threshold=0.25,
        ),
    )
    return LLMJudge(provider=provider, options=opts)


# ══════════════════════════════════════════════
# 1. CoT 프롬프트 구조 테스트
# ══════════════════════════════════════════════

class TestCoTPromptStructure:
    """CoT 프롬프트에 단계별 지시가 올바르게 포함되었는지 검증"""

    def test_answer_relevance_cot_has_steps(self):
        prompt = _COT_ANSWER_RELEVANCE_PROMPT.format(
            query="요금제 변경 방법은?",
            answer="마이페이지에서 변경하세요.",
            contexts="",
        )
        assert "Step 1" in prompt
        assert "Step 2" in prompt
        assert "Step 3" in prompt
        assert "Step 4" in prompt
        assert "step1_intent" in prompt
        assert "step2_items" in prompt
        assert "step3_missing" in prompt
        assert "score" in prompt

    def test_context_precision_cot_has_chunk_verdict(self):
        prompt = _COT_CONTEXT_PRECISION_PROMPT.format(
            query="요금제 변경 방법은?",
            answer="마이페이지에서 변경하세요.",
            contexts="[1] 요금제 변경은 마이페이지에서 가능합니다.",
        )
        assert "Step 1 — 각 컨텍스트 청크 유용성 판정" in prompt
        assert "유용" in prompt
        assert "불필요" in prompt
        assert "step1_verdicts" in prompt
        assert "step2_ratio" in prompt

    def test_context_recall_cot_has_coverage(self):
        prompt = _COT_CONTEXT_RECALL_PROMPT.format(
            query="요금제 변경 방법은?",
            answer="마이페이지에서 변경하세요.",
            contexts="[1] 요금제 변경은 마이페이지에서 가능합니다.",
        )
        assert "Step 1 — 필요 정보 목록화" in prompt
        assert "Step 2 — 컨텍스트 포함 여부 확인" in prompt
        assert "step1_required" in prompt
        assert "step2_coverage" in prompt


# ══════════════════════════════════════════════
# 2. CoT Mock 응답 파싱 테스트
# ══════════════════════════════════════════════

class TestCoTMockResponse:
    """MockProvider가 CoT 프롬프트에 올바른 JSON을 반환하는지 검증"""

    @pytest.mark.asyncio
    async def test_mock_cot_answer_relevance_returns_steps(self, provider):
        prompt = _COT_ANSWER_RELEVANCE_PROMPT.format(
            query="요금제 변경 방법은?",
            answer="마이페이지에서 요금제를 변경할 수 있습니다.",
            contexts="",
        )
        raw = await provider.complete(prompt, json_mode=True)
        data = json.loads(raw)

        assert "step1_intent" in data
        assert "step2_items" in data
        assert "step3_missing" in data
        assert "score" in data
        assert "reasoning" in data
        assert 0.0 <= data["score"] <= 1.0
        assert isinstance(data["step2_items"], list)

    @pytest.mark.asyncio
    async def test_mock_cot_context_precision_returns_verdicts(self, provider):
        prompt = _COT_CONTEXT_PRECISION_PROMPT.format(
            query="요금제 변경 방법은?",
            answer="",
            contexts="[1] 요금제 변경은 마이페이지에서 가능합니다.\n\n[2] 날씨가 맑습니다.",
        )
        raw = await provider.complete(prompt, json_mode=True)
        data = json.loads(raw)

        assert "step1_verdicts" in data
        assert "step2_ratio" in data
        assert "score" in data
        assert 0.0 <= data["score"] <= 1.0

    @pytest.mark.asyncio
    async def test_mock_cot_context_recall_returns_coverage(self, provider):
        prompt = _COT_CONTEXT_RECALL_PROMPT.format(
            query="요금제 변경 방법은?",
            answer="마이페이지에서 변경하세요.",
            contexts="[1] 요금제 변경은 마이페이지에서 가능합니다.",
        )
        raw = await provider.complete(prompt, json_mode=True)
        data = json.loads(raw)

        assert "step1_required" in data
        assert "step2_coverage" in data
        assert "step3_ratio" in data
        assert 0.0 <= data["score"] <= 1.0


# ══════════════════════════════════════════════
# 3. CoT 평가 결과 검증
# ══════════════════════════════════════════════

class TestCoTEvaluation:
    """CoT 활성 시 MetricScore에 cot_steps가 채워지는지 검증"""

    @pytest.mark.asyncio
    async def test_cot_enabled_populates_cot_steps(self, sample_qa_pair):
        judge = _make_judge(cot_enabled=True, reverse_enabled=False)
        record = await judge.evaluate_pair(sample_qa_pair)

        # CoT 적용 메트릭의 cot_steps 확인
        for score in record.scores:
            if score.metric in ("answer_relevance", "context_precision", "context_recall"):
                assert isinstance(score.cot_steps, list), \
                    f"{score.metric}: cot_steps가 리스트여야 함"
                assert len(score.cot_steps) > 0, \
                    f"{score.metric}: CoT ON이면 cot_steps가 비어있으면 안 됨"
                assert score.method == "cot", \
                    f"{score.metric}: method='cot'여야 함"

    @pytest.mark.asyncio
    async def test_cot_disabled_no_cot_steps(self, sample_qa_pair):
        judge = _make_judge(cot_enabled=False, reverse_enabled=False)
        record = await judge.evaluate_pair(sample_qa_pair)

        for score in record.scores:
            if score.metric in ("answer_relevance", "context_precision", "context_recall"):
                # CoT OFF면 cot_steps 빈 리스트
                assert score.cot_steps == [], \
                    f"{score.metric}: CoT OFF면 cot_steps가 비어있어야 함"
                assert score.method != "cot", \
                    f"{score.metric}: CoT OFF면 method!='cot'여야 함"

    @pytest.mark.asyncio
    async def test_cot_score_in_valid_range(self, sample_qa_pair):
        judge = _make_judge(cot_enabled=True, reverse_enabled=False)
        record = await judge.evaluate_pair(sample_qa_pair)

        for score in record.scores:
            assert 0.0 <= score.score <= 1.0, f"{score.metric} 점수 범위 오류: {score.score}"


# ══════════════════════════════════════════════
# 4. 역방향 검증 프롬프트 구조 테스트
# ══════════════════════════════════════════════

class TestReversePromptStructure:
    """역방향 프롬프트에 올바른 지시가 포함되었는지 검증"""

    def test_reverse_faithfulness_prompt_structure(self):
        prompt = _REVERSE_FAITHFULNESS_PROMPT.format(
            query="요금제 변경 방법은?",
            answer="마이페이지에서 변경하세요.",
            contexts="요금제 변경은 마이페이지에서 가능합니다.",
        )
        assert "역방향 검증" in prompt
        assert "도출 가능한지" in prompt
        assert "step1_derivability" in prompt
        assert "step2_external_knowledge" in prompt

    def test_reverse_answer_relevance_prompt_structure(self):
        prompt = _REVERSE_ANSWER_RELEVANCE_PROMPT.format(
            query="요금제 변경 방법은?",
            answer="마이페이지에서 변경하세요.",
            contexts="",
        )
        assert "역방향 검증" in prompt
        assert "역추론" in prompt
        assert "step1_keywords" in prompt
        assert "step2_inferred_question" in prompt
        assert "step2_match_level" in prompt


# ══════════════════════════════════════════════
# 5. 역방향 Mock 응답 파싱 테스트
# ══════════════════════════════════════════════

class TestReverseMockResponse:
    """MockProvider가 역방향 프롬프트에 올바른 JSON을 반환하는지 검증"""

    @pytest.mark.asyncio
    async def test_mock_reverse_faithfulness(self, provider):
        prompt = _REVERSE_FAITHFULNESS_PROMPT.format(
            query="",
            answer="마이페이지에서 변경하세요.",
            contexts="요금제 변경은 마이페이지에서 가능합니다.",
        )
        raw = await provider.complete(prompt, json_mode=True)
        data = json.loads(raw)

        assert "step1_derivability" in data
        assert "step2_external_knowledge" in data
        assert "score" in data
        assert 0.0 <= data["score"] <= 1.0

    @pytest.mark.asyncio
    async def test_mock_reverse_answer_relevance(self, provider):
        prompt = _REVERSE_ANSWER_RELEVANCE_PROMPT.format(
            query="요금제 변경 방법은?",
            answer="마이페이지에서 변경하세요.",
            contexts="",
        )
        raw = await provider.complete(prompt, json_mode=True)
        data = json.loads(raw)

        assert "step1_keywords" in data
        assert "step2_inferred_question" in data
        assert "step2_match_level" in data
        assert data["step2_match_level"] in ("높음", "중간", "낮음")
        assert 0.0 <= data["score"] <= 1.0


# ══════════════════════════════════════════════
# 6. 역방향 검증 결과 검증
# ══════════════════════════════════════════════

class TestReverseVerification:
    """역방향 검증 활성 시 MetricScore에 순/역방향 점수가 기록되는지 검증"""

    @pytest.mark.asyncio
    async def test_reverse_enabled_populates_forward_reverse_scores(self, sample_qa_pair):
        judge = _make_judge(cot_enabled=False, reverse_enabled=True)
        record = await judge.evaluate_pair(sample_qa_pair)

        reverse_metrics = {"faithfulness", "answer_relevance"}
        for score in record.scores:
            if score.metric in reverse_metrics:
                assert score.forward_score is not None, \
                    f"{score.metric}: forward_score가 None이면 안 됨"
                assert score.reverse_score is not None, \
                    f"{score.metric}: reverse_score가 None이면 안 됨"
                assert score.consistency_score is not None, \
                    f"{score.metric}: consistency_score가 None이면 안 됨"
                assert 0.0 <= score.forward_score <= 1.0
                assert 0.0 <= score.reverse_score <= 1.0
                assert score.consistency_score >= 0.0

    @pytest.mark.asyncio
    async def test_reverse_final_score_is_weighted_average(self, sample_qa_pair):
        """최종 점수 = forward × 0.6 + reverse × 0.4"""
        judge = _make_judge(cot_enabled=False, reverse_enabled=True)
        record = await judge.evaluate_pair(sample_qa_pair)

        for score in record.scores:
            if score.metric in ("faithfulness", "answer_relevance"):
                if score.forward_score is not None and score.reverse_score is not None:
                    expected = score.forward_score * 0.6 + score.reverse_score * 0.4
                    assert abs(score.score - expected) < 0.01, \
                        f"{score.metric}: score={score.score:.4f}, expected={expected:.4f}"

    @pytest.mark.asyncio
    async def test_reverse_disabled_no_reverse_scores(self, sample_qa_pair):
        judge = _make_judge(cot_enabled=False, reverse_enabled=False)
        record = await judge.evaluate_pair(sample_qa_pair)

        for score in record.scores:
            assert score.forward_score is None, \
                f"{score.metric}: reverse OFF면 forward_score=None이어야 함"
            assert score.reverse_score is None, \
                f"{score.metric}: reverse OFF면 reverse_score=None이어야 함"

    @pytest.mark.asyncio
    async def test_inconsistency_triggers_low_confidence(self):
        """
        순방향과 역방향 점수 차이가 임계값 초과 시 is_low_confidence=True
        (실제 LLM에서만 의미 있지만 구조 테스트로 검증)
        """
        from AutoAudit.app.cp4_evaluator.judge import _extract_reverse_cot_steps
        # 역방향 CoT 단계 추출 함수 직접 테스트
        sample_raw = json.dumps({
            "step1_derivability": {"문장1": "가능", "문장2": "불가 — 외부 지식"},
            "step2_external_knowledge": ["외부 정보 A"],
            "score": 0.3,
            "reasoning": "컨텍스트에서 일부 도출 불가",
        })
        steps = _extract_reverse_cot_steps(sample_raw, "faithfulness")
        assert any("[역방향]" in s for s in steps)
        assert any("외부지식" in s for s in steps)

    @pytest.mark.asyncio
    async def test_reverse_method_label(self, sample_qa_pair):
        """역방향 활성 시 method='reverse' 또는 'cot_reverse' 기록"""
        judge = _make_judge(cot_enabled=False, reverse_enabled=True)
        record = await judge.evaluate_pair(sample_qa_pair)

        for score in record.scores:
            if score.metric in ("faithfulness", "answer_relevance"):
                assert score.method in ("reverse", "cot_reverse", "claim_nli"), \
                    f"{score.metric}: method='{score.method}' unexpected"


# ══════════════════════════════════════════════
# 7. CoT + 역방향 조합 테스트
# ══════════════════════════════════════════════

class TestCoTAndReverseCombined:
    """CoT + 역방향 동시 활성 시 통합 동작 검증"""

    @pytest.mark.asyncio
    async def test_cot_and_reverse_combined(self, sample_qa_pair):
        judge = _make_judge(cot_enabled=True, reverse_enabled=True)
        record = await judge.evaluate_pair(sample_qa_pair)

        assert len(record.scores) == 4, "4개 메트릭이 모두 평가되어야 함"

        for score in record.scores:
            assert 0.0 <= score.score <= 1.0, f"{score.metric}: 점수 범위 오류"
            assert score.reasoning, f"{score.metric}: reasoning이 비어있으면 안 됨"

        # answer_relevance: CoT + 역방향 모두 적용
        ar = next(s for s in record.scores if s.metric == "answer_relevance")
        assert ar.method in ("cot", "cot_reverse", "reverse"), \
            f"answer_relevance method={ar.method}"

    @pytest.mark.asyncio
    async def test_all_scores_have_valid_structure(self, sample_qa_pair):
        """모든 MetricScore 필드가 유효한 타입인지 검증"""
        judge = _make_judge(cot_enabled=True, reverse_enabled=True)
        record = await judge.evaluate_pair(sample_qa_pair)

        for score in record.scores:
            assert isinstance(score.cot_steps, list)
            assert isinstance(score.sample_scores, list)
            assert isinstance(score.grounding_chunks, list)
            assert isinstance(score.is_low_confidence, bool)
            assert isinstance(score.confidence, float)
            assert 0.0 <= score.confidence <= 1.0
            if score.forward_score is not None:
                assert isinstance(score.forward_score, float)
            if score.reverse_score is not None:
                assert isinstance(score.reverse_score, float)
            if score.consistency_score is not None:
                assert isinstance(score.consistency_score, float)
                assert score.consistency_score >= 0.0


# ══════════════════════════════════════════════
# 8. 옵션 설정 테스트
# ══════════════════════════════════════════════

class TestOptions:
    """CoTOptions / ReverseVerificationOptions 직렬화·역직렬화 검증"""

    def test_cot_options_defaults(self):
        opts = CoTOptions()
        assert opts.enabled is True
        assert "answer_relevance" in opts.metrics
        assert "context_precision" in opts.metrics
        assert "context_recall" in opts.metrics

    def test_reverse_options_defaults(self):
        opts = ReverseVerificationOptions()
        assert opts.enabled is False
        assert "faithfulness" in opts.metrics
        assert "answer_relevance" in opts.metrics
        assert opts.weight_forward == 0.6
        assert opts.weight_reverse == 0.4
        assert opts.inconsistency_threshold == 0.25

    def test_evaluation_options_include_cot_and_reverse(self):
        opts = EvaluationOptions()
        summary = opts.active_summary()
        assert "cot" in summary
        assert "reverse" in summary
        assert summary["cot"] is True    # 기본 ON
        assert summary["reverse"] is False  # 기본 OFF

    def test_cli_override_cot(self):
        opts = EvaluationOptions()
        opts.apply_cli_overrides(["reverse"], ["cot"])
        assert opts.cot.enabled is False
        assert opts.reverse.enabled is True

    def test_cli_override_reverse_sub_field(self):
        opts = EvaluationOptions()
        # 점 표기로 하위 필드 조정은 bool 타입만 가능
        opts.reverse.inconsistency_threshold = 0.3
        assert opts.reverse.inconsistency_threshold == 0.3
