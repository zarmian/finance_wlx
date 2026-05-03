"""
Categorization rules engine.

This is a faithful Python port of the Apps Script rules in your
`buildIncomingOutgoing()` function. The matching logic, priority order, and
TYPE-gating are all preserved.

The rules below are exactly your Apps Script rules:

INCOMING (priority order — first match wins):
  1. payer/desc contains "A UDDIN"           -> KM20YYX IN
  2. is_job_related_topup                    -> JOBS IN
  3. desc contains MONTCLARES/BLACKLANE/     -> JOBS IN
     WELUX CHAUFFEURS LTD
  4. reference contains a London airport     -> JOBS IN
  5. desc contains "INSURANCE"               -> MISC PAYMENT IN
  6. desc contains "LOAN"                    -> MISC PAYMENT IN
  7. desc contains "WX21VZN"                 -> WX21VZN IN

OUTGOING (priority order — first match wins):
  1. is_job_related_topup                    -> JOBS OUT
  2. desc contains "LOAN"                    -> MISC PAYMENT OUT
  3. desc contains "ZARYAB"                  -> EXPENSES (overrides Wages)
  4. type == CARD_PAYMENT and:
     a. is_parking                           -> PARKING
     b. is_fuel                              -> FUEL
     c. is_ev_charging                       -> EQV OUT
     d. else                                 -> EXPENSES

`is_job_related_topup` requires:
  - type == "TOPUP"
  - AND (job-keyword OR airport-name OR airport-code-as-token)

Bug fixes vs Apps Script (per your instruction "fix obvious bugs"):
  - Removed duplicate "1ST NATIONWIDE" in destinations list
  - Token-aware matching (already in your `containsToken_`) is preserved here
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List, Callable
import re

from core.schema import Transaction


# ============================================================
# Bucket definitions — single source of truth (no duplicates)
# ============================================================

INCOMING_BUCKETS = [
    "JOBS IN",
    "WX21VZN IN",
    "EQV IN",
    "KM20YYX IN",
    "MISC PAYMENT IN",
]

OUTGOING_BUCKETS = [
    "JOBS OUT",
    "WX21VZN OUT",
    "EQV OUT",
    "MISC PAYMENT OUT",
    "EXPENSES",
    "FUEL",
    "PARKING",
    "WATER + OTHER",
    "WALEED EXPENSE",
    "IMRAN EXPENSE",
    "WAYZ MOTORS",
    "1ST NATIONWIDE",
]

ALL_BUCKETS = INCOMING_BUCKETS + OUTGOING_BUCKETS


# ============================================================
# Token matching (port of your containsToken_ function)
# ============================================================

def _contains_token(text_upper: str, token_upper: str) -> bool:
    """
    Port of Apps Script containsToken_().

    Match the token only at word boundaries — so "BP" matches "BP PULSE"
    but not "PROBLEM"; "LCY" matches "LCY DROP OFF" but not "POLICY".
    """
    if not text_upper or not token_upper:
        return False
    pattern = rf"(^|[^A-Z0-9]){re.escape(token_upper)}([^A-Z0-9]|$)"
    return bool(re.search(pattern, text_upper))


# ============================================================
# Helper predicates — direct ports from Apps Script
# ============================================================

JOB_KEYWORDS = ["JOB", "AD", "ADS", "ADVERTISEMENT"]
LONDON_AIRPORT_NAMES = ["HEATHROW", "GATWICK", "STANSTED", "LUTON", "LONDON CITY"]
LONDON_AIRPORT_CODES = ["LHR", "LGW", "STN", "LTN", "LCY"]


def _is_job_related_topup(type_upper: str, match_key_upper: str) -> bool:
    """Port of isJobRelatedTopup_()."""
    if type_upper != "TOPUP":
        return False

    # Substring match for job keywords (matches your Apps Script — note
    # this means "AD" inside "READ" would falsely trigger; that's the
    # same behavior as the original. If you want token-strict for
    # JOB_KEYWORDS too, change this to _contains_token.)
    if any(k in match_key_upper for k in JOB_KEYWORDS):
        return True

    if any(name in match_key_upper for name in LONDON_AIRPORT_NAMES):
        return True

    if any(_contains_token(match_key_upper, c) for c in LONDON_AIRPORT_CODES):
        return True

    return False


def _is_fuel(match_key_upper: str) -> bool:
    """Port of isFuel_()."""
    return (
        "SHELL" in match_key_upper
        or "ESSO" in match_key_upper
        or "MFG" in match_key_upper
        or _contains_token(match_key_upper, "BP")
    )


EV_CHARGING_PHRASES = [
    "UBITRICITY", "EV CHARGING", "EVCHARGING", "EV-CHARGING",
    "ELECTRIC VEHICLE CHARGING", "CHARGEPOINT", "CHARGE POINT",
    "POD POINT", "PODPOINT", "BP PULSE", "BPPULSE",
    "SHELL RECHARGE", "SHELLRECHARGE", "IONITY", "INSTAVOLT",
    "OCTOPUS ELECTROVERSE", "ELECTROVERSE", "GENIEPOINT", "GENIE POINT",
    "SOURCE LONDON", "CHARGEMASTER", "TESLA SUPERCHARGER", "TESLA CHARGING",
]


def _is_ev_charging(match_key_upper: str) -> bool:
    """Port of isEvCharging_()."""
    return any(p in match_key_upper for p in EV_CHARGING_PHRASES)


PARKING_PHRASES = [
    "APCOA", "NCP", "RINGGO", "RING GO", "JUSTPARK", "JUST PARK",
    "Q-PARK", "QPARK", "SABA", "EURO CAR PARKS", "EUROCARPARKS",
    "PARKING", "CAR PARK", "PARK & RIDE", "PARK AND RIDE",
    "HEATHROW", "GATWICK", "STANSTED", "LUTON", "LONDON CITY",
]


def _is_parking(match_key_upper: str) -> bool:
    """Port of isParking_()."""
    if any(p in match_key_upper for p in PARKING_PHRASES):
        return True
    return any(_contains_token(match_key_upper, c) for c in LONDON_AIRPORT_CODES)


def _contains_london_airport(ref_upper: str) -> bool:
    """Port of containsLondonAirport_()."""
    if not ref_upper:
        return False
    if any(name in ref_upper for name in LONDON_AIRPORT_NAMES):
        return True
    return any(_contains_token(ref_upper, c) for c in LONDON_AIRPORT_CODES)


# ============================================================
# Main routing function — port of buildIncomingOutgoing rules
# ============================================================

@dataclass
class RoutingResult:
    bucket: str = ""
    rule_applied: str = ""
    needs_review: bool = False


# ============================================================
# Tier-2 rules — extensions beyond the Apps Script
# These cover predictable recurring transactions your script left
# in the manual triage queue. They fire AFTER the primary rules so
# they never override your explicit Apps Script logic.
# ============================================================

# Driver wages — TRANSFER + driver name + "wages"/"salary" reference
WAGE_KEYWORDS = ["WAGES", "WAGE", "SALARY", "PAYROLL", "PAY"]

# 1st Nationwide vehicle leases — TRANSFER to "1st nationwide security"
NATIONWIDE_KEYWORDS = ["1ST NATIONWIDE", "FIRST NATIONWIDE"]

# DVLA vehicle tax / fines
DVLA_KEYWORDS = ["DVLA"]

# UK Fuels card account
UK_FUELS_KEYWORDS = ["UK FUELS"]

# TfL congestion / ULEZ
TFL_KEYWORDS = ["TFL ", "TFL-", "TRANSPORT FOR LONDON", "CONGESTN", "CONGESTION", "ULEZ"]

# HMRC tax payments
HMRC_KEYWORDS = ["HMRC"]

# Wise/Revolut fees
FEE_KEYWORDS = ["WISE BUSINESS FEE", "REVOLUT BUSINESS FEE", "MONTHLY FEE", "BASIC PLAN FEE"]


def _has_any(text: str, keywords: list) -> bool:
    return any(k in text for k in keywords)


def route(txn: Transaction) -> RoutingResult:
    """
    Apply categorization rules to a transaction.
    Returns the bucket and which rule was applied (for audit).

    If no rule fires, bucket is empty and needs_review is True.
    """
    # Build the same `matchKey` your Apps Script uses
    match_key = f"{txn.payer} {txn.raw_description} {txn.reference}".upper()
    ref_key = txn.reference.upper()
    type_upper = txn.raw_type.upper()

    if txn.is_incoming:
        # Rule 1: A UDDIN -> KM20YYX IN
        if "A UDDIN" in match_key:
            return RoutingResult("KM20YYX IN", "incoming.a_uddin")

        # Rule 2: TOPUP + job/airport -> JOBS IN
        if _is_job_related_topup(type_upper, match_key):
            return RoutingResult("JOBS IN", "incoming.topup_job_related")

        # Rule 3: Major client names -> JOBS IN
        if "MONTCLARES" in match_key:
            return RoutingResult("JOBS IN", "incoming.client_montclares")
        if "BLACKLANE" in match_key:
            return RoutingResult("JOBS IN", "incoming.client_blacklane")
        if "WELUX CHAUFFEURS LTD" in match_key:
            return RoutingResult("JOBS IN", "incoming.client_welux_chauffeurs")

        # Rule 4: Airport in reference -> JOBS IN
        if _contains_london_airport(ref_key):
            return RoutingResult("JOBS IN", "incoming.airport_in_reference")

        # Rule 5: INSURANCE -> MISC PAYMENT IN
        if "INSURANCE" in match_key:
            return RoutingResult("MISC PAYMENT IN", "incoming.insurance")

        # Rule 6: LOAN -> MISC PAYMENT IN
        if "LOAN" in match_key:
            return RoutingResult("MISC PAYMENT IN", "incoming.loan")

        # Rule 7: WX21VZN -> WX21VZN IN
        if "WX21VZN" in match_key:
            return RoutingResult("WX21VZN IN", "incoming.wx21vzn")

        # No match — needs review
        return RoutingResult("", "", needs_review=True)

    else:  # Outgoing
        # Rule 1: TOPUP + job/airport (note: TOPUPs are usually incoming;
        # this handles the rare outgoing TOPUP case from your script)
        if _is_job_related_topup(type_upper, match_key):
            return RoutingResult("JOBS OUT", "outgoing.topup_job_related")

        # Rule 2: LOAN -> MISC PAYMENT OUT
        if "LOAN" in match_key:
            return RoutingResult("MISC PAYMENT OUT", "outgoing.loan")

        # Rule 3: ZARYAB -> EXPENSES (override per your script)
        if "ZARYAB" in match_key:
            return RoutingResult("EXPENSES", "outgoing.zaryab_override")

        # Rule 4: TYPE-gated card payment routing
        if type_upper == "CARD_PAYMENT":
            if _is_parking(match_key):
                return RoutingResult("PARKING", "outgoing.card.parking")
            if _is_fuel(match_key):
                return RoutingResult("FUEL", "outgoing.card.fuel")
            if _is_ev_charging(match_key):
                return RoutingResult("EQV OUT", "outgoing.card.ev_charging")
            return RoutingResult("EXPENSES", "outgoing.card.default")

        # === Tier-2 rules (opt-in, beyond your Apps Script) ===
        # These cover predictable recurring patterns your script left in the
        # manual triage queue. They only fire if ENABLE_TIER2 is True.
        # Default is False so behavior matches your Apps Script exactly —
        # turn on when you're ready to reduce manual triage.
        if ENABLE_TIER2:
            # 1st Nationwide vehicle lease payments
            if _has_any(match_key, NATIONWIDE_KEYWORDS):
                return RoutingResult("1ST NATIONWIDE", "outgoing.nationwide_lease")

            # UK Fuels card account top-up
            if _has_any(match_key, UK_FUELS_KEYWORDS):
                return RoutingResult("FUEL", "outgoing.uk_fuels")

            # DVLA payments — vehicle tax/fines
            if _has_any(match_key, DVLA_KEYWORDS):
                return RoutingResult("EXPENSES", "outgoing.dvla")

            # TfL congestion / ULEZ
            if _has_any(match_key, TFL_KEYWORDS):
                return RoutingResult("EXPENSES", "outgoing.tfl")

            # HMRC tax payments
            if _has_any(match_key, HMRC_KEYWORDS):
                return RoutingResult("EXPENSES", "outgoing.hmrc")

            # Bank/payment-platform fees
            if type_upper == "FEE" or _has_any(match_key, FEE_KEYWORDS):
                return RoutingResult("EXPENSES", "outgoing.platform_fee")

            # Driver wages — TRANSFER with wage reference
            if (type_upper == "TRANSFER"
                and _has_any(match_key, WAGE_KEYWORDS)):
                return RoutingResult("EXPENSES", "outgoing.driver_wages")

        # Non-card-payment outgoing with no specific rule — needs review
        return RoutingResult("", "", needs_review=True)


# Set ENABLE_TIER2 = True in your local config to turn on the extra rules
# above. Default OFF means default behavior matches your Apps Script.
ENABLE_TIER2 = False


def apply_rules(txn: Transaction) -> Transaction:
    """Apply rules in-place to a transaction and return it."""
    result = route(txn)
    txn.bucket = result.bucket
    txn.rule_applied = result.rule_applied
    txn.needs_review = result.needs_review
    return txn
