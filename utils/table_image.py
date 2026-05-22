"""
Renders a wide table as a high-res JPG so it's readable on Telegram
(mobile + web). Tables sent inline as Markdown wrap and become unreadable;
the image preserves column alignment with a large font.
"""

import re
from io import BytesIO
from typing import Optional

import matplotlib

matplotlib.use("Agg")  # headless — required on Railway
import matplotlib.pyplot as plt


# Strip emoji + other non-BMP/symbol chars that DejaVu Sans can't render.
# The action-column background color already encodes severity, so losing the
# emoji prefix doesn't lose information.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols & pictographs
    "\U00002300-\U000023FF"  # misc technical (⏱ stopwatch, etc.)
    "\U00002500-\U000027BF"  # box drawing + misc symbols + dingbats
    "\U00002B00-\U00002BFF"  # arrows
    "\U0001F1E0-\U0001F1FF"  # flags
    "‍️"           # ZWJ, variation selector
    "]+",
    flags=re.UNICODE,
)


def _clean(text: str) -> str:
    return _EMOJI_RE.sub("", str(text)).strip()


# Action -> (background, text color). Anything not listed renders plain.
_ACTION_COLORS = {
    "EXIT":     ("#ffd4d4", "#990000"),
    "SL HIT":   ("#ffd4d4", "#990000"),
    "EXIT NOW": ("#ff9999", "#660000"),
    "STALL":    ("#ffe4b8", "#8a4a00"),
    "TIGHTEN":  ("#fff3b8", "#7a5a00"),
    "CHECK SL": ("#fff3b8", "#7a5a00"),
    "TRAIL":    ("#cfeccf", "#1a5a1a"),
    "HOLD":     ("#ffffff", "#222222"),
}


def _action_style(action_text: str) -> tuple[str, str]:
    """Pick (bg, fg) by matching the first known keyword in the action label."""
    up = action_text.upper()
    for key, colors in _ACTION_COLORS.items():
        if key in up:
            return colors
    return ("#ffffff", "#222222")


def render_table_image(
    title: str,
    headers: list[str],
    rows: list[list[str]],
    footer_row: Optional[list[str]] = None,
    action_col_index: Optional[int] = None,
) -> bytes:
    """
    Render a table to JPG bytes at a font size that stays readable on a
    phone without zooming. The image width is sized to the column count so
    the text never has to shrink to fit.
    """
    headers = [_clean(h) for h in headers]
    rows = [[_clean(c) for c in r] for r in rows]
    footer_row = [_clean(c) for c in footer_row] if footer_row else None

    n_cols = len(headers)
    n_data = len(rows) + (1 if footer_row else 0)

    # Size each column to its widest cell so nothing gets clipped.
    # 0.13 in/char at 200 DPI ≈ comfortable spacing for 16pt text.
    col_text_widths = []
    for j in range(n_cols):
        col_cells = [headers[j]] + [r[j] for r in rows]
        if footer_row:
            col_cells.append(footer_row[j])
        col_text_widths.append(max(len(c) for c in col_cells))

    # 16pt DejaVu Sans bold renders ~0.16 in/char; pad generously so nothing
    # ever clips, especially the bold header and action labels.
    col_widths_in = [max(1.0, 0.17 * w + 0.5) for w in col_text_widths]
    fig_w = sum(col_widths_in) + 0.6
    fig_h = 1.4 + 0.55 * (n_data + 1)  # +1 for header row

    # Normalised widths (matplotlib's colWidths is in axes units, not inches)
    total = sum(col_widths_in)
    col_widths_norm = [w / total for w in col_widths_in]

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=200)
    ax.axis("off")
    ax.set_title(title, fontsize=20, fontweight="bold", loc="left", pad=14)

    body = [list(r) for r in rows]
    if footer_row:
        body.append(list(footer_row))

    table = ax.table(
        cellText=body,
        colLabels=headers,
        colWidths=col_widths_norm,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(16)
    table.scale(1.0, 2.2)

    n_body_rows = len(body)
    footer_row_idx = n_body_rows if footer_row else None  # +0 header offset below

    for (row_i, col_j), cell in table.get_celld().items():
        cell.set_edgecolor("#888888")
        cell.set_linewidth(0.6)

        # Header row
        if row_i == 0:
            cell.set_facecolor("#1f2a44")
            cell.set_text_props(color="white", fontweight="bold", fontsize=16)
            cell.set_height(cell.get_height() * 1.15)
            continue

        # Footer row (totals)
        if footer_row_idx is not None and row_i == footer_row_idx:
            cell.set_facecolor("#e6e8f0")
            cell.set_text_props(fontweight="bold", fontsize=16)
            continue

        # Body rows — zebra stripe + action column color
        cell.set_facecolor("#f7f7fa" if row_i % 2 == 0 else "#ffffff")

        if action_col_index is not None and col_j == action_col_index:
            action_text = body[row_i - 1][col_j]
            bg, fg = _action_style(action_text)
            cell.set_facecolor(bg)
            cell.set_text_props(color=fg, fontweight="bold", fontsize=16)

    buf = BytesIO()
    fig.savefig(
        buf,
        format="jpg",
        bbox_inches="tight",
        pad_inches=0.25,
        dpi=200,
        facecolor="white",
    )
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
