"""
Tests for AI chatbot domain guardrails.

Off-topic queries must return the standard refusal string.
On-topic queries must produce consistent, lender-specific output.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock, patch

from api.core.gemini import GeminiChatClient, _OUT_OF_SCOPE_REPLY


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """GeminiChatClient with a stub API key (no real calls in off-topic tests)."""
    with patch("api.core.gemini.genai.Client"):
        return GeminiChatClient(api_key="test-key")


# ---------------------------------------------------------------------------
# Off-topic refusal — generate_grounded_answer
# ---------------------------------------------------------------------------

OFF_TOPIC_QUERIES = [
    "Who won the IPL yesterday?",
    "What is the weather in Mumbai today?",
    "Give me a recipe for biryani.",
    "Which stocks should I buy this week?",
    "Tell me about the latest Bollywood movie.",
    "How do I apply for a passport?",
    "What is the capital of France?",
    "Recommend a good Python tutorial.",
    "Who is the Prime Minister of India?",
    "How do I fix a leaking tap at home?",
]


@pytest.mark.parametrize("query", OFF_TOPIC_QUERIES)
def test_out_of_scope_returns_refusal_string(client, query):
    """Non-lending queries must return the standardized refusal string."""
    answer = client.generate_grounded_answer(
        question=query,
        intent="out_of_scope",
        lenders=[],
        history=[],
    )
    assert answer == _OUT_OF_SCOPE_REPLY


@pytest.mark.parametrize("query", OFF_TOPIC_QUERIES)
def test_out_of_scope_stream_yields_refusal_string(client, query):
    """Streaming endpoint must also yield the standardized refusal string."""
    tokens = list(client.generate_grounded_answer_stream(
        question=query,
        intent="out_of_scope",
        lenders=[],
        history=[],
    ))
    assert tokens == [_OUT_OF_SCOPE_REPLY]


# ---------------------------------------------------------------------------
# Refusal string content check
# ---------------------------------------------------------------------------

def test_refusal_string_mentions_key_domains():
    """Refusal message must mention all four core domains."""
    for term in ("lenders", "loan products", "NBFCs", "interest rates"):
        assert term in _OUT_OF_SCOPE_REPLY


# ---------------------------------------------------------------------------
# On-topic variance — generate_grounded_answer
# ---------------------------------------------------------------------------

_LENDER_RECORD = {
    "company_name": "Bajaj Finance",
    "company_type": "NBFC",
    "aum_crores": 310000,
    "hq_state": "Maharashtra",
    "hq_location": "Pune",
    "primary_loan_segments": ["Personal Loan", "Business Loan"],
}

_ON_TOPIC_QUESTION = "Tell me about Bajaj Finance"

# Simulate 10 slightly different Gemini responses for the same on-topic query.
_MOCK_RESPONSES = [
    f"Bajaj Finance is a large NBFC headquartered in Pune, Maharashtra, with an AUM of ₹3,10,000 Cr. Response variant {i}."
    for i in range(10)
]


def test_on_topic_query_variance(client):
    """Same on-topic question run 10× must always mention the requested lender name."""
    mock_response = MagicMock()
    # Cycle through slightly different responses to simulate real variance.
    mock_response.text = _MOCK_RESPONSES[0]
    client._client.models.generate_content.return_value = mock_response

    results = []
    for i, variant in enumerate(_MOCK_RESPONSES):
        client._client.models.generate_content.return_value.text = variant
        answer = client.generate_grounded_answer(
            question=_ON_TOPIC_QUESTION,
            intent="lender_detail",
            lenders=[_LENDER_RECORD],
            history=[],
        )
        results.append(answer)

    # Every response must mention the lender name (core consistency check).
    for i, answer in enumerate(results):
        assert "Bajaj Finance" in answer, (
            f"Run {i+1}: lender name missing from answer: {answer[:120]!r}"
        )


# ---------------------------------------------------------------------------
# _search_lenders_semantic — embedding fallback
# ---------------------------------------------------------------------------

import asyncio
from unittest.mock import AsyncMock, patch as _patch


def test_semantic_search_falls_back_to_keyword_on_embedding_error():
    """When embedding API fails, semantic search falls back to _search_lenders_for_qa."""
    import api.routers.chat as _chat_mod
    from api.routers.chat import _search_lenders_semantic
    # Use the same EmbeddingUnavailableError class that chat.py imported
    # (it may live under 'core.embeddings' or 'api.core.embeddings' depending on sys.path)
    _EmbeddingUnavailableError = _chat_mod.EmbeddingUnavailableError

    mock_db = MagicMock()

    async def run():
        with _patch("api.routers.chat.embed_query", side_effect=_EmbeddingUnavailableError("down")):
            with _patch("api.routers.chat._search_lenders_for_qa", new_callable=AsyncMock) as mock_qa:
                mock_qa.return_value = []
                result = await _search_lenders_semantic(mock_db, "gold loan lenders")
                mock_qa.assert_called_once_with(mock_db, "gold loan lenders")
                return result

    assert asyncio.run(run()) == []
