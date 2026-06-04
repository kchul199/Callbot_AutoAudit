"""
tests/test_answer_quality.py
콜봇 답변 품질 검증 정확도 강화 4종 테스트:
  ① 정답성(Answer Correctness) + ② 참조 기반 채점
  ③ 대화 맥락 주입(Multi-turn Context)
  ④ 적정 거절(Appropriate Abstention) + ⑤ 결정적 수치 가드(Numeric Guard)
  ⑦ 휴먼 정합 자동 보정 루프(Auto-Calibration)

AUTOAUDIT_MOCK=1 환경에서 API 키 없이 실행 가능.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

os.environ["AUTOAUDIT_MOCK"] = "1"

from AutoAudit.app.core.mock_provider import MockProvider
from AutoAudit.app.core.types import (
    EvaluationRecord,
    MetricScore,
    QAPair,
    RetrievalResult,
    RetrievedContext,
)
from AutoAudit.app.cp4_evaluator.abstention import AbstentionDetector
from AutoAudit.app.cp4_evaluator.auto_calibration import (
    AutoCalibrator,
    _IsotonicMap,
    _LinearMap,
    _PlattMap,
)
from AutoAudit.app.cp4_evaluator.correctness import CorrectnessEvaluator
from AutoAudit.app.cp4_evaluator.judge import (
    _COT_ANSWER_RELEVANCE_REF_PROMPT,
    LLMJudge,
)
from AutoAudit.app.cp4_evaluator.numeric_guard import (
    apply_guard,
    extract_numerics,
    find_conflicts,
)
from AutoAudit.app.cp4_evaluator.options import (
    AutoCalibrationOptions,
    CorrectnessOptions,
    EvaluationOptions,
)

# ──────────────────────────────────────────────
# 픽스처 / 헬퍼
# ──────────────────────────────────────────────


@pytest.fixture
def provider():
    return MockProvider()


def _retrieval(query: str, *chunks: str) -> RetrievalResult:
    return RetrievalResult(
        query=query,
        contexts=[
            RetrievedContext(chunk_id=f"c{i}", content=ch, score=0.9 - i * 0.1, source_call_id="C1")
            for i, ch in enumerate(chunks)
        ],
    )


def _make_judge(provider, **flags) -> LLMJudge:
    """지정 플래그만 켠 EvaluationOptions로 judge 생성."""
    opts = EvaluationOptions()
    for key, val in flags.items():
        group, _, leaf = key.partition(".")
        target = getattr(opts, group)
        setattr(target, leaf or "enabled", val)
    return LLMJudge(provider=provider, options=opts)


# ══════════════════════════════════════════════
# ⑤ 결정적 수치 가드 (순수 함수)
# ══════════════════════════════════════════════


class TestNumericGuard:
    def test_extract_numbers_with_units(self):
        nums = extract_numerics("기본 요금제는 월 30000원이고 약정은 12개월입니다.")
        joined = "".join(nums)
        assert "30000" in joined
        assert "12" in joined

    def test_extract_dates(self):
        nums = extract_numerics("신청일은 2024-03-15 입니다.", check_dates=True)
        assert any("2024" in n for n in nums)

    def test_conflict_detected(self):
        # 답변의 35000원이 컨텍스트(30000원)에 없음 → 충돌
        conflicts = find_conflicts(
            answer="월 35000원입니다.",
            contexts_text="기본 요금은 월 30000원입니다.",
        )
        assert any("35000" in c for c in conflicts)

    def test_no_conflict_when_numbers_match(self):
        conflicts = find_conflicts(
            answer="월 30000원입니다.",
            contexts_text="기본 요금은 월 30000원이며 약정은 12개월입니다.",
        )
        assert conflicts == []

    def test_apply_guard_penalizes(self):
        guarded, conflicts = apply_guard(
            0.9, "월 35000원", "기본 요금 30000원", penalty_per_conflict=0.3
        )
        assert conflicts  # 충돌 발견
        assert guarded < 0.9  # 감점됨

    def test_apply_guard_noop_without_conflict(self):
        guarded, conflicts = apply_guard(0.9, "월 30000원", "월 30000원입니다")
        assert conflicts == []
        assert guarded == 0.9


class TestNumericGuardIntegration:
    @pytest.mark.asyncio
    async def test_faithfulness_flags_numeric_hallucination(self, provider):
        judge = _make_judge(provider, **{"numeric_guard.enabled": True})
        rr = _retrieval("요금", "기본 요금제는 월 30000원입니다.")
        rec = await judge.evaluate(
            call_id="C1", query="얼마예요?",
            generated_answer="월 35000원입니다.", retrieval_result=rr,
        )
        faith = next(s for s in rec.scores if s.metric == "faithfulness")
        assert faith.numeric_flags  # 35000원 충돌 기록
        assert faith.is_low_confidence


# ══════════════════════════════════════════════
# ④ 적정 거절
# ══════════════════════════════════════════════


class TestAbstentionDetection:
    @pytest.mark.parametrize("answer", [
        "죄송합니다. 해당 정보가 없어 안내해 드리기 어렵습니다.",
        "확인이 어렵습니다.",
        "제공할 수 없는 정보입니다.",
        "잘 모르겠습니다.",
        "상담사에 연결해 드리겠습니다.",
    ])
    def test_detects_refusal(self, answer):
        assert AbstentionDetector.is_abstention(answer)

    @pytest.mark.parametrize("answer", [
        "기본 요금제는 월 30000원입니다.",
        "마이페이지에서 변경 가능합니다.",
    ])
    def test_normal_answer_not_abstention(self, answer):
        assert not AbstentionDetector.is_abstention(answer)

    @pytest.mark.asyncio
    async def test_assess_appropriate_when_context_lacks_info(self, provider):
        det = AbstentionDetector(provider)
        res = await det.assess(
            query="해외로밍 요금은 얼마인가요?",
            answer="해당 정보가 없어 안내가 어렵습니다.",
            contexts_text="[1] 고객센터 운영시간은 09시부터 18시입니다.",
        )
        assert res.is_abstention
        assert res.appropriate  # 컨텍스트에 로밍 정보 없음 → 정당

    @pytest.mark.asyncio
    async def test_assess_normal_answer_short_circuits(self, provider):
        det = AbstentionDetector(provider)
        res = await det.assess("질문", "월 30000원입니다.", "월 30000원")
        assert not res.is_abstention


class TestAbstentionExemption:
    @pytest.mark.asyncio
    async def test_appropriate_refusal_exempts_metrics(self, provider):
        judge = _make_judge(provider, **{"abstention.enabled": True})
        rr = _retrieval("해외로밍", "고객센터 운영시간은 09시부터 18시입니다.")
        rec = await judge.evaluate(
            call_id="C1", query="해외로밍 요금은 얼마인가요?",
            generated_answer="죄송합니다. 해당 정보가 없어 안내가 어렵습니다.",
            retrieval_result=rr,
        )
        faith = next(s for s in rec.scores if s.metric == "faithfulness")
        rel = next(s for s in rec.scores if s.metric == "answer_relevance")
        assert faith.method == "abstention_exempt"
        assert faith.abstention is True
        assert faith.score == 1.0
        assert rel.abstention is True

    @pytest.mark.asyncio
    async def test_normal_answer_not_exempted(self, provider):
        judge = _make_judge(provider, **{"abstention.enabled": True})
        rr = _retrieval("요금", "기본 요금제는 월 30000원입니다.")
        rec = await judge.evaluate(
            call_id="C1", query="얼마예요?",
            generated_answer="기본 요금제는 월 30000원입니다.", retrieval_result=rr,
        )
        assert all(not s.abstention for s in rec.scores)


# ══════════════════════════════════════════════
# ① 정답성 + ② 참조 기반 채점
# ══════════════════════════════════════════════


class TestCorrectness:
    @pytest.mark.asyncio
    async def test_correctness_metric_populated(self, provider):
        ev = CorrectnessEvaluator(provider, CorrectnessOptions(enabled=True))
        ms = await ev.evaluate(
            answer="기본 요금제는 월 30000원이며 약정은 12개월입니다.",
            ground_truth="기본 요금제는 월 30000원이고 약정 기간은 12개월입니다.",
        )
        assert ms.metric == "answer_correctness"
        assert ms.method == "answer_correctness"
        assert ms.correctness_f1 is not None
        assert ms.correctness_sim is not None
        assert 0.0 <= ms.score <= 1.0

    @pytest.mark.asyncio
    async def test_empty_ground_truth(self, provider):
        ev = CorrectnessEvaluator(provider, CorrectnessOptions(enabled=True))
        ms = await ev.evaluate(answer="아무 답변", ground_truth="")
        assert ms.score == 0.0

    @pytest.mark.asyncio
    async def test_high_overlap_high_f1(self, provider):
        ev = CorrectnessEvaluator(provider, CorrectnessOptions(enabled=True))
        ms = await ev.evaluate(
            answer="마이페이지에서 요금제를 변경할 수 있습니다.",
            ground_truth="마이페이지에서 요금제를 변경할 수 있습니다.",
        )
        assert ms.correctness_f1 == 1.0


class TestCorrectnessIntegration:
    @pytest.mark.asyncio
    async def test_correctness_added_when_enabled_with_gt(self, provider):
        judge = _make_judge(provider, **{"correctness.enabled": True})
        rr = _retrieval("요금", "기본 요금제는 월 30000원입니다.")
        rec = await judge.evaluate(
            call_id="C1", query="얼마예요?",
            generated_answer="월 30000원입니다.", retrieval_result=rr,
            ground_truth="기본 요금제는 월 30000원입니다.",
        )
        assert any(s.metric == "answer_correctness" for s in rec.scores)

    @pytest.mark.asyncio
    async def test_correctness_skipped_without_gt(self, provider):
        judge = _make_judge(provider, **{"correctness.enabled": True})
        rr = _retrieval("요금", "기본 요금제는 월 30000원입니다.")
        rec = await judge.evaluate(
            call_id="C1", query="얼마예요?",
            generated_answer="월 30000원입니다.", retrieval_result=rr,
        )
        assert not any(s.metric == "answer_correctness" for s in rec.scores)


class TestReferenceGuided:
    def test_ref_prompt_used_for_answer_relevance(self, provider):
        judge = _make_judge(provider)  # correctness.reference_guided 기본 True
        prompt = judge._build_prompt(
            "answer_relevance", "질문", "답변", "컨텍스트",
            use_cot=True, ground_truth="모범답안 텍스트",
        )
        assert "[모범답안]" in prompt
        assert "모범답안 텍스트" in prompt

    def test_no_ref_prompt_without_gt(self, provider):
        judge = _make_judge(provider)
        prompt = judge._build_prompt(
            "answer_relevance", "질문", "답변", "컨텍스트", use_cot=True,
        )
        assert "[모범답안]" not in prompt

    def test_ref_template_has_steps(self):
        assert "Step 1 — 질문 의도 파악" in _COT_ANSWER_RELEVANCE_REF_PROMPT
        assert "[모범답안]" in _COT_ANSWER_RELEVANCE_REF_PROMPT


# ══════════════════════════════════════════════
# ③ 대화 맥락 주입
# ══════════════════════════════════════════════


class TestContextInjection:
    def test_history_injected_for_configured_metric(self, provider):
        judge = _make_judge(provider, **{"context_injection.enabled": True})
        prompt = judge._build_prompt(
            "answer_relevance", "그거 얼마?", "3만원", "컨텍스트", use_cot=True,
            history=["고객: 요금제 알려줘", "콜봇: 어떤 요금제요?"],
        )
        assert "[직전 대화 맥락]" in prompt
        assert "요금제 알려줘" in prompt

    def test_history_not_injected_for_unconfigured_metric(self, provider):
        judge = _make_judge(provider, **{"context_injection.enabled": True})
        # 기본 metrics = [answer_relevance, faithfulness] → context_precision 제외
        prompt = judge._build_prompt(
            "context_precision", "질문", "답변", "컨텍스트", use_cot=True,
            history=["고객: 이전 발화"],
        )
        assert "[직전 대화 맥락]" not in prompt

    def test_history_disabled_no_injection(self, provider):
        judge = _make_judge(provider)  # context_injection.enabled = False
        prompt = judge._build_prompt(
            "answer_relevance", "질문", "답변", "컨텍스트", use_cot=True,
            history=["고객: 이전 발화"],
        )
        assert "[직전 대화 맥락]" not in prompt

    def test_max_history_turns_truncates(self, provider):
        judge = _make_judge(provider, **{"context_injection.enabled": True})
        judge.options.context_injection.max_history_turns = 2
        history = [f"고객: 발화{i}" for i in range(10)]
        block = judge._format_history_block(history)
        assert "발화9" in block  # 최근
        assert "발화0" not in block  # 오래된 것 잘림

    @pytest.mark.asyncio
    async def test_pair_history_threaded(self, provider):
        judge = _make_judge(provider, **{"context_injection.enabled": True})
        rr = _retrieval("요금", "기본 요금제는 월 30000원입니다.")
        pair = QAPair(
            qa_id="qa1", call_id="C1", subscriber_id="S1",
            question="그거 얼마예요?", bot_answer="월 30000원입니다.",
            turn_index=2, retrieval_result=rr,
            history=["고객: 요금제 안내해줘", "콜봇: 어떤 요금제요?"],
        )
        rec = await judge.evaluate_pair(pair)  # 예외 없이 평가 완료
        assert rec.scores


# ══════════════════════════════════════════════
# ⑦ 휴먼 정합 자동 보정
# ══════════════════════════════════════════════


class TestCalibrationMaps:
    def test_isotonic_monotonic(self):
        m = _IsotonicMap.fit([(0.6, 0.4), (0.7, 0.5), (0.8, 0.6), (0.9, 0.65)])
        assert m.apply(0.6) <= m.apply(0.9)

    def test_linear_pulls_down_overconfident(self):
        # judge가 항상 +0.2 후한 경우 → 보정은 끌어내려야
        m = _LinearMap.fit([(0.6, 0.4), (0.7, 0.5), (0.8, 0.6), (0.9, 0.7)])
        assert m.apply(0.8) < 0.8

    def test_platt_bounded_0_1(self):
        m = _PlattMap.fit([(0.2, 0.1), (0.5, 0.5), (0.9, 0.95)])
        for x in (0.0, 0.5, 1.0):
            assert 0.0 <= m.apply(x) <= 1.0


class TestAutoCalibrator:
    def _golden_file(self, rows):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        for qa, metric, human in rows:
            f.write(json.dumps({"qa_id": qa, "metric": metric, "human_score": human}) + "\n")
        f.close()
        return f.name

    def _records(self, rows, judge_offset=0.2):
        recs = []
        for i, (qa, metric, human) in enumerate(rows):
            recs.append(EvaluationRecord(
                eval_id=f"e{i}", qa_id=qa, call_id="C1", query="q",
                generated_answer="a", retrieval_result=RetrievalResult(query="q", contexts=[]),
                scores=[MetricScore(metric=metric, score=min(1.0, human + judge_offset), reasoning="")],
            ))
        return recs

    def test_fit_and_apply(self):
        rows = [(f"qa{i}", "faithfulness", round(0.4 + 0.04 * i, 2)) for i in range(12)]
        path = self._golden_file(rows)
        recs = self._records(rows)
        cal = AutoCalibrator(AutoCalibrationOptions(
            enabled=True, golden_set_path=path, method="isotonic", min_samples=10,
        ))
        assert cal.fit(recs)
        n = cal.apply_to_records(recs)
        assert n > 0
        # 보정 전 원점수 보존
        assert recs[0].scores[0].calibrated_from is not None
        os.unlink(path)

    def test_no_golden_returns_false(self):
        cal = AutoCalibrator(AutoCalibrationOptions(
            enabled=True, golden_set_path="/nonexistent/golden.jsonl",
        ))
        assert not cal.fit([])
        assert cal.report()["available"] is False

    def test_min_samples_skips_undersized_metric(self):
        rows = [(f"qa{i}", "faithfulness", 0.5) for i in range(3)]
        path = self._golden_file(rows)
        recs = self._records(rows)
        cal = AutoCalibrator(AutoCalibrationOptions(
            enabled=True, golden_set_path=path, min_samples=10,
        ))
        assert not cal.fit(recs)  # 3 < 10 → 보정 생략
        os.unlink(path)

    def test_sla_auto_tune(self):
        # human 합격(>=0.5)/불합격이 judge 0.5 부근에서 갈리는 데이터
        rows = [(f"qa{i}", "faithfulness", 0.2 if i < 6 else 0.8) for i in range(12)]
        path = self._golden_file(rows)
        recs = self._records(rows, judge_offset=0.1)
        cal = AutoCalibrator(AutoCalibrationOptions(
            enabled=True, golden_set_path=path, method="linear",
            min_samples=10, auto_tune_sla=True,
        ))
        assert cal.fit(recs)
        assert "faithfulness" in cal.tuned_sla
        assert 0.0 < cal.tuned_sla["faithfulness"] < 1.0
        os.unlink(path)


# ══════════════════════════════════════════════
# 옵션 통합
# ══════════════════════════════════════════════


class TestOptions:
    def test_new_options_in_active_summary(self):
        opts = EvaluationOptions()
        summary = opts.active_summary()
        for key in ("correctness", "context_injection", "abstention", "numeric_guard", "auto_calibration"):
            assert key in summary

    def test_cli_override_enables_new_options(self):
        opts = EvaluationOptions().apply_cli_overrides(
            ["correctness", "abstention", "numeric_guard"], None
        )
        assert opts.correctness.enabled
        assert opts.abstention.enabled
        assert opts.numeric_guard.enabled

    def test_defaults_off(self):
        opts = EvaluationOptions()
        # 답변 품질 강화 기법은 기본 OFF (correctness 등 추가 입력 필요)
        assert not opts.correctness.enabled
        assert not opts.context_injection.enabled
        assert not opts.abstention.enabled
        assert not opts.numeric_guard.enabled
        assert not opts.auto_calibration.enabled
        # reference_guided 자체는 기본 True (correctness 활성 시 적용)
        assert opts.correctness.reference_guided
