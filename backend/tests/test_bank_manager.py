import pytest
from unittest.mock import AsyncMock, MagicMock
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from enrichers import EnrichmentPayload
from bank_manager import BankManager, ValidationResult


def make_payload(**kwargs):
    defaults = dict(
        policy_id="123",
        field="interest_rate",
        value_min=12.0,
        value_max=18.0,
        source="bankbazaar",
        confidence=0.70,
        raw_value={}
    )
    defaults.update(kwargs)
    return EnrichmentPayload(**defaults)


# Check 1 — RBI guardrails
def test_guardrail_rejects_rate_below_floor():
    bm = BankManager(db=None)
    result = bm._check_guardrails(make_payload(field="interest_rate", value_min=3.0, value_max=12.0))
    assert result.valid is False
    assert result.reason == "guardrail_violation"


def test_guardrail_rejects_rate_above_ceiling():
    bm = BankManager(db=None)
    result = bm._check_guardrails(make_payload(field="interest_rate", value_min=40.0, value_max=52.0))
    assert result.valid is False
    assert result.reason == "guardrail_violation"


def test_guardrail_passes_valid_rate():
    bm = BankManager(db=None)
    result = bm._check_guardrails(make_payload(field="interest_rate", value_min=12.0, value_max=18.0))
    assert result.valid is True


# Check 2 — Range sanity
def test_range_sanity_rejects_min_greater_than_max():
    bm = BankManager(db=None)
    result = bm._check_range_sanity(make_payload(value_min=20.0, value_max=10.0))
    assert result.valid is False
    assert result.reason == "range_invalid"


def test_range_sanity_rejects_interest_rate_spread_too_wide():
    bm = BankManager(db=None)
    result = bm._check_range_sanity(make_payload(field="interest_rate", value_min=8.0, value_max=38.0))
    assert result.valid is False
    assert result.reason == "range_invalid"


def test_range_sanity_passes_valid_range():
    bm = BankManager(db=None)
    result = bm._check_range_sanity(make_payload(value_min=12.0, value_max=18.0))
    assert result.valid is True


# Check 3 — Rank gate
@pytest.mark.anyio
async def test_rank_gate_accepts_when_vault_empty():
    bm = BankManager(db=None)
    bm._get_highest_validated_rank = AsyncMock(return_value=None)
    result = await bm._check_rank_gate(make_payload(source="bankbazaar"))
    assert result.valid is True


@pytest.mark.anyio
async def test_rank_gate_rejects_lower_rank():
    bm = BankManager(db=None)
    bm._get_highest_validated_rank = AsyncMock(return_value=3)
    result = await bm._check_rank_gate(make_payload(source="bankbazaar"))
    assert result.valid is False
    assert result.reason == "lower_rank_exists"


@pytest.mark.anyio
async def test_rank_gate_accepts_higher_rank():
    bm = BankManager(db=None)
    bm._get_highest_validated_rank = AsyncMock(return_value=2)
    result = await bm._check_rank_gate(make_payload(source="fpc_pdf"))
    assert result.valid is True
