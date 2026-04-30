from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional

from enrichers import EnrichmentPayload, SOURCE_RANKS

log = logging.getLogger(__name__)

FIELD_GUARDRAILS = {
    "interest_rate":  {"min": 8.0,   "max": 48.0},
    "loan_amount":    {"min": 0.5,   "max": 50_000.0},
    "tenure":         {"min": 1.0,   "max": 360.0},
    "credit_score":   {"min": 300.0, "max": 900.0},
    "processing_fee": {"min": 0.0,   "max": 5.0},
}

RANGE_SANITY = {
    "interest_rate": {"max_spread": 20.0},
    "loan_amount":   {"max_spread_ratio": 100.0},
}


@dataclass
class ValidationResult:
    valid:  bool
    reason: Optional[str] = None


class BankManager:
    def __init__(self, db):
        self._db = db   # asyncpg pool; may be None in tests

    def _check_guardrails(self, p: EnrichmentPayload) -> ValidationResult:
        guard = FIELD_GUARDRAILS.get(p.field)
        if guard is None:
            return ValidationResult(valid=True)
        lo, hi = guard["min"], guard["max"]
        if p.value_min < lo or p.value_max > hi:
            log.debug("guardrail_violation field=%s min=%s max=%s", p.field, p.value_min, p.value_max)
            return ValidationResult(valid=False, reason="guardrail_violation")
        return ValidationResult(valid=True)

    def _check_range_sanity(self, p: EnrichmentPayload) -> ValidationResult:
        if p.value_min > p.value_max:
            return ValidationResult(valid=False, reason="range_invalid")
        sanity = RANGE_SANITY.get(p.field, {})
        if "max_spread" in sanity:
            spread = p.value_max - p.value_min
            if spread > sanity["max_spread"]:
                return ValidationResult(valid=False, reason="range_invalid")
        if "max_spread_ratio" in sanity and p.value_min > 0:
            if p.value_max / p.value_min > sanity["max_spread_ratio"]:
                return ValidationResult(valid=False, reason="range_invalid")
        return ValidationResult(valid=True)

    async def _get_highest_validated_rank(self, policy_id: str, field: str) -> Optional[int]:
        async with self._db.acquire() as conn:
            return await conn.fetchval(
                """SELECT MAX(source_rank) FROM policy_enrichments
                   WHERE policy_id = $1 AND field = $2 AND validated = true""",
                policy_id, field
            )

    async def _check_rank_gate(self, p: EnrichmentPayload) -> ValidationResult:
        existing_rank = await self._get_highest_validated_rank(p.policy_id, p.field)
        incoming_rank = SOURCE_RANKS.get(p.source, 0)
        if existing_rank is None:
            return ValidationResult(valid=True)
        if incoming_rank >= existing_rank:
            return ValidationResult(valid=True)
        return ValidationResult(valid=False, reason="lower_rank_exists")

    async def _check_outlier(self, p: EnrichmentPayload, loan_type: str) -> ValidationResult:
        if p.source != "bankbazaar":
            return ValidationResult(valid=True)
        async with self._db.acquire() as conn:
            stats = await conn.fetchrow(
                """SELECT COUNT(*) as n, AVG((value_min+value_max)/2) as mean,
                          STDDEV((value_min+value_max)/2) as stddev
                   FROM policy_enrichments pe
                   JOIN policies pol ON pol.id = pe.policy_id::BIGINT
                   WHERE pe.field = $1 AND pe.validated = true AND pol.loan_type = $2""",
                p.field, loan_type
            )
        if stats["n"] < 10 or stats["stddev"] is None or stats["stddev"] == 0:
            return ValidationResult(valid=True)
        mid = (p.value_min + p.value_max) / 2
        z = abs(mid - float(stats["mean"])) / float(stats["stddev"])
        if z > 3.0:
            return ValidationResult(valid=False, reason=f"outlier:z={z:.1f}")
        return ValidationResult(valid=True)

    async def validate_and_store(self, p: EnrichmentPayload, loan_type: str = "") -> bool:
        for check_fn in [self._check_guardrails, self._check_range_sanity]:
            result = check_fn(p)
            if not result.valid:
                log.info("REJECT field=%s source=%s reason=%s", p.field, p.source, result.reason)
                return False

        rank_result = await self._check_rank_gate(p)
        if not rank_result.valid:
            log.debug("SKIP field=%s source=%s reason=%s", p.field, p.source, rank_result.reason)
            return False

        outlier_result = await self._check_outlier(p, loan_type)
        validated = outlier_result.valid
        rejection_reason = None if validated else outlier_result.reason

        async with self._db.acquire() as conn:
            await conn.execute(
                """INSERT INTO policy_enrichments
                   (policy_id, field, value_min, value_max, source, source_rank,
                    validated, rejection_reason, raw_value, confidence)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                   ON CONFLICT (policy_id, field, source) DO UPDATE SET
                     value_min=EXCLUDED.value_min, value_max=EXCLUDED.value_max,
                     source_rank=EXCLUDED.source_rank, validated=EXCLUDED.validated,
                     rejection_reason=EXCLUDED.rejection_reason,
                     raw_value=EXCLUDED.raw_value, confidence=EXCLUDED.confidence,
                     updated_at=now()""",
                p.policy_id, p.field, p.value_min, p.value_max,
                p.source, SOURCE_RANKS.get(p.source, 0),
                validated, rejection_reason,
                p.raw_value, p.confidence
            )
        return validated
