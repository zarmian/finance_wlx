"""
Generic PDF statement parser.

PDF parsing is inherently unreliable across banks because each bank's
PDF layout is different. This adapter does best-effort extraction and
flags every row with `needs_review = True` so you can confirm.

For best results, prefer CSV exports. PDFs are a fallback for banks
that don't offer CSV.
"""
from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path
from typing import List

import pdfplumber

from core.schema import Transaction, make_txn_id, encode_raw


# Date patterns at start of a transaction line
DATE_PATTERNS = [
    r"^(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    r"^(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})",
    r"^(\d{4}-\d{2}-\d{2})",
]

# Currency amount: optional sign, optional symbol, digits with separators, optional CR/DR
AMOUNT_PATTERN = r"[-+]?[£$€]?\s?\d{1,3}(?:[,\s]\d{3})*(?:\.\d{2})?(?:\s?CR|\s?DR)?"


def _parse_amount(s: str) -> float:
    if not s:
        return 0.0
    is_neg = False
    s = s.strip()
    if s.upper().endswith("DR"):
        is_neg = True
        s = s[:-2].strip()
    elif s.upper().endswith("CR"):
        s = s[:-2].strip()
    s = re.sub(r"[£$€,\s]", "", s)
    if s.startswith("-"):
        is_neg = True
        s = s[1:]
    try:
        v = float(s)
        return -v if is_neg else v
    except ValueError:
        return 0.0


def _parse_date_str(s: str):
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_pdf_statement(filepath: str | Path, source_account: str) -> List[Transaction]:
    filepath = Path(filepath)

    txns = []
    with pdfplumber.open(filepath) as pdf:
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text() or ""
            full_text += text + "\n"

    lines = full_text.split("\n")

    for idx, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        # Find date at start of line
        date_match = None
        for pattern in DATE_PATTERNS:
            m = re.match(pattern, line)
            if m:
                date_match = m
                break
        if not date_match:
            continue

        d = _parse_date_str(date_match.group(1))
        if not d:
            continue

        rest = line[date_match.end():].strip()

        # Find trailing amounts (last 1-2 numbers are amount + balance)
        amounts = re.findall(AMOUNT_PATTERN, rest)
        amounts = [a.strip() for a in amounts if a.strip() and re.search(r"\d", a)]
        if not amounts:
            continue

        if len(amounts) >= 2:
            # Heuristic: last two trailing numbers, last is balance, second-to-last is amount
            amount_str = amounts[-2]
            balance_str = amounts[-1]
            desc_end = rest.rfind(amount_str)
        else:
            amount_str = amounts[-1]
            balance_str = None
            desc_end = rest.rfind(amount_str)

        description = rest[:desc_end].strip()
        if not description:
            continue

        amount = _parse_amount(amount_str)
        if amount == 0:
            continue

        # PDFs often don't indicate sign reliably — assume debit unless explicit credit marker
        # User will need to fix in review. We flag needs_review=True below.

        balance = _parse_amount(balance_str) if balance_str else None

        txn_id = make_txn_id(
            source_account, "",
            fallback_key=f"{d.isoformat()}|{description}|{amount}|{idx}"
        )

        txn = Transaction(
            txn_id=txn_id,
            source_account=source_account,
            date=d,
            description=description,
            amount=amount,
            raw_type="UNKNOWN",
            payer="",
            reference="",
            raw_description=description,
            balance_after=balance,
            source_file=filepath.name,
            raw_payload=encode_raw({"line": line, "page_idx": idx}),
            needs_review=True,  # Always flag PDF rows for confirmation
        )
        txns.append(txn)

    if not txns:
        raise ValueError(
            f"Could not extract any transactions from {filepath.name}. "
            "The PDF may be scanned (image-only). Try CSV export from your bank."
        )

    return txns
