"""
Ingestion pipeline.

The full flow:
  1. Adapter parses the file into Transaction objects
  2. Rules engine assigns a bucket
  3. Tagging engine sets asset_tag and person_tag
  4. Store inserts (deduping by txn_id)
  5. Returns a summary

Use as CLI:    python ingest.py path/to/statement.csv welux_wise_gbp
Use from API:  ingest_file(path, account)
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import sys

from core.schema import Transaction
from core.store import Store
from core.rules import apply_rules
from core.tagging import apply_tags
from adapters import parse, detect_format


def ingest_file(filepath: str | Path, source_account: str,
                 store: Optional[Store] = None,
                 force_format: str = "") -> dict:
    """
    Ingest one statement file end-to-end.
    Returns: {'inserted': N, 'duplicates': M, 'needs_review': K, 'format': fmt}
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(filepath)

    if store is None:
        store = Store()

    fmt = force_format or detect_format(filepath)
    txns = parse(filepath, source_account, force_format=fmt)

    # Apply rules then tagging. Skip rules if the adapter already pinned
    # a bucket (e.g. welux_history XLSX preserves manual classifications).
    for t in txns:
        if not t.bucket:
            apply_rules(t)
        apply_tags(t)

    # Ensure account exists (creates it if it's the first time)
    store.upsert_account(source_account)

    # Historical XLSX should override bucket on existing rows so the
    # manual classification wins over any earlier rule-based import.
    overwrite_bucket = (fmt == "welux_history")
    result = store.insert_transactions(txns, overwrite_bucket=overwrite_bucket)
    needs_review = sum(1 for t in txns if t.needs_review)

    return {
        "format": fmt,
        "parsed": len(txns),
        "inserted": result["inserted"],
        "duplicates": result["duplicates"],
        "needs_review": needs_review,
    }


def main():
    if len(sys.argv) < 3:
        print("Usage: python ingest.py <statement_file> <account_name> [format]")
        print("  format (optional): wise | monzo | uk_generic | pdf")
        print("\nExamples:")
        print("  python ingest.py statements/wise_jan26.csv welux_wise_gbp")
        print("  python ingest.py statements/monzo_jan26.csv personal_monzo monzo")
        sys.exit(1)

    filepath = sys.argv[1]
    account = sys.argv[2]
    fmt = sys.argv[3] if len(sys.argv) > 3 else ""

    result = ingest_file(filepath, account, force_format=fmt)
    print(f"\n✓ Imported {filepath}")
    print(f"  Format detected: {result['format']}")
    print(f"  Rows parsed:     {result['parsed']}")
    print(f"  Inserted:        {result['inserted']}")
    print(f"  Duplicates:      {result['duplicates']}")
    print(f"  Need review:     {result['needs_review']}")


if __name__ == "__main__":
    main()
