# Deploy to Fly.io with a custom domain + Cloudflare Access login

End result: `https://finance.weluxchauffeurs.co.uk` runs the dashboard,
Cloudflare Access fronts it so only your email gets in, and your main
`weluxchauffeurs.co.uk` website on Namecheap is untouched.

You'll need accounts on:
- **Fly.io** — flyctl runs the app. Free tier covers single-user use.
- **Cloudflare** — DNS + Access (login). Free.

You already have Namecheap (for the domain) and Supabase (for the DB).

The full set-up takes ~45 min the first time; subsequent deploys are
`git push` + Fly auto-builds.

---

## Part 1 — Get Fly.io running on `*.fly.dev` (15 min)

### 1.1. Install `flyctl` on your laptop

Mac: `brew install flyctl`
Windows: `iwr https://fly.io/install.ps1 -useb | iex`
Linux: `curl -L https://fly.io/install.sh | sh`

### 1.2. Sign in

```
fly auth signup     # if you don't have an account
fly auth login      # if you do
```

### 1.3. Launch the app (run this from the repo root)

```
cd path/to/finance_wlx
fly launch --no-deploy
```

It'll ask:
- **App name** — try `welux-finance`. If taken, pick another (e.g. `welux-finance-zr`). Update `app =` in `fly.toml` afterwards if you pick something else.
- **Region** — pick **`lhr` (London)**.
- **Postgres / Redis** — **No** to both (you're using Supabase already).
- **Deploy now?** — **No** (we still need to add secrets).

Fly will write a few files. Keep them.

### 1.4. Add the secrets

These are the same values you set in Streamlit Cloud:

```
fly secrets set \
  APP_PASSWORD='pick-a-strong-password-here' \
  DATABASE_URL='postgresql://postgres.abcd:YourPassword@aws-0-eu-west-2.pooler.supabase.com:6543/postgres'
```

(Use the same DATABASE_URL you have in Streamlit Cloud secrets.)

### 1.5. Deploy

```
fly deploy
```

First deploy takes 3-5 min (builds the Docker image, uploads, boots
the VM). When it's done you get a URL like `welux-finance.fly.dev`.

### 1.6. Verify it works

Open `https://welux-finance.fly.dev`. You should see the password
gate. Type `APP_PASSWORD`. Dashboard should load, sidebar badge should
say **🟢 Postgres**.

If anything's broken: `fly logs` shows live application logs.

---

## Part 2 — Move DNS to Cloudflare (15 min)

Cloudflare Access only works on domains where Cloudflare is the
authoritative DNS provider. So we shift `weluxchauffeurs.co.uk`'s
nameservers from Namecheap → Cloudflare. Your existing website and
email will keep working — Cloudflare imports the existing records.

### 2.1. Sign up at https://www.cloudflare.com

### 2.2. Add your site

Dashboard → **Add a site** → enter `weluxchauffeurs.co.uk`. Pick the
**Free** plan.

### 2.3. Review imported DNS records

Cloudflare scans your current DNS and shows what it found. **Look
carefully** — make sure these are present and unchanged:

- The `A` or `CNAME` for `weluxchauffeurs.co.uk` (your main site)
- The `www` record
- All `MX` records (email — critical, don't break this)
- Any `TXT` records (SPF, DKIM, domain verification, etc.)

If anything's missing, click **Add record** before continuing. Set the
proxy status (orange/grey cloud) to **DNS only (grey)** on `MX` and any
email-related records; orange-cloud them on your website if you want
Cloudflare in front of it (optional).

### 2.4. Change nameservers at Namecheap

Cloudflare shows you two nameservers like `lara.ns.cloudflare.com` and
`mike.ns.cloudflare.com`. Copy these.

In Namecheap → **Domain List** → **weluxchauffeurs.co.uk** → **Manage**
→ **Nameservers** → switch to **Custom DNS** → paste the Cloudflare
nameservers → **Save**.

Propagation: usually 10-30 min, sometimes up to a few hours.
Cloudflare will email you once it's active.

---

## Part 3 — Point `finance.weluxchauffeurs.co.uk` at Fly (5 min)

### 3.1. Add the custom domain in Fly

```
fly certs add finance.weluxchauffeurs.co.uk
```

Fly will print two DNS targets (an A record IP and an AAAA record IPv6).
Copy them.

### 3.2. Add a DNS record in Cloudflare

Cloudflare dashboard → **DNS** → **Add record**:
- **Type**: `A`
- **Name**: `finance`
- **IPv4 address**: the A target Fly printed
- **Proxy status**: **DNS only (grey cloud)** for now — we'll flip to
  orange after the cert validates

Add another record for the AAAA (IPv6) target Fly gave you, same proxy
setting.

### 3.3. Wait for Fly's cert

```
fly certs show finance.weluxchauffeurs.co.uk
```

Run this every minute or so until you see `Status: Ready` and the
cert is issued. Usually 1-2 min.

### 3.4. Switch Cloudflare proxy to orange-cloud

Back in Cloudflare DNS, click the grey cloud on the `finance` records
to flip them to **orange (Proxied)**. This routes traffic through
Cloudflare so Access can authenticate it.

### 3.5. Set SSL/TLS mode to "Full (strict)"

Cloudflare dashboard → **SSL/TLS** → **Overview** → set to
**Full (strict)**. This makes Cloudflare validate Fly's cert end-to-end.

### 3.6. Verify

Open `https://finance.weluxchauffeurs.co.uk`. You should see the same
password gate as before, served over HTTPS, on your custom domain.

---

## Part 4 — Cloudflare Access (10 min)

Replaces the password gate with a "Sign in with email" flow that only
lets in addresses you allowlist.

### 4.1. Enable Zero Trust

Cloudflare dashboard → **Zero Trust** (left sidebar) → first-time setup
asks for a team name (e.g. `welux`) — pick anything, it just becomes
part of the Access URL. Choose the **Free** plan.

### 4.2. Create an Access application

Zero Trust dashboard → **Access** → **Applications** → **Add an
application** → **Self-hosted**.

- **Application name**: `Welux Finance`
- **Session duration**: `24 hours` (or longer; up to you)
- **Application domain**:
  - Type: `Public hostname`
  - Subdomain: `finance`
  - Domain: `weluxchauffeurs.co.uk`
- **Identity providers**: tick **One-time PIN** (email magic-link).
  Optionally add Google if you'd prefer SSO.

Click **Next**.

### 4.3. Add a policy

- **Policy name**: `Welux owner`
- **Action**: `Allow`
- **Include**: **Emails** → add your email address
- (Optional: add a second `Include` group for your accountant or
  Imran if you want them to log in too.)

Click **Next** → **Add application**.

### 4.4. Test it

Open `https://finance.weluxchauffeurs.co.uk` in an incognito window.
You should now see a Cloudflare login screen asking for an email.
Type yours, get a 6-digit code via email, paste it in. You land on the
Welux Finance password gate.

Either:
- **Keep both layers** — Cloudflare lets you through to your domain,
  then the in-app `APP_PASSWORD` is one more wall. Defence in depth.
- **Remove the in-app gate** — `fly secrets unset APP_PASSWORD` and
  redeploy. Cloudflare Access is sufficient on its own.

---

## Day-to-day deploys after this

Edit code → commit → push to GitHub → run `fly deploy` from the repo
root. Or set up the GitHub Action Fly suggests during `fly launch` for
auto-deploy on push.

To check logs: `fly logs`
To open a shell on the running VM: `fly ssh console`
To scale to a bigger VM if needed: `fly scale memory 1024`

## Costs

- Fly.io: ~£0/mo at single-user volume with `auto_stop_machines`. If
  the app is hit constantly: ~£3-5/mo for the 512MB VM running 24/7.
- Cloudflare DNS + Access: £0/mo (free tier covers ≤50 users).
- Namecheap domain: whatever you already pay.
- Supabase Postgres: £0/mo (free tier, 500MB).

## Cutover from Streamlit Cloud

Once `finance.weluxchauffeurs.co.uk` is verified working:

1. In Streamlit Cloud → your app → **Manage app** → **Delete app**.
2. The Supabase DB stays — it was always separate.
3. Update any bookmarks to the new URL.

You can also leave Streamlit Cloud running in parallel — both
deployments hit the same Supabase DB, so they stay in sync. Just don't
forget which URL is the "real" one.
