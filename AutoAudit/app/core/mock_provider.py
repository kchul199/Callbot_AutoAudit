"""
core/mock_provider.py
로컬 개발/테스트용 Mock LLM Provider + In-Memory 벡터 스토어.

API 키·외부 의존성(openai/chromadb/sentence-transformers) 없이 전체 파이프라인을
end-to-end로 돌려보기 위한 결정적(deterministic) 구현.

- MockProvider.complete: 프롬프트 패턴(claim/nugget/verdict/메트릭 JSON)을 인식해
  그럴듯한 휴리스틱 JSON 응답을 생성. 토큰 중첩 기반이라 결정적·무비용.
- MockProvider.embed: 해시 기반 결정적 임베딩 (동일 텍스트 → 동일 벡터).
- InMemoryVectorStore: chromadb.Collection 호환 인터페이스 (add/upsert/get/query).
"""
from __future__ import annotations

import hashlib
import json
import math
import re

from AutoAudit.app.core.logger import get_logger

logger = get_logger(__name__)

_DIM = 64
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")


def _tokens(text: str) -> set[str]:
    base = set(_TOKEN_RE.findall(text.lower()))
    compact = re.sub(r"\s+", "", text)
    base |= {compact[i : i + 2] for i in range(len(compact) - 1)}
    return base


def deterministic_embedding(text: str, dim: int = _DIM) -> list[float]:
    """텍스트 토큰을 해시 버킷에 매핑한 L2 정규화 임베딩 (결정적)."""
    vec = [0.0] * dim
    for tok in _tokens(text) or {text}:
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class MockProvider:
    """LLMProvider 프로토콜 호환 — 결정적 휴리스틱 응답."""

    def __init__(self, cost_tracker=None) -> None:
        self.cost = cost_tracker
        self.dimensions = _DIM

    async def embed(self, text: str) -> list[float]:
        return deterministic_embedding(text)

    async def complete(
        self, prompt: str, *, system: str | None = None,
        temperature: float = 0.0, max_tokens: int = 2048, json_mode: bool = False,
    ) -> str:
        # 프롬프트 유형별 분기 (judge/faithfulness/nugget/domain 프롬프트 인식)
        if "원자적 주장(claim)" in prompt:
            return self._claims(prompt)
        if "지지되는지 판정" in prompt and "주장 목록" in prompt:
            return self._verdicts(prompt)
        if "핵심 정보 조각(nugget)을 추출" in prompt:
            return self._nuggets(prompt)
        if "정보 조각" in prompt and "포함" in prompt:
            return self._matches(prompt)
        if "재작성" in prompt:                       # multi-query
            return self._rewrites(prompt)
        if "이상적인 답변" in prompt:                 # HyDE
            return "콜봇 매뉴얼 기준 표준 안내 답변입니다."
        if "모순되는 진술" in prompt:                 # 멀티턴 일관성
            return json.dumps({"consistent": True, "score": 0.9, "reasoning": "mock 일관성", "conflicts": []})
        if "컴플라이언스 위반" in prompt:             # 안전성
            return json.dumps({"safe": True, "score": 0.95, "reasoning": "mock 안전", "risks": []})
        if json_mode:                                # 일반 메트릭 평가
            return self._metric_score(prompt)
        return "mock 응답"

    # ---- 휴리스틱 응답 생성기 ----

    @staticmethod
    def _extract_section(prompt: str, header: str) -> str:
        """[헤더] 다음 블록 텍스트 추출 (대략)."""
        idx = prompt.find(header)
        if idx == -1:
            return ""
        return prompt[idx + len(header): idx + len(header) + 400]

    def _metric_score(self, prompt: str) -> str:
        """질문/답변/컨텍스트 토큰 중첩으로 0~1 점수 근사."""
        answer = self._extract_section(prompt, "[답변]")
        contexts = self._extract_section(prompt, "[컨텍스트]") or self._extract_section(prompt, "[검색된 컨텍스트]")
        query = self._extract_section(prompt, "[질문]")
        ref = contexts or query
        a, c = _tokens(answer), _tokens(ref)
        overlap = (len(a & c) / len(a)) if a else 0.8
        score = round(min(1.0, 0.5 + overlap / 2), 2)   # 0.5~1.0 범위
        return json.dumps({"score": score, "reasoning": "mock 휴리스틱 평가", "grounding_chunks": []})

    def _claims(self, prompt: str) -> str:
        answer = self._extract_section(prompt, "[답변]")
        sents = [s.strip() for s in re.split(r"[.!?。\n]", answer) if len(s.strip()) > 3][:5]
        return json.dumps({"claims": sents or ["mock claim"]})

    def _verdicts(self, prompt: str) -> str:
        block = self._extract_section(prompt, "[주장 목록]")
        claims = [re.sub(r"^\d+\.\s*", "", c.strip()) for c in block.splitlines() if c.strip()][:5]
        contexts = self._extract_section(prompt, "[컨텍스트]")
        ctx_tokens = _tokens(contexts)
        verdicts = []
        for c in claims:
            supported = bool(_tokens(c) & ctx_tokens)
            verdicts.append({
                "claim": c,
                "verdict": "supported" if supported else "unsupported",
                "reasoning": "mock NLI",
            })
        return json.dumps({"verdicts": verdicts})

    def _nuggets(self, prompt: str) -> str:
        ans = self._extract_section(prompt, "[참고 답변]")
        sents = [s.strip() for s in re.split(r"[.!?。\n]", ans) if len(s.strip()) > 3][:5]
        return json.dumps({"nuggets": sents or ["mock nugget"]})

    def _matches(self, prompt: str) -> str:
        block = self._extract_section(prompt, "[정보 조각 목록]")
        contexts = self._extract_section(prompt, "[컨텍스트]")
        ctx_tokens = _tokens(contexts)
        nuggets = [re.sub(r"^\d+\.\s*", "", n.strip()) for n in block.splitlines() if n.strip()][:8]
        matches = [
            {"nugget": n, "found": bool(_tokens(n) & ctx_tokens), "chunk_hint": ""}
            for n in nuggets
        ]
        return json.dumps({"matches": matches})

    @staticmethod
    def _rewrites(prompt: str) -> str:
        return "변형 질의 1\n변형 질의 2\n변형 질의 3"


# ============================================================
# In-Memory 벡터 스토어 (chromadb.Collection 호환 부분집합)
# ============================================================

class InMemoryVectorStore:
    """add/upsert/get/query 만 지원하는 경량 코사인 검색 스토어."""

    def __init__(self) -> None:
        self._ids: list[str] = []
        self._embs: list[list[float]] = []
        self._docs: list[str] = []
        self._metas: list[dict] = []

    def upsert(self, ids, embeddings, documents, metadatas):
        for i, _id in enumerate(ids):
            if _id in self._ids:
                idx = self._ids.index(_id)
                self._embs[idx] = embeddings[i]
                self._docs[idx] = documents[i]
                self._metas[idx] = metadatas[i]
            else:
                self._ids.append(_id)
                self._embs.append(embeddings[i])
                self._docs.append(documents[i])
                self._metas.append(metadatas[i])

    # chromadb 호환 시그니처
    def add(self, ids, embeddings, documents, metadatas):
        self.upsert(ids, embeddings, documents, metadatas)

    def get(self, include=None):
        return {
            "ids": list(self._ids),
            "documents": list(self._docs),
            "metadatas": list(self._metas),
        }

    def query(self, query_embeddings, n_results=10, include=None):
        q = query_embeddings[0]
        scored = [
            (self._cosine(q, e), i) for i, e in enumerate(self._embs)
        ]
        scored.sort(reverse=True)
        top = scored[:n_results]
        return {
            "ids": [[self._ids[i] for _, i in top]],
            "documents": [[self._docs[i] for _, i in top]],
            "metadatas": [[self._metas[i] for _, i in top]],
            "distances": [[1.0 - s for s, _ in top]],   # cosine distance
        }

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)
