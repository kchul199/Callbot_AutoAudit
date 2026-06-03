"""
cp4_evaluator/nugget.py
Nugget 기반 context recall (TREC RAG 2024 스타일, GT-free).

절차:
  1. 질문에 답하기 위해 필요한 핵심 정보 조각(nugget)을 LLM으로 추출
  2. 각 nugget이 검색 컨텍스트에 존재하는지 NLI 판정
  3. context_recall = 컨텍스트에서 발견된 nugget 비율

claim 분해(faithfulness)와 쌍대(dual): claim=답변→컨텍스트, nugget=질문→컨텍스트.
"""
from __future__ import annotations

import json

from AutoAudit.app.core.llm_client import LLMProvider
from AutoAudit.app.core.logger import get_logger
from AutoAudit.app.core.types import MetricScore, Nugget, RetrievalResult
from AutoAudit.app.cp4_evaluator.options import NuggetOptions

logger = get_logger(__name__)

_SYSTEM = "당신은 정보 검색 평가 전문가입니다. 반드시 유효한 JSON만 반환하세요."

_NUGGET_EXTRACT = """
다음 [질문](과 모범 답변)에 제대로 답하기 위해 반드시 필요한 핵심 정보 조각(nugget)을 추출하세요.
- 각 nugget은 검증 가능한 단일 정보 단위입니다.
- 최대 {max_n}개.

[질문]
{query}

[참고 답변]
{answer}

출력 (JSON): {{"nuggets": ["정보1", "정보2", ...]}}
"""

_NUGGET_MATCH = """
아래 [컨텍스트]에 각 [정보 조각]이 포함(명시 또는 함의)되어 있는지 판정하세요.
외부 지식 금지, 오직 컨텍스트 기반.

[컨텍스트]
{contexts}

[정보 조각 목록]
{nuggets}

출력 (JSON):
{{"matches": [{{"nugget": "원문", "found": true/false, "chunk_hint": "근거 일부 또는 빈문자열"}}]}}
"""


class NuggetRecallEvaluator:
    def __init__(self, provider: LLMProvider, options: NuggetOptions) -> None:
        self.provider = provider
        self.opts = options

    async def evaluate(
        self, query: str, answer: str, retrieval: RetrievalResult
    ) -> tuple[MetricScore, list[Nugget]]:
        contexts_text = self._format_contexts(retrieval)
        nugget_texts = await self._extract(query, answer)
        if not nugget_texts:
            return (
                MetricScore(metric="context_recall", score=1.0,
                            reasoning="추출된 nugget 없음", method="nugget"),
                [],
            )
        nuggets = await self._match(nugget_texts, contexts_text)
        found = sum(1 for n in nuggets if n.found_in_context)
        recall = found / len(nuggets) if nuggets else 0.0
        score = MetricScore(
            metric="context_recall",
            score=round(recall, 4),
            reasoning=f"nugget {len(nuggets)}개 중 {found}개 검색됨",
            method="nugget",
            confidence=1.0,
        )
        return score, nuggets

    async def _extract(self, query: str, answer: str) -> list[str]:
        raw = await self.provider.complete(
            _NUGGET_EXTRACT.format(query=query, answer=answer, max_n=self.opts.max_nuggets),
            system=_SYSTEM, temperature=0.0, json_mode=True,
        )
        try:
            data = json.loads(raw)
            return [n.strip() for n in data.get("nuggets", []) if isinstance(n, str) and n.strip()][
                : self.opts.max_nuggets
            ]
        except (json.JSONDecodeError, AttributeError):
            return []

    async def _match(self, nugget_texts: list[str], contexts_text: str) -> list[Nugget]:
        block = "\n".join(f"{i+1}. {n}" for i, n in enumerate(nugget_texts))
        raw = await self.provider.complete(
            _NUGGET_MATCH.format(contexts=contexts_text, nuggets=block),
            system=_SYSTEM, temperature=0.0, json_mode=True,
        )
        by_text: dict[str, dict] = {}
        try:
            for m in json.loads(raw).get("matches", []):
                if isinstance(m, dict) and "nugget" in m:
                    by_text[m["nugget"].strip()] = m
        except (json.JSONDecodeError, AttributeError):
            pass

        out: list[Nugget] = []
        for text in nugget_texts:
            m = by_text.get(text.strip())
            out.append(
                Nugget(
                    text=text,
                    found_in_context=bool(m.get("found", False)) if m else False,
                    matched_chunk_id=(m.get("chunk_hint") or None) if m else None,
                )
            )
        return out

    @staticmethod
    def _format_contexts(retrieval: RetrievalResult) -> str:
        parts = [f"[{i+1}] {c.content}" for i, c in enumerate(retrieval.contexts)]
        return "\n\n".join(parts) if parts else "(컨텍스트 없음)"
