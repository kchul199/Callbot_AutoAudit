"""
tests/test_paraphrase_sampling.py
#5 패러프레이즈 기반 자기일치성 샘플링.
"""
import json

import pytest

from AutoAudit.app.core.types import RetrievalResult, RetrievedContext
from AutoAudit.app.cp4_evaluator.judge import LLMJudge


class SpyProvider:
    """호출된 프롬프트를 기록하는 테스트 provider."""

    def __init__(self, score: float = 0.8):
        self.prompts: list[str] = []
        self._payload = json.dumps({"score": score, "reasoning": "ok", "grounding_chunks": []})

    async def complete(self, prompt, *, system=None, temperature=0.0, max_tokens=2048, json_mode=False):
        self.prompts.append(prompt)
        return self._payload

    async def embed(self, text):
        return [0.0] * 8


def _rr():
    return RetrievalResult(
        query="q?",
        contexts=[RetrievedContext(chunk_id="c1", content="컨텍스트 내용", score=0.9, source_call_id="C1")],
    )


# ----------------------------------------------------------------
# _paraphrase_variants
# ----------------------------------------------------------------

def test_variants_count_and_original_first():
    judge = LLMJudge(provider=SpyProvider())
    judge.paraphrase_templates = 3
    variants = judge._paraphrase_variants("BASE_PROMPT", 3)
    assert len(variants) == 3
    assert variants[0] == "BASE_PROMPT"                 # 원본 포함
    assert all("BASE_PROMPT" in v for v in variants)    # 본문 보존
    assert len(set(variants)) >= 2                       # 표현 변주


def test_variants_respect_template_cap():
    judge = LLMJudge(provider=SpyProvider())
    judge.paraphrase_templates = 1                       # 원본 + 1 프레이밍만
    variants = judge._paraphrase_variants("P", 4)
    # 풀 크기 2 → 변형은 2종만 (순환)
    assert len(set(variants)) == 2


# ----------------------------------------------------------------
# 평가 경로: paraphrase 전략이 서로 다른 프롬프트로 샘플링
# ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_paraphrase_sampling_uses_distinct_prompts():
    spy = SpyProvider(score=0.8)
    judge = LLMJudge(provider=spy)
    judge.sampling_strategy = "paraphrase"
    judge.n_samples = 3
    judge.use_claim_faithfulness = False     # faithfulness도 기본 샘플링 경로로
    judge.options.cot.enabled = False        # CoT 끄고 순수 샘플링 경로 확인

    record = await judge.evaluate(
        call_id="C1", query="q?", generated_answer="답변", retrieval_result=_rr(),
    )
    faith = next(s for s in record.scores if s.metric == "faithfulness")
    assert faith.method == "multi_sample_paraphrase"
    assert len(faith.sample_scores) == 3
    # 같은 메트릭을 서로 다른 표현의 프롬프트로 호출했는지
    assert len(set(spy.prompts)) >= 2


@pytest.mark.asyncio
async def test_temperature_strategy_uses_same_prompt():
    spy = SpyProvider(score=0.8)
    judge = LLMJudge(provider=spy)
    judge.sampling_strategy = "temperature"
    judge.n_samples = 3
    judge.use_claim_faithfulness = False
    judge.options.cot.enabled = False

    await judge.evaluate(call_id="C1", query="q?", generated_answer="답변", retrieval_result=_rr())
    # faithfulness 3회 호출이 모두 동일 프롬프트 (온도만 변주)
    faith_prompts = [p for p in spy.prompts if "충실한지" in p]
    assert len(faith_prompts) == 3 and len(set(faith_prompts)) == 1
