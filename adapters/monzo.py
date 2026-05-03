"""
Monzo CSV adapter.

Monzo's standard CSV export columns:
  Transaction ID, Date, Time, Type, Name, Emoji, Category,
  Amount, Currency, Local amount, Local currency, Notes and #tags,
  Address, Receipt, Description, Category split, Money Out, Money In

We normalize this to our canonical Transaction schema, mapping to the
same raw_type vocabulary your Apps Script uses ("CARD_PAYMENT", "TRANSFER",
"TOPUP") so the routing rules apply uniformly.
"""
from __future__ import annotations
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import List

from core.schema import Transaction, make_txn_id, encode_raw


MONZO_REQUIRED = {"Transaction ID", "Date", "Amount"}


def looks_like_monzo(headers: list) -> bool:
    headers_clean = {str(h).strip() for h in headers}
    return MONZO_REQUIRED.issubset(headers_clean)


# Map Monzo's "Type" field to our canonical raw_type vocabulary
MONZO_TYPE_MAP = {
    "Card payment": "CARD_PAYMENT",
    "Faster payment": "TRANSFER",
    "Bank transfer": "TRANSFER",
    "Direct Debit": "TRANSFER",
    "Standing order": "TRANSFER",
    "Pot transfer": "TRANSFER",
    "Topup": "TOPUP",
    "Top up": "TOPUP",
}


def _parse_amount(v) -> float:
    if pd.isna(v) or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("£", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_date(value):
    if pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return pd.to_datetime(s, dayfirst=True, errors="coerce").date()
    except Exception:
        return None


def parse_monzo_csv(filepath: str | Path, source_account: str) -> List[Transaction]:
    filepath = Path(filepath)
    df = pd.read_csv(filepath, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]

    if not looks_like_monzo(df.columns):
        raise ValueError(
            f"{filepath.name} doesn't look like a Monzo export. Got: {list(df.columns)}"
        )

    txns = []
    for idx, row in df.iterrows():
        bank_id = str(row.get("Transaction ID", "")).strip()
        amount = _parse_amount(row.get("Amount"))
        if amount == 0:
            continue

        d = _parse_date(row.get("Date"))
        if not d:
            continue

        name = str(row.get("Name", "")).strip()
        notes = str(row.get("Notes and #tags", "")).strip()
        desc = str(row.get("Description", "")).strip()
        monzo_type = str(row.get("Type", "")).strip()
        category = str(row.get("Category", "")).strip()

        raw_desc = name or desc or category
        reference = notes
        description = f"{raw_desc} - {reference}" if reference else raw_desc

        # Map Monzo type to our vocabulary; default to TRANSFER if unknown
        raw_type = MONZO_TYPE_MAP.get(monzo_type, "TRANSFER")

        txn_id = (
            make_txn_id(source_account, bank_id)
            if bank_id else
            make_txn_id(source_account, "",
                         fallback_key=f"{d.isoformat()}|{description}|{amount}|{idx}")
        )

        txns.append(Transaction(
            txn_id=txn_id,
            source_account=source_account,
            date=d,
            description=description,
            amount=amount,
            raw_type=raw_type,
            payer=name if amount > 0 else "",  # Monzo uses Name for both
            reference=reference,
            raw_description=raw_desc,
            source_file=filepath.name,
            raw_payload=encode_raw(row.to_dict()),
        ))

    return txns
