"""
cp4_evaluator/faithfulness.py
RAGAS 스타일 claim 단위 NLI 분해 기반 Faithfulness 평가.

  1. Claim 추출: 답변을 검증 가능한 원자적 주장(claim) 목록으로 분해
  2. NLI 판정: 각 claim이 검색 컨텍스트로 entailment(지지)되는지 개별 판정
  3. 점수 산출: faithfulness = 지지된 claim 수 / 전체 claim 수

장점: 환각이 '어느 주장'인지 claim 단위로 특정 → Evidence View와 직결,
      결정적 비율 계산이라 LLM 점수 부여보다 분산이 작다.
"""
from __future__ import annotations

import json

from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.llm_client import LLMProvider
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import ClaimVerdict, MetricScore

logger = get_logger(__name__)

_SYSTEM = "당신은 RAG 사실성 검증 전문가입니다. 반드시 유효한 JSON만 반환하세요."

_CLAIM_EXTRACTION_PROMPT = """
다음 [답변]을 검증 가능한 원자적 주장(claim)들로 분해하세요.
- 각 claim은 하나의 독립된 사실/주장만 담습니다.
- 인사말·질문 되묻기 등 사실 주장이 아닌 문장은 제외합니다.
- 최대 {max_n}개까지만 추출합니다.

[답변]
{answer}

출력 형식 (JSON):
{{"claims": ["주장1", "주장2", ...]}}
"""

_NLI_VERDICT_PROMPT = """
아래 [컨텍스트]만을 근거로, 각 [주장]이 지지되는지 판정하세요.
- 컨텍스트에 명시/함의되면 "supported"
- 컨텍스트와 모순되면 "contradicted"
- 컨텍스트에 정보가 없으면 "unsupported"
- 외부 지식 사용 금지 (오직 컨텍스트 기반).

[컨텍스트]
{contexts}

[주장 목록]
{claims}

출력 형식 (JSON):
{{"verdicts": [
  {{"claim": "주장 원문", "verdict": "supported|unsupported|contradicted", "reasoning": "근거"}}
]}}
"""


class FaithfulnessEvaluator:
    """Claim 추출 → NLI 판정 → 비율 점수"""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.max_claims: int = cfg_get("cp4.faithfulness.max_claims", default=10)

    async def evaluate(self, answer: str, contexts_text: str) -> MetricScore:
        claims = await self._extract_claims(answer)
        if not claims:
            return MetricScore(
                metric="faithfulness", score=1.0,
                reasoning="검증 가능한 사실 주장이 없어 환각 위험 없음",
                method="claim_nli", claims=[],
            )

        verdicts = await self._verify_claims(claims, contexts_text)
        supported = sum(1 for v in verdicts if v.supported)
        score = supported / len(verdicts) if verdicts else 0.0

        contradicted = sum(1 for v in verdicts if v.verdict == "contradicted")
        unsupported = sum(1 for v in verdicts if v.verdict == "unsupported")
        reasoning = (
            f"claim {len(verdicts)}개 중 {supported}개 지지 "
            f"(모순 {contradicted}, 미지지 {unsupported})"
        )
        return MetricScore(
            metric="faithfulness",
            score=round(score, 4),
            reasoning=reasoning,
            claims=verdicts,
            confidence=1.0,
            method="claim_nli",
        )

    async def _extract_claims(self, answer: str) -> list[str]:
        raw = await self.provider.complete(
            _CLAIM_EXTRACTION_PROMPT.format(answer=answer, max_n=self.max_claims),
            system=_SYSTEM, temperature=0.0, json_mode=True,
        )
        try:
            data = json.loads(raw)
            return [c.strip() for c in data.get("claims", []) if isinstance(c, str) and c.strip()][
                : self.max_claims
            ]
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.warning(f"claim 추출 파싱 실패: {exc}")
            return []

    async def _verify_claims(self, claims: list[str], contexts_text: str) -> list[ClaimVerdict]:
        block = "\n".join(f"{i+1}. {c}" for i, c in enumerate(claims))
        raw = await self.provider.complete(
            _NLI_VERDICT_PROMPT.format(contexts=contexts_text, claims=block),
            system=_SYSTEM, temperature=0.0, json_mode=True,
        )
        return self._parse_verdicts(raw, claims)

    @staticmethod
    def _parse_verdicts(raw: str, claims: list[str]) -> list[ClaimVerdict]:
        try:
            verdicts_raw = json.loads(raw).get("verdicts", [])
        except (json.JSONDecodeError, AttributeError):
            verdicts_raw = []

        by_claim: dict[str, dict] = {}
        for v in verdicts_raw:
            if isinstance(v, dict) and "claim" in v:
                by_claim[v["claim"].strip()] = v

        results: list[ClaimVerdict] = []
        for claim in claims:
            v = by_claim.get(claim.strip())
            if v is None:
                results.append(
                    ClaimVerdict(claim=claim, supported=False, verdict="unsupported",
                                 reasoning="판정 누락 — 보수적 미지지 처리")
                )
                continue
            verdict = str(v.get("verdict", "unsupported")).lower()
            if verdict not in ("supported", "unsupported", "contradicted"):
                verdict = "unsupported"
            results.append(
                ClaimVerdict(
                    claim=claim,
                    supported=(verdict == "supported"),
                    verdict=verdict,
                    reasoning=v.get("reasoning", ""),
                )
            )
        return results
