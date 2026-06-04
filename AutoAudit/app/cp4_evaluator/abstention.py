"""
cp4_evaluator/abstention.py
적정 거절/모름 판정(Appropriate Abstention) ④.

콜봇이 "정보가 없습니다 / 안내가 어렵습니다 / 확인해 드릴 수 없습니다" 처럼
정보 제공을 거절·보류하는 경우가 있다. 컨텍스트에 실제로 정보가 없어서
거절이 정당하다면, 이는 오히려 '환각하지 않은 올바른 행동'이다.

그러나 기존 relevance/faithfulness는 이런 정당한 거절도 "질문에 답을 안 했다"고
감점한다(거짓 실패). 이 모듈은:
  1. 답변이 거절/모름인지 정규식으로 즉시 탐지 (무비용)
  2. 거절이면 컨텍스트에 정말 정보가 없었는지 LLM으로 정당성 판정
  3. 정당하면 해당 메트릭 감점을 면제 (exempt_score 부여, abstention=True 플래그)
"""
from __future__ import annotations

import json
import re

from AutoAudit.app.core.llm_client import LLMProvider
from AutoAudit.app.core.logger import get_logger

logger = get_logger(__name__)

_SYSTEM = "당신은 콜봇 답변 적정성 판정 전문가입니다. 반드시 JSON만 반환하세요."

# 거절/보류/모름 신호 (한국어 콜봇 맥락)
_REFUSAL_PATTERNS = [
    r"정보가?\s*없",
    r"확인(이|해\s*드릴\s*수|하기)?\s*(어렵|불가|힘들)",
    r"안내(가|해\s*드리기|해\s*드릴\s*수)?\s*(어렵|불가|힘들)",
    r"제공(이|할\s*수)?\s*(어렵|불가|없)",
    r"답변(이|하기|해\s*드리기)?\s*(어렵|불가)",
    r"도와드리기\s*(어렵|힘들)",
    r"(알|모르)\s*겠|모르겠습니다",
    r"권한이\s*없",
    r"해당\s*내용은?\s*(없|찾을\s*수\s*없)",
    r"상담(사|원)에?\s*(연결|문의)",
]
_REFUSAL_RE = re.compile("|".join(_REFUSAL_PATTERNS))

_APPROPRIATE_PROMPT = """
콜봇이 아래 [답변]에서 정보 제공을 거절하거나 모른다고 했습니다.
[컨텍스트]에 이 [질문]에 답할 정보가 실제로 없어서 거절이 정당한지 판정하세요.
(이 거절이 정당한지 = 컨텍스트에 근거가 없어 거절·안내가 옳은 행동인지)

[질문]
{query}

[컨텍스트]
{contexts}

[답변]
{answer}

판정 기준:
- 컨텍스트에 답할 근거가 없는데 거절 → 정당 (appropriate=true)
- 컨텍스트에 근거가 있는데도 거절 → 부당 (appropriate=false, 실제론 답해야 함)

출력 형식 (JSON):
{{"appropriate": true/false, "reasoning": "근거(한국어)"}}
"""


class AbstentionResult:
    """거절 판정 결과 (경량 컨테이너)."""

    __slots__ = ("is_abstention", "appropriate", "reasoning")

    def __init__(self, is_abstention: bool, appropriate: bool, reasoning: str) -> None:
        self.is_abstention = is_abstention
        self.appropriate = appropriate
        self.reasoning = reasoning


class AbstentionDetector:
    """거절 탐지(정규식) + 정당성 판정(LLM)."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    @staticmethod
    def is_abstention(answer: str) -> bool:
        """정규식으로 거절/모름/보류 신호 즉시 탐지 (LLM 불필요)."""
        return bool(_REFUSAL_RE.search(answer or ""))

    async def assess(self, query: str, answer: str, contexts_text: str) -> AbstentionResult:
        """답변이 거절이면 정당성까지 판정. 거절이 아니면 즉시 반환."""
        if not self.is_abstention(answer):
            return AbstentionResult(False, False, "거절 신호 없음")

        raw = await self.provider.complete(
            _APPROPRIATE_PROMPT.format(query=query, answer=answer, contexts=contexts_text),
            system=_SYSTEM, temperature=0.0, json_mode=True,
        )
        try:
            d = json.loads(raw)
            appropriate = bool(d.get("appropriate", False))
            reasoning = d.get("reasoning", "")
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.warning(f"거절 정당성 파싱 실패: {exc}")
            appropriate, reasoning = False, f"Parse error: {exc}"

        if appropriate:
            logger.info(f"[abstention] 정당한 거절 감지 — 감점 면제: {reasoning[:60]}")
        return AbstentionResult(True, appropriate, reasoning)
