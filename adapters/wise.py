"""
Wise / Revolut CSV adapter.

Wise's business statement export uses these exact column names:
  Date started (UTC), Date completed (UTC), ID, Type, State, Description,
  Reference, Payer, Card number, Card label, Card state, Orig currency,
  Orig amount, Payment currency, Amount, Total amount, Exchange rate,
  Fee, Fee currency, Balance, Account, Beneficiary account number,
  Beneficiary sort code or routing number, Beneficiary IBAN,
  Beneficiary BIC, MCC, Related transaction id, Spend program

Revolut Business uses a similar schema. We treat them with the same adapter.

Field mapping (matches your Apps Script mapRevolutHeaders_):
  txn_id           <- "ID"
  date             <- "Date completed (UTC)" (fallback to "Date started (UTC)")
  description      <- "Description" + " - " + "Reference" if reference present
  raw_description  <- "Description"
  reference        <- "Reference"
  payer            <- "Payer"
  raw_type         <- "Type"
  amount           <- "Total amount" (fallback to "Amount")
  fee              <- "Fee"
  balance_after    <- "Balance"
"""
from __future__ import annotations
import pandas as pd
from datetime import date, datetime
from pathlib import Path
from typing import List

from core.schema import Transaction, make_txn_id, encode_raw


WISE_REQUIRED = {"ID", "Type", "Description"}


def looks_like_wise(headers: list) -> bool:
    """Check whether a CSV has the Wise/Revolut shape."""
    headers_clean = {str(h).strip() for h in headers}
    return WISE_REQUIRED.issubset(headers_clean)


def _parse_date(value) -> date:
    """Parse Wise date — both 'Date completed (UTC)' (YYYY-MM-DD) and 'Date started (UTC)' (DD/MM/YYYY)."""
    if pd.isna(value):
        return None
    if isinstance(value, (date, datetime)):
        return value.date() if isinstance(value, datetime) else value
    s = str(value).strip()
    if not s:
        return None

    # Try ISO first (Date completed format)
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    # Last resort — pandas with dayfirst (Wise UK uses DD/MM/YYYY in started field)
    try:
        return pd.to_datetime(s, dayfirst=True, errors="coerce").date()
    except Exception:
        return None


def _parse_amount(value) -> float:
    if pd.isna(value) or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "").replace("£", "").replace("$", "").replace("€", "").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_wise_csv(filepath: str | Path, source_account: str) -> List[Transaction]:
    """
    Parse a Wise/Revolut CSV statement into Transaction objects.

    `source_account` is the logical account name in our system, e.g.
    "welux_wise_gbp" or "personal_revolut".
    """
    filepath = Path(filepath)
    df = pd.read_csv(filepath, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]

    if not looks_like_wise(df.columns):
        raise ValueError(
            f"{filepath.name} doesn't look like a Wise/Revolut export. "
            f"Missing required columns. Got: {list(df.columns)}"
        )

    # Determine which date column to use — prefer completed
    date_col = "Date completed (UTC)" if "Date completed (UTC)" in df.columns else "Date started (UTC)"

    # Determine amount column
    amount_col = "Total amount" if "Total amount" in df.columns else "Amount"

    txns = []
    for idx, row in df.iterrows():
        bank_id = str(row.get("ID", "")).strip()
        amount = _parse_amount(row.get(amount_col))

        # Skip zero-amount rows (status messages, etc.)
        if amount == 0:
            continue

        d = _parse_date(row.get(date_col))
        if not d:
            # If completed date is empty (pending tx), fall back to started
            d = _parse_date(row.get("Date started (UTC)"))
        if not d:
            continue  # genuinely unparseable

        raw_desc = str(row.get("Description", "")).strip()
        reference = str(row.get("Reference", "")).strip()
        payer = str(row.get("Payer", "")).strip()
        raw_type = str(row.get("Type", "")).strip()
        fee = _parse_amount(row.get("Fee", 0))
        balance = _parse_amount(row.get("Balance", 0)) or None

        # Combined description matches your Apps Script: "<desc> - <reference>"
        description = f"{raw_desc} - {reference}" if reference else raw_desc

        # Build txn_id — prefer bank ID
        if bank_id:
            txn_id = make_txn_id(source_account, bank_id)
        else:
            txn_id = make_txn_id(
                source_account, "",
                fallback_key=f"{d.isoformat()}|{description}|{amount}|{idx}"
            )

        txns.append(Transaction(
            txn_id=txn_id,
            source_account=source_account,
            date=d,
            description=description,
            amount=amount,
            raw_type=raw_type,
            payer=payer,
            reference=reference,
            raw_description=raw_desc,
            fee=fee,
            balance_after=balance,
            source_file=filepath.name,
            raw_payload=encode_raw(row.to_dict()),
        ))

    return txns
