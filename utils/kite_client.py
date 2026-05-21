"""
KiteClient — wraps kiteconnect, handles session refresh and parallel data fetch.

Required env vars:
  KITE_API_KEY       — your Zerodha API key
  KITE_API_SECRET    — your Zerodha API secret
  KITE_ACCESS_TOKEN  — stored after login; refresh daily via the login utility
  KITE_REQUEST_TOKEN — optional; used for first-time token generation only
"""

import os
import logging
import concurrent.futures
from typing import Optional

from kiteconnect import KiteConnect

log = logging.getLogger(__name__)

# Nifty indices we always fetch for regime check
INDEX_INSTRUMENTS = [
    "NSE:NIFTY 50",
    "NSE:NIFTY BANK",
    "NSE:INDIA VIX",
    "NSE:NIFTY IT",
    "NSE:NIFTY AUTO",
    "NSE:NIFTY PHARMA",
    "NSE:NIFTY FMCG",
    "NSE:NIFTY METAL",
    "NSE:NIFTY ENERGY",
    "NSE:NIFTY REALTY",
    "NSE:NIFTY PSU BANK",
    "NSE:NIFTY FIN SERVICE",
]


class KiteClient:
    def __init__(self):
        self.api_key = os.environ["KITE_API_KEY"]
        self.kite = KiteConnect(api_key=self.api_key)
        self.kite.set_access_token(os.environ["KITE_ACCESS_TOKEN"])

    def ensure_session(self) -> bool:
        """Ping Kite with a lightweight profile call. Return True if alive."""
        try:
            self.kite.profile()
            return True
        except Exception as e:
            log.warning(f"Session check failed: {e}")
            # Attempt token refresh if request token is available
            req_token = os.environ.get("KITE_REQUEST_TOKEN")
            secret = os.environ.get("KITE_API_SECRET")
            if req_token and secret:
                try:
                    data = self.kite.generate_session(req_token, api_secret=secret)
                    new_token = data["access_token"]
                    self.kite.set_access_token(new_token)
                    # Persist so next tick uses it
                    os.environ["KITE_ACCESS_TOKEN"] = new_token
                    log.info("Session refreshed via request token.")
                    return True
                except Exception as ex:
                    log.error(f"Token refresh failed: {ex}")
            return False

    # ── Individual fetchers ───────────────────────────────────────────────────

    def _get_positions(self):
        """Net + day positions merged."""
        pos = self.kite.positions()
        # day positions are the live MIS ones
        return pos.get("day", [])

    def _get_orders(self):
        """All orders placed today."""
        return self.kite.orders()

    def _get_index_quotes(self):
        """Regime-check indices."""
        return self.kite.quote(INDEX_INSTRUMENTS)

    def _get_position_ltp(self, trading_symbols: list[str]):
        """LTP for each open position instrument."""
        if not trading_symbols:
            return {}
        instruments = [f"NSE:{s}" for s in trading_symbols]
        quotes = self.kite.ltp(instruments)
        # Normalise: { "INFY": 1205.5, ... }
        return {
            sym.replace("NSE:", ""): q["last_price"]
            for sym, q in quotes.items()
        }

    # ── Parallel fetch ────────────────────────────────────────────────────────

    def fetch_all(self) -> Optional[dict]:
        """
        Fetch positions, orders, and indices in parallel.
        Returns a unified dict or None on failure.
        """
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
                f_pos    = ex.submit(self._get_positions)
                f_orders = ex.submit(self._get_orders)
                f_idx    = ex.submit(self._get_index_quotes)

            positions = f_pos.result(timeout=8)
            orders    = f_orders.result(timeout=8)
            indices   = f_idx.result(timeout=8)

            # Open positions only (quantity != 0)
            open_pos = [p for p in positions if p.get("quantity", 0) != 0]

            # Fetch LTPs for open positions
            symbols = [p["tradingsymbol"] for p in open_pos]
            ltp_map = self._get_position_ltp(symbols) if symbols else {}

            return {
                "positions":  open_pos,
                "orders":     orders,
                "indices":    indices,
                "ltp":        ltp_map,
            }

        except Exception as e:
            log.error(f"fetch_all failed: {e}")
            return None
