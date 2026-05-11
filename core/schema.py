"""
Canonical transaction schema. Every bank adapter produces objects in this shape.

This is the contract between the bank-specific parsers and the rest of the system.
Adding a new bank means writing one adapter that outputs Transaction objects.
"""
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional
import hashlib
import json


# Placeholder used when a row has no usable date (e.g. the historical
# Welux XLSX had blank Date cells on a handful of rows). Re-importing
# the corresponding Wise CSV fills in the real date — see Store
# .insert_transactions.
MISSING_DATE = date(1900, 1, 1)


@dataclass
class Transaction:
    # === Identity ===
    txn_id: str                          # bank-provided ID if available, else hash
    source_account: str                  # e.g. "welux_wise_gbp", "personal_revolut"

    # === Core fields ===
    date: date                           # transaction date (use completion date when available)
    description: str                     # combined description (e.g. "<desc> - <reference>")
    amount: float                        # signed: positive = incoming, negative = outgoing

    # === Bank metadata (preserved from source) ===
    raw_type: str = ""                   # e.g. "CARD_PAYMENT", "TOPUP", "TRANSFER", "FEE"
    payer: str = ""                      # who paid (incoming) or recipient (outgoing)
    reference: str = ""                  # bank reference field
    raw_description: str = ""            # original description before any combining
    fee: float = 0.0                     # transaction fee (Wise-style)
    balance_after: Optional[float] = None  # running balance if statement provides it

    # === System-assigned ===
    direction: str = ""                  # "Incoming" or "Outgoing" — derived from amount
    bucket: str = ""                     # category bucket: "JOBS IN", "EXPENSES", etc.
    asset_tag: str = ""                  # vehicle reg if applicable: "WX21VZN", "EQV", "KM20YYX"
    person_tag: str = ""                 # driver/person if applicable: "WALEED", "IMRAN"
    vat: Optional[float] = None          # VAT amount (None = unset, 0 = explicitly no VAT)
    vat_rate: Optional[float] = None     # 0.20 for standard, 0.05 reduced, 0 zero-rated, None unset
    notes: str = ""                      # free-text notes
    rule_applied: str = ""               # which rule routed this (for debugging)
    needs_review: bool = False           # true if rules couldn't classify with confidence

    # === Provenance ===
    source_file: str = ""                # filename it was imported from
    raw_payload: str = ""                # JSON-encoded original row, for audit

    def __post_init__(self):
        if not self.direction:
            self.direction = "Incoming" if self.amount > 0 else "Outgoing"

    @property
    def is_incoming(self) -> bool:
        return self.amount > 0

    @property
    def gross(self) -> float:
        """Absolute value (always positive) for sums."""
        return abs(self.amount)

    @property
    def net(self) -> Optional[float]:
        """Amount excluding VAT, if VAT is set."""
        if self.vat is None:
            return None
        return self.amount - self.vat

    def fingerprint(self) -> str:
        """Stable fingerprint for dedup if no bank ID available."""
        key = f"{self.source_account}|{self.date.isoformat()}|{self.description}|{self.amount:.2f}"
        return hashlib.md5(key.encode()).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d


def make_txn_id(source_account: str, bank_id: str = "", *, fallback_key: str = "") -> str:
    """
    Build the system-internal transaction ID.

    If the bank gave us an ID, use it (prefixed with account so the same UUID
    from two different accounts can't collide). Otherwise hash a fallback key.
    """
    if bank_id:
        return f"{source_account}::{bank_id}"
    if not fallback_key:
        raise ValueError("Need either bank_id or fallback_key")
    h = hashlib.md5(f"{source_account}|{fallback_key}".encode()).hexdigest()
    return f"{source_account}::HASH-{h[:16]}"


def encode_raw(row: dict) -> str:
    """Serialize a raw bank row for audit storage."""
    # Convert any non-JSON-serializable values to strings
    clean = {}
    for k, v in row.items():
        try:
            json.dumps(v)
            clean[k] = v
        except (TypeError, ValueError):
            clean[k] = str(v)
    return json.dumps(clean, default=str)
