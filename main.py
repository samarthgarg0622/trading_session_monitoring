"""
Kite Position Monitor — runs every 5 minutes during market hours.
Deploy on Railway as a background worker (no web server needed).

The TickMemory singleton in strategy/analyzer.py persists across ticks
for the life of this process — that's what enables stall detection,
Nifty reversal tracking, and sector flip detection.
"""

import time
import logging
import schedule
from datetime import datetime
import pytz

from utils.kite_client import KiteClient
from utils.market_hours import is_market_open
from strategy.analyzer import analyze_positions
from utils.table_image import render_table_image
from utils.telegram import send_telegram_message, send_telegram_photo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


def run_monitor():
    """Main job: fetch → analyze → alert. Designed to complete in <10s."""
    now_ist = datetime.now(IST)
    log.info(f"Monitor tick at {now_ist.strftime('%H:%M:%S IST')}")

    # ── 1. Market hours gate ──────────────────────────────────────────────────
    if not is_market_open(now_ist):
        log.info("Market closed — skipping.")
        return

    # ── 2. Kite session ───────────────────────────────────────────────────────
    kite = KiteClient()
    if not kite.ensure_session():
        send_telegram_message("⚠️ *Kite session dead* — login manually via /login.")
        log.error("Kite session unavailable.")
        return

    # ── 3. Fetch everything in parallel ──────────────────────────────────────
    data = kite.fetch_all()
    if data is None:
        send_telegram_message("⚠️ *Kite data fetch failed.* Retrying next tick.")
        return

    # ── 4. Strategy analysis ──────────────────────────────────────────────────
    report = analyze_positions(data, now_ist)

    # ── 5. Send Telegram ──────────────────────────────────────────────────────
    if not report:
        log.info("No open positions — nothing to report.")
        return

    send_telegram_message(report["text"])
    for tbl in report.get("tables", []):
        try:
            img = render_table_image(
                title=tbl["title"],
                headers=tbl["headers"],
                rows=tbl["rows"],
                footer_row=tbl.get("footer"),
                action_col_index=tbl.get("action_col_index"),
            )
            send_telegram_photo(img)
        except Exception as e:
            log.error(f"Failed to render/send table '{tbl.get('title')}': {e}")
    log.info("Alert + table image(s) sent to Telegram.")


def main():
    log.info("Kite Position Monitor starting...")
    schedule.every(5).minutes.do(run_monitor)

    # Run immediately on start so you get a report right away
    run_monitor()

    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    main()
