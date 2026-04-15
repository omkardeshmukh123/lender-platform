# backend/api/core/gemini.py
"""
Gemini chat client for the lender chatbot.
Uses gemini-2.0-flash with JSON schema mode for structured intent extraction.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import google.generativeai as genai

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a helpful AI assistant for MITRAM360, an Indian lender discovery platform.
You help users find NBFCs and banks based on their financing requirements.

Classify every user message into one of three intents:
- "filter"  — user wants to search/list lenders with specific criteria
- "compare" — user wants to compare 2–3 specific named lenders side-by-side
- "qa"      — general question about lending, finance, NBFCs, or the platform

VALID FILTER VALUES (use only these exact strings):
loan_type: MSME Loan, Personal Loan, Home Loan, Business Loan, Vehicle Loan,
           Gold Loan, Education Loan, Micro Loan, Loan Against Property,
           Working Capital, Agriculture Loan, EV Loan, Two Wheeler Loan,
           Rural Loan, Microfinance, Supply Chain Finance,
           Consumer Durable Loan, Credit Card

company_type: NBFC, Private Bank, PSU Bank, Foreign Bank,
              Cooperative Bank, NBFC-MFI, Small Finance Bank

aum_category: Micro, Small, Mid, Large
sort_by: aum_crores, established_year, employee_count, quality_score, company_name
sort_dir: asc, desc

RULES:
- Always populate "answer" with a friendly, concise reply (1–3 sentences).
- For "filter": extract all criteria mentioned. Omit fields that are not mentioned.
- For "compare": list company names in compare_names exactly as the user said them.
- For "qa": answer the question directly; leave filters and compare_names empty.
- If the user's language is Hindi or Hinglish, reply in Hinglish.
"""

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["filter", "compare", "qa"],
        },
        "answer": {"type": "string"},
        "filters": {
            "type": "object",
            "properties": {
                "q":            {"type": "string"},
                "loan_type":    {"type": "array", "items": {"type": "string"}},
                "state":        {"type": "string"},
                "company_type": {"type": "array", "items": {"type": "string"}},
                "aum_category": {"type": "array", "items": {"type": "string"}},
                "aum_min":      {"type": "number"},
                "aum_max":      {"type": "number"},
                "pan_india":    {"type": "boolean"},
                "is_listed":    {"type": "boolean"},
                "sort_by":      {"type": "string"},
                "sort_dir":     {"type": "string", "enum": ["asc", "desc"]},
            },
        },
        "compare_names": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["intent", "answer"],
}


class GeminiChatClient:
    """Wraps google-generativeai for structured lender chat responses."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for the chat feature")
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            system_instruction=_SYSTEM_PROMPT,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
                temperature=0.3,
                max_output_tokens=1024,
            ),
        )
        logger.info("GeminiChatClient initialized (model=gemini-2.0-flash)")

    def parse_response(self, message: str, history: list[dict]) -> dict:
        """
        Send a user message + conversation history to Gemini.
        Returns parsed dict with keys: intent, answer, filters, compare_names.
        history: list of {role: "user"|"model", parts: [str]} dicts
        """
        contents = list(history) + [{"role": "user", "parts": [message]}]
        try:
            response = self._model.generate_content(contents)
            raw = response.text
        except Exception as exc:
            logger.error("Gemini API error: %s", exc)
            raise

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("Gemini returned non-JSON: %s | raw=%s", exc, raw[:200])
            return {"intent": "qa", "answer": raw, "filters": {}, "compare_names": []}

        data.setdefault("filters", {})
        data.setdefault("compare_names", [])
        return data


_client: Optional[GeminiChatClient] = None


def get_gemini_client() -> GeminiChatClient:
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        _client = GeminiChatClient(api_key)
    return _client
