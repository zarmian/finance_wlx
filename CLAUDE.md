# Welux Finance — Project Context for Claude Code

## What this is

A Python rebuild of a Google Sheets + Apps Script bookkeeping system for **Welux Chauffeurs Ltd** — a UK chauffeur/private hire business. The owner had built a categorization system in Sheets that auto-routes Wise transactions into category sheets (JOBS IN, EXPENSES, FUEL, per-vehicle sheets etc.) using ~500 lines of Apps Script. This Python system replicates that logic, adds multi-bank support, and is deployed to Streamlit Cloud + Supabase.

## Business context — read this before changing rules

The owner runs a fleet of chauffeur vehicles. Key entities:

**Vehicles** (registration plates): `WX21VZN`, `WR24MRY`, `WR19EOU`, `KM20YYX`, `LC24YNH`, `CA71ADZ`. Plus a logical group `EQV` for an electric vehicle. These appear in transaction descriptions as "Dvla-wx21vzn", "WX21VZN RENT", "VclassJan" etc.

**Drivers/people**: WALEED (Ahmed), IMRAN (Niazi), HAFIZ (Raza), ZARYAB (Rashid), SOHAIL, SHAUKAT, ALEX, MOUGHEES, TAMIM, RIZWAN, IKRAM, KASAHUN.

**Major clients**: MONTCLARES, BLACKLANE (HAVN UK), HARIS SERVICES, VINTAGE LUXURY CHAUFFEURS, ENT MULT SER LTD (vehicle rental subagency).

**Suppliers/recurring costs**:
- 1st Nationwide Security (vehicle leases — references like "VclassJan", "ImrSjan", "Sclass jan")
- DVLA (per-plate vehicle tax)
- Apcoa, Ringgo, NCP, City of Westminster, LCA Drop Off (parking)
- Shell, Esso, BP, MFG, UK Fuels Limited (fuel)
- Pod Point, BP Pulse, Ubitricity, Octopus Electroverse (EV charging)
- TfL Congestion Charge / ULEZ
- HMRC (corp tax, VAT)
- Addison Accounts, Moorside Legal (professional services)

VAT registered with mixed handling — some transactions have VAT, some are zero-rated.

## The buckets (categorization destinations)

Incoming: `JOBS IN`, `WX21VZN IN`, `EQV IN`, `KM20YYX IN`, `MISC PAYMENT IN`

Outgoing: `JOBS OUT`, `WX21VZN OUT`, `EQV OUT`, `MISC PAYMENT OUT`, `EXPENSES`, `FUEL`, `PARKING`, `WATER + OTHER`, `WALEED EXPENSE`, `IMRAN EXPENSE`, `WAYZ MOTORS`, `1ST NATIONWIDE`

These names are FROZEN — they match the original Apps Script and the owner's existing dashboard. Don't rename them.

## Categorization rules — the heart of the system

`core/rules.py` has the rule engine. The rules are a **faithful port of `buildIncomingOutgoing()` from the original Apps Script**. Priority order is deliberate. Matching logic uses `containsToken_` (regex word boundaries) so e.g. "BP" doesn't match inside "PROBLEM" and "LCY" doesn't match inside "POLICY".

**Tier-2 rules** are an extension beyond the original Apps Script — they catch predictable recurring patterns (DVLA, TfL, wages, 1st Nationwide, UK Fuels, fees) that the original script left in the manual triage queue. They're behind `ENABLE_TIER2` (default off) so default behavior matches Apps Script exactly. The user can toggle this in the Streamlit sidebar.

**When asked to change rules, do the minimum:** add the new rule at the right priority slot. Don't restructure unless asked.

## Architecture

```
app.py                       # Streamlit dashboard — only entry point users see
ingest.py                    # CLI: python ingest.py file account_name
adapters/
  __init__.py                # auto-detect format, dispatch
  wise.py                    # Wise/Revolut Business CSV
  monzo.py                   # Monzo CSV
  uk_generic.py              # Barclays/HSBC/Lloyds-style CSVs
  pdf_generic.py             # PDF best-effort, flags rows for review
core/
  schema.py                  # Transaction dataclass — canonical format
  store.py                   # SQLAlchemy store, SQLite locally / Postgres prod
  rules.py                   # Rule engine + Tier-2
  tagging.py                 # Vehicle reg + driver name detection
  vat.py                     # UK VAT return Boxes 1-9
```

Every adapter outputs `Transaction` objects in the same canonical shape. Rules and tagging operate on this canonical form. The store is dialect-portable.

## Database

- **Local dev**: SQLite at `data/welux.db`
- **Production**: Postgres on Supabase, via `DATABASE_URL` secret

The store auto-detects which to use. ON CONFLICT clauses are written to work on both. Indexes are on `date`, `bucket`, `source_account`, `asset_tag`, `person_tag`, `needs_review`.

Dedup is by `txn_id` which is `<source_account>::<bank_id>` if the bank gave us an ID, otherwise `<source_account>::HASH-<md5>`. Re-importing a statement is safe — duplicates are silently skipped.

## Deployment

Streamlit Cloud + Supabase, both free tiers. See `DEPLOY.md` for the click-by-click. The `APP_PASSWORD` secret enables the password gate (top of `app.py`). Without it, no auth (fine for local dev).

## Conventions

- Buckets are SHOUTY UPPERCASE strings (matches Apps Script and the existing sheet)
- Vehicle tags are SHOUTY UPPERCASE alphanumeric (`WX21VZN`)
- Person tags are SHOUTY UPPERCASE single names (`WALEED` not `Waleed Ahmed`)
- All amounts are **signed**: positive = incoming, negative = outgoing. Never store the absolute value.
- VAT field is `None` if unset, `0` if explicitly zero-rated, otherwise the VAT amount (signed same as parent transaction)
- Dates: always `datetime.date`, never strings, never datetimes (we don't care about time)

## What to be careful with

1. **Don't change rule priority order** without asking — the order is load-bearing. ZARYAB → EXPENSES override exists *because* a TRANSFER to Zaryab would otherwise hit no rule and need manual routing.
2. **Don't break dedup** — `txn_id` must be stable across re-imports of the same data. Adding fields to the hash inputs would invalidate all existing rows.
3. **Keep adapter output canonical** — when adding a new bank, that adapter's only job is to emit `Transaction` objects. Don't put rules or tagging in adapters.
4. **`raw_payload` is for audit only** — store the original row as JSON, never read it back as a primary data source. If a column is needed downstream, promote it to a real field on `Transaction`.
5. **Streamlit reruns the whole script on every interaction** — don't put expensive work outside `@st.cache_data` boundaries.

## Common tasks

**Adding a new vehicle reg**: edit `KNOWN_VEHICLES` set in `core/tagging.py`. That's it.

**Adding a new driver**: edit `KNOWN_PEOPLE` dict in `core/tagging.py`. Add aliases as needed.

**Adding a new categorization rule**: add a clause to `route()` in `core/rules.py` at the appropriate priority position. Test with `python ingest.py sample_statements/welux_wise_sample.csv welux_wise_gbp` after `rm data/welux.db`.

**Adding a new bank**: write `adapters/<bankname>.py` with `parse_<bankname>_csv()` returning `List[Transaction]` and a `looks_like_<bankname>()` detector. Register both in `adapters/__init__.py`.

**Running locally**: `streamlit run app.py`. Visit http://localhost:8501.

**Running tests** (after writing them): there are no tests yet. The sample statements in `sample_statements/` are de-facto fixtures — re-importing them after changes is the manual smoke test.

## What NOT to do

- Don't add features that weren't requested. The owner has clear priorities; speculative complexity hurts.
- Don't replace the rule engine with ML. Rules are auditable and the owner needs to understand every routing decision for HMRC purposes.
- Don't add a frontend framework on top of Streamlit. Streamlit Cloud free tier is the deployment target.
- Don't add user accounts / multi-tenancy. Single user, single business.
- Don't add a mobile app. The Streamlit URL works on mobile.
- Don't add bank API integrations (Plaid/TrueLayer). The owner uses Wise CSV exports — not negotiable.

## How the owner works with you

The owner is technical-adjacent — runs a chauffeur business, built the original Sheet + Apps Script themselves. Comfortable with code review but doesn't want to write Python. When they ask for a change:

- **Make the change directly**, don't make them edit code by hand
- **Run the test ingestion** afterward to confirm nothing broke
- **Show the diff** in plain English ("I changed X so that Y")
- **Don't explain the obvious** — they've seen the codebase before

When they say "doesn't work" — ask which specific transaction or bucket, then go look at `move_log` and the rule that fired. Don't speculate.
