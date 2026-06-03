"""
core/async_utils.py
비동기 병렬 처리 헬퍼 — tenacity 재시도 데코레이터 포함
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterable
from typing import Any, TypeVar

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

T = TypeVar("T")


async def gather_with_concurrency(
    coros: Iterable[Coroutine[Any, Any, T]],
    concurrency: int = 5,
) -> list[T]:
    """
    최대 `concurrency` 개 코루틴을 동시에 실행.
    API Rate Limit 보호용.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(coro: Coroutine[Any, Any, T]) -> T:
        async with semaphore:
            return await coro

    return list(await asyncio.gather(*[bounded(c) for c in coros]))


def with_retry(
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 10.0,
    reraise: bool = True,
) -> Callable:
    """
    tenacity 기반 비동기 재시도 데코레이터 팩토리.

    사용 예:
        @with_retry(max_attempts=3)
        async def call_llm(...): ...
    """
    def decorator(func: Callable) -> Callable:
        import functools

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max_attempts),
                wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
                retry=retry_if_exception_type(Exception),
                reraise=reraise,
            ):
                with attempt:
                    return await func(*args, **kwargs)

        return wrapper

    return decorator
