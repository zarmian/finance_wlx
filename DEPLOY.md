# Deploy to Streamlit Cloud — step by step

This guides you from "zip on your laptop" to "URL you can bookmark." Total time: ~25 minutes.

You'll need free accounts on:
- **GitHub** (hosts the code) — github.com
- **Supabase** (hosts the database) — supabase.com
- **Streamlit Cloud** (runs the app) — streamlit.io

## Part 1 — Put the code on GitHub (5 min)

1. Go to **github.com** and sign up if you don't have an account.
2. Click the **+** in the top-right → **New repository**.
3. Name it `welux-finance`. Set it to **Private** (very important — your bank data parsing logic and merchant rules go here).
4. Don't tick "Add a README" or anything else. Click **Create repository**.
5. GitHub now shows a page with commands. Ignore those — we'll use a simpler way.
6. On the same page, click **uploading an existing file** (it's a link in the middle of the page).
7. Unzip the `welux_finance.zip` I gave you somewhere on your computer.
8. Drag **all the contents of the `welux_finance` folder** (not the folder itself — the files inside it: `app.py`, `core/`, `adapters/`, `requirements.txt`, etc.) onto the GitHub upload area.
9. Wait for the upload to finish, then click **Commit changes** at the bottom.

You should now see all the files listed in your repo.

## Part 2 — Set up the database on Supabase (8 min)

1. Go to **supabase.com** → **Start your project** → sign up with GitHub.
2. Click **New project**.
3. Fill in:
   - **Name**: `welux-finance`
   - **Database password**: Click "Generate a password" and **copy it somewhere safe**. You can't see it again later.
   - **Region**: pick the one closest to you (London if you're in the UK).
   - **Pricing plan**: Free.
4. Click **Create new project**. It takes about 2 minutes to provision.
5. Once provisioned, in the left sidebar click the **gear icon (Settings)** → **Database**.
6. Scroll to **Connection string** → tab labelled **URI**.
7. You'll see something like `postgresql://postgres.abcd:[YOUR-PASSWORD]@aws-0-eu-west-2.pooler.supabase.com:6543/postgres`.
8. Replace `[YOUR-PASSWORD]` with the password you saved earlier.
9. **Copy the full string** — you'll paste it in the next part.

> ⚠️ Use the **"Connection pooling"** string (port 6543), not the direct connection (port 5432). Streamlit Cloud restarts often and pooled connections handle that better.

## Part 3 — Deploy on Streamlit Cloud (5 min)

1. Go to **streamlit.io/cloud** → **Sign in with GitHub**.
2. Authorize Streamlit to see your repos.
3. Click **New app** (top-right) → **Deploy a public app from GitHub**.
4. Fill in:
   - **Repository**: `your-username/welux-finance`
   - **Branch**: `main`
   - **Main file path**: `app.py`
   - **App URL**: pick something like `welux-finance` — that becomes `welux-finance.streamlit.app`
5. Before clicking Deploy, click **Advanced settings** at the bottom.
6. In the **Secrets** box, paste this (replacing the placeholder values):

   ```toml
   APP_PASSWORD = "pick-a-strong-password-here"
   DATABASE_URL = "postgresql://postgres.abcd:YourPassword@aws-0-eu-west-2.pooler.supabase.com:6543/postgres"
   ```

   - `APP_PASSWORD` is what *you* will type to log in. Make it long and not your bank password.
   - `DATABASE_URL` is the Supabase string from Part 2.
7. Click **Save** on the secrets panel, then **Deploy**.

The first deploy takes about 3-5 minutes (Streamlit installs all the Python packages). You'll see logs streaming.

## Part 4 — First login + import (2 min)

1. When deploy finishes, your app is at `welux-finance.streamlit.app` (or whatever you picked).
2. Bookmark it.
3. Open it — you'll see the password gate.
4. Type the `APP_PASSWORD` you set above. Click Sign in.
5. Go to the **Import** tab in the sidebar.
6. Drop in your Wise CSV. Done.

## Updating the code later

If you (or I) need to change something in the code:

1. Edit the file on GitHub directly (small changes), or
2. Re-upload modified files via GitHub's "Add file → Upload files" button.

Streamlit Cloud watches the repo and auto-redeploys within ~1 minute of any push. No action needed on your end.

## What if I want to access from my phone?

The same URL works on mobile. Streamlit's mobile layout is okay but not amazing. The sidebar collapses to a hamburger menu in the top-left.

## Costs

- GitHub: free for private repos
- Supabase: free up to 500 MB database (you'll never hit this)
- Streamlit Cloud: free for one private app per account

Total: £0/month.

## If something breaks

Streamlit Cloud has a **Manage app** button that shows live logs. Most errors there will be obvious (missing dependency, bad SQL). Drop me the error message if it isn't.

Common fixes:

- **"could not connect to server"** — your DATABASE_URL is wrong. Double-check you replaced `[YOUR-PASSWORD]` with the actual password (no brackets).
- **App restarts and data is gone** — that means it fell back to local SQLite (which gets wiped). The DATABASE_URL secret isn't being read. Check the secrets panel in the Streamlit dashboard.
- **"App is sleeping"** — Streamlit free tier puts apps to sleep after a week of inactivity. Just click "wake" and it's back in 30 seconds.

## Migrating your existing Sheet data

Once you're comfortable with the new system on a single Wise CSV:

1. In Wise, export your full statement history (one big CSV covering 2025-04 to today).
2. Import it via the Import tab.
3. Spot-check the buckets vs your existing Sheet's Dashboard for one month.

The categorization rules are line-for-line ports of your Apps Script, so totals should match closely. Where they differ, those are the rows you manually moved in Sheets — they'll show up in the Triage tab.
