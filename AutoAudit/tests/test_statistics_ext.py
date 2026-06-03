"""
tests/test_statistics_ext.py
부트스트랩 CI / 순열검정 / PPI 검증
"""

from AutoAudit.app.cp5_aggregator.statistics_ext import (
    bootstrap_ci,
    paired_permutation_test,
    ppi_mean,
)


def test_bootstrap_ci_basic():
    ci = bootstrap_ci([0.8, 0.82, 0.79, 0.81, 0.83], confidence=0.95)
    assert ci.low <= ci.mean <= ci.high
    assert ci.n == 5


def test_bootstrap_ci_single():
    ci = bootstrap_ci([0.7])
    assert ci.low == ci.mean == ci.high == 0.7


def test_bootstrap_ci_empty():
    ci = bootstrap_ci([])
    assert ci.n == 0


def test_significant_drop_detected():
    cur = [0.5, 0.52, 0.48, 0.51, 0.49]
    prev = [0.85, 0.88, 0.86, 0.87, 0.84]
    sig = paired_permutation_test(cur, prev)
    assert sig.delta < 0
    assert sig.significant is True
    assert sig.p_value < 0.05


def test_no_significant_difference():
    cur = [0.80, 0.81, 0.79, 0.82]
    prev = [0.81, 0.80, 0.82, 0.79]
    sig = paired_permutation_test(cur, prev)
    assert sig.significant is False


def test_ppi_corrects_classifier_bias():
    # 분류기는 0.7로 과소평가, 실제(judge)는 ~0.85
    classifier_all = [0.7] * 200
    labeled = [(0.7, 0.85), (0.7, 0.83), (0.7, 0.87), (0.7, 0.84)]
    est = ppi_mean(classifier_all, labeled)
    # 보정 후 추정치는 분류기 평균(0.7)보다 높아야 함
    assert est.point_estimate > est.naive_classifier_mean
    assert est.ci_low <= est.point_estimate <= est.ci_high
    assert est.n_labeled == 4
    assert est.n_total == 200
