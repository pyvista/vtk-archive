"""The activity chart on the home page, as inline SVG with no JavaScript.

Two panels rather than one, because comments outnumber opened items by about
25 to 1 and a single scale would flatten issues and merge requests to nothing.
Two scales on one axis would be worse, so the year axis is shared and each
measure keeps its own panel.

Colors are slots 1 to 3 of the validated categorical palette. Aqua sits below
3:1 on a white surface, so the table view under the figure is required relief,
not a nicety.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from markupsafe import Markup

W = 760
PAD_L = 46
PAD_R = 8
PANEL_H = 96
GAP = 34
AXIS_H = 22
BAR_GAP = 5
RADIUS = 4

SERIES = {
    "issues": {"label": "Issues", "light": "#2a78d6", "dark": "#3987e5"},
    "merge_requests": {"label": "Merge requests", "light": "#eb6834", "dark": "#d95926"},
    # Comments is a lone series in its own panel, so it needs no categorical slot
    # of its own and no legend entry; the panel title names it.
    "comments": {"label": "Comments", "light": "#2a78d6", "dark": "#3987e5"},
}


def tally(corpora: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-year counts of items opened and comments written."""
    opened: dict[str, Counter[str]] = {k: Counter() for k in ("issues", "merge_requests")}
    comments: Counter[str] = Counter()

    for kind, records in corpora.items():
        for rec in records.values():
            if created := (rec.get("created_at") or "")[:4]:
                opened[kind][created] += 1
            for note in rec.get("notes") or []:
                if note.get("system"):
                    continue
                if at := (note.get("created_at") or "")[:4]:
                    comments[at] += 1

    years = sorted(
        {y for c in opened.values() for y in c} | set(comments),
    )
    if not years:
        return []
    span = range(int(years[0]), int(years[-1]) + 1)
    return [
        {
            "year": str(y),
            "issues": opened["issues"][str(y)],
            "merge_requests": opened["merge_requests"][str(y)],
            "comments": comments[str(y)],
        }
        for y in span
    ]


def _nice(top: int) -> int:
    """A round number at or above `top`, for the axis label."""
    if top <= 0:
        return 1
    step = 10 ** (len(str(top)) - 1)
    for mult in (1, 2, 2.5, 5, 10):
        if step * mult >= top:
            return int(step * mult)
    return top


def _bar(x: float, y: float, w: float, h: float, *, round_top: bool) -> str:
    """A bar whose top corners are rounded and whose base sits on the axis."""
    if h <= 0:
        return ""
    r = min(RADIUS, w / 2, h)
    if not round_top or h < r:
        return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}"/>'
    return (
        f'<path d="M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} '
        f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
        f'V{y + h:.1f} Z"/>'
    )


def _panel(rows, top_y, keys, label, scale_max) -> list[str]:
    out = [f'<text class="panel-label" x="0" y="{top_y - 8:.0f}">{label}</text>']
    base = top_y + PANEL_H
    for gy, frac in ((top_y, 1.0), (top_y + PANEL_H / 2, 0.5)):
        out.append(
            f'<line class="grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{gy:.1f}" y2="{gy:.1f}"/>'
        )
        out.append(
            f'<text class="tick" x="{PAD_L - 6}" y="{gy + 3:.1f}" text-anchor="end">'
            f"{int(scale_max * frac):,}</text>"
        )
    out.append(f'<line class="axis" x1="{PAD_L}" x2="{W - PAD_R}" y1="{base}" y2="{base}"/>')

    span = (W - PAD_R - PAD_L) / len(rows)
    bw = max(2.0, span - BAR_GAP)
    for i, row in enumerate(rows):
        x = PAD_L + i * span + (span - bw) / 2
        cursor = base
        drawn = [k for k in keys if row[k]]
        for k in keys:
            value = row[k]
            if not value:
                continue
            h = value / scale_max * PANEL_H
            # A 2px surface gap keeps stacked segments from reading as one mark.
            gap = 2 if k != drawn[-1] else 0
            y = cursor - h
            out.append(
                f'<g class="s-{k}"><title>{row["year"]}: {value:,} '
                f"{SERIES[k]['label'].lower()}</title>"
                f"{_bar(x, y, bw, max(0.0, h - gap), round_top=(k == drawn[-1]))}</g>"
            )
            cursor = y
    return out


def svg(rows: list[dict[str, Any]]) -> Markup:
    """The whole figure. Returns empty markup when there is nothing to draw."""
    if not rows:
        return Markup()

    opened_max = _nice(max((r["issues"] + r["merge_requests"]) for r in rows))
    comment_max = _nice(max(r["comments"] for r in rows))
    p1, p2 = AXIS_H, AXIS_H + PANEL_H + GAP
    height = p2 + PANEL_H + AXIS_H

    parts = [
        f'<svg class="activity" viewBox="0 0 {W} {height}" role="img" '
        f'aria-label="VTK activity per year, {rows[0]["year"]} to {rows[-1]["year"]}">'
    ]
    parts += _panel(rows, p1, ["issues", "merge_requests"], "Opened", opened_max)
    parts += _panel(rows, p2, ["comments"], "Comments written", comment_max)

    span = (W - PAD_R - PAD_L) / len(rows)
    every = 1 if len(rows) <= 14 else 2
    for i, row in enumerate(rows):
        if i % every and i != len(rows) - 1:
            continue
        x = PAD_L + i * span + span / 2
        parts.append(
            f'<text class="tick" x="{x:.1f}" y="{p2 + PANEL_H + 15:.0f}" '
            f'text-anchor="middle">{row["year"][2:]}</text>'
        )
    parts.append("</svg>")
    return Markup("".join(parts))


def payload(rows: list[dict[str, Any]]) -> Markup:
    """The same numbers as JSON, for the interactive chart to draw from.

    Emitted into the page rather than fetched, because it is under 2 KB and a
    second request for it would be slower than the markup carrying it.
    """
    data = {
        "years": [r["year"] for r in rows],
        "issues": [r["issues"] for r in rows],
        "merge_requests": [r["merge_requests"] for r in rows],
        "comments": [r["comments"] for r in rows],
    }
    # `</script>` inside a script element would close it early.
    return Markup(json.dumps(data, separators=(",", ":")).replace("<", "\\u003c"))
