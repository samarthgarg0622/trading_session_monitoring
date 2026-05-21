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
2. Set redirect URL to any URL you control (e.g. `https://127.0.0.1`)
3. Note your **API Key** and **API Secret**

### 2. Generate Daily Access Token

Kite access tokens expire at midnight every day. Run this locally each morning before market open:

```bash
KITE_API_KEY=your_key KITE_API_SECRET=your_secret python login.py
```

Copy the printed `KITE_ACCESS_TOKEN` into your Railway environment variables.

> **Tip:** Automate this with a local cron job or a simple Railway cron service that hits Kite's login page at 8:30 AM IST.

### 3. Telegram Bot

1. Message `@BotFather` → create a new bot → copy the token
2. Send a message to your bot, then visit:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Copy the `chat.id` from the response

---

## Environment Variables (Railway)

| Variable              | Description                          |
|-----------------------|--------------------------------------|
| `KITE_API_KEY`        | Zerodha app API key                  |
| `KITE_API_SECRET`     | Zerodha app API secret               |
| `KITE_ACCESS_TOKEN`   | Generated daily via `login.py`       |
| `TELEGRAM_BOT_TOKEN`  | Your Telegram bot token              |
| `TELEGRAM_CHAT_ID`    | Your personal or group chat ID       |

---

## Deploy on Railway

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and create project
railway login
railway init
railway up
```

Set env vars in the Railway dashboard under **Variables**, then trigger a redeploy.

The service runs as a **background worker** — no web port needed. Set the start command to:
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
