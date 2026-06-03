"""
tests/test_eval_options.py
EvaluationOptions 로드 + CLI 오버라이드 검증
"""
import pytest

from AutoAudit.app.cp4_evaluator.options import EvaluationOptions


def test_defaults_from_config():
    o = EvaluationOptions.from_config()
    s = o.active_summary()
    # config 기본: diagnosis/statistics ON, 나머지 OFF
    assert s["diagnosis"] is True
    assert s["statistics"] is True
    assert s["ensemble"] is False


def test_enable_group():
    o = EvaluationOptions()
    o.apply_cli_overrides(["ensemble", "nugget"], None)
    assert o.ensemble.enabled is True
    assert o.nugget.enabled is True


def test_disable_group():
    o = EvaluationOptions()
    o.apply_cli_overrides(None, ["diagnosis"])
    assert o.diagnosis.enabled is False


def test_enable_nested_field():
    o = EvaluationOptions()
    o.apply_cli_overrides(["calibration.g_eval_logprobs"], None)
    assert o.calibration.g_eval_logprobs is True


def test_unknown_path_raises():
    o = EvaluationOptions()
    with pytest.raises(ValueError):
        o.apply_cli_overrides(["nonexistent"], None)


def test_enable_then_disable_order():
    o = EvaluationOptions()
    o.apply_cli_overrides(["ensemble"], ["ensemble"])
    # enable 먼저, disable 나중 → 최종 False
    assert o.ensemble.enabled is False
