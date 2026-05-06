"""
Welux historical-spreadsheet adapter.

Reads the Google Sheets export (XLSX) where each bucket lives on its own
sheet (JOBS IN, EXPENSES, FUEL, ...). For every row in a bucket sheet
we emit a Transaction with `bucket` already pinned to that sheet's name
— so the rules engine doesn't get a chance to override the user's
historical classifications.

The TxnID column in the spreadsheet is the original Wise UUID, so we
build the system's txn_id with the same `make_txn_id` format the Wise
adapter uses. That way, when a future Wise CSV is imported the same
UUIDs collide and the dedup keeps the historical row.

We only touch four columns per sheet — TxnID / Date / Description /
Amount. The other columns drift between sheets (Direction, Status,
inline VAT, OriginalRow, etc.) and aren't reliable.
"""
from __future__ import annotations
from datetime import datetime, date
from pathlib import Path
from typing import List

import pandas as pd

from core.schema import Transaction, make_txn_id


# Bucket-sheet names recognized by this adapter. Mirrors core.rules
# ALL_BUCKETS plus the historical sheets that may have been emptied.
BUCKET_SHEETS = [
    "JOBS IN", "JOBS OUT",
    "MISC PAYMENT IN", "MISC PAYMENT OUT",
    "EXPENSES", "FUEL", "PARKING", "WATER + OTHER",
    "WALEED EXPENSE", "IMRAN EXPENSE", "WAYZ MOTORS",
    "WX21VZN IN", "WX21VZN OUT",
    "EQV IN", "EQV OUT",
    "KM20YYX IN",
    "1ST NATIONWIDE",
]


def looks_like_welux_history(filepath: str | Path) -> bool:
    """Cheap header check: does the workbook contain at least one of our
    bucket-sheet names?"""
    try:
        xls = pd.ExcelFile(filepath, engine="openpyxl")
    except Exception:
        return False
    return any(name in xls.sheet_names for name in BUCKET_SHEETS)


def _to_date(value) -> date | None:
    if pd.isna(value) or value in ("", None):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None


def _to_amount(value) -> float | None:
    if pd.isna(value) or value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_welux_history_xlsx(filepath: str | Path,
                               source_account: str) -> List[Transaction]:
    filepath = Path(filepath)
    xls = pd.ExcelFile(filepath, engine="openpyxl")

    txns: List[Transaction] = []
    seen_txn_ids: set[str] = set()

    for sheet_name in BUCKET_SHEETS:
        if sheet_name not in xls.sheet_names:
            continue

        df = pd.read_excel(xls, sheet_name=sheet_name, dtype=object,
                            keep_default_na=False)
        if df.empty:
            continue

        # Bucket sheets duplicate column names ("OriginalRow" appears
        # twice on some) — pandas auto-suffixes them so name-based access
        # still works for the four columns we care about.
        required = {"TxnID", "Date", "Description", "Amount"}
        if not required.issubset(set(df.columns)):
            continue

        for _, row in df.iterrows():
            txn_id_raw = str(row["TxnID"]).strip() if not pd.isna(row["TxnID"]) else ""
            if not txn_id_raw:
                continue

            d = _to_date(row["Date"])
            if d is None:
                continue

            description = str(row["Description"]).strip() if not pd.isna(row["Description"]) else ""
            if not description:
                continue

            amount = _to_amount(row["Amount"])
            if amount is None or amount == 0:
                continue

            # Split the combined "<desc> - <reference>" form so downstream
            # tagging still has a reference to look at.
            if " - " in description:
                raw_desc, _, reference = description.partition(" - ")
            else:
                raw_desc, reference = description, ""

            txn_id = make_txn_id(source_account, txn_id_raw)
            # If the same TxnID appears in two bucket sheets (shouldn't
            # happen, but the data is user-edited), keep the first.
            if txn_id in seen_txn_ids:
                continue
            seen_txn_ids.add(txn_id)

            txns.append(Transaction(
                txn_id=txn_id,
                source_account=source_account,
                date=d,
                description=description,
                amount=amount,
                raw_type="",
                payer="",
                reference=reference,
                raw_description=raw_desc,
                bucket=sheet_name,
                rule_applied="imported.history_xlsx",
                needs_review=False,
                source_file=filepath.name,
            ))

    return txns
