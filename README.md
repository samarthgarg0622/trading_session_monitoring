# Kite Position Monitor

Runs every 5 minutes during NSE market hours (Mon–Fri, 09:15–15:30 IST).
Fetches open positions, analyzes them against your intraday strategy rules, and sends a Telegram alert.

---

## Project Structure

```
kite-monitor/
├── main.py                  # Scheduler entry point
├── login.py                 # One-time daily token generator
├── requirements.txt
├── railway.toml
├── utils/
│   ├── kite_client.py       # Kite session + parallel data fetch
│   ├── market_hours.py      # IST market open/close gate
│   └── telegram.py          # Telegram message sender
└── strategy/
    └── analyzer.py          # Strategy rules → action report
```

---

## Setup

### 1. Kite Developer App

1. Create an app at [kite.trade/developers](https://kite.trade/developers)
2. Set redirect URL to `http://127.0.0.1` (it does not need to actually serve anything — see [Generate Daily Access Token](#3-generate-daily-access-token) below)
3. Make sure the app is in **Active** state (not just created)
4. Note your **API Key** and **API Secret**

### 2. Local Python Setup

Modern macOS / Homebrew Python is "externally managed" ([PEP 668](https://peps.python.org/pep-0668/)), so `pip install` system-wide is blocked. Use a virtualenv:

```bash
cd kite-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The `.venv/` directory is gitignored. Each new terminal session, re-activate with:
```bash
source .venv/bin/activate
```

### 3. Generate Daily Access Token

Kite access tokens expire at ~6 AM IST every day. Run this locally each trading morning before market open.

**Step 1 — Run the script:**
```bash
export KITE_API_KEY=your_api_key
export KITE_API_SECRET=your_api_secret
python login.py
```
(Or run `python login.py` with no exports — it prompts for the values.)

**Step 2 — Log in via the browser tab it opens:**
- Enter your Zerodha credentials + PIN/TOTP
- Kite redirects you to a URL like:
  ```
  http://127.0.0.1/?request_token=M7Lj15hNj0Us85AhIbX6xKtO303e03Qm&action=login&type=login&status=success
  ```
- **The page itself will fail to load with `ERR_CONNECTION_REFUSED` — this is expected.** Nothing is running on `127.0.0.1`. The information you need is in the URL bar, not on the page.

**Step 3 — Copy the `request_token` value from the URL:**
- From the example above, copy just `M7Lj15hNj0Us85AhIbX6xKtO303e03Qm`
- Paste it into the terminal at the `Paste the request_token from the URL:` prompt
- **Do this quickly** — the `request_token` is single-use and expires in ~2 minutes. If it times out, just re-run `python login.py`.

**Step 4 — Copy the printed `KITE_ACCESS_TOKEN` into Railway** (see [Setting Env Vars on Railway](#setting-env-vars-on-railway) below).

### 4. Telegram Bot

1. Message `@BotFather` → create a new bot → copy the token
2. Send a message to your bot, then visit:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Copy the `chat.id` from the response (negative number for groups, positive for DMs)

---

## Environment Variables (Railway)

| Variable              | Description                          | Example                                  |
|-----------------------|--------------------------------------|------------------------------------------|
| `KITE_API_KEY`        | Zerodha app API key                  | `abc123xyz`                              |
| `KITE_API_SECRET`     | Zerodha app API secret               | `secretXYZ123`                           |
| `KITE_ACCESS_TOKEN`   | Generated daily via `login.py`       | `xxxxxxxxxxxxxx`                         |
| `TELEGRAM_BOT_TOKEN`  | Your Telegram bot token              | `1234567890:AAAAAAAAAAAAAAAAAAAAA`       |
| `TELEGRAM_CHAT_ID`    | Telegram chat ID (negative = group)  | `-1001234567890`                         |

### Setting Env Vars on Railway

**Via the dashboard (one at a time):**
1. Open your project → click the deployed service card
2. Click the **Variables** tab
3. Click **+ New Variable** → enter key and value → **Add**
4. Repeat for each variable — Railway auto-redeploys on each change

**Bulk add via Raw Editor (faster):**
1. In the **Variables** tab, click **Raw Editor**
2. Paste all variables, one per line, no quotes around values:
   ```
   KITE_API_KEY=xxxxx
   KITE_API_SECRET=xxxxx
   KITE_ACCESS_TOKEN=xxxxx
   TELEGRAM_BOT_TOKEN=xxxxx
   TELEGRAM_CHAT_ID=xxxxx
   ```
3. Click **Update Variables**

**Daily token refresh:**
After running `login.py` each morning, update only the `KITE_ACCESS_TOKEN` value in the Variables tab — Railway redeploys automatically. Or use the CLI:
```bash
railway variables set KITE_ACCESS_TOKEN=newtoken
```

---

## Deploy on Railway

**Option A — Deploy from GitHub (recommended):**
1. Push this repo to GitHub
2. On Railway → **New Project** → **Deploy from GitHub repo** → pick the repo
3. If the repo doesn't appear, install / configure the Railway GitHub app: https://github.com/settings/installations → Railway → grant access to the repo
4. Set env vars (see above)
5. Railway uses [railway.toml](railway.toml) to start the service

**Option B — Deploy via CLI:**
```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

The service runs as a **background worker** — no web port needed. Start command:
```
python main.py
```

---

## Sample Telegram Output

```
📊 Position Monitor — 10:45 IST
Nifty: 24,521 | VIX: 14.3
Regime: 🟢 NORMAL — full playbook

OPEN POSITIONS
Symbol         Side   Qty      Avg      LTP      P&L  Action
────────────────────────────────────────────────────────────────────
INFY           LONG    47  1198.0  1215.5    +822  TRAIL SL → ENTRY
KOTAKBANK      SHORT  130   762.0   754.0    +520  HOLD
────────────────────────────────────────────────────────────────────
                                      Total P&L   +1,342

NOTES
🟢 INFY — +1.5% — move SL to avg price
🟢 KOTAKBANK — holding well below entry

PENDING ORDERS
Symbol         Type     Side  Qty    Price Status
───────────────────────────────────────────────────────
INFY           SL-M     SELL   47   1198.0 TRIGGER PENDING
```

---

## Strategy Rules Applied

| Rule | Logic |
|------|-------|
| Regime | Nifty < prev close AND VIX > 19 → flag LONG positions |
| Trail SL | Move ≥ 1.5% → action: TRAIL SL TO ENTRY |
| Trail SL | Move ≥ 2.5% → action: TRAIL SL TO T1 |
| SL breach | Move ≤ -1.8% → EXIT signal |
| Time cutoff | After 14:00 → MONITOR mode, no new trades |
| Square-off | After 15:15 → EXIT NOW (MIS cutoff buffer) |

---

## Notes

- **Access token must be refreshed daily** — Kite invalidates it at midnight
- The monitor only sends a message when there are open positions
- Market closed / weekends → silently skips, no Telegram spam
- All fetches happen in parallel — typically completes in 2–4 seconds
