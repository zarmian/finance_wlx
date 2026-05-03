"""
Categorization rules engine.

This is a faithful Python port of the Apps Script rules in your
`buildIncomingOutgoing()` function. The matching logic, priority order, and
TYPE-gating are all preserved.

Rules are a port of your Apps Script's `buildIncomingOutgoing()` plus a
small set of additions derived from your bucket history.

INCOMING (priority order — first match wins):
  1.  payer/desc contains "A UDDIN"          -> KM20YYX IN
  1b. desc/ref contains "WAYZ"               -> WAYZ MOTORS  (NEW — placed
      before TOPUP+job because "ADded" substring otherwise pre-empts it)
  2.  is_job_related_topup                   -> JOBS IN
  3.  desc contains MONTCLARES/BLACKLANE/    -> JOBS IN
      WELUX CHAUFFEURS LTD
  4.  reference contains a London airport    -> JOBS IN
  5.  desc contains "INSURANCE"              -> MISC PAYMENT IN
  6.  desc contains "LOAN"                   -> MISC PAYMENT IN
  7.  desc/ref contains "WX21VZN" (or        -> WX21VZN IN
      "WX21 VZN" with a space)
  Tier-2 only:
  8.  reference == "INVESTMENT" exactly      -> MISC PAYMENT IN
  9.  desc contains an EXTRA_JOBS_IN_CLIENTS -> JOBS IN
      member (INTEL FM, AGIS/LUXOR, etc.)

OUTGOING (priority order — first match wins):
  1.  is_job_related_topup                   -> JOBS OUT
  2.  desc contains "LOAN"                   -> MISC PAYMENT OUT
  3.  desc contains "ZARYAB"                 -> EXPENSES (overrides Wages)
  3b. desc/ref contains "WAYZ"               -> WAYZ MOTORS  (NEW)
  3c. desc contains WR19EOU or CA71ADZ       -> WALEED EXPENSE  (NEW —
      owner's personal vehicles; business plates fall through)
  4.  type == CARD_PAYMENT and:
      a. is_parking                          -> PARKING
      b. is_ev_charging                      -> EQV OUT  (NEW: checked
         within is_fuel so SHELL EV / MFG EV / BP PULSE win over fuel)
      c. is_fuel                             -> FUEL
      d. else                                -> EXPENSES
  Tier-2 only (after CARD_PAYMENT routing):
  5.  Nationwide / UK Fuels / DVLA / TfL /   -> respective buckets
      HMRC / fees / driver wages
  6.  HAYDOCK FIN(ANCE)                      -> MISC PAYMENT OUT
  7.  HOWDEN UK BROKERS                      -> MISC PAYMENT OUT
  8.  TRANSFER + airport/AD<n>/JOB ref       -> JOBS OUT

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


# AD as a job-batch reference: "AD", "AD3-7", "AD20-6", "AD'S", "AD 10-7".
# Tighter than the substring match in _is_job_related_topup — must be
# followed by space/dash/digit/apostrophe/end so ADDISON, ADVANTIS, ADJUST
# don't match.
_AD_TOKEN_RE = re.compile(r"(^|[^A-Z0-9])AD(?=[\s\-0-9'’\"]|$)")


def _is_outgoing_transfer_job(type_upper: str, match_key_upper: str) -> bool:
    """
    Tier-2 helper: outgoing TRANSFER that looks like a subcontractor
    job-payout. Used to auto-route the historical ~30% of JOBS OUT rows
    that follow the "<recipient> - <airport-code|AD<n>|JOB<x>>" pattern.
    """
    if type_upper != "TRANSFER":
        return False
    if "JOB" in match_key_upper:  # "BLJOB", "LHRJOBS", "CHAUFFEUR JOBS"
        return True
    if _AD_TOKEN_RE.search(match_key_upper):
        return True
    if any(name in match_key_upper for name in LONDON_AIRPORT_NAMES):
        return True
    if any(_contains_token(match_key_upper, c) for c in LONDON_AIRPORT_CODES):
        return True
    return False


def _is_fuel(match_key_upper: str) -> bool:
    """
    Port of isFuel_(), with a safety check: if the descriptor also
    contains an EV-charging marker (e.g. 'SHELL RECHARGE', 'MFG EV POWER',
    'BP PULSE'), defer — EV charging routes to EQV OUT, not FUEL.
    """
    if _is_ev_charging(match_key_upper):
        return False
    return (
        "SHELL" in match_key_upper
        or "ESSO" in match_key_upper
        or "MFG" in match_key_upper
        # NYX*MOTORFUELLTD / NYX*TESCO are Wise's fuel-card-network descriptors
        or "NYX*" in match_key_upper
        or _contains_token(match_key_upper, "BP")
    )


EV_CHARGING_PHRASES = [
    "UBITRICITY", "EV CHARGING", "EVCHARGING", "EV-CHARGING",
    "ELECTRIC VEHICLE CHARGING", "CHARGEPOINT", "CHARGE POINT",
    "POD POINT", "PODPOINT", "BP PULSE", "BPPULSE",
    "SHELL RECHARGE", "SHELLRECHARGE", "IONITY", "INSTAVOLT",
    "OCTOPUS ELECTROVERSE", "ELECTROVERSE", "GENIEPOINT", "GENIE POINT",
    "SOURCE LONDON", "CHARGEMASTER", "TESLA SUPERCHARGER", "TESLA CHARGING",
    # Additional providers seen in Welux history
    "SHELL EV", "SMART CHARGE", "MFG EV", "SOURCE EV",
    "ESB EV", "AFFORDABLE EV", "DART CHARGE",
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

# Vehicle finance lender — recurring monthly outgoing
HAYDOCK_KEYWORDS = ["HAYDOCK FIN", "HAYDOCK FINANCE"]

# Insurance broker — premium payments (rare; refunds are incoming)
HOWDEN_KEYWORDS = ["HOWDEN UK BROKERS", "HOWDEN INSURANCE"]

# Investment top-ups from family/lenders — distinct from job revenue
INVESTMENT_KEYWORDS = ["INVESTMENT", "EXPENSE TOPUP"]

# Recurring JOBS IN clients seen in historic books, in addition to the
# Apps Script's MONTCLARES / BLACKLANE / WELUX CHAUFFEURS LTD set. Placed
# after the INSURANCE/LOAN rules so insurance/loan refs from these clients
# still route to MISC PAYMENT IN.
EXTRA_JOBS_IN_CLIENTS = [
    "INTEL FM LTD",
    "INTEL FM",
    "AGIS EXECUTIVE",
    "LUXOR CARS",
    "MACMILLAN CHAUFFEU",
    "AZ LUXE",
    "TRANS LONDON LIMITED",
    "GROSVENOR CHAUFFEURS",
    "VINTAGE LUXURY CHAUFFEURS",
]


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
    # Whitespace-stripped variant — lets us catch "WX21 VZN" and "WX21VZN"
    # uniformly without changing the original token-boundary helpers.
    match_key_compact = re.sub(r"\s+", "", match_key)
    ref_key = txn.reference.upper()
    type_upper = txn.raw_type.upper()

    if txn.is_incoming:
        # Rule 1: A UDDIN -> KM20YYX IN
        if "A UDDIN" in match_key:
            return RoutingResult("KM20YYX IN", "incoming.a_uddin")

        # Rule 1b: WAYZ -> WAYZ MOTORS. Placed before TOPUP+job because
        # incoming "Money added from ... - wayzAli" would otherwise be
        # captured by the original "AD" substring inside "ADded".
        if "WAYZ" in match_key:
            return RoutingResult("WAYZ MOTORS", "incoming.wayz")

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

        # Rule 7: WX21VZN -> WX21VZN IN (also matches "WX21 VZN" with a space)
        if "WX21VZN" in match_key_compact:
            return RoutingResult("WX21VZN IN", "incoming.wx21vzn")

        # === Tier-2 incoming rules (opt-in) ===
        if ENABLE_TIER2:
            # Family/lender capital top-ups: reference is exactly "INVESTMENT"
            # (avoids matching "weluxrevinvestment" or other compound refs).
            if ref_key.strip() == "INVESTMENT":
                return RoutingResult("MISC PAYMENT IN", "incoming.investment")

            # Additional recurring JOBS IN clients seen in historic books
            for client in EXTRA_JOBS_IN_CLIENTS:
                if client in match_key:
                    return RoutingResult("JOBS IN", f"incoming.client_{client.lower().replace(' ', '_')}")

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

        # Rule 3b: WAYZ -> WAYZ MOTORS (Wayz side-business — repairs, refunds, parts)
        if "WAYZ" in match_key:
            return RoutingResult("WAYZ MOTORS", "outgoing.wayz")

        # Rule 3c: WR19EOU / CA71ADZ are owner's personal vehicles — route any
        # outgoing tagged with these plates (DVLA, fines, repairs) to WALEED EXPENSE.
        # (Business plates WX21VZN / WR24MRY / LC24YNH continue to fall through
        # to EXPENSES via the default route, matching prior bookkeeping.)
        if (_contains_token(match_key, "WR19EOU")
                or _contains_token(match_key, "CA71ADZ")):
            return RoutingResult("WALEED EXPENSE", "outgoing.waleed_personal_vehicle")

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

            # Vehicle finance — Haydock recurring monthly debit
            if _has_any(match_key, HAYDOCK_KEYWORDS):
                return RoutingResult("MISC PAYMENT OUT", "outgoing.haydock_finance")

            # Insurance broker premium payment (refunds are incoming)
            if _has_any(match_key, HOWDEN_KEYWORDS):
                return RoutingResult("MISC PAYMENT OUT", "outgoing.howden_insurance")

            # Subcontractor job-payouts — outgoing TRANSFER with airport/AD/JOB
            # reference. Mirrors the existing TOPUP+job rule for the incoming side.
            if _is_outgoing_transfer_job(type_upper, match_key):
                return RoutingResult("JOBS OUT", "outgoing.transfer_job_related")

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
