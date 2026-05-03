"""
Generic UK bank CSV adapter.

Handles statements from Barclays, HSBC, Lloyds, NatWest, Santander, etc.
that don't fit the Wise or Monzo schemas.

Strategy:
  - Auto-detect column meanings from header keywords
  - Handle separate Money In / Money Out columns OR single signed Amount
  - Detect date format (UK DD/MM/YYYY vs US MM/DD/YYYY)
  - Generate hash-based txn_ids since these banks rarely include a stable ID

Output rows have raw_type = "TRANSFER" by default since most non-Wise banks
don't differentiate. This means the CARD_PAYMENT-gated rules in your script
won't fire for these — that's intentional. Card payments from a regular UK
bank will land in EXPENSES via manual review or you'll create generic-bank
specific rules later.
"""
from __future__ import annotations
import pandas as pd
import re
from datetime import datetime
from pathlib import Path
from typing import List

from core.schema import Transaction, make_txn_id, encode_raw


HEADER_PATTERNS = {
    "date": ["date", "transaction date", "posted", "posting date"],
    "description": ["description", "details", "narrative", "merchant", "payee",
                     "memo", "transaction", "particulars"],
    "amount": ["amount", "transaction amount", "value"],
    "debit": ["debit", "money out", "withdrawal", "paid out", "outflow"],
    "credit": ["credit", "money in", "deposit", "paid in", "inflow"],
    "balance": ["balance", "running balance"],
    "type": ["type", "transaction type"],
    "reference": ["reference", "memo"],
}


def _detect_columns(columns):
    mapping = {}
    for col in columns:
        col_lower = col.lower().strip()
        for canonical, patterns in HEADER_PATTERNS.items():
            if canonical in mapping:
                continue
            if any(p == col_lower or p in col_lower for p in patterns):
                mapping[canonical] = col
                break
    return mapping


def _parse_amount(v) -> float:
    if pd.isna(v) or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return 0.0
    is_neg = s.startswith("(") and s.endswith(")")
    if is_neg:
        s = s[1:-1]
    s = re.sub(r"[£$€,\s]", "", s)
    if s.startswith("-"):
        is_neg = True
        s = s[1:]
    try:
        v = float(s)
        return -v if is_neg else v
    except ValueError:
        return 0.0


def _detect_dayfirst(series: pd.Series) -> bool:
    """Decide UK vs US date format from unambiguous samples."""
    day_first = 0
    month_first = 0
    for val in series.dropna().head(50):
        m = re.match(r"^(\d{1,2})[/-](\d{1,2})", str(val).strip())
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > 12 and b <= 12:
                day_first += 1
            elif b > 12 and a <= 12:
                month_first += 1
    return day_first >= month_first  # default UK


def _parse_date(value, dayfirst: bool):
    if pd.isna(value):
        return None
    return pd.to_datetime(value, dayfirst=dayfirst, errors="coerce").date() if pd.to_datetime(value, dayfirst=dayfirst, errors="coerce") is not pd.NaT else None


def parse_uk_generic_csv(filepath: str | Path, source_account: str) -> List[Transaction]:
    filepath = Path(filepath)

    # Find header row (some banks have metadata at the top)
    with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
        raw_lines = f.readlines()

    header_row = 0
    for i, line in enumerate(raw_lines[:15]):
        ll = line.lower()
        if "date" in ll and any(kw in ll for kw in
                                  ["amount", "debit", "credit", "balance",
                                   "description", "details", "narrative"]):
            header_row = i
            break

    df = pd.read_csv(
        filepath, skiprows=header_row, dtype=str, keep_default_na=False,
        on_bad_lines="skip", encoding="utf-8-sig",
    )
    df.columns = [c.strip() for c in df.columns]

    mapping = _detect_columns(df.columns)
    if "date" not in mapping or "description" not in mapping:
        raise ValueError(
            f"{filepath.name}: couldn't auto-detect required columns (date, description). "
            f"Found: {list(df.columns)}"
        )

    dayfirst = _detect_dayfirst(df[mapping["date"]])

    txns = []
    for idx, row in df.iterrows():
        d = _parse_date(row[mapping["date"]], dayfirst)
        if not d:
            continue

        desc = str(row[mapping["description"]]).strip()
        if not desc:
            continue

        # Amount: single column or debit/credit pair
        if "amount" in mapping:
            amount = _parse_amount(row[mapping["amount"]])
        else:
            debit = _parse_amount(row[mapping["debit"]]) if "debit" in mapping else 0
            credit = _parse_amount(row[mapping["credit"]]) if "credit" in mapping else 0
            amount = credit - abs(debit)

        if amount == 0:
            continue

        balance = _parse_amount(row[mapping["balance"]]) if "balance" in mapping else None
        ref = str(row[mapping["reference"]]).strip() if "reference" in mapping else ""
        raw_type = str(row[mapping["type"]]).strip().upper() if "type" in mapping else "TRANSFER"

        txn_id = make_txn_id(
            source_account, "",
            fallback_key=f"{d.isoformat()}|{desc}|{amount}|{idx}"
        )

        full_desc = f"{desc} - {ref}" if ref else desc

        txns.append(Transaction(
            txn_id=txn_id,
            source_account=source_account,
            date=d,
            description=full_desc,
            amount=amount,
            raw_type=raw_type,
            payer="",
            reference=ref,
            raw_description=desc,
            balance_after=balance if balance != 0 else None,
            source_file=filepath.name,
            raw_payload=encode_raw(row.to_dict()),
        ))

    return txns
