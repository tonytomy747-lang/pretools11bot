# Telegram USDT Shop Bot

A complete digital-products shop that lives inside Telegram. Buyers browse a
paginated catalogue with images, pick a product, pay USDT on the network they
prefer, and submit their transaction hash. You get a notification with an
Approve / Reject button and the buyer's Telegram ID.

Everything is managed from inside Telegram — you never touch the code again
after deploying.

---

## What's included

**For buyers**
- Categorised, paginated catalogue with product photos
- Product pages with description, price, stock
- Checkout on TRC20, BEP20, ERC20, Polygon, Solana or TON (whichever you enable)
- Copy-tappable wallet address + a clear "wrong network = lost funds" warning
- "I have paid" → submits TXID → order goes under review
- "My orders" list with live status
- **Profile screen**: balance, orders placed, total spent, open ticket status
- **Wallet top-up**: add USDT balance (same TXID → admin-approve flow as orders),
  then pay for any product instantly from balance — no waiting, no TXID
- **Support tickets**: opens a persistent two-way thread inside the same chat;
  admin replies land back on the buyer instantly, no external group needed

**For you (admin)**
- Add / edit / hide / delete products, all from chat
- Product images: send a photo, done (Telegram stores it — costs you nothing)
- Categories: create, rename, delete
- Wallet addresses per network, editable any time without redeploying
- Instant notification on every submitted payment, with Approve / Reject
- Duplicate-TXID protection (the same hash can never be submitted twice)
- Optional auto-delivery: attach a key/link to a product and the buyer receives
  it the second you tap Approve
- Stock counters (auto-decrement), broadcast to all users, stats, editable
  welcome text and support handle
- **Topups queue**: approve/reject wallet top-ups the same way as orders;
  approving credits the buyer's balance and notifies them
- **Tickets inbox**: see open/closed tickets, reply from the admin panel —
  your reply is delivered straight to the buyer's chat
- **Sample catalogue seeder** (`python seed.py`) — one command populates ~90
  ready-made digital products across 6 categories to get the shop started

**Cost: $0.** Render free tier + Supabase free Postgres + Telegram's own image
hosting. No credit card required anywhere.

---

## Before you start (3 minutes)

You need three things. Collect them in a note before touching any dashboard.

### 1. Bot token

Open Telegram → talk to **@BotFather** → `/newbot` → pick a name and a username
ending in `bot`. He replies with a token like
`8123456789:AAF3xK...`. **Keep it secret** — anyone with it controls your bot.

While you're there (optional but nice):
- `/setdescription` — text shown before someone starts the bot
- `/setuserpic` — your shop logo
- `/setcommands` — paste this:
  ```
  start - Open the shop
  shop - Open the shop
  id - Show my Telegram ID
  cancel - Cancel current action
  ```

### 2. Your Telegram numeric ID

Message **@userinfobot** and it replies with your ID (e.g. `7712345678`).
That number goes in `ADMIN_IDS`. Without it, nobody can open the admin panel.

### 3. Your USDT receiving addresses

At minimum a TRC20 (Tron) and a BEP20 (BNB Chain) address from your exchange or
wallet. You'll paste these into the bot later, not into the code. **Triple-check
them** — this is where your money lands.

---

## Deployment — Path A: Render + Supabase (recommended, ~15 min)

Render's free instances have no permanent disk, so the shop database lives on
Supabase's free Postgres. Both are free forever within these limits.

### Step 1 — Free Postgres on Supabase (4 min)

1. Go to **supabase.com** → sign up with GitHub → **New project**.
2. Name it anything, choose a region near you, and set a database password.
   **Save that password.**
3. Wait ~2 minutes for provisioning.
4. Click **Connect** (top of the dashboard) → find the connection string
   labelled **Session pooler** (or **Transaction pooler**).

   > ⚠️ Do **not** use the one labelled *Direct connection*. It resolves over
   > IPv6 only and Render can't reach it. The pooler strings are IPv4 and work.

5. Copy it. It looks like:
   ```
   postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:5432/postgres
   ```
6. Replace `[YOUR-PASSWORD]` with the password from step 2. That final string is
   your `DATABASE_URL`.

   If your password contains `@ : / ? # &`, URL-encode it (`@` → `%40`) or just
   reset it to something alphanumeric — far less painful.

You don't need to create any tables. The bot builds its own schema on first run.

### Step 2 — Put the code on GitHub (3 min)

Create a **new repository** on github.com (private is fine), then upload the
files. Easiest without a terminal: on the empty repo page click
**uploading an existing file** and drag in every file from this folder.

With git:
```bash
git init
git add .
git commit -m "telegram usdt shop"
git branch -M main
git remote add origin https://github.com/YOURNAME/YOURREPO.git
git push -u origin main
```

`.gitignore` already excludes `.env` and `*.db`, so no secrets get pushed.

### Step 3 — Deploy on Render (5 min)

1. **render.com** → sign up with GitHub → **New +** → **Web Service**.
2. Connect your repository.
3. Settings:
   - **Language / Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
   - **Instance Type**: **Free**
4. Scroll to **Environment Variables** and add:

   | Key | Value |
   |---|---|
   | `BOT_TOKEN` | the token from BotFather |
   | `ADMIN_IDS` | your numeric ID (comma-separated for several admins) |
   | `DATABASE_URL` | the Supabase pooler string from Step 1 |
   | `SHOP_NAME` | e.g. `Nova Digital` |
   | `CURRENCY` | `USDT` |

5. **Create Web Service**. First build takes 2–3 minutes.
6. When the log prints `running as @yourbot`, your bot messages you
   **"🤖 Shop bot is online."** That's the green light.

### Step 4 — Stop it falling asleep (2 min)

Render's free tier suspends a service after 15 minutes with no HTTP traffic.
The bot ships with a tiny health endpoint so an external pinger can hold it open.

1. Copy your service URL from Render (e.g. `https://yourshop.onrender.com`).
2. Go to **cron-job.org** (free, no card) → **Create cronjob**.
3. URL = your Render URL, schedule = **every 10 minutes**. Save and enable.

UptimeRobot works equally well (5-minute HTTP monitor). Free instance hours are
750/month, which covers one service running 24/7 with room to spare.

> This also keeps Supabase happy — free Postgres projects pause after 7 days of
> zero activity, and the bot pings its database periodically on its own.

### Step 5 — Set the shop up inside Telegram (3 min)

Open your bot and send `/start`, then tap **⚙️ Admin panel**.

1. **💠 Wallets** → tap `TRC20` → paste your Tron address → repeat for `BEP20`
   and any other network you want. Only networks with an address appear at
   checkout, so leaving the rest blank is how you disable them.
2. **📂 Categories** → *Add category* → e.g. "Courses", "Accounts", "Software".
   (Optional — with zero categories the shop just shows one flat product list.)
3. **📦 Products** → *Add product* → the bot walks you through six steps:
   title → description → price → image → auto-delivery text → category.
   Send `/skip` on any optional step.
4. **📝 Texts** → customise the welcome message and your support handle.

### Seeding sample products

The bot seeds its ~90-product sample catalogue **automatically on every
boot** — including every Render deploy — so a fresh deployment already has a
full shop without any manual step. It creates 6 categories and ~90 products
(AI tools, dev/productivity, design/creative, streaming, VPN,
business/LinkedIn) with descriptions and prices already set. It's idempotent:
matched by product name, so it only ever adds what's missing (safe across
restarts and redeploys, won't duplicate or touch products you've already
edited). No images are attached (the source had none to reuse); add real
photos per-product via **📦 Products → (pick one) → 🖼 Image**.

Set `SEED_ON_BOOT=false` in your environment variables to turn this off (e.g.
once your own catalogue has replaced the sample one). You can also still run
it by hand any time: `python seed.py`.

### Step 6 — Test it end to end (2 min)

From a second Telegram account (or your own — you can buy from your own shop):
`/start` → **Browse shop** → open a product → **Buy** → pick a network → you see
the address and order number → **I have paid** → paste any 10+ character string
as a fake TXID.

Your admin account gets the review notification. Tap **Approve** and confirm the
buyer receives the confirmation. Then reject a second test order to see that
path too. Delete the test orders? No need — nothing is charged, and stats only
count approved ones.

**Done.** Share your bot link: `https://t.me/yourbotusername`

---

## Deployment — Path B: your own machine, VPS, or Android

No cloud accounts, no Postgres, no keepalive. Data goes in a local `shop.db`
file. This is the right choice if you already have a VPS or an always-on PC.

```bash
git clone https://github.com/YOURNAME/YOURREPO.git
cd YOURREPO
pip install -r requirements.txt
cp .env.example .env
nano .env                 # fill in BOT_TOKEN and ADMIN_IDS, leave DATABASE_URL empty
python bot.py
```

Keep it running after you close the terminal:
```bash
# Linux VPS
nohup python bot.py > bot.log 2>&1 &

# or properly, with auto-restart on reboot:
sudo tee /etc/systemd/system/tgshop.service > /dev/null <<'EOF'
[Unit]
Description=Telegram USDT Shop
After=network.target

[Service]
WorkingDirectory=/root/YOURREPO
ExecStart=/usr/bin/python3 bot.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now tgshop
```

**Android via Termux** works too: `pkg install python git`, then the same steps.
Back up `shop.db` occasionally — it *is* your shop.

**Docker**, if you prefer:
```bash
docker build -t tgshop .
docker run -d --restart always --env-file .env -v $PWD/data:/app tgshop
```

---

## Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `BOT_TOKEN` | ✅ | — | From @BotFather |
| `ADMIN_IDS` | ✅ | — | `123456,789012` — numeric IDs, not usernames |
| `DATABASE_URL` | on cloud hosts | empty | Postgres URL. Empty = local SQLite |
| `SHOP_NAME` | | `Digital Shop` | Shown in the welcome + shop header |
| `CURRENCY` | | `USDT` | Label only; change if you take something else |
| `SQLITE_PATH` | | `shop.db` | Only used when `DATABASE_URL` is empty |
| `PORT` | | `8080` | Health endpoint port; hosts set this for you |
| `SEED_ON_BOOT` | | `true` | Auto-runs `seed.py` on every startup. Set `false` to disable once you have your own catalogue |

Changing wallets, texts, products or categories does **not** require touching
these — that's all done in the admin panel.

---

## Running the shop day to day

### When a payment arrives

You get a message like:

```
🔔 New payment to review
🧾 Order #00042
Product: Python Masterclass
Amount: 29.99 USDT
Network: TRC20
Buyer: @someone
Buyer ID: 7712345678
TXID: 3f1a9c...
```

**Verify before approving.** Paste the TXID into the right explorer:

| Network | Explorer |
|---|---|
| TRC20 | tronscan.org |
| BEP20 | bscscan.com |
| ERC20 | etherscan.io |
| Polygon | polygonscan.com |
| Solana | solscan.io |
| TON | tonviewer.com |

Check four things: the transaction is **confirmed**, the **destination** is your
address, the **amount** matches the order, and the **token** is actually USDT
(not a lookalike token with the same name). The bot already guarantees the hash
hasn't been submitted before.

Then tap **✅ Approve** → the buyer is notified instantly and stock decrements.
Tap **❌ Reject** → you're asked for a reason, which is forwarded to the buyer.

### Auto-delivery vs manual delivery

If a product has **auto-delivery text** (a licence key, a download link, an
account credential), the buyer receives it automatically the moment you approve.
Leave it empty and the buyer is told an admin will deliver shortly — then you
message them yourself using the Buyer ID shown on the order.

For one-of-a-kind items (unique keys), leave auto-delivery empty and set
**Stock** so the product hides itself when it sells out.

### Finding an old order

Admin panel → **🔎 Review payments** → tabs for Pending / Paid / All.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Bot silent, Render log shows `Conflict: terminated by other getUpdates` | The same token is running twice. Stop your local copy, or the old Render deploy. One token = one running instance. |
| `BOT_TOKEN is not set` | Env var missing or misspelled in the Render dashboard. Re-check, then **Manual Deploy → Clear build cache & deploy**. |
| `ADMIN_IDS is empty` warning, no admin panel button | You put a `@username` instead of the numeric ID. Send `/id` to your bot to get the right number. |
| `could not translate host name` / `connection refused` | Wrong `DATABASE_URL`. Confirm you used the **Session pooler** string and replaced `[YOUR-PASSWORD]`. |
| `password authentication failed` | Special characters in the password. Reset it in Supabase → Settings → Database to something alphanumeric. |
| Shop works, then stops after ~15 min | The keepalive pinger isn't running. Re-check Step 4. |
| Everything vanished after a redeploy | You deployed without `DATABASE_URL`, so it used SQLite on an ephemeral disk. Add the Postgres URL. |
| "Project paused" on Supabase | 7 days with no activity. Restore it from the dashboard; the keepalive prevents repeats. |
| Product image won't send | Send it as a **photo**, not as a file/document. Telegram compresses photos and gives the bot a reusable `file_id`. |
| Buyer says "already submitted" | That TXID is already in the database. Either they double-submitted or they're reusing someone's hash — check the explorer. |

Render logs: **Dashboard → your service → Logs**. Nearly every failure explains
itself there.

---

## Notes on security and money

- Never commit `.env` or paste your token into a chat. If it leaks, run
  `/revoke` with @BotFather and update `BOT_TOKEN`.
- **The bot does not verify blockchain transactions.** That's deliberate — it's
  what keeps this free and dependency-light. You are the verifier. Never approve
  an order without opening the explorer.
- Use a receiving address you control directly. Some exchange deposit addresses
  are shared or rotate.
- Add a second admin ID so you're not the single point of failure.
- Digital-goods sales are generally final, but say so in your welcome text —
  it prevents most disputes.
- Free Supabase gives 500 MB, which is tens of thousands of orders. Only the
  image `file_id` strings are stored, never the images themselves.

---

## Customising

- **More networks**: edit the `NETWORKS` list at the top of `bot.py`.
- **Products per page**: `PAGE_SIZE` in `bot.py`.
- **Order statuses / labels**: `STATUS_LABEL` dict.
- **Everything else** — texts, prices, wallets, catalogue — is in the admin
  panel, no redeploy needed.

Files: `bot.py` (screens + handlers), `db.py` (storage, SQLite and Postgres),
`seed.py` (one-time sample catalogue seeder), `render.yaml` / `Dockerfile` /
`Procfile` (deployment targets).
