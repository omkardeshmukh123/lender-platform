"""
backend/pipeline/circuit_breaker.py
=====================================
Circuit breaker for the Gemini API.

Problem:
  If Gemini is rate-limited or unavailable, every extraction task hard-fails.
  The entire daily_refresh DAG stops, leaving all lenders with stale data.
  No recovery is attempted — the operator finds out the next morning.

Solution:
  The GeminiCircuitBreaker wraps every Gemini call. It tracks consecutive
  failures. After `failure_threshold` failures, it opens the circuit:
    - All calls immediately raise CircuitOpenError (no network call made)
    - After `reset_timeout` seconds, the circuit moves to HALF-OPEN
    - One probe call is allowed; if it succeeds, circuit closes
    - If it fails, circuit re-opens for another reset_timeout interval

States:
  CLOSED   → normal operation, failures counted
  OPEN     → all calls blocked, reset timer running
  HALF_OPEN → one probe call allowed

Thread safety:
  Uses threading.Lock — safe for Airflow LocalExecutor and CeleryExecutor
  (each worker process has its own instance; not shared across processes).

Usage:
  from pipeline.circuit_breaker import GeminiCircuitBreaker, CircuitOpenError

  _gemini_cb = GeminiCircuitBreaker(failure_threshold=5, reset_timeout=300)

  def call_gemini_safely(fn, *args, **kwargs):
      try:
          return _gemini_cb.call(fn, *args, **kwargs)
      except CircuitOpenError:
          # Circuit open — skip this lender, do NOT fail the whole DAG
          logger.warning("Gemini circuit open — skipping lender")
          return None
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is attempted while the circuit is open."""


class GeminiCircuitBreaker:
    """
    Thread-safe circuit breaker for external API calls (Gemini).

    Args:
        failure_threshold: consecutive failures before opening (default 5)
        reset_timeout:     seconds before trying again after opening (default 300)
        success_threshold: consecutive successes in HALF_OPEN before closing (default 2)
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout:     float = 300.0,
        success_threshold: int = 2,
    ) -> None:
        self._failure_threshold  = failure_threshold
        self._reset_timeout      = reset_timeout
        self._success_threshold  = success_threshold

        self._state              = CircuitState.CLOSED
        self._failure_count      = 0
        self._success_count      = 0
        self._opened_at: float | None = None
        self._lock               = threading.Lock()

        # Stats — never reset, used for monitoring/logging
        self.total_calls         = 0
        self.total_failures      = 0
        self.total_circuit_opens = 0
        self.total_blocked       = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def call(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        """
        Execute fn(*args, **kwargs) through the circuit breaker.

        Raises:
            CircuitOpenError: if circuit is open (caller should skip, not fail)
            Exception:        any exception from fn propagates after being counted
        """
        with self._lock:
            self._check_state()
            if self._state == CircuitState.OPEN:
                self.total_blocked += 1
                raise CircuitOpenError(
                    f"Gemini circuit is OPEN — will retry after "
                    f"{self._seconds_until_reset():.0f}s"
                )
            self.total_calls += 1

        try:
            result = fn(*args, **kwargs)
            with self._lock:
                self._on_success()
            return result
        except CircuitOpenError:
            raise
        except Exception as exc:
            with self._lock:
                self._on_failure(exc)
            raise

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._check_state()
            return self._state

    def stats(self) -> dict:
        with self._lock:
            self._check_state()
            return {
                "state":               self._state.value,
                "failure_count":       self._failure_count,
                "total_calls":         self.total_calls,
                "total_failures":      self.total_failures,
                "total_circuit_opens": self.total_circuit_opens,
                "total_blocked":       self.total_blocked,
                "seconds_until_reset": self._seconds_until_reset(),
            }

    def reset(self) -> None:
        """Manually close the circuit (e.g., after fixing the Gemini API key)."""
        with self._lock:
            self._close()
            logger.info("Gemini circuit manually reset to CLOSED")

    # ── State machine ───────────────────────────────────────────────────────────

    def _check_state(self) -> None:
        """Transition OPEN → HALF_OPEN if reset timeout has elapsed."""
        if (
            self._state == CircuitState.OPEN
            and self._opened_at is not None
            and time.monotonic() - self._opened_at >= self._reset_timeout
        ):
            self._state         = CircuitState.HALF_OPEN
            self._success_count = 0
            logger.info(
                "Gemini circuit → HALF_OPEN (probe call allowed after %.0fs)",
                self._reset_timeout,
            )

    def _on_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self._success_threshold:
                self._close()
                logger.info(
                    "Gemini circuit → CLOSED (%d consecutive successes in HALF_OPEN)",
                    self._success_threshold,
                )
        elif self._state == CircuitState.CLOSED:
            # Reset failure count on any success
            self._failure_count = 0

    def _on_failure(self, exc: Exception) -> None:
        self.total_failures += 1
        if self._state == CircuitState.HALF_OPEN:
            # Probe failed — re-open immediately
            self._open(exc)
            return

        self._failure_count += 1
        if self._failure_count >= self._failure_threshold:
            self._open(exc)

    def _open(self, exc: Exception) -> None:
        self._state      = CircuitState.OPEN
        self._opened_at  = time.monotonic()
        self.total_circuit_opens += 1
        logger.error(
            "Gemini circuit → OPEN after %d failures (last: %s: %s). "
            "Lenders will be skipped until circuit closes in %.0fs.",
            self._failure_count, type(exc).__name__, exc, self._reset_timeout,
        )
        # Emit to pipeline metrics so Grafana can alert
        try:
            import os
            import psycopg2
            db_url = os.environ.get("DATABASE_URL", "")
            if db_url:
                conn = psycopg2.connect(db_url, connect_timeout=5)
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO pipeline_runs "
                        "(pipeline, run_date, dag_run_id, total_processed, failed_count, metadata) "
                        "VALUES (%s, CURRENT_DATE, %s, 0, 0, %s)",
                        (
                            "gemini_circuit_breaker",
                            "",
                            '{"event":"circuit_open","failure_count":' + str(self._failure_count) + "}",
                        ),
                    )
                conn.commit()
                conn.close()
        except Exception:
            pass  # Never crash on metrics write

    def _close(self) -> None:
        self._state         = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at     = None

    def _seconds_until_reset(self) -> float:
        if self._state != CircuitState.OPEN or self._opened_at is None:
            return 0.0
        elapsed = time.monotonic() - self._opened_at
        return max(0.0, self._reset_timeout - elapsed)


# ── Module-level singleton ─────────────────────────────────────────────────────
# One circuit breaker instance per process. In CeleryExecutor, each worker
# process has its own instance — the circuit opens independently per worker.
# This is acceptable: a worker that hits quota opens its circuit; others continue.

_gemini_circuit: GeminiCircuitBreaker | None = None


def get_gemini_circuit(
    failure_threshold: int = 5,
    reset_timeout: float = 300.0,
) -> GeminiCircuitBreaker:
    """Return (and lazily create) the module-level Gemini circuit breaker."""
    global _gemini_circuit
    if _gemini_circuit is None:
        _gemini_circuit = GeminiCircuitBreaker(
            failure_threshold=failure_threshold,
            reset_timeout=reset_timeout,
        )
    return _gemini_circuit
