"""
Data store. Supports two backends:

  - SQLite (default, for local dev) — file at data/welux.db
  - Postgres (for Streamlit Cloud) — set DATABASE_URL secret

When deployed to Streamlit Cloud, set DATABASE_URL in the app's secrets
to your Supabase Postgres connection string. Locally, leave it unset
and it falls back to SQLite.

The schema works on both backends with minor patching for SERIAL vs
INTEGER PRIMARY KEY AUTOINCREMENT.
"""
from __future__ import annotations
import os
from contextlib import contextmanager
from pathlib import Path
from datetime import date, datetime
from typing import Iterable, Optional
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from core.schema import Transaction


DEFAULT_SQLITE_PATH = Path(__file__).parent.parent / "data" / "welux.db"


def _get_database_url() -> str:
    """
    Returns the SQLAlchemy database URL.
    Priority: env var → Streamlit secrets → local SQLite fallback.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        try:
            import streamlit as st
            if "DATABASE_URL" in st.secrets:
                url = str(st.secrets["DATABASE_URL"]).strip()
        except Exception:
            pass

    if url:
        # Supabase pastes the URL as either "postgres://" or "postgresql://".
        # We use the psycopg (v3) driver explicitly because psycopg2-binary
        # stopped publishing wheels at Python 3.12 and breaks on Streamlit
        # Cloud's current 3.14 runtime.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                url = "postgresql+psycopg://" + url[len(prefix):]
                break
        return url

    # Local SQLite fallback
    DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_SQLITE_PATH}"


# Schema written portably; some dialect-specific patches applied below
SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS transactions (
        txn_id TEXT PRIMARY KEY,
        source_account TEXT NOT NULL,
        date DATE NOT NULL,
        description TEXT NOT NULL,
        amount DOUBLE PRECISION NOT NULL,
        raw_type TEXT,
        payer TEXT,
        reference TEXT,
        raw_description TEXT,
        fee DOUBLE PRECISION DEFAULT 0,
        balance_after DOUBLE PRECISION,
        direction TEXT NOT NULL,
        bucket TEXT,
        asset_tag TEXT,
        person_tag TEXT,
        vat DOUBLE PRECISION,
        vat_rate DOUBLE PRECISION,
        notes TEXT,
        rule_applied TEXT,
        needs_review INTEGER DEFAULT 0,
        source_file TEXT,
        raw_payload TEXT,
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date)",
    "CREATE INDEX IF NOT EXISTS idx_tx_bucket ON transactions(bucket)",
    "CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions(source_account)",
    "CREATE INDEX IF NOT EXISTS idx_tx_asset ON transactions(asset_tag)",
    "CREATE INDEX IF NOT EXISTS idx_tx_person ON transactions(person_tag)",
    "CREATE INDEX IF NOT EXISTS idx_tx_review ON transactions(needs_review)",
    """
    CREATE TABLE IF NOT EXISTS move_log (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        txn_id TEXT NOT NULL,
        from_bucket TEXT,
        to_bucket TEXT,
        note TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_log_txn ON move_log(txn_id)",
    """
    CREATE TABLE IF NOT EXISTS accounts (
        name TEXT PRIMARY KEY,
        bank TEXT,
        account_type TEXT,
        currency TEXT DEFAULT 'GBP',
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """,
]

# SQLite needs different keywords
SQLITE_PATCHES = {
    "SERIAL PRIMARY KEY": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "DOUBLE PRECISION": "REAL",
}


def _adapt_for_sqlite(sql: str) -> str:
    for k, v in SQLITE_PATCHES.items():
        sql = sql.replace(k, v)
    return sql


class Store:
    """SQL-backed transaction store. Works on SQLite or Postgres."""

    _engine_cache: Optional[Engine] = None
    _engine_url: Optional[str] = None

    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or _get_database_url()
        self.is_sqlite = self.db_url.startswith("sqlite")
        self._init_schema()

    def backend_summary(self) -> dict:
        """Human-readable info for the UI's database-status indicator."""
        if self.is_sqlite:
            path = self.db_url.replace("sqlite:///", "", 1)
            return {"backend": "SQLite", "host": path, "ephemeral": True}
        # Postgres URL: postgresql://user:pass@host:port/db
        host = self.db_url
        try:
            after_at = host.split("@", 1)[1]
            host = after_at.split("/", 1)[0]
        except IndexError:
            pass
        return {"backend": "Postgres", "host": host, "ephemeral": False}

    @property
    def engine(self) -> Engine:
        if Store._engine_cache is None or Store._engine_url != self.db_url:
            Store._engine_cache = create_engine(
                self.db_url,
                pool_pre_ping=True,
                pool_size=2 if not self.is_sqlite else 5,
                max_overflow=5,
            )
            Store._engine_url = self.db_url
        return Store._engine_cache

    def _init_schema(self):
        with self.engine.begin() as conn:
            for stmt in SCHEMA_SQL:
                if self.is_sqlite:
                    stmt = _adapt_for_sqlite(stmt)
                conn.execute(text(stmt))

    @contextmanager
    def conn(self):
        with self.engine.begin() as c:
            yield c

    # ---------- Account management ----------

    def upsert_account(self, name: str, bank: str = "", account_type: str = "",
                        currency: str = "GBP", notes: str = ""):
        # ON CONFLICT clause is the same in modern SQLite (3.24+) and Postgres
        sql = """
        INSERT INTO accounts (name, bank, account_type, currency, notes)
        VALUES (:name, :bank, :type, :cur, :notes)
        ON CONFLICT (name) DO UPDATE SET
          bank=EXCLUDED.bank, account_type=EXCLUDED.account_type,
          currency=EXCLUDED.currency, notes=EXCLUDED.notes
        """
        with self.conn() as c:
            c.execute(text(sql), {
                "name": name, "bank": bank, "type": account_type,
                "cur": currency, "notes": notes,
            })

    def list_accounts(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM accounts ORDER BY name", self.engine)

    # ---------- Transaction insert ----------

    def insert_transactions(self, txns: Iterable[Transaction]) -> dict:
        inserted = 0
        duplicates = 0

        sql = """
        INSERT INTO transactions (
            txn_id, source_account, date, description, amount,
            raw_type, payer, reference, raw_description, fee,
            balance_after, direction, bucket, asset_tag, person_tag,
            vat, vat_rate, notes, rule_applied, needs_review,
            source_file, raw_payload
        ) VALUES (
            :txn_id, :source_account, :date, :description, :amount,
            :raw_type, :payer, :reference, :raw_description, :fee,
            :balance_after, :direction, :bucket, :asset_tag, :person_tag,
            :vat, :vat_rate, :notes, :rule_applied, :needs_review,
            :source_file, :raw_payload
        )
        ON CONFLICT (txn_id) DO NOTHING
        """

        with self.conn() as c:
            for t in txns:
                params = {
                    "txn_id": t.txn_id,
                    "source_account": t.source_account,
                    "date": t.date.isoformat() if hasattr(t.date, 'isoformat') else t.date,
                    "description": t.description,
                    "amount": t.amount,
                    "raw_type": t.raw_type,
                    "payer": t.payer,
                    "reference": t.reference,
                    "raw_description": t.raw_description,
                    "fee": t.fee,
                    "balance_after": t.balance_after,
                    "direction": t.direction,
                    "bucket": t.bucket,
                    "asset_tag": t.asset_tag,
                    "person_tag": t.person_tag,
                    "vat": t.vat,
                    "vat_rate": t.vat_rate,
                    "notes": t.notes,
                    "rule_applied": t.rule_applied,
                    "needs_review": 1 if t.needs_review else 0,
                    "source_file": t.source_file,
                    "raw_payload": t.raw_payload,
                }
                result = c.execute(text(sql), params)
                if result.rowcount > 0:
                    inserted += 1
                    self._log(c, t.txn_id, None, t.bucket or "Unmoved",
                              "Imported" + (" + auto-routed" if t.bucket else ""))
                else:
                    duplicates += 1

        return {"inserted": inserted, "duplicates": duplicates}

    # ---------- Manual edits ----------

    def update_bucket(self, txn_id: str, new_bucket: str, note: str = "Manual edit"):
        with self.conn() as c:
            row = c.execute(
                text("SELECT bucket FROM transactions WHERE txn_id = :id"),
                {"id": txn_id},
            ).fetchone()
            if not row:
                return
            old = row[0] or ""
            # An empty new_bucket means no rule matched — leave it for triage.
            # Any non-empty bucket means a decision was made, clear needs_review.
            needs_review = 0 if new_bucket else 1
            c.execute(
                text("UPDATE transactions SET bucket = :b, needs_review = :nr WHERE txn_id = :id"),
                {"b": new_bucket, "nr": needs_review, "id": txn_id},
            )
            self._log(c, txn_id, old, new_bucket, note)

    def update_tags(self, txn_id: str, asset_tag: Optional[str] = None,
                     person_tag: Optional[str] = None):
        sets = []
        params = {"id": txn_id}
        if asset_tag is not None:
            sets.append("asset_tag = :asset")
            params["asset"] = asset_tag
        if person_tag is not None:
            sets.append("person_tag = :person")
            params["person"] = person_tag
        if not sets:
            return
        with self.conn() as c:
            c.execute(
                text(f"UPDATE transactions SET {', '.join(sets)} WHERE txn_id = :id"),
                params,
            )

    def update_vat(self, txn_id: str, vat: float, vat_rate: Optional[float] = None):
        with self.conn() as c:
            c.execute(
                text("UPDATE transactions SET vat = :v, vat_rate = :r WHERE txn_id = :id"),
                {"v": vat, "r": vat_rate, "id": txn_id},
            )

    def update_notes(self, txn_id: str, notes: str):
        with self.conn() as c:
            c.execute(
                text("UPDATE transactions SET notes = :n WHERE txn_id = :id"),
                {"n": notes, "id": txn_id},
            )

    def delete_transaction(self, txn_id: str):
        with self.conn() as c:
            c.execute(
                text("DELETE FROM transactions WHERE txn_id = :id"),
                {"id": txn_id},
            )
            self._log(c, txn_id, "*", None, "Deleted")

    # ---------- Queries ----------

    def all(self) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM transactions ORDER BY date DESC, txn_id",
            self.engine, parse_dates=["date", "imported_at"],
        )

    def by_bucket(self, bucket: str, start: Optional[date] = None,
                   end: Optional[date] = None) -> pd.DataFrame:
        sql = "SELECT * FROM transactions WHERE bucket = :b"
        params = {"b": bucket}
        if start:
            sql += " AND date >= :start"
            params["start"] = start.isoformat()
        if end:
            sql += " AND date <= :end"
            params["end"] = end.isoformat()
        sql += " ORDER BY date DESC"
        return pd.read_sql_query(text(sql), self.engine, params=params, parse_dates=["date"])

    def needing_review(self) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM transactions WHERE bucket IS NULL OR bucket = '' OR needs_review = 1 ORDER BY date DESC",
            self.engine, parse_dates=["date"],
        )

    def search(self, keyword: str = "", start: Optional[date] = None,
                end: Optional[date] = None) -> pd.DataFrame:
        sql = "SELECT * FROM transactions WHERE 1=1"
        params = {}
        if keyword:
            sql += " AND (UPPER(description) LIKE :kw OR UPPER(payer) LIKE :kw OR UPPER(reference) LIKE :kw)"
            params["kw"] = f"%{keyword.upper()}%"
        if start:
            sql += " AND date >= :start"
            params["start"] = start.isoformat()
        if end:
            sql += " AND date <= :end"
            params["end"] = end.isoformat()
        sql += " ORDER BY date DESC"
        return pd.read_sql_query(text(sql), self.engine, params=params, parse_dates=["date"])

    # ---------- Logging ----------

    def _log(self, conn, txn_id: str, from_bucket: Optional[str],
              to_bucket: Optional[str], note: str):
        conn.execute(
            text("INSERT INTO move_log (txn_id, from_bucket, to_bucket, note) VALUES (:t, :f, :tb, :n)"),
            {"t": txn_id, "f": from_bucket, "tb": to_bucket, "n": note},
        )

    def move_log(self, limit: int = 200) -> pd.DataFrame:
        return pd.read_sql_query(
            text("SELECT * FROM move_log ORDER BY id DESC LIMIT :lim"),
            self.engine, params={"lim": limit}, parse_dates=["timestamp"],
        )

    # ---------- Reset ----------

    def reset_transactions(self):
        with self.conn() as c:
            c.execute(text("DELETE FROM transactions"))
            c.execute(text("DELETE FROM move_log"))
