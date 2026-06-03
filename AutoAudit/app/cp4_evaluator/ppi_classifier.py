"""
cp4_evaluator/ppi_classifier.py
PPI용 저비용 분류기 — LLM 호출 없이 휴리스틱으로 메트릭 점수 근사.

faithfulness/recall를 답변·질문·컨텍스트 간 토큰 중첩으로 빠르게 추정.
이 분류기로 전체를 예측하고, 소량만 LLM Judge로 보정(PPI) → 비용 절감.
"""
from __future__ import annotations

import re

from AutoAudit.app.core.types import QAPair

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")


def _tokens(text: str) -> set[str]:
    base = set(_TOKEN_RE.findall(text.lower()))
    # 한국어 보강: 2-gram
    compact = re.sub(r"\s+", "", text)
    bigrams = {compact[i : i + 2] for i in range(len(compact) - 1)}
    return base | bigrams


def _overlap(a: set[str], b: set[str]) -> float:
    if not a:
        return 0.0
    return len(a & b) / len(a)


class HeuristicClassifier:
    """토큰 중첩 기반 메트릭 근사 (0~1)"""

    def faithfulness(self, pair: QAPair) -> float:
        if pair.retrieval_result is None:
            return 0.0
        ans = _tokens(pair.bot_answer)
        ctx = set()
        for c in pair.retrieval_result.contexts:
            ctx |= _tokens(c.content)
        return round(_overlap(ans, ctx), 4)

    def context_recall(self, pair: QAPair) -> float:
        if pair.retrieval_result is None:
            return 0.0
        q = _tokens(pair.question)
        ctx = set()
        for c in pair.retrieval_result.contexts:
            ctx |= _tokens(c.content)
        return round(_overlap(q, ctx), 4)

    def score(self, pair: QAPair, metric: str) -> float:
        if metric == "faithfulness":
            return self.faithfulness(pair)
        if metric == "context_recall":
            return self.context_recall(pair)
        # 그 외 메트릭은 답변-질문 관련성으로 근사
        return round(_overlap(_tokens(pair.bot_answer), _tokens(pair.question)), 4)
