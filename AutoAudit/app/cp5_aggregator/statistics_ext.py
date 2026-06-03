"""
cp5_aggregator/statistics_ext.py
부트스트랩 신뢰구간 + paired 유의성 검정 + PPI 추정.

stdlib(random)만 사용. 대규모/GT-free 환경에서 메트릭 추정의
불확실성을 정량화하고, run 간 차이가 통계적으로 유의한지 판정한다.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass


@dataclass
class ConfidenceInterval:
    mean: float
    low: float
    high: float
    n: int


def bootstrap_ci(
    values: list[float],
    n_samples: int = 1000,
    confidence: float = 0.95,
    seed: int = 42,
) -> ConfidenceInterval:
    """평균의 부트스트랩 신뢰구간 (percentile 방식)"""
    if not values:
        return ConfidenceInterval(0.0, 0.0, 0.0, 0)
    if len(values) == 1:
        v = values[0]
        return ConfidenceInterval(v, v, v, 1)

    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_samples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    alpha = 1 - confidence
    lo_idx = int((alpha / 2) * n_samples)
    hi_idx = min(n_samples - 1, int((1 - alpha / 2) * n_samples))
    return ConfidenceInterval(
        mean=sum(values) / n,
        low=means[lo_idx],
        high=means[hi_idx],
        n=n,
    )


@dataclass
class SignificanceResult:
    delta: float                 # current_mean - previous_mean
    p_value: float               # 순열검정 근사 p-value
    significant: bool
    ci_low: float
    ci_high: float


def paired_permutation_test(
    current: list[float],
    previous: list[float],
    n_permutations: int = 1000,
    alpha: float = 0.05,
    seed: int = 42,
) -> SignificanceResult:
    """
    두 표본 평균 차이의 순열검정 (비모수).
    qa_id 정렬이 보장되지 않을 수 있어 unpaired 순열로 안전하게 처리.
    """
    if not current or not previous:
        return SignificanceResult(0.0, 1.0, False, 0.0, 0.0)

    observed = statistics.mean(current) - statistics.mean(previous)
    combined = current + previous
    n_cur = len(current)
    rng = random.Random(seed)

    count = 0
    for _ in range(n_permutations):
        rng.shuffle(combined)
        perm_cur = combined[:n_cur]
        perm_prev = combined[n_cur:]
        perm_delta = statistics.mean(perm_cur) - statistics.mean(perm_prev)
        if abs(perm_delta) >= abs(observed):
            count += 1
    p_value = (count + 1) / (n_permutations + 1)

    # delta 부트스트랩 CI
    diffs_ci = bootstrap_ci(
        [c for c in current], n_samples=500, confidence=1 - alpha, seed=seed
    )
    prev_ci = bootstrap_ci(previous, n_samples=500, confidence=1 - alpha, seed=seed)
    return SignificanceResult(
        delta=observed,
        p_value=p_value,
        significant=p_value < alpha,
        ci_low=diffs_ci.low - prev_ci.high,
        ci_high=diffs_ci.high - prev_ci.low,
    )


@dataclass
class PPIEstimate:
    """Prediction-Powered Inference 추정 결과"""
    point_estimate: float
    ci_low: float
    ci_high: float
    n_labeled: int
    n_total: int
    naive_classifier_mean: float


def ppi_mean(
    classifier_scores: list[float],
    labeled_pairs: list[tuple[float, float]],   # (classifier_score, llm_judge_score)
    confidence: float = 0.95,
    seed: int = 42,
) -> PPIEstimate:
    """
    PPI 평균 추정: 저비용 분류기 전체 예측 + 소량 LLM 라벨로 편향 보정.

    estimate = mean(classifier_all) + mean(judge_labeled - classifier_labeled)
             = 분류기 예측 + 보정항(rectifier)
    소량 LLM 호출로 통계적으로 유효한 추정 + 신뢰구간 확보 → 대량 평가 비용↓.
    """
    if not classifier_scores or not labeled_pairs:
        return PPIEstimate(0.0, 0.0, 0.0, 0, len(classifier_scores), 0.0)

    clf_mean = sum(classifier_scores) / len(classifier_scores)
    rectifiers = [judge - clf for clf, judge in labeled_pairs]
    rect_ci = bootstrap_ci(rectifiers, confidence=confidence, seed=seed)

    point = clf_mean + rect_ci.mean
    return PPIEstimate(
        point_estimate=round(point, 4),
        ci_low=round(clf_mean + rect_ci.low, 4),
        ci_high=round(clf_mean + rect_ci.high, 4),
        n_labeled=len(labeled_pairs),
        n_total=len(classifier_scores),
        naive_classifier_mean=round(clf_mean, 4),
    )
