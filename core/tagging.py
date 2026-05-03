"""
Asset and person tagging.

Vehicle registration plates and driver names are extracted as separate
fields, not just by which bucket the transaction lives in. This means:
  - A DVLA payment for "WX21VZN" gets asset_tag="WX21VZN" regardless of bucket
  - A wages transfer to "Hafiz Raza" gets person_tag="HAFIZ"
  - Per-vehicle P&L = SQL group by asset_tag
  - Per-driver P&L = SQL group by person_tag
"""
from __future__ import annotations
import re
from typing import Optional, Tuple

from core.schema import Transaction


# Vehicle registrations recorded for the business.
# Includes private/cherished plates (B22CEO, BO07CEO, LO07RDG) where the
# UK age-format regex doesn't apply — these are matched by explicit name.
KNOWN_VEHICLES = {
    "WX21VZN",
    "WR24MRY",
    "WR19EOU",
    "KM20YYX",
    "KM20YYS",
    "LC24YNH",
    "CA71ADZ",
    "B22CEO",
    "BO07CEO",
    "LO07RDG",
    # Logical vehicle group from your sheet
    "EQV",  # appears as "EQV IN/OUT" — your EV
}

# Plate-change aliases — newer plate maps to older canonical plate so all
# transactions for the same physical vehicle group together in P&L.
VEHICLE_ALIASES = {
    "BO07CEO": "CA71ADZ",
    "LO07RDG": "WR24MRY",
}

# Directors only — non-director drivers' wages/jobs route via merchant
# rules (JOBS OUT, EXPENSES) and don't need person-tagging.
KNOWN_PEOPLE = {
    "WALEED": ["WALEED AHMED", "WALEED"],
    "IMRAN": ["IMRAN ALI KHAN NIAZI", "IMRAN NIAZI", "IMRAN N", "IMRAN"],
}


# UK number plate pattern (post-2001 format): two letters + two digits + space? + three letters
# Examples: WX21VZN, WR24MRY, LC24YNH
UK_REG_PATTERN = re.compile(r"\b([A-Z]{2}\d{2}\s?[A-Z]{3})\b")


def _normalize_reg(s: str) -> str:
    """Strip spaces and uppercase: 'wx21 vzn' -> 'WX21VZN'."""
    return re.sub(r"\s+", "", s).upper()


def _flexible_plate_pattern(plate: str) -> str:
    """
    Build a regex that matches the plate with optional whitespace at every
    letter↔digit boundary, so KNOWN_VEHICLES entries like "B22CEO" match
    both "B22CEO" and "B22 CEO" in source text.
    """
    parts = re.findall(r"[A-Z]+|\d+", plate.upper())
    body = r"\s*".join(re.escape(p) for p in parts)
    return rf"(^|[^A-Z0-9]){body}([^A-Z0-9]|$)"


_VEHICLE_PATTERNS = {
    v: re.compile(_flexible_plate_pattern(v)) for v in KNOWN_VEHICLES
}


def find_vehicle_tag(txn: Transaction) -> Optional[str]:
    """
    Find the vehicle reg plate associated with this transaction.
    Searches description + reference + payer.

    Returns the canonical tag (e.g. "WX21VZN") or None. Plate aliases
    (BO07CEO → CA71ADZ, LO07RDG → WR24MRY) collapse to the canonical name.
    """
    haystack = " ".join([
        txn.description, txn.reference, txn.payer, txn.raw_description
    ]).upper()

    # First check exact known-vehicle matches (covers EQV which isn't a real
    # plate, plus private plates like B22 CEO that don't fit the UK regex).
    for vehicle, pat in _VEHICLE_PATTERNS.items():
        if pat.search(haystack):
            return VEHICLE_ALIASES.get(vehicle, vehicle)

    # Then try UK plate pattern in case there's a new plate we haven't catalogued
    matches = UK_REG_PATTERN.findall(haystack)
    if matches:
        # Take the first valid-looking match
        candidate = _normalize_reg(matches[0])
        return VEHICLE_ALIASES.get(candidate, candidate)

    return None


def find_person_tag(txn: Transaction) -> Optional[str]:
    """
    Find the driver/person associated with this transaction.
    Returns canonical tag (e.g. "WALEED") or None.

    Note: this looks at recipients/payers, not "Waleed Ahmed" who is the
    Wise account holder (i.e. "Payer = Waleed Ahmed" on outgoing transfers
    just means he authorized it, not that the money went TO him).
    """
    # For outgoing: look in description (which is the recipient)
    # For incoming: look in payer/description (who sent money)
    if txn.is_incoming:
        haystack = f"{txn.payer} {txn.raw_description} {txn.reference}".upper()
    else:
        # Outgoing: ignore payer (it's always the account holder),
        # look at description (which contains "To <recipient>")
        haystack = f"{txn.raw_description} {txn.reference}".upper()

    for canonical, aliases in KNOWN_PEOPLE.items():
        for alias in aliases:
            pattern = rf"(^|[^A-Z0-9]){re.escape(alias)}([^A-Z0-9]|$)"
            if re.search(pattern, haystack):
                return canonical

    return None


def apply_tags(txn: Transaction) -> Transaction:
    """Apply asset_tag and person_tag in-place."""
    if not txn.asset_tag:
        v = find_vehicle_tag(txn)
        if v:
            txn.asset_tag = v
    if not txn.person_tag:
        p = find_person_tag(txn)
        if p:
            txn.person_tag = p
    return txn
