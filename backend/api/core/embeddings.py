"""
Embedding generation for semantic lender search.
Converts lender profiles and user queries to 768-dim vectors via Gemini text-embedding-004.
"""
from __future__ import annotations

import os
import threading
from typing import Optional

from google import genai


class EmbeddingUnavailableError(Exception):
    """Raised when the embedding API is unavailable or not configured."""


def _fmt_inr(n: int) -> str:
    """Format integer in Indian numbering (e.g. 2500000 → 25,00,000)."""
    s = str(abs(n))
    if len(s) <= 3:
        return s
    result = s[-3:]
    s = s[:-3]
    while s:
        result = s[-2:] + "," + result
        s = s[:-2]
    return result


def build_lender_text(lender: dict) -> str:
    """Convert a lender record into a rich text document for embedding."""
    parts: list[str] = []

    name = lender.get("company_name") or lender.get("name", "")
    if name:
        parts.append(name)

    if ct := lender.get("company_type"):
        parts.append(ct)

    aum = lender.get("aum_crores")
    if aum is not None:
        parts.append(f"AUM: ₹{_fmt_inr(int(aum))} Cr")

    if ac := lender.get("aum_category"):
        parts.append(f"{ac} AUM")

    hq_loc   = lender.get("hq_location")
    hq_state = lender.get("hq_state")
    if hq_loc and hq_state:
        parts.append(f"HQ: {hq_loc}, {hq_state}")
    elif hq_state:
        parts.append(f"HQ: {hq_state}")

    if lender.get("pan_india"):
        parts.append("Pan India operations")
    else:
        states = lender.get("operating_states") or []
        if states:
            parts.append(f"Operating in: {', '.join(states[:5])}")

    segments = lender.get("primary_loan_segments") or []
    if segments:
        parts.append(f"Loan products: {', '.join(segments)}")

    if sector := lender.get("business_sector"):
        parts.append(f"Sector: {sector}")

    if lender.get("is_listed"):
        parts.append("Listed company")

    if year := lender.get("established_year"):
        parts.append(f"Est: {year}")

    if emp := lender.get("employee_count"):
        parts.append(f"Employees: {emp}")

    if intensity := lender.get("operating_intensity"):
        parts.append(intensity)

    return " | ".join(parts)


_client: Optional[genai.Client] = None
_lock = threading.Lock()


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                api_key = os.environ.get("GEMINI_API_KEY", "")
                if not api_key:
                    raise EmbeddingUnavailableError("GEMINI_API_KEY not set")
                _client = genai.Client(api_key=api_key)
    return _client


def embed_text(text: str) -> list[float]:
    """Embed a text string. Returns a 768-dim float vector."""
    model = os.environ.get("GEMINI_EMBEDDING_MODEL", "text-embedding-004")
    try:
        client = _get_client()
        result = client.models.embed_content(model=model, contents=text)
        return list(result.embeddings[0].values)
    except EmbeddingUnavailableError:
        raise
    except Exception as exc:
        raise EmbeddingUnavailableError(f"Embedding API error: {exc}") from exc


def embed_query(query: str) -> list[float]:
    """Embed a user query for semantic search."""
    return embed_text(query)


def embed_lender(lender: dict) -> list[float]:
    """Build text document from lender dict and embed it."""
    return embed_text(build_lender_text(lender))


def reset_client() -> None:
    """Force re-creation of the embedding client. Used in tests."""
    global _client
    with _lock:
        _client = None
