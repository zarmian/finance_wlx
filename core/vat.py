"""
VAT module.

UK VAT return needs:
  Box 1: VAT due on sales (output VAT) — sum of VAT on incoming
  Box 4: VAT reclaimed on purchases (input VAT) — sum of VAT on outgoing
  Box 6: Total sales ex-VAT — sum of (incoming - VAT)
  Box 7: Total purchases ex-VAT — sum of (outgoing - VAT)
  Box 3 = Box 1 (no EU acquisitions for most UK businesses post-Brexit)
  Box 5 = Box 1 - Box 4 (net VAT to pay HMRC)
  Box 8 = 0 (no EU goods supplied)
  Box 9 = 0 (no EU goods acquired)

Per-row VAT rate is stored, so mixed-rate handling works.
"""
from __future__ import annotations
from datetime import date
from typing import Optional
import pandas as pd

from core.store import Store


def calc_vat_amount(amount: float, rate: float, is_gross: bool) -> float:
    """
    Calculate the VAT component of an amount.

    is_gross=True  : amount includes VAT (typical for UK retail, restaurants)
                     vat = amount - amount/(1+rate)
    is_gross=False : amount excludes VAT (typical for B2B invoices)
                     vat = amount * rate

    Sign is preserved (incoming amount → positive VAT, outgoing → negative VAT).
    """
    if rate <= 0:
        return 0.0
    sign = 1 if amount >= 0 else -1
    base = abs(amount)
    if is_gross:
        vat_abs = base - (base / (1 + rate))
    else:
        vat_abs = base * rate
    return round(sign * vat_abs, 2)


def vat_return(store: Store, start: date, end: date) -> dict:
    """
    Compute UK VAT return values for a date range.
    Only transactions with VAT explicitly set (not None) are counted —
    blank VAT means "not yet reviewed" and is excluded from the return.
    """
    df = store.all()
    if df.empty:
        return _empty_return()

    df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]

    # Only rows with VAT explicitly set
    has_vat = df[df["vat"].notna()].copy()

    incoming = has_vat[has_vat["amount"] > 0]
    outgoing = has_vat[has_vat["amount"] < 0]

    box1 = round(incoming["vat"].sum(), 2)             # output VAT (positive)
    box4 = round(abs(outgoing["vat"].sum()), 2)        # input VAT (positive for display)

    # Sales ex-VAT: gross sales minus VAT on sales
    gross_sales = round(incoming["amount"].sum(), 2)
    box6 = round(gross_sales - box1, 2)

    # Purchases ex-VAT: |gross purchases| minus VAT on purchases
    gross_purchases = round(abs(outgoing["amount"].sum()), 2)
    box7 = round(gross_purchases - box4, 2)

    # Coverage — what fraction of in-period rows have VAT set?
    in_period = df[df["amount"] != 0]
    coverage_in = _coverage(in_period[in_period["amount"] > 0])
    coverage_out = _coverage(in_period[in_period["amount"] < 0])

    return {
        "period_start": start,
        "period_end": end,
        "box1_output_vat": box1,
        "box3_total_vat_due": box1,           # for non-EU-trading businesses
        "box4_input_vat": box4,
        "box5_net_vat": round(box1 - box4, 2),
        "box6_sales_ex_vat": box6,
        "box7_purchases_ex_vat": box7,
        "box8_eu_goods_supplied": 0.0,
        "box9_eu_goods_acquired": 0.0,
        "coverage_in_pct": coverage_in,
        "coverage_out_pct": coverage_out,
        "rows_total": len(in_period),
        "rows_with_vat": len(has_vat),
    }


def _coverage(df: pd.DataFrame) -> float:
    if len(df) == 0:
        return 0.0
    with_vat = df[df["vat"].notna()]
    return round(100.0 * len(with_vat) / len(df), 1)


def _empty_return() -> dict:
    return {
        "box1_output_vat": 0.0, "box3_total_vat_due": 0.0,
        "box4_input_vat": 0.0, "box5_net_vat": 0.0,
        "box6_sales_ex_vat": 0.0, "box7_purchases_ex_vat": 0.0,
        "box8_eu_goods_supplied": 0.0, "box9_eu_goods_acquired": 0.0,
        "coverage_in_pct": 0.0, "coverage_out_pct": 0.0,
        "rows_total": 0, "rows_with_vat": 0,
    }
