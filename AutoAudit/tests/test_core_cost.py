"""
tests/test_core_cost.py
비용 추적기 + 예산 circuit breaker 검증
"""
import pytest

from AutoAudit.app.core.llm_client import (
    BudgetExceededError,
    CostTracker,
    _is_transient,
)


def test_cost_accumulation():
    tracker = CostTracker()
    tracker.record("gpt-4o", input_tokens=1_000_000, output_tokens=0)
    # gpt-4o input = $2.50 / 1M
    assert tracker.stats.total_cost_usd == pytest.approx(2.50, abs=1e-6)
    assert tracker.stats.call_count == 1


def test_budget_exceeded_raises():
    tracker = CostTracker(budget_usd=1.0)
    with pytest.raises(BudgetExceededError):
        tracker.record("gpt-4o", input_tokens=1_000_000)  # $2.50 > $1.0


def test_budget_not_exceeded():
    tracker = CostTracker(budget_usd=10.0)
    tracker.record("gpt-4o", input_tokens=100_000, output_tokens=10_000)
    assert tracker.stats.total_cost_usd < 10.0


def test_unknown_model_zero_cost():
    tracker = CostTracker()
    tracker.record("unknown-model", input_tokens=1000)
    assert tracker.stats.total_cost_usd == 0.0


def test_summary_structure():
    tracker = CostTracker()
    tracker.record("gpt-4o", 100, 50)
    s = tracker.summary()
    assert set(s) >= {"input_tokens", "output_tokens", "total_cost_usd", "call_count"}


# ---- 선택적 재시도 판별 ----

class FakeRateLimit(Exception):
    status_code = 429


class FakeBadRequest(Exception):
    status_code = 400


def test_transient_429_retried():
    assert _is_transient(FakeRateLimit()) is True


def test_non_transient_400_not_retried():
    assert _is_transient(FakeBadRequest()) is False
