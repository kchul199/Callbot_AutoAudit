"""
tests/test_cp6_regression.py
run 간 회귀 감지 검증
"""
from AutoAudit.app.cp6_reporter.regression import detect_regression


def _summary(faith: float, rel: float) -> dict:
    return {
        "metrics": [
            {"metric": "faithfulness", "mean": faith},
            {"metric": "answer_relevance", "mean": rel},
        ]
    }


def test_no_previous_no_regression():
    rep = detect_regression(_summary(0.5, 0.5), None)
    assert rep.has_regression is False
    assert rep.deltas == []


def test_regression_detected():
    cur = _summary(0.70, 0.80)
    prev = _summary(0.85, 0.80)  # faithfulness 0.15 하락
    rep = detect_regression(cur, prev, threshold=0.05)
    assert rep.has_regression is True
    faith = next(d for d in rep.deltas if d.metric == "faithfulness")
    assert faith.is_regression is True
    assert faith.delta < 0


def test_improvement_not_regression():
    cur = _summary(0.90, 0.85)
    prev = _summary(0.80, 0.80)
    rep = detect_regression(cur, prev, threshold=0.05)
    assert rep.has_regression is False


def test_small_drop_within_threshold():
    cur = _summary(0.82, 0.80)
    prev = _summary(0.85, 0.80)  # 0.03 하락 < 0.05 임계
    rep = detect_regression(cur, prev, threshold=0.05)
    assert rep.has_regression is False


def test_summary_text_format():
    rep = detect_regression(_summary(0.70, 0.80), _summary(0.85, 0.80), threshold=0.05)
    text = rep.summary_text()
    assert "faithfulness" in text
    assert "회귀" in text
