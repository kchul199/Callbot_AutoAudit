"""
tests/test_precision_at_k.py
#4 context_precision 청크별 순위가중(precision@k) 검증.
"""
import json

from AutoAudit.app.cp4_evaluator.judge import LLMJudge


# ----------------------------------------------------------------
# 순위가중 공식
# ----------------------------------------------------------------

def test_rank_weighted_all_relevant():
    assert LLMJudge._rank_weighted_precision([1, 1, 1]) == 1.0


def test_rank_weighted_none_relevant():
    assert LLMJudge._rank_weighted_precision([0, 0, 0]) == 0.0


def test_rank_weighted_rewards_top_rank():
    """관련 청크가 상위에 있을수록 점수가 높다 (순위 민감성)."""
    top = LLMJudge._rank_weighted_precision([1, 0, 0, 0])     # 1위만 관련
    bottom = LLMJudge._rank_weighted_precision([0, 0, 0, 1])  # 4위만 관련
    assert top > bottom
    assert top == 1.0
    assert bottom == 0.25


def test_rank_weighted_same_count_differs_by_order():
    """동일 관련 개수라도 순위에 따라 점수 차등."""
    a = LLMJudge._rank_weighted_precision([1, 1, 0, 0])
    b = LLMJudge._rank_weighted_precision([0, 0, 1, 1])
    assert a > b


# ----------------------------------------------------------------
# CoT 응답 파싱 → precision@k 대체
# ----------------------------------------------------------------

def test_parse_cot_uses_per_chunk_when_verdicts_present():
    """청크 판정이 있으면 LLM 홀리스틱 점수 대신 순위가중 precision으로 대체."""
    raw = json.dumps({
        "step1_verdicts": {"[1]": "유용 — 관련", "[2]": "불필요 — 무관", "[3]": "불필요 — 무관"},
        "score": 0.9,                       # LLM 홀리스틱 (무시되어야 함)
        "reasoning": "mock",
        "grounding_chunks": [],
    })
    ms = LLMJudge._parse_cot_score(raw, "context_precision", per_chunk=True)
    # rel=[1,0,0] → precision@k = 1.0/1 = 1.0 (1위 관련)
    assert ms.score == 1.0
    assert "precision@k" in ms.reasoning


def test_parse_cot_falls_back_without_verdicts():
    """청크 판정이 없으면 LLM 점수를 그대로 사용 (폴백)."""
    raw = json.dumps({"score": 0.73, "reasoning": "mock", "grounding_chunks": []})
    ms = LLMJudge._parse_cot_score(raw, "context_precision", per_chunk=True)
    assert ms.score == 0.73


def test_parse_cot_per_chunk_disabled():
    """per_chunk=False면 대체하지 않고 LLM 점수 유지."""
    raw = json.dumps({
        "step1_verdicts": {"[1]": "불필요", "[2]": "유용"},
        "score": 0.88, "reasoning": "mock", "grounding_chunks": [],
    })
    ms = LLMJudge._parse_cot_score(raw, "context_precision", per_chunk=False)
    assert ms.score == 0.88


def test_low_ranked_relevant_scores_lower_than_holistic():
    """관련 청크가 하위면 순위가중 점수가 단순 비율보다 낮아진다."""
    raw = json.dumps({
        "step1_verdicts": {"[1]": "불필요", "[2]": "불필요", "[3]": "유용"},
        "score": 0.33, "reasoning": "mock", "grounding_chunks": [],
    })
    ms = LLMJudge._parse_cot_score(raw, "context_precision", per_chunk=True)
    # rel=[0,0,1] → precision@k = (1/3)/1 ≈ 0.3333
    assert abs(ms.score - 0.3333) < 0.001
