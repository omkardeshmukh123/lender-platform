# backend/api/core/ai_client.py
"""
AI client factory — returns OpenRouter if OPENROUTER_API_KEY is set, else Gemini.
Switch provider by adding/removing OPENROUTER_API_KEY in the environment.
Switch model by setting OPENROUTER_MODEL (e.g. deepseek/deepseek-chat).
"""
from __future__ import annotations

import os
import logging
import threading

logger = logging.getLogger(__name__)

_client = None
_lock = threading.Lock()


def get_ai_client():
    """Return a chat client with parse_intent / generate_grounded_answer interface."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
                if openrouter_key:
                    from core.openrouter import OpenRouterChatClient
                    model = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat")
                    logger.info("AI client: OpenRouter (model=%s)", model)
                    _client = OpenRouterChatClient(openrouter_key, model=model)
                else:
                    from core.gemini import get_gemini_client
                    logger.info("AI client: Gemini (OPENROUTER_API_KEY not set)")
                    _client = get_gemini_client()
    return _client


def reset_ai_client() -> None:
    """Force re-creation of the singleton (e.g. after env change in tests)."""
    global _client
    with _lock:
        _client = None
