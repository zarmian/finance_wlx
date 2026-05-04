"""
Welux Finance Dashboard.

Run with: streamlit run app.py

Tabs:
  1. Dashboard       — your existing summary, ported faithfully
  2. Vehicle P&L     — per-vehicle income vs expenses
  3. Triage          — review queue + assign bucket
  4. VAT             — UK VAT return Boxes 1-9
  5. Transactions    — searchable, editable list
  6. Import          — drop a statement, ingest
  7. Move Log        — audit trail
"""
from __future__ import annotations
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import hmac

import core.rules as rules_module
from core.store import Store
from core.rules import (
    route, apply_rules,
    INCOMING_BUCKETS, OUTGOING_BUCKETS, ALL_BUCKETS,
)
from core.tagging import apply_tags, KNOWN_VEHICLES
from core.vat import vat_return, calc_vat_amount
from ingest import ingest_file
from adapters import detect_format


st.set_page_config(
    page_title="Welux Finance",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# Password gate (active when APP_PASSWORD secret is set)
# ============================================================

def _check_password() -> bool:
    """
    Returns True if the user provided the correct password.
    If APP_PASSWORD isn't set in secrets, no gate is shown (local dev mode).
    """
    try:
        configured = st.secrets.get("APP_PASSWORD", "")
    except Exception:
        configured = ""

    # Local dev fallback — no password set, just open the app
    if not configured:
        return True

    if st.session_state.get("authenticated"):
        return True

    st.title("Welux Finance")
    st.caption("This system is private. Enter the password to continue.")

    pw = st.text_input("Password", type="password", key="_pw_input")
    if st.button("Sign in"):
        # constant-time compare
        if hmac.compare_digest(pw, configured):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    return False


if not _check_password():
    st.stop()


store = Store()


# ============================================================
# Sidebar
# ============================================================

with st.sidebar:
    st.title("💼 Welux Finance")

    accounts = store.list_accounts()
    all_data = store.all()

    if all_data.empty:
        st.info("No data yet. Import a statement to start.")
        date_range = (date.today() - timedelta(days=30), date.today())
        selected_accounts = []
    else:
        min_d = all_data["date"].min().date()
        max_d = all_data["date"].max().date()

        st.subheader("📅 Period")
        preset = st.radio(
            "Range",
            ["This month", "Last month", "Last 3 months", "This year", "All time", "Custom"],
            index=0, label_visibility="collapsed",
        )

        today = date.today()
        if preset == "This month":
            start = date(today.year, today.month, 1)
            end = max_d
        elif preset == "Last month":
            first_this_month = date(today.year, today.month, 1)
            end = first_this_month - timedelta(days=1)
            start = date(end.year, end.month, 1)
        elif preset == "Last 3 months":
            start = today - timedelta(days=90)
            end = max_d
        elif preset == "This year":
            start = date(today.year, 1, 1)
            end = max_d
        elif preset == "Custom":
            r = st.date_input("Custom range", value=(min_d, max_d),
                                min_value=min_d, max_value=max_d)
            if isinstance(r, tuple) and len(r) == 2:
                start, end = r
            else:
                start, end = min_d, max_d
        else:  # All time
            start, end = min_d, max_d

        date_range = (start, end)

        st.divider()
        st.subheader("🏦 Accounts")
        if not accounts.empty:
            selected_accounts = st.multiselect(
                "Filter",
                options=accounts["name"].tolist(),
                default=accounts["name"].tolist(),
                label_visibility="collapsed",
            )
        else:
            selected_accounts = []

        st.divider()
        # Tier-2 rules toggle
        st.subheader("⚙️ Settings")
        tier2 = st.checkbox(
            "Enable Tier-2 auto-rules",
            value=rules_module.ENABLE_TIER2,
            help="Auto-categorize recurring patterns (DVLA, TfL, wages, fees, "
                  "1st Nationwide, UK Fuels) instead of leaving them for "
                  "manual triage. Default off matches your Apps Script behavior."
        )
        if tier2 != rules_module.ENABLE_TIER2:
            rules_module.ENABLE_TIER2 = tier2
            st.info("Tier-2 toggled. Click 'Re-categorize all' below to apply to existing rows.")

        if st.button("🔄 Re-categorize all"):
            with st.spinner("Re-running rules..."):
                from core.schema import Transaction as Txn
                df = store.all()
                count = 0
                for _, row in df.iterrows():
                    # Build a Transaction-like object just to run rules
                    t = Txn(
                        txn_id=row["txn_id"],
                        source_account=row["source_account"],
                        date=row["date"].date() if hasattr(row["date"], "date") else row["date"],
                        description=row["description"],
                        amount=row["amount"],
                        raw_type=row["raw_type"] or "",
                        payer=row["payer"] or "",
                        reference=row["reference"] or "",
                        raw_description=row["raw_description"] or "",
                    )
                    apply_rules(t)
                    apply_tags(t)
                    if t.bucket != (row["bucket"] or "") or t.asset_tag != (row["asset_tag"] or "") or t.person_tag != (row["person_tag"] or ""):
                        store.update_bucket(t.txn_id, t.bucket, "Re-categorize")
                        store.update_tags(t.txn_id, t.asset_tag, t.person_tag)
                        count += 1
                st.success(f"Re-categorized: {count} rows changed")
                st.rerun()

        st.divider()
        with st.expander("⚠️ Danger zone"):
            st.caption(
                "Wipes every transaction and the move log. Accounts and "
                "settings are kept. Use this before re-importing a fresh "
                "history. Cannot be undone."
            )
            row_count = len(all_data)
            confirm = st.text_input(
                f"Type RESET to delete all {row_count} transaction(s)",
                key="_reset_confirm",
            )
            if st.button("🗑️ Reset all data", disabled=(confirm != "RESET")):
                store.reset_transactions()
                st.success(f"Deleted {row_count} transaction(s). Refreshing...")
                st.rerun()


# ============================================================
# Filter helper
# ============================================================

def filter_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    start, end = date_range
    mask = (df["date"].dt.date >= start) & (df["date"].dt.date <= end)
    if selected_accounts:
        mask &= df["source_account"].isin(selected_accounts)
    return df[mask].copy()


# ============================================================
# Empty-state landing
# ============================================================

if all_data.empty:
    st.title("Welux Finance Dashboard")
    st.markdown(
        """
        ### Getting started

        1. Use the **Import** tab to drop a Wise CSV (or Monzo, Revolut, or any UK bank CSV)
        2. The system parses it, applies your categorization rules, tags vehicles/drivers, and stores it
        3. Re-import next month — duplicates are detected by transaction ID

        Your existing Apps Script logic is preserved. Tier-2 rules add automation
        for predictable patterns (DVLA, TfL, wages, etc.) but are off by default.
        """
    )

    # Skip straight to the import tab
    tab_import, = st.tabs(["📤 Import"])
    with tab_import:
        _render_import = True  # Will hit the import section below
    # Continue to render import below
    tab_dashboard = tab_vehicle = tab_triage = tab_vat = tab_tx = tab_log = None

else:
    tx_all = filter_df(all_data)

    tab_dashboard, tab_vehicle, tab_triage, tab_vat, tab_tx, tab_import, tab_log = st.tabs([
        "📊 Dashboard",
        "🚗 Vehicle P&L",
        "🔍 Triage",
        "🧾 VAT",
        "📋 Transactions",
        "📤 Import",
        "📜 Move Log",
    ])


# ============================================================
# Tab: Dashboard (priority — replicates your Apps Script summary)
# ============================================================

if not all_data.empty:
    with tab_dashboard:
        st.header(f"Dashboard  ·  {date_range[0]} → {date_range[1]}")

        # Build summary in your Apps Script structure
        # JOBS, WX21VZN, EQV, MISC PAYMENT — IN/OUT pairs
        # EXPENSES, FUEL, WALEED EXPENSE, IMRAN EXPENSE, WAYZ MOTORS — OUT only

        groups = [
            ("JOBS", "JOBS IN", "JOBS OUT"),
            ("WX21VZN", "WX21VZN IN", "WX21VZN OUT"),
            ("EQV", "EQV IN", "EQV OUT"),
            ("KM20YYX", "KM20YYX IN", None),  # No KM20YYX OUT in your script
            ("MISC PAYMENT", "MISC PAYMENT IN", "MISC PAYMENT OUT"),
        ]
        expense_only = [
            "EXPENSES", "FUEL", "PARKING", "WALEED EXPENSE",
            "IMRAN EXPENSE", "WAYZ MOTORS", "1ST NATIONWIDE", "WATER + OTHER",
        ]

        rows = []
        for label, in_bucket, out_bucket in groups:
            in_df = tx_all[tx_all["bucket"] == in_bucket] if in_bucket else pd.DataFrame()
            out_df = tx_all[tx_all["bucket"] == out_bucket] if out_bucket else pd.DataFrame()

            in_amt = in_df["amount"].sum() if not in_df.empty else 0
            out_amt = abs(out_df["amount"].sum()) if not out_df.empty else 0
            balance = in_amt - out_amt

            in_vat = in_df["vat"].sum() if not in_df.empty and in_df["vat"].notna().any() else 0
            out_vat = abs(out_df["vat"].sum()) if not out_df.empty and out_df["vat"].notna().any() else 0

            in_cov = (in_df["vat"].notna().mean() * 100) if not in_df.empty else 0
            out_cov = (out_df["vat"].notna().mean() * 100) if not out_df.empty else 0

            rows.append({
                "Category": label,
                "IN": in_amt,
                "OUT": out_amt,
                "BALANCE": balance,
                "VAT IN": in_vat,
                "VAT OUT": out_vat,
                "VAT IN %": in_cov,
                "VAT OUT %": out_cov,
            })

        for label in expense_only:
            out_df = tx_all[tx_all["bucket"] == label]
            out_amt = abs(out_df["amount"].sum()) if not out_df.empty else 0
            out_vat = abs(out_df["vat"].sum()) if not out_df.empty and out_df["vat"].notna().any() else 0
            out_cov = (out_df["vat"].notna().mean() * 100) if not out_df.empty else 0

            rows.append({
                "Category": label,
                "IN": None,
                "OUT": out_amt,
                "BALANCE": -out_amt,
                "VAT IN": None,
                "VAT OUT": out_vat,
                "VAT IN %": None,
                "VAT OUT %": out_cov,
            })

        summary_df = pd.DataFrame(rows)

        # Profit from jobs (your Apps Script definition: jobs balance only)
        jobs_balance = summary_df[summary_df["Category"] == "JOBS"]["BALANCE"].iloc[0]
        total_balance = summary_df["BALANCE"].sum()

        # Headline metrics
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Profit from Jobs", f"£{jobs_balance:,.2f}")
        c2.metric("Total balance (period)", f"£{total_balance:,.2f}")
        c3.metric("Transactions", f"{len(tx_all):,}")
        review_count = (tx_all["needs_review"] == 1).sum() + (tx_all["bucket"].isna() | (tx_all["bucket"] == "")).sum()
        c4.metric("Needs review", f"{review_count}", delta_color="inverse")

        st.divider()

        # Summary table
        st.subheader("Category Summary")
        styled = summary_df.style.format({
            "IN": lambda x: f"£{x:,.2f}" if pd.notna(x) else "",
            "OUT": "£{:,.2f}",
            "BALANCE": "£{:,.2f}",
            "VAT IN": lambda x: f"£{x:,.2f}" if pd.notna(x) else "",
            "VAT OUT": "£{:,.2f}",
            "VAT IN %": lambda x: f"{x:.0f}%" if pd.notna(x) else "",
            "VAT OUT %": "{:.0f}%",
        })
        st.dataframe(styled, use_container_width=True, hide_index=True)

        st.divider()

        # Cash flow over time chart
        st.subheader("Cash Flow Over Time")
        flow = tx_all.copy()
        flow["month"] = flow["date"].dt.to_period("M").astype(str)
        flow["kind"] = flow["amount"].apply(lambda x: "Income" if x > 0 else "Expense")
        flow_grouped = flow.groupby(["month", "kind"])["amount"].sum().abs().reset_index()

        fig = px.bar(
            flow_grouped, x="month", y="amount", color="kind",
            barmode="group",
            color_discrete_map={"Income": "#22c55e", "Expense": "#ef4444"},
            labels={"amount": "£", "month": ""},
        )
        fig.update_layout(height=380, showlegend=True)
        st.plotly_chart(fig, use_container_width=True)


# ============================================================
# Tab: Vehicle P&L (priority #3)
# ============================================================

if not all_data.empty:
    with tab_vehicle:
        st.header("Vehicle P&L")
        st.caption("Income and costs allocated per vehicle registration plate. "
                    "Vehicle tags are auto-detected from descriptions; you can fix "
                    "missed ones in the Triage tab.")

        tagged = tx_all[tx_all["asset_tag"].notna() & (tx_all["asset_tag"] != "")]

        if tagged.empty:
            st.info(
                "No transactions tagged with a vehicle. Vehicles are detected from "
                "DVLA references, ENT MULT SER WX21VZN-style rents, and known plates. "
                f"Currently tracked: {', '.join(sorted(KNOWN_VEHICLES))}"
            )
        else:
            # Per-vehicle summary
            per_vehicle = tagged.groupby("asset_tag").agg(
                income=("amount", lambda s: s[s > 0].sum()),
                expenses=("amount", lambda s: abs(s[s < 0].sum())),
                txn_count=("amount", "count"),
            ).reset_index()
            per_vehicle["net"] = per_vehicle["income"] - per_vehicle["expenses"]
            per_vehicle = per_vehicle.sort_values("net", ascending=False)

            # KPIs
            total_income = per_vehicle["income"].sum()
            total_expenses = per_vehicle["expenses"].sum()
            c1, c2, c3 = st.columns(3)
            c1.metric("Vehicle income", f"£{total_income:,.2f}")
            c2.metric("Vehicle expenses", f"£{total_expenses:,.2f}")
            c3.metric("Net", f"£{total_income - total_expenses:,.2f}")

            st.divider()

            st.dataframe(
                per_vehicle.style.format({
                    "income": "£{:,.2f}",
                    "expenses": "£{:,.2f}",
                    "net": "£{:,.2f}",
                }),
                use_container_width=True, hide_index=True,
            )

            # Stacked bar: income vs expenses per vehicle
            fig = go.Figure()
            fig.add_trace(go.Bar(
                name="Income", x=per_vehicle["asset_tag"], y=per_vehicle["income"],
                marker_color="#22c55e",
            ))
            fig.add_trace(go.Bar(
                name="Expenses", x=per_vehicle["asset_tag"], y=-per_vehicle["expenses"],
                marker_color="#ef4444",
            ))
            fig.update_layout(barmode="relative", height=400, title="Per-vehicle Income vs Expenses")
            st.plotly_chart(fig, use_container_width=True)

            # Drill-down
            st.divider()
            chosen = st.selectbox("Drill into vehicle", per_vehicle["asset_tag"].tolist())
            v_tx = tagged[tagged["asset_tag"] == chosen].sort_values("date", ascending=False)
            st.dataframe(
                v_tx[["date", "description", "amount", "bucket", "vat"]].style.format({
                    "amount": "£{:,.2f}",
                    "vat": lambda x: f"£{x:,.2f}" if pd.notna(x) else "",
                }),
                use_container_width=True, hide_index=True,
            )


# ============================================================
# Tab: Triage (priority #4)
# ============================================================

if not all_data.empty:
    with tab_triage:
        st.header("Triage Queue")
        st.caption(
            "Transactions that the rules couldn't auto-classify. Set a bucket "
            "for each one. Saving applies to the database; rules don't get "
            "modified — to teach the system about a recurring pattern, also "
            "edit core/rules.py or enable Tier-2 in the sidebar."
        )

        review = store.needing_review()
        review = filter_df(review) if not review.empty else review

        if review.empty:
            st.success("✓ No transactions in the triage queue for this period.")
        else:
            c1, c2 = st.columns([3, 1])
            with c1:
                triage_search = st.text_input(
                    "🔎 Search description / reference / payer",
                    key="triage_search",
                )
            with c2:
                bulk_target = st.selectbox(
                    "Bulk move to →",
                    options=["(pick a bucket)"] + ALL_BUCKETS,
                    key="triage_bulk_target",
                )

            filtered = review
            if triage_search:
                sk = triage_search.upper()
                mask = (
                    filtered["description"].fillna("").str.upper().str.contains(sk, na=False)
                    | filtered["payer"].fillna("").str.upper().str.contains(sk, na=False)
                    | filtered["reference"].fillna("").str.upper().str.contains(sk, na=False)
                )
                filtered = filtered[mask]

            st.markdown(
                f"**{len(filtered)} of {len(review)} transaction(s) shown.** "
                "Use the Select column for bulk moves, or set Move to → on individual rows."
            )

            edit_df = filtered[[
                "txn_id", "date", "description", "amount", "raw_type",
                "asset_tag", "person_tag", "bucket"
            ]].copy()
            edit_df.insert(0, "select", False)
            edit_df["new_bucket"] = ""

            edited = st.data_editor(
                edit_df,
                use_container_width=True,
                column_config={
                    "select": st.column_config.CheckboxColumn("Select", width="small"),
                    "txn_id": st.column_config.TextColumn("ID", disabled=True, width="small"),
                    "date": st.column_config.DateColumn("Date", disabled=True),
                    "description": st.column_config.TextColumn("Description", disabled=True, width="large"),
                    "amount": st.column_config.NumberColumn("Amount", disabled=True, format="£%.2f"),
                    "raw_type": st.column_config.TextColumn("Type", disabled=True, width="small"),
                    "asset_tag": st.column_config.TextColumn("Vehicle", width="small"),
                    "person_tag": st.column_config.TextColumn("Person", width="small"),
                    "bucket": st.column_config.TextColumn("Current bucket", disabled=True),
                    "new_bucket": st.column_config.SelectboxColumn(
                        "Move to →", options=[""] + ALL_BUCKETS, required=False,
                    ),
                },
                hide_index=True,
                key="triage_editor",
            )

            selected_count = int(edited["select"].sum())

            b1, b2 = st.columns(2)
            with b1:
                bulk_clicked = st.button(
                    f"📦 Move {selected_count} selected to {bulk_target}"
                    if bulk_target != "(pick a bucket)" else
                    f"📦 Move {selected_count} selected (pick a bucket first)",
                    type="primary",
                    disabled=(selected_count == 0 or bulk_target == "(pick a bucket)"),
                )
            with b2:
                row_clicked = st.button("💾 Apply per-row moves")

            if bulk_clicked:
                moved = 0
                for _, row in edited.iterrows():
                    if row["select"]:
                        store.update_bucket(row["txn_id"], bulk_target, "Triage bulk move")
                        moved += 1
                st.success(f"Moved {moved} transaction(s) to {bulk_target}.")
                st.rerun()

            if row_clicked:
                applied = 0
                for _, row in edited.iterrows():
                    new_b = row["new_bucket"]
                    if new_b and new_b != row["bucket"]:
                        store.update_bucket(row["txn_id"], new_b, "Triage move")
                        applied += 1
                    # Also persist any tag edits
                    orig = edit_df[edit_df["txn_id"] == row["txn_id"]]
                    if not orig.empty:
                        if row["asset_tag"] != orig["asset_tag"].iloc[0]:
                            store.update_tags(row["txn_id"], asset_tag=row["asset_tag"])
                        if row["person_tag"] != orig["person_tag"].iloc[0]:
                            store.update_tags(row["txn_id"], person_tag=row["person_tag"])
                if applied:
                    st.success(f"Moved {applied} transaction(s).")
                    st.rerun()
                else:
                    st.info("No changes to apply.")


# ============================================================
# Tab: VAT (priority #5)
# ============================================================

if not all_data.empty:
    with tab_vat:
        st.header("VAT Return")
        st.caption(f"Period: {date_range[0]} → {date_range[1]}")

        vat = vat_return(store, date_range[0], date_range[1])

        c1, c2, c3 = st.columns(3)
        c1.metric("Box 1: Output VAT (sales)", f"£{vat['box1_output_vat']:,.2f}")
        c2.metric("Box 4: Input VAT (purchases)", f"£{vat['box4_input_vat']:,.2f}")
        c3.metric("Box 5: Net VAT due", f"£{vat['box5_net_vat']:,.2f}",
                    help="Positive = you owe HMRC; Negative = HMRC owes you")

        c1, c2 = st.columns(2)
        c1.metric("Box 6: Sales ex-VAT", f"£{vat['box6_sales_ex_vat']:,.2f}")
        c2.metric("Box 7: Purchases ex-VAT", f"£{vat['box7_purchases_ex_vat']:,.2f}")

        st.divider()

        st.subheader("VAT Coverage")
        c1, c2 = st.columns(2)
        c1.metric("Income rows with VAT set", f"{vat['coverage_in_pct']:.1f}%")
        c2.metric("Expense rows with VAT set", f"{vat['coverage_out_pct']:.1f}%")
        st.caption("Coverage = % of in-period rows with VAT explicitly set. "
                    "Blank VAT rows are excluded from the boxes above. "
                    "Bring coverage to 100% for an accurate VAT return.")

        st.divider()

        # Quick VAT calculator
        st.subheader("Bulk VAT Calculator")
        st.caption("Apply a VAT rate to a selection of transactions.")

        vat_bucket = st.selectbox("Bucket", ALL_BUCKETS)
        vat_rate_pct = st.number_input("Rate (%)", min_value=0.0, max_value=100.0, value=20.0, step=0.5)
        vat_is_gross = st.radio("Amounts are:", ["Gross (VAT included)", "Net (VAT excluded)"], horizontal=True)
        only_unset = st.checkbox("Only update rows where VAT is currently unset", value=True)

        bucket_tx = store.by_bucket(vat_bucket, date_range[0], date_range[1])
        if not bucket_tx.empty:
            st.markdown(f"**{len(bucket_tx)} transaction(s)** in {vat_bucket} for period")

            if st.button(f"Apply {vat_rate_pct}% VAT", type="primary"):
                rate = vat_rate_pct / 100
                is_gross = vat_is_gross.startswith("Gross")
                count = 0
                for _, row in bucket_tx.iterrows():
                    if only_unset and pd.notna(row["vat"]):
                        continue
                    vat_amt = calc_vat_amount(row["amount"], rate, is_gross)
                    store.update_vat(row["txn_id"], vat_amt, rate)
                    count += 1
                st.success(f"Updated VAT on {count} row(s)")
                st.rerun()
        else:
            st.info(f"No transactions in {vat_bucket} for this period.")


# ============================================================
# Tab: Transactions
# ============================================================

if not all_data.empty:
    with tab_tx:
        st.header("Transactions")

        c1, c2, c3 = st.columns([3, 1, 1])
        with c1:
            search = st.text_input("🔎 Search description / reference / payer")
        with c2:
            bucket_filter = st.selectbox("Bucket", ["(all)"] + ALL_BUCKETS)
        with c3:
            bulk_target_tx = st.selectbox(
                "Bulk move to →",
                options=["(pick a bucket)"] + ALL_BUCKETS,
                key="tx_bulk_target",
            )

        df = tx_all.copy()
        if search:
            sk = search.upper()
            mask = (
                df["description"].str.upper().str.contains(sk, na=False)
                | df["payer"].fillna("").str.upper().str.contains(sk, na=False)
                | df["reference"].fillna("").str.upper().str.contains(sk, na=False)
            )
            df = df[mask]
        if bucket_filter != "(all)":
            df = df[df["bucket"] == bucket_filter]

        df = df.sort_values("date", ascending=False)
        st.markdown(f"**{len(df)} matching transaction(s)**")

        edit_df = df[["txn_id", "date", "source_account", "description", "amount",
                       "bucket", "asset_tag", "person_tag", "vat", "needs_review"]].copy()
        edit_df.insert(0, "select", False)

        edited_tx = st.data_editor(
            edit_df,
            use_container_width=True,
            hide_index=True,
            height=600,
            column_config={
                "select": st.column_config.CheckboxColumn("Select", width="small"),
                "txn_id": st.column_config.TextColumn("ID", disabled=True, width="small"),
                "date": st.column_config.DateColumn("Date", disabled=True),
                "source_account": st.column_config.TextColumn("Account", disabled=True, width="small"),
                "description": st.column_config.TextColumn("Description", disabled=True, width="large"),
                "amount": st.column_config.NumberColumn("Amount", disabled=True, format="£%.2f"),
                "bucket": st.column_config.TextColumn("Bucket", disabled=True),
                "asset_tag": st.column_config.TextColumn("Vehicle", disabled=True, width="small"),
                "person_tag": st.column_config.TextColumn("Person", disabled=True, width="small"),
                "vat": st.column_config.NumberColumn("VAT", disabled=True, format="£%.2f"),
                "needs_review": st.column_config.CheckboxColumn("Review?", disabled=True, width="small"),
            },
            key="tx_editor",
        )

        selected_count_tx = int(edited_tx["select"].sum())

        b1, b2 = st.columns([2, 1])
        with b1:
            move_clicked_tx = st.button(
                f"📦 Move {selected_count_tx} selected to {bulk_target_tx}"
                if bulk_target_tx != "(pick a bucket)" else
                f"📦 Move {selected_count_tx} selected (pick a bucket first)",
                type="primary",
                disabled=(selected_count_tx == 0 or bulk_target_tx == "(pick a bucket)"),
            )
        with b2:
            st.download_button(
                "📥 Export filtered as CSV",
                df.to_csv(index=False),
                file_name=f"transactions_{date_range[0]}_{date_range[1]}.csv",
                mime="text/csv",
            )

        if move_clicked_tx:
            moved = 0
            for _, row in edited_tx.iterrows():
                if row["select"]:
                    store.update_bucket(row["txn_id"], bulk_target_tx, "Transactions bulk move")
                    moved += 1
            st.success(f"Moved {moved} transaction(s) to {bulk_target_tx}.")
            st.rerun()


# ============================================================
# Tab: Import (priority #1)
# ============================================================

with (tab_import if not all_data.empty else st.container()):
    st.header("Import Statement")
    st.markdown(
        "Drop a Wise/Revolut, Monzo, or any UK bank CSV. PDFs are also accepted but "
        "less reliable — prefer CSV when possible."
    )

    uploaded = st.file_uploader("Choose file", type=["csv", "pdf"], key="upload")

    if uploaded:
        # Save to temp
        suffix = Path(uploaded.name).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded.getvalue())
            tmp_path = tmp.name

        # Detect format
        try:
            fmt = detect_format(tmp_path)
            st.info(f"Detected format: **{fmt}**")
        except Exception as e:
            st.error(f"Detection failed: {e}")
            fmt = "unknown"

        # Account selection
        account_options = ["+ New account"]
        if not accounts.empty:
            account_options = accounts["name"].tolist() + account_options

        account_choice = st.selectbox("Account", account_options)

        if account_choice == "+ New account":
            new_name = st.text_input("Account name (no spaces, e.g. welux_wise_gbp)")
            new_bank = st.text_input("Bank name (optional)")
            account_name = new_name
        else:
            account_name = account_choice
            new_bank = ""

        force_format = st.selectbox(
            "Force format (override detection)",
            ["(use detected)", "wise", "monzo", "uk_generic", "pdf"],
        )
        force_arg = "" if force_format == "(use detected)" else force_format

        if st.button("Import →", type="primary"):
            if not account_name:
                st.error("Please specify an account name.")
            else:
                if account_choice == "+ New account":
                    store.upsert_account(account_name, bank=new_bank)
                try:
                    with st.spinner(f"Parsing {uploaded.name}..."):
                        result = ingest_file(tmp_path, account_name, force_format=force_arg)
                    st.success(
                        f"Imported. Format: {result['format']}, "
                        f"parsed: {result['parsed']}, "
                        f"new: {result['inserted']}, "
                        f"duplicates: {result['duplicates']}, "
                        f"need review: {result['needs_review']}"
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Import failed: {e}")
                finally:
                    Path(tmp_path).unlink(missing_ok=True)


# ============================================================
# Tab: Move Log
# ============================================================

if not all_data.empty:
    with tab_log:
        st.header("Move Log")
        st.caption("Audit trail — every routing decision and manual edit.")

        log_df = store.move_log(limit=500)
        if log_df.empty:
            st.info("No log entries yet.")
        else:
            st.dataframe(
                log_df.rename(columns={
                    "timestamp": "When",
                    "txn_id": "TxnID",
                    "from_bucket": "From",
                    "to_bucket": "To",
                    "note": "Note",
                }),
                use_container_width=True, hide_index=True, height=600,
            )
