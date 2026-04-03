"""
backend/pipeline/retry.py
==========================
Retry decorator and utilities for external API calls.

Problem:
  Transient failures (network blips, 429 rate limits, 503 overload) cause
  individual extraction tasks to fail permanently without any retry attempt.
  This wastes the entire chunk's work for a problem that resolves in seconds.

Solution:
  retry_with_backoff() wraps any callable and retries on specified exceptions
  with exponential backoff + jitter. Works with both sync and async functions.

Usage (sync — for Gemini/scraper calls in Airflow):
  from pipeline.retry import retry_with_backoff

  result = retry_with_backoff(
      extract_with_gemini,
      name, website, summary, focus, rate_lim,
      attempts=3,
      delay=10.0,
      backoff=2.0,
      exceptions=(ConnectionError, TimeoutError, google.api_core.exceptions.ResourceExhausted),
  )

Usage (async — for DB operations in FastAPI):
  result = await async_retry(my_async_fn, *args, attempts=3, delay=1.0)
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Callable, Tuple, Type

logger = logging.getLogger(__name__)


def retry_with_backoff(
    fn: Callable,
    *args: Any,
    attempts:    int   = 3,
    delay:       float = 10.0,     # initial delay seconds
    backoff:     float = 2.0,      # multiply delay by this each attempt
    jitter:      float = 0.25,     # random ±jitter fraction of delay
    exceptions:  Tuple[Type[Exception], ...] = (Exception,),
    label:       str   = "",       # for logging
    **kwargs: Any,
) -> Any:
    """
    Synchronous retry with exponential backoff + jitter.

    Args:
        fn:         callable to invoke
        *args:      positional args passed to fn
        attempts:   max number of attempts (default 3)
        delay:      initial wait in seconds between attempts (default 10)
        backoff:    delay multiplier per retry (default 2 → 10s, 20s, 40s)
        jitter:     randomize delay by ±jitter fraction (prevents thundering herd)
        exceptions: tuple of exception types to retry on
        label:      human-readable label for log messages
        **kwargs:   keyword args passed to fn

    Returns:
        fn(*args, **kwargs) result on success

    Raises:
        Last exception if all attempts exhausted
    """
    tag = f"[{label}] " if label else ""
    last_exc: Exception | None = None
    current_delay = delay

    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except exceptions as exc:
            last_exc = exc
            if attempt == attempts:
                logger.error(
                    "%sAll %d attempts failed. Last error: %s",
                    tag, attempts, exc,
                )
                break

            jitter_amount = current_delay * jitter * (2 * random.random() - 1)
            wait = max(0.1, current_delay + jitter_amount)

            logger.warning(
                "%sAttempt %d/%d failed (%s: %s). Retrying in %.1fs...",
                tag, attempt, attempts, type(exc).__name__, exc, wait,
            )
            time.sleep(wait)
            current_delay *= backoff

    raise last_exc  # type: ignore[misc]


async def async_retry(
    fn: Callable,
    *args: Any,
    attempts:   int   = 3,
    delay:      float = 1.0,
    backoff:    float = 2.0,
    jitter:     float = 0.20,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    label:      str   = "",
    **kwargs: Any,
) -> Any:
    """
    Async version of retry_with_backoff. Awaits fn and uses asyncio.sleep.
    """
    tag = f"[{label}] " if label else ""
    last_exc: Exception | None = None
    current_delay = delay

    for attempt in range(1, attempts + 1):
        try:
            return await fn(*args, **kwargs)
        except exceptions as exc:
            last_exc = exc
            if attempt == attempts:
                logger.error(
                    "%sAll %d attempts failed. Last error: %s",
                    tag, attempts, exc,
                )
                break

            jitter_amount = current_delay * jitter * (2 * random.random() - 1)
            wait = max(0.01, current_delay + jitter_amount)

            logger.warning(
                "%sAttempt %d/%d failed (%s: %s). Retrying in %.2fs...",
                tag, attempt, attempts, type(exc).__name__, exc, wait,
            )
            await asyncio.sleep(wait)
            current_delay *= backoff

    raise last_exc  # type: ignore[misc]
