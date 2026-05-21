"""Unit tests for the embeddings module."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# build_lender_text
# ---------------------------------------------------------------------------

def test_build_lender_text_full():
    from api.core.embeddings import build_lender_text
    lender = {
        "company_name": "HDFC Bank",
        "company_type": "Private Bank",
        "aum_crores": 2500000,
        "aum_category": "Large",
        "hq_location": "Mumbai",
        "hq_state": "Maharashtra",
        "pan_india": True,
        "primary_loan_segments": ["Home Loan", "Personal Loan", "Vehicle Loan"],
        "business_sector": "Housing",
        "is_listed": True,
        "established_year": 1994,
        "employee_count": 177000,
        "operating_intensity": "Pan India",
    }
    text = build_lender_text(lender)
    assert "HDFC Bank" in text
    assert "Private Bank" in text
    assert "₹25,00,000 Cr" in text
    assert "Large AUM" in text
    assert "HQ: Mumbai, Maharashtra" in text
    assert "Pan India operations" in text
    assert "Home Loan" in text
    assert "Housing" in text
    assert "Listed company" in text
    assert "Est: 1994" in text


def test_build_lender_text_null_fields_omitted():
    """Null fields must not emit 'None' or 'null' into the text."""
    from api.core.embeddings import build_lender_text
    lender = {"company_name": "Test MFI", "company_type": "NBFC-MFI"}
    text = build_lender_text(lender)
    assert "None" not in text
    assert "null" not in text.lower()
    assert "Test MFI" in text
    assert "NBFC-MFI" in text


def test_build_lender_text_regional_shows_states():
    """Regional lenders (pan_india=False) should list operating states."""
    from api.core.embeddings import build_lender_text
    lender = {
        "company_name": "Regional Bank",
        "company_type": "Cooperative Bank",
        "pan_india": False,
        "operating_states": ["Maharashtra", "Goa", "Karnataka"],
    }
    text = build_lender_text(lender)
    assert "Maharashtra" in text
    assert "Pan India" not in text


def test_build_lender_text_hq_state_only():
    """When hq_location is missing, use hq_state alone."""
    from api.core.embeddings import build_lender_text
    lender = {"company_name": "Rural Lender", "hq_state": "Bihar"}
    text = build_lender_text(lender)
    assert "HQ: Bihar" in text


# ---------------------------------------------------------------------------
# EmbeddingUnavailableError raised when API key missing
# ---------------------------------------------------------------------------

def test_embed_text_raises_when_no_api_key():
    from api.core.embeddings import embed_text, EmbeddingUnavailableError, reset_client
    reset_client()
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(EmbeddingUnavailableError, match="GEMINI_API_KEY"):
            embed_text("test")


# ---------------------------------------------------------------------------
# embed_text delegates to Gemini client
# ---------------------------------------------------------------------------

def test_embed_text_returns_vector():
    from api.core.embeddings import embed_text, reset_client
    reset_client()

    mock_values = [0.1] * 768
    mock_embedding = MagicMock()
    mock_embedding.values = mock_values
    mock_result = MagicMock()
    mock_result.embeddings = [mock_embedding]

    mock_client = MagicMock()
    mock_client.models.embed_content.return_value = mock_result

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
        with patch("api.core.embeddings.genai.Client", return_value=mock_client):
            reset_client()
            result = embed_text("find gold loan lenders")

    assert len(result) == 768
    assert result[0] == pytest.approx(0.1)


def test_embed_text_wraps_api_error_as_unavailable():
    from api.core.embeddings import embed_text, EmbeddingUnavailableError, reset_client
    reset_client()

    mock_client = MagicMock()
    mock_client.models.embed_content.side_effect = RuntimeError("connection refused")

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
        with patch("api.core.embeddings.genai.Client", return_value=mock_client):
            reset_client()
            with pytest.raises(EmbeddingUnavailableError, match="Embedding API error"):
                embed_text("test")
