"""
core/llm_client.py
LLM/임베딩 Provider 추상화 계층.

설계 목표:
  - OpenAI / Azure / Anthropic / 로컬 모델을 settings.yaml 한 줄로 전환
  - 토큰·비용 추적 (예산 상한 = circuit breaker)
  - 429(RateLimit) 등 일시 오류만 선택적 재시도 (400 등은 즉시 실패)
  - 전 CP가 동일 인터페이스로 주입받아 사용
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from AutoAudit.app.core.config import get as cfg_get
from AutoAudit.app.core.logger import get_logger

logger = get_logger(__name__)


# ============================================================
# 예산 초과 예외 (circuit breaker)
# ============================================================

class BudgetExceededError(RuntimeError):
    """일일/실행 예산 상한 초과 시 발생 → 파이프라인 중단"""


# ============================================================
# 비용 추적기 (스레드/코루틴 안전)
# ============================================================

# USD per 1M tokens (근사치 — settings에서 override 가능)
_DEFAULT_PRICING = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "text-embedding-3-large": {"input": 0.13, "output": 0.0},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
    # Anthropic
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    # Google Gemini
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
}


@dataclass
class UsageStats:
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_tokens: int = 0
    total_cost_usd: float = 0.0
    call_count: int = 0


class CostTracker:
    """실행 단위 비용 누적 + 예산 상한 강제"""

    def __init__(self, budget_usd: float | None = None, pricing: dict | None = None) -> None:
        self.budget_usd = budget_usd
        self.pricing = pricing or _DEFAULT_PRICING
        self.stats = UsageStats()
        self._lock = threading.Lock()

    def record(self, model: str, input_tokens: int, output_tokens: int = 0) -> None:
        price = self.pricing.get(model, {"input": 0.0, "output": 0.0})
        cost = (input_tokens / 1_000_000) * price["input"] + (
            output_tokens / 1_000_000
        ) * price["output"]
        with self._lock:
            self.stats.input_tokens += input_tokens
            self.stats.output_tokens += output_tokens
            self.stats.total_cost_usd += cost
            self.stats.call_count += 1
            if self.budget_usd is not None and self.stats.total_cost_usd > self.budget_usd:
                raise BudgetExceededError(
                    f"예산 초과: ${self.stats.total_cost_usd:.4f} > ${self.budget_usd:.2f} "
                    f"(call #{self.stats.call_count})"
                )

    def summary(self) -> dict[str, Any]:
        return {
            "input_tokens": self.stats.input_tokens,
            "output_tokens": self.stats.output_tokens,
            "embedding_tokens": self.stats.embedding_tokens,
            "total_cost_usd": round(self.stats.total_cost_usd, 4),
            "call_count": self.stats.call_count,
        }


# ============================================================
# 선택적 재시도: 일시 오류만 (429/5xx/timeout)
# ============================================================

def _is_transient(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    transient_markers = ("ratelimit", "timeout", "apiconnection", "internalserver", "serviceunavailable")
    if any(m in name for m in transient_markers):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return status in (408, 409, 429, 500, 502, 503, 504)


# ============================================================
# Provider 인터페이스
# ============================================================

@runtime_checkable
class LLMProvider(Protocol):
    async def complete(
        self, prompt: str, *, system: str | None = None,
        temperature: float = 0.0, max_tokens: int = 2048, json_mode: bool = False,
    ) -> str: ...

    async def embed(self, text: str) -> list[float]: ...


# ============================================================
# OpenAI / Azure 구현
# ============================================================

class OpenAIProvider:
    """OpenAI 및 Azure OpenAI 공용 구현"""

    def __init__(self, cost_tracker: CostTracker, use_azure: bool = False) -> None:
        from openai import AsyncAzureOpenAI, AsyncOpenAI

        self.model: str = cfg_get("llm.model", default="gpt-4o")
        self.embedding_model: str = cfg_get("llm.embedding_model", default="text-embedding-3-large")
        self.dimensions: int = cfg_get("llm.embedding_dimensions", default=3072)
        self.cost = cost_tracker
        self.max_attempts: int = cfg_get("llm.max_retries", default=4)

        if use_azure:
            import os
            self.client = AsyncAzureOpenAI(
                azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
                api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
                api_version=cfg_get("llm.azure_api_version", default="2024-06-01"),
            )
        else:
            self.client = AsyncOpenAI()

    async def _retry(self, coro_factory):
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=20),
            retry=retry_if_exception(_is_transient),
            reraise=True,
        ):
            with attempt:
                return await coro_factory()

    async def complete(
        self, prompt: str, *, system: str | None = None,
        temperature: float = 0.0, max_tokens: int = 2048, json_mode: bool = False,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = dict(
            model=self.model, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        async def _call():
            return await self.client.chat.completions.create(**kwargs)

        resp = await self._retry(_call)
        usage = getattr(resp, "usage", None)
        if usage:
            self.cost.record(self.model, usage.prompt_tokens, usage.completion_tokens)
        return resp.choices[0].message.content.strip()

    async def embed(self, text: str) -> list[float]:
        async def _call():
            return await self.client.embeddings.create(
                input=text, model=self.embedding_model, dimensions=self.dimensions,
            )

        resp = await self._retry(_call)
        usage = getattr(resp, "usage", None)
        if usage:
            self.cost.record(self.embedding_model, usage.prompt_tokens, 0)
        return resp.data[0].embedding


# ============================================================
# Anthropic 구현 (LLM only — 임베딩은 OpenAI fallback)
# ============================================================

class AnthropicProvider:
    """Claude 기반 Judge. 임베딩은 미지원 → OpenAI로 위임."""

    def __init__(self, cost_tracker: CostTracker) -> None:
        from anthropic import AsyncAnthropic

        self.model: str = cfg_get("llm.model", default="claude-sonnet-4-5")
        self.cost = cost_tracker
        self.max_attempts: int = cfg_get("llm.max_retries", default=4)
        self.client = AsyncAnthropic()
        self._embed_fallback = OpenAIProvider(cost_tracker)

    async def complete(
        self, prompt: str, *, system: str | None = None,
        temperature: float = 0.0, max_tokens: int = 2048, json_mode: bool = False,
    ) -> str:
        if json_mode:
            prompt += "\n\n반드시 유효한 JSON 객체만 출력하세요."

        async def _call():
            return await self.client.messages.create(
                model=self.model, max_tokens=max_tokens, temperature=temperature,
                system=system or "You are a helpful assistant.",
                messages=[{"role": "user", "content": prompt}],
            )

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=20),
            retry=retry_if_exception(_is_transient), reraise=True,
        ):
            with attempt:
                resp = await _call()
        usage = getattr(resp, "usage", None)
        if usage:
            self.cost.record(self.model, usage.input_tokens, usage.output_tokens)
        return resp.content[0].text.strip()

    async def embed(self, text: str) -> list[float]:
        return await self._embed_fallback.embed(text)


# ============================================================
# Google Gemini 구현 (LLM only — 임베딩은 OpenAI fallback)
# ============================================================

class GoogleProvider:
    """Google Gemini 기반 Judge. 임베딩은 OpenAI로 위임."""

    def __init__(self, cost_tracker: CostTracker) -> None:
        import os

        from google import genai

        self.model: str = cfg_get("llm.model", default="gemini-2.5-pro")
        self.cost = cost_tracker
        self.max_attempts: int = cfg_get("llm.max_retries", default=4)
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.client = genai.Client(api_key=api_key)
        self._embed_fallback = OpenAIProvider(cost_tracker)

    async def complete(
        self, prompt: str, *, system: str | None = None,
        temperature: float = 0.0, max_tokens: int = 2048, json_mode: bool = False,
    ) -> str:
        from google.genai import types as gx

        full = prompt
        if json_mode:
            full += "\n\n반드시 유효한 JSON 객체만 출력하세요."
        config = gx.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system or "You are a helpful assistant.",
            response_mime_type="application/json" if json_mode else None,
        )

        async def _call():
            # google-genai 비동기 인터페이스
            return await self.client.aio.models.generate_content(
                model=self.model, contents=full, config=config,
            )

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=20),
            retry=retry_if_exception(_is_transient), reraise=True,
        ):
            with attempt:
                resp = await _call()
        usage = getattr(resp, "usage_metadata", None)
        if usage:
            self.cost.record(
                self.model,
                getattr(usage, "prompt_token_count", 0) or 0,
                getattr(usage, "candidates_token_count", 0) or 0,
            )
        return (resp.text or "").strip()

    async def embed(self, text: str) -> list[float]:
        return await self._embed_fallback.embed(text)


# ============================================================
# 팩토리
# ============================================================

def create_provider(cost_tracker: CostTracker | None = None) -> LLMProvider:
    """settings.yaml의 llm.provider 값에 따라 Provider 생성"""
    if cost_tracker is None:
        budget = cfg_get("llm.budget_usd", default=None)
        cost_tracker = CostTracker(budget_usd=budget)

    import os
    provider = cfg_get("llm.provider", default="openai").lower()
    # 환경변수로 강제 mock 전환 (로컬 개발용)
    if os.environ.get("AUTOAUDIT_MOCK") == "1":
        provider = "mock"
    logger.info(f"LLM provider = {provider}")

    if provider == "mock":
        from AutoAudit.app.core.mock_provider import MockProvider
        return MockProvider(cost_tracker)
    if provider == "openai":
        return OpenAIProvider(cost_tracker)
    if provider == "azure":
        return OpenAIProvider(cost_tracker, use_azure=True)
    if provider == "anthropic":
        return AnthropicProvider(cost_tracker)
    if provider in ("google", "gemini"):
        return GoogleProvider(cost_tracker)
    raise ValueError(f"Unknown llm.provider: {provider}")
