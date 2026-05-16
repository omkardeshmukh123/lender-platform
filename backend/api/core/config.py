"""
backend/api/core/config.py
===========================
Single source of truth for all tunable configuration.
All values come from environment variables with safe defaults.
Import as: from core.config import cfg
"""

from __future__ import annotations
import os
import logging

logger = logging.getLogger(__name__)


def _int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        logger.warning("Config %s invalid — using default %s", key, default)
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        logger.warning("Config %s invalid — using default %s", key, default)
        return default


def _str(key: str, default: str) -> str:
    return os.environ.get(key, default)


class Config:
    # Database
    db_pool_min:        int   = _int("DB_POOL_MIN", 2)
    db_pool_max:        int   = _int("DB_POOL_MAX", 5)
    db_command_timeout: int   = _int("DB_COMMAND_TIMEOUT", 30)

    # Cache
    cache_ttl_match:    int   = _int("CACHE_TTL_MATCH",  300)
    cache_ttl_search:   int   = _int("CACHE_TTL_SEARCH", 120)
    cache_ttl_detail:   int   = _int("CACHE_TTL_DETAIL", 600)
    cache_ttl_stats:    int   = _int("CACHE_TTL_STATS",  300)
    cache_key_length:   int   = _int("CACHE_KEY_LENGTH",  32)

    # Sentry
    sentry_traces_rate: float = _float("SENTRY_TRACES_RATE", 0.10)

    # Guardrails
    guardrails_min_quality:     float = _float("GUARDRAILS_MIN_QUALITY",           0.30)
    guardrails_non_lender_conf: float = _float("GUARDRAILS_NON_LENDER_CONFIDENCE", 0.90)

    # Gemini pipeline
    gemini_circuit_threshold:  int   = _int("GEMINI_CIRCUIT_THRESHOLD",  5)
    gemini_circuit_reset_secs: float = _float("GEMINI_CIRCUIT_RESET_S", 300.0)
    gemini_retry_attempts:     int   = _int("GEMINI_RETRY_ATTEMPTS",     3)
    gemini_retry_delay_secs:   float = _float("GEMINI_RETRY_DELAY_S",    10.0)
    scraper_timeout_secs:      int   = _int("SCRAPER_TIMEOUT_S",         30)

    # Chat
    gemini_api_key:           str   = _str("GEMINI_API_KEY", "")
    gemini_model:             str   = _str("GEMINI_MODEL", "gemini-2.5-flash")
    chat_history_limit:       int   = _int("CHAT_HISTORY_LIMIT", 20)
    chat_context_turns:       int   = _int("CHAT_CONTEXT_TURNS", 6)
    chat_timeout_secs:        int   = _int("CHAT_TIMEOUT_S", 45)          # kept for back-compat
    chat_intent_timeout_secs: int   = _int("CHAT_INTENT_TIMEOUT_S", 20)   # Pass 1 — fast classification
    chat_answer_timeout_secs: int   = _int("CHAT_ANSWER_TIMEOUT_S", 90)   # Pass 2 — answer generation
    gemini_chat_retries:      int   = _int("GEMINI_CHAT_RETRIES", 2)      # attempts per Gemini call

    # Environment
    env: str = _str("ENV", "production")

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    def log_summary(self) -> None:
        logger.info(
            "Config: env=%s db_pool=%d-%d cache_match=%ds "
            "guardrails_quality=%.0f%% circuit_threshold=%d",
            self.env, self.db_pool_min, self.db_pool_max,
            self.cache_ttl_match,
            self.guardrails_min_quality * 100,
            self.gemini_circuit_threshold,
        )


cfg = Config()
