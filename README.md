# Welux Finance

A Python rebuild of your Google Sheets bookkeeping system. Designed for Welux Chauffeurs Ltd: multi-account ingestion (Wise, Revolut, Monzo, generic UK CSV, PDF), per-vehicle and per-driver P&L, VAT-return-ready reporting.

## Get it online

**👉 Read [DEPLOY.md](DEPLOY.md)** — step-by-step guide to put this on Streamlit Cloud + Supabase. Free, password-protected, ~25 minutes.

When you're done, you'll have a URL like `welux-finance.streamlit.app` that only you can access. Drop CSVs in, see the dashboard, repeat monthly.

## What this replaces

Your existing Google Sheet does:
- RAW_IMPORT → Apps Script categorization → category sheets → Dashboard
- VAT field per row + dashboard coverage %
- Per-vehicle / per-driver tracking via separate sheets

This system does the same things, plus:

| | Google Sheets | This system |
|---|---|---|
| Banks supported | Wise/Revolut only | Wise, Revolut, Monzo, UK CSV (Barclays/HSBC/etc.), PDF |
| Speed | Apps Script + sheet formulas | Pandas + Postgres, instant |
| Vehicle/driver | Separate sheets | Tags on every row — true per-asset P&L |
| VAT return | Coverage % | Full UK Boxes 1-9 |
| Triage queue | Two sheets (In/Out) | One tab |
| Audit log | Move_Log sheet | move_log table |

## Categorization rules

Faithfully ported from your `buildIncomingOutgoing()` in Apps Script — same priority order, same TYPE-gating, same `containsToken_` boundary matching:

**Incoming**: A UDDIN → KM20YYX IN; TOPUP+job-related → JOBS IN; major clients → JOBS IN; airport in reference → JOBS IN; INSURANCE → MISC PAYMENT IN; LOAN → MISC PAYMENT IN; WX21VZN → WX21VZN IN.

**Outgoing**: TOPUP+job-related → JOBS OUT; LOAN → MISC PAYMENT OUT; ZARYAB override → EXPENSES; CARD_PAYMENT routing (parking/fuel/EV/default).

**Recurring-pattern rules**: predictable transfers your Apps Script left in the unmoved queue (DVLA, TfL, wages, 1st Nationwide, UK Fuels, Haydock finance, Howden insurance, subcontractor TRANSFERs by airport / AD / JOB / invoice / postcode-TO / SERVICES) are auto-routed by default.

**Bug fixes vs Apps Script**: removed duplicate `1ST NATIONWIDE` from your destinations push; the prompt-destination batch move no longer drops VAT data.

## File layout

```
welux_finance/
├── app.py                  # Streamlit dashboard
├── ingest.py               # CLI: python ingest.py file account
├── adapters/               # bank-specific parsers
├── core/
│   ├── schema.py           # Transaction model
│   ├── store.py            # SQLAlchemy store (SQLite or Postgres)
│   ├── rules.py            # Apps Script port + recurring-pattern rules
│   ├── tagging.py          # vehicle/driver detection
│   └── vat.py              # UK VAT return
├── data/                   # local SQLite (gitignored)
├── sample_statements/      # test fixtures
├── DEPLOY.md               # ← cloud deployment guide
└── requirements.txt
```

## Local development (optional)

If you want to test changes before pushing to production:

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Locally it uses SQLite at `data/welux.db` and skips the password gate (no `APP_PASSWORD` set). Deployed to Streamlit Cloud, it uses your Supabase Postgres and enforces the password.

## Extending

- **New bank**: write an adapter in `adapters/` that produces `Transaction` objects, register in `adapters/__init__.py`
- **New vehicle**: add to `KNOWN_VEHICLES` in `core/tagging.py`
- **New driver**: add to `KNOWN_PEOPLE` in `core/tagging.py`
- **New rule**: add a clause to `route()` in `core/rules.py`
- **New bucket**: add to `INCOMING_BUCKETS`/`OUTGOING_BUCKETS` in `core/rules.py`

## Privacy

When deployed: data lives in your private Supabase database. Only you have the password. Code is in your private GitHub repo. No third party sees your bank data.
