"""
American Express Business Gold Charge Card — PDF statement adapter.

Imran and Waleed each have an Amex Business Gold under Welux Chauffeurs
Ltd. The card settles its balance via a monthly direct debit from Wise,
so the merchant-level detail only exists on these PDFs; the Wise CSV
just shows the lump settlement.

To avoid double-counting:
- Every merchant charge becomes a Transaction (negative amount, since
  it's outgoing from Welux's pocket) and goes to triage with
  `rule_applied = "imported.amex_pdf"` — the user manually moves each
  to the right bucket (FUEL / PARKING / IMRAN EXPENSE / etc.).
- The "PAYMENT RECEIVED - THANK YOU" row (positive, incoming to the
  Amex account) is auto-pinned to the AMEX SETTLEMENT bucket. The
  matching outgoing from Wise ("To American Express") is routed to the
  same bucket by core/rules.py:outgoing.amex_settlement. Both sides net
  to zero in the P&L.
- Mid-statement credits (refunds, chargebacks, "OTHER ACCOUNT
  TRANSACTIONS" refunds) become positive-amount transactions in triage.

The adapter extracts the cardholder name from the "Prepared for" line
and sets source_account accordingly (`welux_amex_imran`, etc.) so
multiple cardholders' statements don't collide. Supplementary cards on
the same statement (two card numbers in Imran's PDFs) are stored under
the primary cardholder's account — the card number goes into
raw_payload for audit.
"""
from __future__ import annotations
import hashlib
import re
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import pdfplumber

from core.schema import Transaction, make_txn_id, encode_raw


MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# A transaction row starts with "<Mmm><D> <Mmm><D> " (two dates: trans / process).
# Dates look like "Mar24", "Jan3", "Feb27" — month letters + 1-2 digit day.
_ROW_HEAD_RE = re.compile(
    r"^([A-Z][a-z]{2})(\d{1,2})\s+([A-Z][a-z]{2})(\d{1,2})\s+(.+)$"
)

# Trailing GBP amount: optional leading minus, digits with comma separators, two decimals.
# Also need to tolerate rows where no amount is on this line (continuation lines).
_TRAIL_AMOUNT_RE = re.compile(r"(-?\d{1,3}(?:,\d{3})*\.\d{2})\s*$")

# Lines that are pure continuation metadata we discard:
#   "FUEL", "GOODS", "TICKETS", "RETAIL", "CRY712 - Croydon",
#   "BP 1 John Street STREET", "9448053629 K 1 TYRES LIMITED"
_SKIP_LINES = {
    "FUEL", "GOODS", "TICKETS", "RETAIL",
}


def looks_like_amex_pdf(filepath: str | Path) -> bool:
    """Cheap check: open page 1 and look for the Amex Business Gold header."""
    try:
        with pdfplumber.open(filepath) as pdf:
            if not pdf.pages:
                return False
            text = (pdf.pages[0].extract_text() or "").upper()
    except Exception:
        return False
    return (
        "AMERICAN EXPRESS" in text
        and ("STATEMENT OF ACCOUNT" in text or "BUSINESS GOLD" in text)
    )


def _cardholder_first_name(full_name: str) -> str:
    """IMRAN ALI KHAN NIAZI -> imran. Used as the source_account suffix."""
    parts = full_name.strip().split()
    return parts[0].lower() if parts else "unknown"


def _parse_statement_period(text: str) -> Optional[tuple[int, int]]:
    """
    Extract the (year_start, year_end) from "Statement Period From
    27January to26March2026". Returns None if not found.
    """
    m = re.search(r"Statement Period From(.+?)Next", text, re.S)
    if not m:
        return None
    period = m.group(1)
    # Note: can't use \b here — Amex prints "to26March2026" with the year
    # glued to the month name, and \b doesn't fire between letters and
    # digits (both are word chars). Match plain 4-digit sequences starting
    # with 20.
    years = re.findall(r"(20\d{2})", period)
    if not years:
        return None
    # Typical format has one year at the end covering both from/to months.
    # Jan-Feb crossover years will have two — use first for start, last for end.
    y_start = int(years[0])
    y_end = int(years[-1])
    return y_start, y_end


def _month_to_year(month_num: int, period: tuple[int, int], statement_end_month: int) -> int:
    """
    Resolve a Mmm<D> date on the statement to a calendar year.
    Statement runs across month boundaries. We know the statement end
    month, so months <= end_month are in year_end; months > end_month
    belong to year_start (the previous calendar year).
    """
    y_start, y_end = period
    if month_num <= statement_end_month:
        return y_end
    return y_start


def _statement_end_month(text: str) -> int:
    """Parse e.g. 'to26March2026' -> 3."""
    m = re.search(r"to\s*\d{1,2}\s*([A-Z][a-z]+)\s*20\d{2}", text)
    if not m:
        return 12
    name = m.group(1)[:3].upper()
    return MONTHS.get(name, 12)


def _parse_cardholder(text: str) -> str:
    """Extract 'IMRAN ALI KHAN NIAZI' from the 'Prepared for' header."""
    m = re.search(
        r"Prepared for.*?\n([A-Z][A-Z ]+?)\s+xxxx[-x]{3,}",
        text, re.S,
    )
    if m:
        return m.group(1).strip()
    # Fallback — scan first page for an all-caps line that looks like a name
    for line in text.split("\n")[:30]:
        line = line.strip()
        if 2 <= len(line.split()) <= 5 and line.replace(" ", "").isalpha() and line.isupper():
            return line
    return "UNKNOWN"


def _is_payment_received(desc_upper: str) -> bool:
    return "PAYMENT RECEIVED" in desc_upper


def parse_amex_pdf(filepath: str | Path, source_account: str) -> List[Transaction]:
    """
    Parse an Amex Business Gold statement PDF into Transaction objects.

    source_account passed by the caller is used as the account name, but
    if the PDF's cardholder is known the adapter will refine it to
    `welux_amex_<firstname>` so Imran's and Waleed's statements don't
    land in the same account.
    """
    filepath = Path(filepath)
    with pdfplumber.open(filepath) as pdf:
        pages_text = [(p.extract_text() or "") for p in pdf.pages]
    full_text = "\n".join(pages_text)

    cardholder = _parse_cardholder(full_text)
    period = _parse_statement_period(full_text)
    if not period:
        raise ValueError(
            f"{filepath.name}: couldn't find statement period. Not an Amex PDF?"
        )
    end_month = _statement_end_month(full_text)

    # Refine source_account based on cardholder — keeps Imran's / Waleed's
    # statements in separate accounts even if the caller passed a generic
    # value.
    first = _cardholder_first_name(cardholder)
    if first and first != "unknown":
        source_account = f"welux_amex_{first}"

    txns: List[Transaction] = []
    current_card: str = ""   # which card number we're currently reading rows under
    in_tx_block = False
    # Tracks how many rows we've already emitted with the exact same
    # (date, desc, amount, card) so same-day identical refunds (e.g. two
    # £9 Next Online CR entries) get distinct hashes instead of
    # collapsing into one via dedup.
    row_ordinals: dict = {}

    def flush_row(row_date: date, desc: str, amount: float,
                   is_credit: bool, card: str) -> None:
        """Create a Transaction from a parsed row. Signs: outgoing = negative,
        incoming / credit = positive."""
        if is_credit:
            signed = abs(amount)
        else:
            signed = -abs(amount)

        desc_upper = desc.upper()
        payment_received = _is_payment_received(desc_upper)

        # Stable per-row id. Amex doesn't give a bank txn id so we hash
        # date + description + amount + card number + an ordinal (for
        # within-statement duplicates like repeated Next refunds).
        ordinal_key = (row_date.isoformat(), desc.strip(), round(signed, 2), card)
        n = row_ordinals.get(ordinal_key, 0)
        row_ordinals[ordinal_key] = n + 1
        fallback_key = f"{row_date.isoformat()}|{desc.strip()}|{signed:.2f}|{card}|{n}"
        txn_id = make_txn_id(source_account, bank_id="", fallback_key=fallback_key)

        if payment_received:
            bucket = "AMEX SETTLEMENT"
            rule_applied = "imported.amex_pdf_settlement"
            needs_review = False
        else:
            # Charge or mid-statement credit: both go to triage so the user
            # can decide bucket (business vs personal, FUEL vs PARKING, etc.)
            bucket = ""
            rule_applied = "imported.amex_pdf"
            needs_review = True

        # Direction derived from amount sign — Transaction.__post_init__
        # will do this automatically.
        txns.append(Transaction(
            txn_id=txn_id,
            source_account=source_account,
            date=row_date,
            description=desc.strip(),
            amount=signed,
            raw_type="CARD_PAYMENT" if not payment_received else "TRANSFER",
            payer=cardholder,
            reference=card,
            raw_description=desc.strip(),
            bucket=bucket,
            rule_applied=rule_applied,
            needs_review=needs_review,
            source_file=filepath.name,
            raw_payload=encode_raw({
                "cardholder": cardholder,
                "card": card,
                "amount": signed,
                "date": row_date.isoformat(),
            }),
        ))

    # Walk lines. Transactions start with the "Mmm<D> Mmm<D>" header; a
    # trailing `CR` on the next line flips the row to a credit. FX
    # transactions have three lines but the first already carries the
    # GBP amount so we only use that.
    for text in pages_text:
        lines = text.split("\n")
        pending_row: Optional[dict] = None   # waiting to see if next line is "CR"

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            # Section markers that delimit the transaction tables
            if "Transaction Process" in line or "Transaction Details" in line:
                in_tx_block = True
                continue
            if (
                "How you can pay your statement" in line
                or "Total new spend" in line
                or "Total of other account" in line
                or "Account Total" in line
                or "Please pay your statement" in line
                or "Membership Rewards" in line
            ):
                # Flush any pending row before leaving the block
                if pending_row is not None:
                    flush_row(**pending_row)
                    pending_row = None
                in_tx_block = False
                continue

            if not in_tx_block:
                continue

            # Card-number header updates the current card
            card_match = re.match(r"Card Number\s+(xxxx[-x]*\d+)", line)
            if card_match:
                if pending_row is not None:
                    flush_row(**pending_row)
                    pending_row = None
                current_card = card_match.group(1)
                continue

            # If this line is "CR" alone, mark the pending row as credit.
            if line == "CR":
                if pending_row is not None:
                    pending_row["is_credit"] = True
                continue

            # "OTHER ACCOUNT TRANSACTIONS" section markers / headings
            if line.upper() in ("OTHER ACCOUNT TRANSACTIONS",):
                continue

            # Try to parse a transaction row header
            head = _ROW_HEAD_RE.match(line)
            if head:
                # Flush any previous pending row before starting a new one
                if pending_row is not None:
                    flush_row(**pending_row)
                    pending_row = None

                m_mon, m_day, p_mon, p_day, rest = head.groups()
                mon_num = MONTHS.get(p_mon.upper())
                if not mon_num:
                    continue
                try:
                    year = _month_to_year(mon_num, period, end_month)
                    row_date = date(year, mon_num, int(p_day))
                except ValueError:
                    continue

                # Trailing amount
                amt_match = _TRAIL_AMOUNT_RE.search(rest)
                if not amt_match:
                    # No amount on this line — skip (shouldn't happen for
                    # real rows, but be defensive).
                    continue
                amount = float(amt_match.group(1).replace(",", ""))
                desc = rest[:amt_match.start()].strip()

                # Some rows look like "SafeKey - APCOA PARKING (UK) LIMITED 40.00"
                # — those live inside "OTHER ACCOUNT TRANSACTIONS" and the
                # section marker set in_tx_block via the "Transaction Details"
                # header above. Handled uniformly.

                pending_row = {
                    "row_date": row_date,
                    "desc": desc,
                    "amount": amount,
                    "is_credit": False,
                    "card": current_card,
                }
                continue

            # Not a row header — must be a continuation line.
            # If it's a pure skip-label (FUEL/GOODS/etc.) we drop it;
            # otherwise it's additional merchant metadata that we also drop,
            # since the row's main info is already captured.
            upper = line.upper()
            if upper in _SKIP_LINES:
                continue
            # Lines like "UNITED STATES DOLLAR", "Exchange Rate ...",
            # "BP Hayes Road SOUTHALL", "9448053629 K 1 TYRES LIMITED",
            # "CRY712 - Croydon", "ILF239 - Ilford" — all discarded.

        # End of page: flush pending
        if pending_row is not None:
            flush_row(**pending_row)
            pending_row = None

    return txns
