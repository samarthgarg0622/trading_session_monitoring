"""
Market hours check — NSE/BSE Monday–Friday 09:15–15:30 IST.
"""

from datetime import time as dtime


MARKET_OPEN  = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)


def is_market_open(now_ist) -> bool:
    """Return True if the current IST datetime falls within market hours."""
    # Weekday: 0=Mon … 4=Fri
    if now_ist.weekday() > 4:
        return False
    current = now_ist.time().replace(second=0, microsecond=0)
    return MARKET_OPEN <= current <= MARKET_CLOSE


def time_label(now_ist) -> str:
    """Return a human label for where we are in the session."""
    t = now_ist.time()
    if t < dtime(9, 15):
        return "pre-market"
    if t <= dtime(10, 30):
        return "early-session"
    if t <= dtime(14, 0):
        return "mid-session"
    if t <= dtime(15, 30):
        return "late-session"   # no new entries after 14:00
    return "post-market"
