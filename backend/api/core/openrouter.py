# backend/api/core/openrouter.py
"""
OpenRouter chat client — OpenAI-compatible API for the lender chatbot.
Supports any OpenRouter model; defaults to deepseek/deepseek-chat.
Switch model by setting OPENROUTER_MODEL env var.

Cheap options:
  deepseek/deepseek-chat          — $0.14/M in, $0.28/M out  (default, best value)
  deepseek/deepseek-r1            — reasoning model, higher cost
  google/gemini-flash-1.5         — $0.075/M in (cheapest option)
  anthropic/claude-3-haiku        — balanced quality/cost
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from typing import Iterator, Optional

from openai import OpenAI

# reuse the same system prompts from gemini.py
from core.gemini import (
    _INTENT_SYSTEM_PROMPT,
    _ANSWER_SYSTEM_PROMPT,
    _CONCEPT_SYSTEM_PROMPT,
    _OUT_OF_SCOPE_REPLY,
    _slim_record,
)
from collections import Counter

logger = logging.getLogger(__name__)

# Appended to the intent system prompt to enforce JSON output structure
_INTENT_JSON_INSTRUCTIONS = """

OUTPUT FORMAT: Respond with ONLY a JSON object. No markdown, no explanation.
Required field: "intent" (string, one of the intents above).
Optional fields:
  "filters": {
    "q": string,
    "loan_type": [string],
    "state": string,
    "company_type": [string],
    "aum_category": [string],
    "aum_min": number,
    "aum_max": number,
    "pan_india": boolean,
    "is_listed": boolean,
    "operating_intensity": [string],
    "business_sector": [string],
    "sort_by": string,
    "sort_dir": "asc" or "desc"
  },
  "compare_names": [string],
  "detail_names": [string]

Example output: {"intent": "filter", "filters": {"company_type": ["NBFC"], "state": "Gujarat"}}
"""

_INTENT_SYSTEM_PROMPT_OR = _INTENT_SYSTEM_PROMPT + _INTENT_JSON_INSTRUCTIONS


def _call_with_retry(fn, *, attempts: int = 2, delay: float = 2.0, label: str = ""):
    last_exc: Exception | None = None
    current_delay = delay
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            err_str = str(exc).lower()
            is_transient = (
                "429" in err_str or "rate limit" in err_str or
                "500" in err_str or "503" in err_str or "504" in err_str or
                "overloaded" in err_str or
                "connection" in err_str or "timeout" in err_str or
                isinstance(exc, (ConnectionError, TimeoutError))
            )
            if not is_transient or attempt == attempts:
                raise
            jitter = current_delay * 0.25 * (2 * random.random() - 1)
            wait = max(0.5, current_delay + jitter)
            logger.warning(
                "OpenRouter %s attempt %d/%d failed (%s: %s). Retrying in %.1fs",
                label, attempt, attempts, type(exc).__name__, exc, wait,
            )
            time.sleep(wait)
            current_delay *= 2
    raise last_exc  # type: ignore[misc]


def _retried_stream(fn, *, attempts: int = 2, delay: float = 2.0, label: str = ""):
    last_exc: Exception | None = None
    current_delay = delay
    for attempt in range(1, attempts + 1):
        yielded = False
        try:
            for chunk in fn():
                yielded = True
                yield chunk
            return
        except Exception as exc:
            if yielded:
                raise
            last_exc = exc
            err_str = str(exc).lower()
            is_transient = (
                "429" in err_str or "rate limit" in err_str or
                "500" in err_str or "503" in err_str or "504" in err_str or
                "overloaded" in err_str or
                "connection" in err_str or "timeout" in err_str or
                isinstance(exc, (ConnectionError, TimeoutError))
            )
            if not is_transient or attempt == attempts:
                raise
            jitter = current_delay * 0.25 * (2 * random.random() - 1)
            wait = max(0.5, current_delay + jitter)
            logger.warning(
                "OpenRouter %s stream attempt %d/%d failed (%s: %s). Retrying in %.1fs",
                label, attempt, attempts, type(exc).__name__, exc, wait,
            )
            time.sleep(wait)
            current_delay *= 2
    raise last_exc  # type: ignore[misc]


class OpenRouterChatClient:
    def __init__(self, api_key: str, model: str = "") -> None:
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required")
        self._client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            default_headers={
                "HTTP-Referer": "https://mitram360.com",
                "X-Title": "MITRAM360 Lender Discovery",
            },
        )
        self._model = model or os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat")
        self._retries = int(os.environ.get("OPENROUTER_CHAT_RETRIES", "2"))
        logger.info("OpenRouterChatClient initialized (model=%s, retries=%d)", self._model, self._retries)

    # ------------------------------------------------------------------
    # Gemini history format → OpenAI message format
    # ------------------------------------------------------------------

    def _to_messages(self, history: list[dict], message: str | None = None) -> list[dict]:
        msgs = []
        for h in history:
            role = "user" if h.get("role") == "user" else "assistant"
            parts = h.get("parts", [])
            text = parts[0] if parts else ""
            msgs.append({"role": role, "content": str(text)})
        if message is not None:
            msgs.append({"role": "user", "content": message})
        return msgs

    # ------------------------------------------------------------------
    # Pass 1 — intent classification
    # ------------------------------------------------------------------

    def parse_intent(self, message: str, history: list[dict]) -> dict:
        messages = self._to_messages(history[-8:], message)

        def _call():
            return self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _INTENT_SYSTEM_PROMPT_OR},
                    *messages,
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=256,
            )

        try:
            response = _call_with_retry(_call, attempts=self._retries, delay=1.5, label="parse_intent")
            raw = response.choices[0].message.content or ""
        except Exception as exc:
            logger.error("OpenRouter parse_intent failed after retries: %s", exc)
            raise

        if not raw:
            logger.warning("OpenRouter returned empty intent response")
            return {"intent": "qa", "filters": {}, "compare_names": [], "detail_names": []}

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error("OpenRouter non-JSON intent response: %s | raw=%s", exc, raw[:200])
            return {"intent": "qa", "filters": {}, "compare_names": [], "detail_names": []}

        data.setdefault("filters", {})
        data.setdefault("compare_names", [])
        data.setdefault("detail_names", [])
        return data

    # ------------------------------------------------------------------
    # Pass 2 — grounded answer (non-streaming)
    # ------------------------------------------------------------------

    def generate_grounded_answer(
        self,
        question: str,
        intent: str,
        lenders: list[dict],
        history: list[dict],
        note: str = "",
    ) -> str:
        if intent == "greeting":
            return (
                "Hi! I'm the MITRAM360 AI assistant. I can help you:\n"
                "- **Find lenders** by loan type, state, AUM, sector, or company type\n"
                "- **Compare** two or three lenders side-by-side\n"
                "- **Look up details** for a specific lender (HQ, contact, products)\n"
                "- **Explain** lending concepts and lender types\n\n"
                "Try: \"Show NBFCs in Maharashtra\" or \"What is a Small Finance Bank?\""
            )

        if intent == "out_of_scope":
            return _OUT_OF_SCOPE_REPLY

        if intent == "concept":
            messages = self._to_messages(history[-(4 * 2):], question)

            def _call():
                return self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": _CONCEPT_SYSTEM_PROMPT},
                        *messages,
                    ],
                    temperature=0.3,
                    max_tokens=500,
                )

            try:
                response = _call_with_retry(_call, attempts=self._retries, delay=2.0, label="concept_answer")
                text = response.choices[0].message.content or ""
                return text if text else "I couldn't generate an answer. Please try again."
            except Exception as exc:
                logger.error("OpenRouter concept answer error: %s", exc)
                raise

        if not lenders:
            if intent in ("compare", "lender_detail"):
                return (
                    "I couldn't find that lender in our database yet — they may not be listed. "
                    "Try browsing our full catalogue or compare other lenders."
                )
            if intent == "qa":
                return (
                    "I couldn't find relevant lenders for that question. "
                    "Try asking about a specific lender, loan type, or state — "
                    "e.g. \"Show NBFCs in Maharashtra\" or \"Tell me about Bajaj Finance\"."
                )
            return "No results found. Try broadening your search — remove a filter or try a different state."

        prompt = self._build_grounded_prompt(question, intent, lenders, note)
        messages = self._to_messages(history[-(6 * 2):])
        messages.append({"role": "user", "content": prompt})

        def _call():
            return self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _ANSWER_SYSTEM_PROMPT},
                    *messages,
                ],
                temperature=0.3,
                max_tokens=1200,
            )

        try:
            response = _call_with_retry(_call, attempts=self._retries, delay=2.0, label="grounded_answer")
            text = response.choices[0].message.content or ""
            if not text:
                logger.warning("OpenRouter returned empty answer response")
                return "I couldn't generate an answer. Please try again."
            return text
        except Exception as exc:
            logger.error("OpenRouter answer generation error: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Pass 2 — streaming variant
    # ------------------------------------------------------------------

    def generate_grounded_answer_stream(
        self,
        question: str,
        intent: str,
        lenders: list[dict],
        history: list[dict],
        note: str = "",
    ) -> Iterator[str]:
        if intent == "greeting":
            yield (
                "Hi! I'm the MITRAM360 AI assistant. I can help you:\n"
                "- **Find lenders** by loan type, state, AUM, sector, or company type\n"
                "- **Compare** two or three lenders side-by-side\n"
                "- **Look up details** for a specific lender (HQ, contact, products)\n"
                "- **Explain** lending concepts and lender types\n\n"
                "Try: \"Show NBFCs in Maharashtra\" or \"What is a Small Finance Bank?\""
            )
            return

        if intent == "out_of_scope":
            yield _OUT_OF_SCOPE_REPLY
            return

        if intent == "concept":
            messages = self._to_messages(history[-(4 * 2):], question)

            def _mk_concept_stream():
                return self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": _CONCEPT_SYSTEM_PROMPT},
                        *messages,
                    ],
                    temperature=0.3,
                    max_tokens=500,
                    stream=True,
                )

            yielded = False
            for chunk in _retried_stream(_mk_concept_stream, attempts=self._retries, delay=1.0, label="concept_stream"):
                text = chunk.choices[0].delta.content
                if text:
                    yielded = True
                    yield text
            if not yielded:
                yield "I couldn't generate an answer. Please try again."
            return

        if not lenders:
            if intent in ("compare", "lender_detail"):
                yield (
                    "I couldn't find that lender in our database yet — they may not be listed. "
                    "Try browsing our full catalogue or compare other lenders."
                )
            elif intent == "qa":
                yield (
                    "I couldn't find relevant lenders for that question. "
                    "Try asking about a specific lender, loan type, or state — "
                    "e.g. \"Show NBFCs in Maharashtra\" or \"Tell me about Bajaj Finance\"."
                )
            else:
                yield "No results found. Try broadening your search — remove a filter or try a different state."
            return

        prompt = self._build_grounded_prompt(question, intent, lenders, note)
        messages = self._to_messages(history[-(6 * 2):])
        messages.append({"role": "user", "content": prompt})

        def _mk_answer_stream():
            return self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _ANSWER_SYSTEM_PROMPT},
                    *messages,
                ],
                temperature=0.3,
                max_tokens=1200,
                stream=True,
            )

        yielded = False
        for chunk in _retried_stream(_mk_answer_stream, attempts=self._retries, delay=1.0, label="answer_stream"):
            text = chunk.choices[0].delta.content
            if text:
                yielded = True
                yield text
        if not yielded:
            yield "I couldn't generate an answer. Please try again."

    # ------------------------------------------------------------------
    # Shared helpers (same logic as gemini.py)
    # ------------------------------------------------------------------

    def _build_grounded_prompt(
        self, question: str, intent: str, lenders: list[dict], note: str
    ) -> str:
        top_names = [
            l.get("company_name") or l.get("name", "")
            for l in lenders[:3]
            if l.get("company_name") or l.get("name")
        ]
        name_hint = f"\nTop lender names for your suggestions: {', '.join(top_names)}" if top_names else ""

        if intent == "filter":
            slim         = [_slim_record(l) for l in lenders]
            total        = len(lenders)
            type_counts  = Counter(l.get("company_type") for l in lenders if l.get("company_type"))
            state_counts = Counter(l.get("hq_state")     for l in lenders if l.get("hq_state"))
            top_types    = ", ".join(f"{t}({c})" for t, c in type_counts.most_common(4))
            top_states   = ", ".join(f"{s}({c})" for s, c in state_counts.most_common(4))
            prefix = (
                f"{note + chr(10) if note else ''}"
                f"Stats — Total: {total}, By type: {top_types}, By HQ state: {top_states}.{name_hint}\n\n"
            )
            context = slim
        else:
            prefix  = f"{note}\n\n{name_hint}\n\n" if (note or name_hint) else ""
            context = lenders

        records_json = json.dumps(context, indent=2, default=str)
        return (
            f"{prefix}"
            f"Database records:\n{records_json}\n\n"
            f"User question: {question}\n\n"
            "Answer in natural language using ONLY the records above. Follow all tone and formatting rules."
        )


_client: Optional[OpenRouterChatClient] = None
_client_lock = threading.Lock()


def get_openrouter_client() -> OpenRouterChatClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                api_key = os.environ.get("OPENROUTER_API_KEY", "")
                model   = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-chat")
                _client = OpenRouterChatClient(api_key, model=model)
    return _client


def reset_openrouter_client() -> None:
    global _client
    with _client_lock:
        _client = None
