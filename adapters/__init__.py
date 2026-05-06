"""
Adapter dispatcher.

Auto-detects bank format and routes to the right adapter, or accepts
an explicit format hint.
"""
from __future__ import annotations
import pandas as pd
from pathlib import Path
from typing import List

from core.schema import Transaction
from adapters.wise import parse_wise_csv, looks_like_wise
from adapters.monzo import parse_monzo_csv, looks_like_monzo
from adapters.uk_generic import parse_uk_generic_csv
from adapters.welux_history import (
    parse_welux_history_xlsx, looks_like_welux_history,
)


def detect_format(filepath: str | Path) -> str:
    """Returns one of: 'wise', 'monzo', 'uk_generic', 'welux_history',
    'pdf', 'unknown'."""
    filepath = Path(filepath)
    suffix = filepath.suffix.lower()

    if suffix == ".pdf":
        return "pdf"

    if suffix in (".xlsx", ".xls"):
        if looks_like_welux_history(filepath):
            return "welux_history"
        return "unknown"

    if suffix not in (".csv", ".tsv", ".txt"):
        return "unknown"

    # Read just the headers
    df = pd.read_csv(filepath, dtype=str, nrows=0, encoding="utf-8-sig")
    headers = [c.strip() for c in df.columns]

    if looks_like_wise(headers):
        return "wise"
    if looks_like_monzo(headers):
        return "monzo"
    return "uk_generic"


def parse(filepath: str | Path, source_account: str,
           force_format: str = "") -> List[Transaction]:
    """
    Parse any supported statement format.
    Auto-detects format from headers; pass `force_format` to override.
    """
    fmt = force_format or detect_format(filepath)

    if fmt == "wise":
        return parse_wise_csv(filepath, source_account)
    if fmt == "monzo":
        return parse_monzo_csv(filepath, source_account)
    if fmt == "uk_generic":
        return parse_uk_generic_csv(filepath, source_account)
    if fmt == "welux_history":
        return parse_welux_history_xlsx(filepath, source_account)
    if fmt == "pdf":
        from adapters.pdf_generic import parse_pdf_statement
        return parse_pdf_statement(filepath, source_account)

    raise ValueError(f"Unknown format for {filepath}")
