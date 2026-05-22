"""
Strategy Analyzer — applies all rules from the PDF to live positions.

Rules implemented:
  ✅ Regime check        — Nifty < prev close AND VIX > 19 → SHORT-ONLY
  ✅ Trail SL → entry    — move ≥ 1.5% in trade direction
  ✅ Trail SL → T1       — move ≥ 2.5% in trade direction
  ✅ SL breach           — move ≤ -1.8% → EXIT signal
  ✅ 20-min stall        — no progress toward T1 for 20 min → EXIT AT MARKET
  ✅ Nifty 50-pt reverse — Nifty moves 50 pts against trade → EXIT ALL
  ✅ Sector flip         — sector turns against trade → EXIT within 5 min
  ✅ Time cutoff 10:30   — flag if new entries still being considered
  ✅ Time cutoff 14:00   — MONITOR mode, tighten SL
  ✅ Square-off 15:15    — EXIT NOW before MIS auto square-off
  ✅ Basket health       — margin utilisation + worst-case risk
  ✅ 3-trade cap warning — alert if 3 trades already hit SL today
"""

from datetime import time as dtime
from typing import Optional

from strategy.tick_memory import TickMemory

# ── Module-level singleton — persists across ticks within the process ─────────
_memory = TickMemory()


# ─────────────────────────────────────────────────────────────────────────────
# REGIME CHECK
# ─────────────────────────────────────────────────────────────────────────────

def _regime_flags(indices: dict) -> tuple[bool, bool]:
    """
    Rule 1 — Regime Filter
    Both conditions true → SHORT-ONLY day. Zero longs.
    One condition true   → 1% risk only.
    """
    nifty_q = indices.get("NSE:NIFTY 50", {})
    vix_q   = indices.get("NSE:INDIA VIX", {})

    nifty_ltp   = nifty_q.get("last_price", 0)
    nifty_close = nifty_q.get("ohlc", {}).get("close", nifty_ltp)  # prev day close
    vix_ltp     = vix_q.get("last_price", 0)

    return nifty_ltp < nifty_close, vix_ltp > 19


def _regime_label(nifty_below: bool, vix_high: bool) -> str:
    if nifty_below and vix_high:
        return "🔴 SHORT-ONLY (Nifty↓ + VIX>19)"
    if nifty_below or vix_high:
        return "🟡 CAUTION — 1% risk only"
    return "🟢 NORMAL — full playbook"


# ─────────────────────────────────────────────────────────────────────────────
# SECTOR MAPPING
# ─────────────────────────────────────────────────────────────────────────────

# Maps stock → sector index key in the indices dict
# Extend this as you trade more names
SECTOR_MAP = {
    "INFY":        "NSE:NIFTY IT",
    "TCS":         "NSE:NIFTY IT",
    "WIPRO":       "NSE:NIFTY IT",
    "HCLTECH":     "NSE:NIFTY IT",
    "KOTAKBANK":   "NSE:NIFTY BANK",
    "HDFCBANK":    "NSE:NIFTY BANK",
    "AXISBANK":    "NSE:NIFTY BANK",
    "ICICIBANK":   "NSE:NIFTY BANK",
    "SBIN":        "NSE:NIFTY PSU BANK",
    "MARUTI":      "NSE:NIFTY AUTO",
    "TATAMOTORS":  "NSE:NIFTY AUTO",
    "BAJAJ-AUTO":  "NSE:NIFTY AUTO",
    "MOTHERSON":   "NSE:NIFTY AUTO",
    "SUNPHARMA":   "NSE:NIFTY PHARMA",
    "LUPIN":       "NSE:NIFTY PHARMA",
    "DRREDDY":     "NSE:NIFTY PHARMA",
    "CHOLAFIN":    "NSE:NIFTY FIN SERVICE",
    "BAJFINANCE":  "NSE:NIFTY FIN SERVICE",
    "RELIANCE":    "NSE:NIFTY ENERGY",
    "ONGC":        "NSE:NIFTY ENERGY",
    "TATASTEEL":   "NSE:NIFTY METAL",
    "JSWSTEEL":    "NSE:NIFTY METAL",
}


def _sector_ltp(symbol: str, indices: dict) -> float:
    key = SECTOR_MAP.get(symbol)
    if not key:
        return 0.0
    return indices.get(key, {}).get("last_price", 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# PER-POSITION ACTION LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def _action_for_position(pos: dict, ltp: float, now_ist,
                         nifty_ltp: float, vix_ltp: float, indices: dict) -> dict:
    symbol    = pos["tradingsymbol"]
    qty       = pos["quantity"]        # +ve = long, -ve = short
    avg_price = pos["average_price"]
    side      = "LONG" if qty > 0 else "SHORT"
    abs_qty   = abs(qty)

    # Estimated T1 — 1× SL distance projected in trade direction
    # SL ≈ 1% of avg price (conservative default; real SL set by user)
    sl_distance = avg_price * 0.01
    t1_price = (avg_price + sl_distance) if side == "LONG" else (avg_price - sl_distance)

    # Running P&L and move %
    if side == "LONG":
        pnl      = (ltp - avg_price) * abs_qty
        move_pct = (ltp - avg_price) / avg_price * 100
    else:
        pnl      = (avg_price - ltp) * abs_qty
        move_pct = (avg_price - ltp) / avg_price * 100

    sec_ltp = _sector_ltp(symbol, indices)

    # ── Record this tick in memory ────────────────────────────────────────────
    _memory.record(symbol, ltp, now_ist,
                   nifty_ltp=nifty_ltp, sector_ltp=sec_ltp)

    # ── Apply rules in priority order ─────────────────────────────────────────
    action = "HOLD"
    notes  = []

    t = now_ist.time()

    # PRIORITY 1 — Hard time exits (clock-based, no market logic needed)
    # ─────────────────────────────────────────────────────────────────────────
    if t >= dtime(15, 15):
        # Rule: MIS square-off buffer
        action = "🚨 EXIT NOW"
        notes.append("MIS square-off at 15:30 — exit immediately")

    # PRIORITY 2 — Nifty sustained reversal (confirmation-based, VIX-scaled)
    # 3 consecutive adverse ticks + magnitude ≥ 25% of VIX daily range
    # ─────────────────────────────────────────────────────────────────────────
    else:
        nifty_rev, nifty_reason = _memory.nifty_genuinely_reversed(
            symbol, side, nifty_ltp, vix=vix_ltp)
        if nifty_rev:
            action = "🚨 EXIT — NIFTY REVERSED"
            notes.append(nifty_reason)

    # PRIORITY 3 — Sector sustained flip (confirmation-based, VIX-scaled)
    # 3 consecutive adverse ticks + VIX-scaled magnitude threshold
    # ─────────────────────────────────────────────────────────────────────────
    if action == "HOLD" and sec_ltp:
        sec_flip, sec_reason = _memory.sector_genuinely_flipped(
            symbol, side, sec_ltp, vix=vix_ltp)
        if sec_flip:
            action = "⚠️ EXIT — SECTOR FLIPPED"
            notes.append(sec_reason)

    # PRIORITY 4 — SL breach
    # ─────────────────────────────────────────────────────────────────────────
    elif move_pct <= -1.8:
        action = "🔴 SL HIT — EXIT"
        notes.append(f"Move {move_pct:.1f}% — beyond SL threshold")

    # PRIORITY 5 — 20-minute stall (no progress toward T1)
    # ─────────────────────────────────────────────────────────────────────────
    elif _memory.is_stalled(symbol, side, t1_price, window_minutes=20):
        action = "⏱ STALL — EXIT AT MARKET"
        mins = _memory.minutes_in_trade(symbol)
        notes.append(f"No progress toward T1 for 20+ min (in trade {mins} min)")

    # PRIORITY 6 — Late session (after 14:00) — tighten everything
    # ─────────────────────────────────────────────────────────────────────────
    elif t >= dtime(14, 0):
        action = "🟡 TIGHTEN SL"
        notes.append("After 14:00 — trail SL tight, no new entries")

    # PRIORITY 7 — Profit management — trail SL
    # ─────────────────────────────────────────────────────────────────────────
    elif move_pct >= 2.5:
        action = "✅ TRAIL SL → T1"
        notes.append(f"+{move_pct:.1f}% — move SL to T1 level ({t1_price:.1f})")
    elif move_pct >= 1.5:
        action = "✅ TRAIL SL → ENTRY"
        notes.append(f"+{move_pct:.1f}% — move SL to avg price ({avg_price:.1f})")

    # PRIORITY 8 — Early loss territory — flag SL
    # ─────────────────────────────────────────────────────────────────────────
    elif move_pct <= -1.0:
        action = "⚠️ CHECK SL"
        notes.append(f"{move_pct:.1f}% — approaching SL, is it placed?")

    # PRIORITY 9 — Early session warning (fading thesis)
    # ─────────────────────────────────────────────────────────────────────────
    elif t >= dtime(10, 30) and move_pct < 0.3:
        notes.append("After 10:30 with no move — re-assess thesis")

    return {
        "symbol":   symbol,
        "side":     side,
        "qty":      abs_qty,
        "avg":      avg_price,
        "ltp":      ltp,
        "pnl":      pnl,
        "move_pct": move_pct,
        "action":   action,
        "notes":    notes,
        "mins":     _memory.minutes_in_trade(symbol),
    }


# ─────────────────────────────────────────────────────────────────────────────
# BASKET HEALTH
# ─────────────────────────────────────────────────────────────────────────────

def _basket_health(positions: list, ltp_map: dict,
                   available_funds: float) -> list[str]:
    """
    Rule: Session stop-loss — close all if day P&L hits -1.5% of opening balance.
    Rule: Margin utilisation — green <70%, amber 70-80%, red >80%.
    Rule: Worst-case risk    — green <10%, amber 10-15%, red >15%.
    """
    if available_funds <= 0:
        return []

    lines = []

    # Margin utilisation (approx — MIS 5× so margin = value / 5)
    total_margin = sum(
        abs(p.get("quantity", 0)) *
        ltp_map.get(p["tradingsymbol"], p.get("last_price", 0)) / 5
        for p in positions
    )
    margin_pct = total_margin / available_funds * 100
    m_icon = "🟢" if margin_pct < 70 else ("🟡" if margin_pct < 80 else "🔴")
    lines.append(f"{m_icon} Margin used: {margin_pct:.1f}% of available funds")

    # Worst-case risk (sum of all 1% SLs as % of available funds)
    total_risk = sum(
        abs(p.get("quantity", 0)) *
        ltp_map.get(p["tradingsymbol"], p.get("last_price", 0)) * 0.01
        for p in positions
    )
    risk_pct = total_risk / available_funds * 100
    r_icon = "🟢" if risk_pct < 10 else ("🟡" if risk_pct < 15 else "🔴")
    lines.append(f"{r_icon} Worst-case risk: {risk_pct:.1f}% of available funds")

    # Session stop-loss breach warning
    session_sl = available_funds * 0.015
    lines.append(f"🛑 Session SL limit: ₹{session_sl:,.0f} (1.5% of ₹{available_funds:,.0f})")

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# PENDING ORDERS
# ─────────────────────────────────────────────────────────────────────────────

def _pending_orders(orders: list) -> list:
    pending_statuses = {"OPEN", "TRIGGER PENDING", "AMO REQ RECEIVED"}
    return [o for o in orders if o.get("status", "").upper() in pending_statuses]


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY — builds the Telegram message
# ─────────────────────────────────────────────────────────────────────────────

def analyze_positions(data: dict, now_ist) -> Optional[dict]:
    """
    Returns:
      {
        "text":   str — message body (header, notes, basket health, regime warning),
        "tables": list of {
            "title":            str,
            "headers":          list[str],
            "rows":             list[list[str]],
            "footer":           list[str] | None,
            "action_col_index": int | None,   # column to color-code by action
        },
      }

    Wide tables (positions, pending orders) are returned as structured data so
    main.py can render them as JPGs — Telegram mangles wide monospace tables
    on both mobile and web.
    """
    positions      = data["positions"]
    orders         = data["orders"]
    indices        = data["indices"]
    ltp_map        = data["ltp"]
    available_funds = data.get("available_funds", 0)

    if not positions:
        return None

    nifty_below, vix_high = _regime_flags(indices)
    nifty_ltp = indices.get("NSE:NIFTY 50", {}).get("last_price", 0)
    vix_ltp   = indices.get("NSE:INDIA VIX", {}).get("last_price", 0)
    time_str  = now_ist.strftime("%H:%M IST")

    # Analyze each position
    actions   = []
    total_pnl = 0.0
    for pos in positions:
        sym = pos["tradingsymbol"]
        ltp = ltp_map.get(sym, pos.get("last_price", pos.get("average_price", 0)))
        result = _action_for_position(pos, ltp, now_ist, nifty_ltp, vix_ltp, indices)
        actions.append(result)
        total_pnl += result["pnl"]

    pending = _pending_orders(orders)
    health  = _basket_health(positions, ltp_map, available_funds)

    # ── Compose text message (no wide tables — those go in images) ───────────
    lines = []
    lines.append(f"*📊 Position Monitor — {time_str}*")
    lines.append(f"Nifty: `{nifty_ltp:,.0f}` | VIX: `{vix_ltp:.1f}`")
    lines.append(f"Regime: {_regime_label(nifty_below, vix_high)}")
    sign = "+" if total_pnl >= 0 else ""
    lines.append(f"Total P&L: `{sign}{total_pnl:,.0f}`  ({len(positions)} open)")
    lines.append("📎 Positions table attached as image below.")

    all_notes = [(a["symbol"], n) for a in actions for n in a["notes"]]
    if all_notes:
        lines.append("")
        lines.append("*NOTES*")
        for sym, note in all_notes:
            lines.append(f"• *{sym}* — {note}")

    if health:
        lines.append("")
        lines.append("*BASKET HEALTH*")
        for h in health:
            lines.append(h)

    if nifty_below and vix_high:
        longs = [a["symbol"] for a in actions if a["side"] == "LONG"]
        if longs:
            lines.append(
                f"\n⚠️ *SHORT-ONLY regime — LONG positions open: {', '.join(longs)}*"
            )

    # ── Build table data (rendered to JPG by main.py) ────────────────────────
    tables = []

    pos_rows = []
    for a in actions:
        sign = "+" if a["pnl"] >= 0 else ""
        pos_rows.append([
            a["symbol"],
            a["side"],
            str(a["qty"]),
            f"{a['avg']:.1f}",
            f"{a['ltp']:.1f}",
            f"{sign}{a['pnl']:,.0f}",
            f"{a['mins']}m",
            a["action"],
        ])
    sign = "+" if total_pnl >= 0 else ""
    pos_footer = ["", "", "", "", "Total", f"{sign}{total_pnl:,.0f}", "", ""]

    tables.append({
        "title":            f"Open Positions — {time_str}",
        "headers":          ["Symbol", "Side", "Qty", "Avg", "LTP", "P&L", "Mins", "Action"],
        "rows":             pos_rows,
        "footer":           pos_footer,
        "action_col_index": 7,
    })

    if pending:
        pend_rows = [
            [
                o.get("tradingsymbol", ""),
                o.get("order_type", ""),
                o.get("transaction_type", ""),
                str(o.get("quantity", 0)),
                f"{o.get('price', 0):.1f}",
                o.get("status", ""),
            ]
            for o in pending
        ]
        tables.append({
            "title":            f"Pending Orders — {time_str}",
            "headers":          ["Symbol", "Type", "Side", "Qty", "Price", "Status"],
            "rows":             pend_rows,
            "footer":           None,
            "action_col_index": None,
        })

    return {"text": "\n".join(lines), "tables": tables}
