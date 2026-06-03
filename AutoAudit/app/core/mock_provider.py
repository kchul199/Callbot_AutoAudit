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
        # 프롬프트 유형별 분기 (judge/faithfulness/nugget/domain/CoT/역방향 프롬프트 인식)
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
        # ── CoT 프롬프트 분기 ──
        if "Step 1 — 질문 의도 파악" in prompt:      # CoT answer_relevance
            return self._cot_answer_relevance(prompt)
        if "Step 1 — 각 컨텍스트 청크 유용성 판정" in prompt:  # CoT context_precision
            return self._cot_context_precision(prompt)
        if "Step 1 — 필요 정보 목록화" in prompt:    # CoT context_recall
            return self._cot_context_recall(prompt)
        # ── 역방향 검증 프롬프트 분기 ──
        if "역방향 검증" in prompt and "도출 가능한지" in prompt:  # reverse faithfulness
            return self._reverse_faithfulness(prompt)
        if "역방향 검증" in prompt and "역추론" in prompt:  # reverse answer_relevance
            return self._reverse_answer_relevance(prompt)
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

    # ── CoT 응답 생성기 ──

    def _cot_answer_relevance(self, prompt: str) -> str:
        """CoT answer_relevance: 단계별 추론 포함 응답"""
        answer = self._extract_section(prompt, "[답변]")
        query = self._extract_section(prompt, "[질문]")
        a_tok, q_tok = _tokens(answer), _tokens(query)
        overlap = (len(a_tok & q_tok) / len(a_tok)) if a_tok else 0.7
        score = round(min(1.0, 0.5 + overlap / 2), 2)
        sents = [s.strip() for s in re.split(r"[.!?。\n]", answer) if len(s.strip()) > 3][:3]
        return json.dumps({
            "step1_intent": "mock 질문 의도 분석",
            "step2_items": sents or ["mock 답변 항목"],
            "step3_missing": [],
            "step3_irrelevant": [],
            "score": score,
            "reasoning": "mock CoT answer_relevance 평가",
            "grounding_chunks": [],
        })

    def _cot_context_precision(self, prompt: str) -> str:
        """CoT context_precision: 청크별 유용성 판정 포함 응답"""
        contexts = self._extract_section(prompt, "[검색된 컨텍스트]")
        query = self._extract_section(prompt, "[질문]")
        q_tok = _tokens(query)
        # [1], [2], ... 청크 감지
        chunk_count = contexts.count("\n\n") + 1
        verdicts = {}
        useful_count = 0
        for i in range(1, min(chunk_count + 1, 6)):
            chunk_text = self._extract_section(contexts, f"[{i}]")
            is_useful = bool(_tokens(chunk_text) & q_tok)
            verdicts[f"[{i}]"] = "유용 — mock" if is_useful else "불필요 — mock"
            if is_useful:
                useful_count += 1
        total = max(1, chunk_count)
        score = round(useful_count / total, 2)
        return json.dumps({
            "step1_verdicts": verdicts,
            "step2_ratio": f"유용{useful_count} / 전체{total}",
            "useful_chunks": [f"[{i+1}]" for i in range(useful_count)],
            "score": score,
            "reasoning": "mock CoT context_precision 평가",
            "grounding_chunks": [],
        })

    def _cot_context_recall(self, prompt: str) -> str:
        """CoT context_recall: 필요 정보 커버리지 포함 응답"""
        query = self._extract_section(prompt, "[질문]")
        contexts = self._extract_section(prompt, "[검색된 컨텍스트]")
        ctx_tok = _tokens(contexts)
        q_sents = [s.strip() for s in re.split(r"[.!?。\n\s]", query) if len(s.strip()) > 2][:4]
        required = q_sents or ["mock 필요 정보1", "mock 필요 정보2"]
        coverage = {r: ("있음" if bool(_tokens(r) & ctx_tok) else "없음") for r in required}
        found = sum(1 for v in coverage.values() if v == "있음")
        score = round(found / max(1, len(required)), 2)
        return json.dumps({
            "step1_required": required,
            "step2_coverage": coverage,
            "step3_ratio": f"포함{found} / 전체{len(required)}",
            "score": score,
            "reasoning": "mock CoT context_recall 평가",
            "grounding_chunks": [],
        })

    # ── 역방향 검증 응답 생성기 ──

    def _reverse_faithfulness(self, prompt: str) -> str:
        """역방향 faithfulness: 컨텍스트→답변 도출 가능성"""
        answer = self._extract_section(prompt, "[답변]")
        contexts = self._extract_section(prompt, "[컨텍스트]")
        a_tok, ctx_tok = _tokens(answer), _tokens(contexts)
        overlap = (len(a_tok & ctx_tok) / len(a_tok)) if a_tok else 0.7
        score = round(min(1.0, 0.5 + overlap / 2), 2)
        sents = [s.strip() for s in re.split(r"[.!?。\n]", answer) if len(s.strip()) > 3][:3]
        derivability = {s[:30]: ("가능" if bool(_tokens(s) & ctx_tok) else "불가 — mock") for s in sents}
        return json.dumps({
            "step1_derivability": derivability,
            "step2_external_knowledge": [],
            "score": score,
            "reasoning": "mock 역방향 faithfulness 평가",
            "grounding_chunks": [],
        })

    def _reverse_answer_relevance(self, prompt: str) -> str:
        """역방향 answer_relevance: 답변→질문 역추론"""
        answer = self._extract_section(prompt, "[답변]")
        query = self._extract_section(prompt, "[질문]")
        a_tok, q_tok = _tokens(answer), _tokens(query)
        overlap = (len(a_tok & q_tok) / len(a_tok)) if a_tok else 0.7
        score = round(min(1.0, 0.5 + overlap / 2), 2)
        keywords = list(a_tok)[:5]
        match_level = "높음" if overlap > 0.6 else "중간" if overlap > 0.3 else "낮음"
        return json.dumps({
            "step1_keywords": keywords,
            "step2_inferred_question": f"mock 역추론 질문 (overlap={overlap:.2f})",
            "step2_match_level": match_level,
            "score": score,
            "reasoning": "mock 역방향 answer_relevance 평가",
            "grounding_chunks": [],
        })


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
