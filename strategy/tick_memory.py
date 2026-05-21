"""
TickMemory — in-process store that tracks price, Nifty, and sector history
across every 5-minute tick for the current session.

Resets automatically when a new trading day begins.

Tracks:
  - Per symbol LTP history          → 20-min stall detection
  - Nifty tick history (global)     → sustained reversal detection
  - Sector tick history per symbol  → sustained sector flip detection
  - Entry Nifty/sector per symbol   → baseline for magnitude check

Key design — NO hardcoded thresholds for reversals:
  - Reversal confirmed only after 3 consecutive adverse ticks (15 min sustained)
  - Magnitude scaled to VIX (daily expected range), not fixed points
  - Operator spikes (1–2 ticks) are ignored by design
"""

from datetime import datetime, date
from collections import defaultdict
from math import sqrt
import pytz

IST = pytz.timezone("Asia/Kolkata")

# Number of consecutive adverse ticks required to confirm a reversal
# 3 ticks × 5 min = 15 minutes of sustained adverse movement
CONFIRMATION_TICKS = 3

# Fraction of expected daily range that must be breached to confirm reversal
# 0.25 = trigger fires when adverse move ≥ 25% of VIX-implied daily range
REVERSAL_RANGE_FRACTION = 0.25

# Minimum sector move % even on very calm days (floor so we don't exit on 0.01% moves)
MIN_SECTOR_THRESHOLD_PCT = 0.15


class TickMemory:
    def __init__(self):
        self._session_date: date | None = None

        # { symbol: [(datetime, ltp), ...] }
        self._price_history: dict[str, list[tuple]] = defaultdict(list)

        # Global Nifty tick history — [(datetime, ltp), ...]
        self._nifty_history: list[tuple] = []

        # { symbol: [(datetime, sector_ltp), ...] }
        self._sector_history: dict[str, list[tuple]] = defaultdict(list)

        # { symbol: float } — Nifty LTP when position was first seen this session
        self._nifty_at_entry: dict[str, float] = {}

        # { symbol: float } — sector LTP when position was first seen this session
        self._sector_at_entry: dict[str, float] = {}

    # ── Session reset — auto-triggered every tick ─────────────────────────────

    def _reset_if_new_session(self, now_ist: datetime):
        today = now_ist.date()
        if self._session_date != today:
            self._session_date = today
            self._price_history.clear()
            self._nifty_history.clear()
            self._sector_history.clear()
            self._nifty_at_entry.clear()
            self._sector_at_entry.clear()

    # ── Record a tick ─────────────────────────────────────────────────────────

    def record(self, symbol: str, ltp: float, now_ist: datetime,
               nifty_ltp: float = 0, sector_ltp: float = 0):
        """
        Call once per monitor tick for each open position.
        Records LTP, Nifty, and sector history. Stores entry baselines
        the first time a symbol is seen each session.
        """
        self._reset_if_new_session(now_ist)

        # Position LTP
        self._price_history[symbol].append((now_ist, ltp))

        # Global Nifty (deduplicated — only append if timestamp is new)
        if not self._nifty_history or self._nifty_history[-1][0] != now_ist:
            if nifty_ltp:
                self._nifty_history.append((now_ist, nifty_ltp))

        # Per-symbol sector history
        if sector_ltp:
            self._sector_history[symbol].append((now_ist, sector_ltp))

        # Entry baselines — recorded only on first tick for this symbol
        if symbol not in self._nifty_at_entry and nifty_ltp:
            self._nifty_at_entry[symbol] = nifty_ltp
        if symbol not in self._sector_at_entry and sector_ltp:
            self._sector_at_entry[symbol] = sector_ltp

    # ── VIX-implied daily range helper ───────────────────────────────────────

    @staticmethod
    def _vix_daily_move(nifty_ltp: float, vix: float) -> float:
        """
        Expected daily move in points based on VIX.
        Formula: Nifty × (VIX% / sqrt(252))
        Example: 24000 × (16 / 100 / 15.87) ≈ 242 points expected daily range
        """
        if not vix or not nifty_ltp:
            return 50  # safe fallback
        return nifty_ltp * (vix / 100) / sqrt(252)

    # ── Rule 1: 20-minute stall ───────────────────────────────────────────────

    def is_stalled(self, symbol: str, side: str, t1_price: float,
                   window_minutes: int = 20) -> bool:
        """
        Returns True if price has made zero progress toward T1
        over the last `window_minutes`.

        LONG  → max LTP in window never exceeded oldest LTP in window
        SHORT → min LTP in window never went below oldest LTP in window

        Requires at least 4 ticks (20 min) before firing — avoids
        false signals in the first few minutes of a trade.
        """
        history = self._price_history.get(symbol, [])
        if len(history) < 4:
            return False

        now_ts = history[-1][0]
        cutoff = now_ts.timestamp() - window_minutes * 60
        window = [(ts, p) for ts, p in history if ts.timestamp() >= cutoff]

        if len(window) < 4:
            return False

        oldest_ltp = window[0][1]

        if side == "LONG":
            return max(p for _, p in window) <= oldest_ltp
        else:
            return min(p for _, p in window) >= oldest_ltp

    # ── Rule 2: Nifty reversal — confirmation-based, VIX-scaled ─────────────

    def nifty_genuinely_reversed(self, symbol: str, side: str,
                                  current_nifty: float, vix: float) -> tuple[bool, str]:
        """
        Returns (True, reason) only when BOTH conditions are met:

        1. TREND — last CONFIRMATION_TICKS (3) Nifty ticks are all moving
                   against the trade direction (sustained, not a spike)

        2. MAGNITUDE — total move from entry ≥ 25% of VIX-implied daily range
                       (scales with volatility — not a fixed 50 points)

        An operator spike lasting 1–2 ticks will NOT trigger this.
        15 minutes of sustained adverse Nifty movement will.
        """
        history = self._nifty_history
        entry_nifty = self._nifty_at_entry.get(symbol)

        if not entry_nifty or not current_nifty or len(history) < CONFIRMATION_TICKS:
            return False, ""

        # ── Check 1: Trend — N consecutive ticks all adverse ─────────────────
        last_n = [p for _, p in history[-CONFIRMATION_TICKS:]]

        if side == "LONG":
            # All ticks declining — each lower than the previous
            trend_confirmed = all(last_n[i] < last_n[i - 1]
                                  for i in range(1, len(last_n)))
        else:
            # All ticks rising
            trend_confirmed = all(last_n[i] > last_n[i - 1]
                                  for i in range(1, len(last_n)))

        if not trend_confirmed:
            return False, ""  # spike, not a trend — ignore

        # ── Check 2: Magnitude — VIX-scaled, not hardcoded ───────────────────
        daily_move = self._vix_daily_move(entry_nifty, vix)
        threshold  = daily_move * REVERSAL_RANGE_FRACTION
        delta      = abs(current_nifty - entry_nifty)

        if delta < threshold:
            return False, ""  # move not large enough relative to today's volatility

        direction = "↓" if side == "LONG" else "↑"
        reason = (
            f"Nifty {direction} {delta:.0f} pts over {CONFIRMATION_TICKS} ticks "
            f"(threshold {threshold:.0f} pts at VIX {vix:.1f})"
        )
        return True, reason

    # ── Rule 3: Sector flip — confirmation-based, VIX-scaled ─────────────────

    def sector_genuinely_flipped(self, symbol: str, side: str,
                                  current_sector: float, vix: float) -> tuple[bool, str]:
        """
        Returns (True, reason) only when BOTH conditions are met:

        1. TREND — last CONFIRMATION_TICKS sector readings all moving
                   against the trade direction

        2. MAGNITUDE — sector move from entry ≥ VIX-scaled threshold %
                       (floor: MIN_SECTOR_THRESHOLD_PCT = 0.15%)

        Filters out intraday sector noise and operator-driven index moves.
        """
        history      = self._sector_history.get(symbol, [])
        entry_sector = self._sector_at_entry.get(symbol)

        if not entry_sector or not current_sector or len(history) < CONFIRMATION_TICKS:
            return False, ""

        # ── Check 1: Trend ────────────────────────────────────────────────────
        last_n = [p for _, p in history[-CONFIRMATION_TICKS:]]

        if side == "LONG":
            trend_confirmed = all(last_n[i] < last_n[i - 1]
                                  for i in range(1, len(last_n)))
        else:
            trend_confirmed = all(last_n[i] > last_n[i - 1]
                                  for i in range(1, len(last_n)))

        if not trend_confirmed:
            return False, ""

        # ── Check 2: Magnitude — VIX-scaled floor ────────────────────────────
        # Higher VIX → sectors swing more → need bigger move to confirm flip
        threshold_pct = max(MIN_SECTOR_THRESHOLD_PCT, vix * 0.012)
        move_pct = abs(current_sector - entry_sector) / entry_sector * 100

        if move_pct < threshold_pct:
            return False, ""

        direction = "↓" if side == "LONG" else "↑"
        reason = (
            f"Sector {direction} {move_pct:.2f}% over {CONFIRMATION_TICKS} ticks "
            f"(threshold {threshold_pct:.2f}% at VIX {vix:.1f})"
        )
        return True, reason

    # ── Utilities ─────────────────────────────────────────────────────────────

    def ticks_recorded(self, symbol: str) -> int:
        return len(self._price_history.get(symbol, []))

    def minutes_in_trade(self, symbol: str) -> int:
        history = self._price_history.get(symbol, [])
        if len(history) < 2:
            return 0
        return int((history[-1][0] - history[0][0]).total_seconds() / 60)
