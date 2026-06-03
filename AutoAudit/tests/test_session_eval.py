"""
tests/test_session_eval.py
세션 레벨 평가기 — resolution/efficiency/escalation/일관성/GT유사도
"""
import json

import pytest

from AutoAudit.app.core.types import CallLog, ConversationTurn, TurnRole
from AutoAudit.app.cp4_evaluator.session_eval import SessionEvaluator


class SessionProvider:
    """세션 판정 + 일관성 + 임베딩을 분기 반환하는 가짜 provider"""

    def __init__(self, session_payload, consistency_payload="{}"):
        self._session = session_payload
        self._consistency = consistency_payload

    async def complete(self, prompt, **kwargs):
        if "대화 전체를 평가" in prompt:
            return self._session
        if "모순되는 진술" in prompt:
            return self._consistency
        return "{}"

    async def embed(self, text):
        # 단순 결정적 임베딩 (길이 기반)
        return [float(len(text) % 7), 1.0, 0.5]


def _log(turns):
    return CallLog(
        call_id="C1", subscriber_id="S1",
        turns=[ConversationTurn(turn_id=i, role=TurnRole(r), content=c)
               for i, (r, c) in enumerate(turns)],
        metadata={"tenant_id": "acme"},
    )


@pytest.mark.asyncio
async def test_resolved_session():
    payload = json.dumps({
        "resolved": True, "resolution_score": 0.9, "efficiency_score": 0.8,
        "escalation_needed": False, "reasoning": "해결됨",
    })
    ev = SessionEvaluator(SessionProvider(payload, json.dumps({"consistent": True, "score": 1.0})))
    log = _log([("user", "요금제 알려줘"), ("bot", "5G 요금제는 6만9천원입니다"),
                ("user", "변경해줘"), ("bot", "변경 완료했습니다")])
    result = await ev.evaluate(log)
    assert result.resolved is True
    assert result.tenant_id == "acme"
    metrics = {s.metric: s.score for s in result.scores}
    assert metrics["resolution"] == pytest.approx(0.9)
    assert metrics["efficiency"] == pytest.approx(0.8)
    assert metrics["escalation_handling"] == pytest.approx(1.0)  # 연결 불필요 → 1.0
    assert "multiturn_consistency" in metrics


@pytest.mark.asyncio
async def test_escalation_needed_scores_zero():
    payload = json.dumps({
        "resolved": False, "resolution_score": 0.2, "efficiency_score": 0.5,
        "escalation_needed": True, "reasoning": "콜봇이 처리 못함",
    })
    ev = SessionEvaluator(SessionProvider(payload))
    log = _log([("user", "복잡한 문의"), ("bot", "잘 모르겠습니다")])
    result = await ev.evaluate(log)
    assert result.escalation_needed is True
    metrics = {s.metric: s.score for s in result.scores}
    assert metrics["escalation_handling"] == pytest.approx(0.0)  # 연결 필요했음 → 0


@pytest.mark.asyncio
async def test_ground_truth_similarity_added():
    payload = json.dumps({"resolved": True, "resolution_score": 1.0,
                          "efficiency_score": 1.0, "escalation_needed": False, "reasoning": ""})
    ev = SessionEvaluator(SessionProvider(payload))
    log = _log([("user", "q"), ("bot", "정답 답변입니다")])
    result = await ev.evaluate(log, ground_truth="정답 답변입니다")
    metrics = {s.metric for s in result.scores}
    assert "gt_similarity" in metrics
    gt = next(s for s in result.scores if s.metric == "gt_similarity")
    assert 0.0 <= gt.score <= 1.0


@pytest.mark.asyncio
async def test_malformed_session_json_graceful():
    ev = SessionEvaluator(SessionProvider("NOT JSON"))
    log = _log([("user", "q"), ("bot", "a")])
    result = await ev.evaluate(log)
    # 파싱 실패해도 점수 레코드는 생성
    assert any(s.metric == "resolution" for s in result.scores)
