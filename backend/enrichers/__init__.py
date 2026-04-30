from dataclasses import dataclass, field
from typing import Any


@dataclass
class EnrichmentPayload:
    policy_id:  str
    field:      str       # 'interest_rate' | 'loan_amount' | 'tenure' | 'credit_score' | 'processing_fee'
    value_min:  float
    value_max:  float
    source:     str       # 'bse_xbrl' | 'fpc_pdf' | 'bankbazaar' | 'legacy'
    confidence: float     # 0.0–1.0
    raw_value:  dict = field(default_factory=dict)

SOURCE_RANKS = {
    "bse_xbrl":   4,
    "fpc_pdf":    3,
    "bankbazaar": 2,
    "legacy":     1,
}
