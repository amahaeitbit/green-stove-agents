"""Inline SVG charts for the HTML replay page.

Pure, dependency-free, deterministic string builders. Every color comes from a CSS
custom property on the host page (``--c1..--c4`` for series, ``--limit`` for
reference lines, ``--ink`` / ``--muted`` / ``--grid`` for text and axes), so the
page owns light/dark theming and the charts never hard-code a color.

Design rules (dataviz skill): one y-axis per chart, 2px round-joined lines,
recessive solid hairline grid, dashed lines reserved for reference limits,
legend for >= 2 series plus selective direct end-labels, text in ink tokens
(never the series color), a native-tooltip hover layer, and ``role="img"`` +
``aria-label`` + ``<title>`` on every chart.
"""

from __future__ import annotations

import math
from html import escape
from typing import Iterable

__all__ = ["line_chart", "bars", "sparkline"]

_FONT = 11.0  # base font size (px) for axis/legend text
_CHAR_W = 0.6  # rough glyph advance as a fraction of font size (for layout)
_MAX_SERIES_COLORS = 4  # --c1..--c4; extra series fold into --muted, never cycled


# ---------------------------------------------------------------------------
# helpers


def _num(v: object) -> float | None:
    """Coerce to a finite float; None / NaN / inf / junk become None."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _c(x: float) -> str:
    """Coordinate to 1 decimal, compact ("12" not "12.0", never "-0")."""
    s = f"{x:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    return "0" if s in ("-0", "") else s


def _fmt(v: float) -> str:
    """Human value label: 1,284 / 12.5 / 2.92 / 0.12."""
    a = abs(v)
    if a >= 100:
        s = f"{v:,.0f}"
    elif a >= 10:
        s = f"{v:.1f}"
    else:
        s = f"{v:.2f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _text_w(s: str, size: float = _FONT) -> float:
    return len(s) * size * _CHAR_W


def _color(i: int) -> str:
    if i < _MAX_SERIES_COLORS:
        return f"var(--c{i + 1},currentColor)"
    return "var(--muted,currentColor)"


def _nice_step(span: float, target: int = 4) -> float:
    if span <= 0 or not math.isfinite(span):
        return 1.0
    raw = span / target
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag * (1 + 1e-9):
            return m * mag
    return 10 * mag


def _hhmm(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60:02d}:{m % 60:02d}"


def _svg_open(width: float, height: float, label: str, extra: str = "") -> str:
    lab = escape(label, quote=True)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_c(width)} {_c(height)}" '
        f'width="{_c(width)}" height="{_c(height)}" role="img" aria-label="{lab}" '
        f'style="max-width:100%;height:auto;font-family:inherit"{extra}>'
        f"<title>{escape(label)}</title>"
    )


def _text(x: float, y: float, s: str, *, size: float = _FONT, fill: str = "--muted",
          anchor: str = "start", weight: str | None = None) -> str:
    w = f' font-weight="{weight}"' if weight else ""
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    return (
        f'<text x="{_c(x)}" y="{_c(y)}" font-size="{_c(size)}" fill="var({fill},currentColor)"'
        f"{a}{w}>{escape(s)}</text>"
    )


def _clean(values: Iterable[object] | None) -> list[float | None]:
    if values is None:
        return []
    try:
        return [_num(v) for v in values]
    except TypeError:
        return []


# ---------------------------------------------------------------------------
# line chart


def line_chart(series: dict[str, list[float]], *, limits: dict[str, float] | None = None,
               title: str = "", y_unit: str = "kW", width: int = 720, height: int = 220,
               x_hours: bool = True, emphasis: str | None = None) -> str:
    """Inline <svg> string. series: name -> 96 values (15-min steps, 00:00–24:00); x axis in hours
    (ticks every 3 h: 00, 03, …, 24). limits: name -> horizontal dashed reference line (e.g. inverter
    limit, battery reserve). Colors via CSS variables --c1..--c4 for series and --limit for limits,
    --ink/--muted/--grid for text/axes; never hard-code colors. Legend with direct labels; <title>
    and aria-label for accessibility; no external assets; deterministic output."""
    width = max(int(_num(width) or 720), 160)
    height = max(int(_num(height) or 220), 100)
    unit = str(y_unit or "")
    data: list[tuple[str, list[float | None]]] = [
        (str(k), _clean(v)) for k, v in (series or {}).items()
    ]
    lims: list[tuple[str, float]] = []
    for k, v in (limits or {}).items():
        f = _num(v)
        if f is not None:
            lims.append((str(k), f))
    emph = emphasis if emphasis in {n for n, _ in data} else None

    n = max((len(v) for _, v in data), default=0)
    finite = [x for _, vals in data for x in vals if x is not None]
    has_data = bool(finite)

    # --- y domain (always includes 0 and every limit) -----------------------
    lo = min([0.0, *finite, *(v for _, v in lims)])
    hi = max([0.0, *finite, *(v for _, v in lims)])
    if hi == lo:
        hi = lo + 1.0
    step = _nice_step(hi - lo)
    y0 = math.floor(lo / step + 1e-9) * step
    y1 = math.ceil(hi / step - 1e-9) * step
    if y1 <= y0:
        y1 = y0 + step
    ticks: list[float] = []
    t = y0
    while t <= y1 + step * 1e-6 and len(ticks) < 12:
        ticks.append(round(t, 10))
        t += step

    # --- x domain -----------------------------------------------------------
    if x_hours:
        x_max = 24.0
        x_of_index = (lambda i: i * 24.0 / n) if n else (lambda i: 0.0)
        x_ticks = [(float(h), f"{h:02d}") for h in range(0, 25, 3)]
    else:
        x_max = float(max(n - 1, 1))
        x_of_index = lambda i: float(i)  # noqa: E731
        xs = _nice_step(x_max, 6)
        x_ticks = []
        k = 0.0
        while k <= x_max + 1e-9 and len(x_ticks) < 20:
            x_ticks.append((k, _fmt(k)))
            k += xs

    # --- layout -------------------------------------------------------------
    top = 6.0
    parts_head: list[str] = []
    if title:
        parts_head.append(_text(0, top + 12, title, size=13, fill="--ink", weight="600"))
        top += 20
    legend_items = data if len(data) >= 2 else []
    if legend_items:
        lx, ly = 0.0, top + 10
        for i, (name, _) in enumerate(legend_items):
            item_w = 18 + _text_w(name) + 14
            if lx > 0 and lx + item_w > width:
                lx, ly = 0.0, ly + 16
            parts_head.append(
                f'<path d="M{_c(lx)} {_c(ly - 3.5)}h14" stroke="{_color(i)}" stroke-width="2" '
                f'stroke-linecap="round"/>'
            )
            parts_head.append(_text(lx + 18, ly, name, fill="--ink"))
            lx += item_w
        top = ly + 10
    top += 18 if unit else 8  # room above the plot for the unit label
    tick_labels = [_fmt(v) for v in ticks]
    left = max(max((_text_w(s) for s in tick_labels), default=0.0), _text_w(unit)) + 8
    direct = len(data) <= _MAX_SERIES_COLORS and has_data
    right = 10.0
    if direct:
        right = min(max((_text_w(nm) for nm, _ in data), default=0.0) + 12, width * 0.25)
        right = max(right, 10.0)
    bottom = 20.0
    pw = max(width - left - right, 20.0)
    ph = max(height - top - bottom, 20.0)

    def sx(x: float) -> float:
        return left + (x / x_max) * pw if x_max else left

    def sy(v: float) -> float:
        return top + ph - (v - y0) / (y1 - y0) * ph

    out: list[str] = []
    # --- accessible summary -------------------------------------------------
    summary = [title] if title else []
    if has_data:
        for name, vals in data:
            pts = [(i, v) for i, v in enumerate(vals) if v is not None]
            if not pts:
                continue
            i_pk, v_pk = max(pts, key=lambda p: p[1])
            where = _hhmm(i_pk * 1440 / n) if x_hours and n else str(i_pk)
            summary.append(f"{name} peak {_fmt(v_pk)} {unit} at {where}".replace("  ", " "))
        for name, v in lims:
            summary.append(f"{name} {_fmt(v)} {unit}".strip())
    else:
        summary.append("no data")
    out.append(_svg_open(width, height, ". ".join(s for s in summary if s)))
    out.extend(parts_head)

    # --- grid + axes --------------------------------------------------------
    out.append(_text(left - 6, top - 9, unit, anchor="end"))
    for v, s in zip(ticks, tick_labels):
        y = sy(v)
        out.append(
            f'<path d="M{_c(left)} {_c(y)}H{_c(left + pw)}" stroke="var(--grid,currentColor)" '
            f'stroke-width="1"/>'
        )
        out.append(_text(left - 6, y + 3.5, s, anchor="end"))
    base_y = sy(0.0) if y0 <= 0 <= y1 else top + ph
    out.append(
        f'<path d="M{_c(left)} {_c(base_y)}H{_c(left + pw)}" stroke="var(--muted,currentColor)" '
        f'stroke-width="1"/>'
    )
    for xv, s in x_ticks:
        out.append(_text(sx(xv), top + ph + 14, s, anchor="middle"))

    # --- limits: dashed reference lines, labeled in-plot at the right end ---
    for name, v in lims:
        y = sy(v)
        out.append(
            f'<path d="M{_c(left)} {_c(y)}H{_c(left + pw)}" stroke="var(--limit,currentColor)" '
            f'stroke-width="1.5" stroke-dasharray="5 4" data-limit="{escape(name, quote=True)}"/>'
        )
        lab = f"{name} {_fmt(v)} {unit}".strip()
        ly = y - 4 if y - 4 > top + 8 else y + 12
        out.append(_text(left + pw - 2, ly, lab, anchor="end", fill="--ink"))

    if not has_data:
        out.append(_text(left + pw / 2, top + ph / 2, "No data", anchor="middle"))
        out.append("</svg>")
        return "".join(out)

    # --- series lines (emphasis drawn last, on top) -------------------------
    order = [i for i, (nm, _) in enumerate(data) if nm != emph]
    order += [i for i, (nm, _) in enumerate(data) if nm == emph]
    ends: dict[int, float] = {}
    for i in order:
        name, vals = data[i]
        d: list[str] = []
        pen_up = True
        for j, v in enumerate(vals):
            if v is None:
                pen_up = True
                continue
            x, y = sx(x_of_index(j)), sy(v)
            d.append(f"{'M' if pen_up else 'L'}{_c(x)} {_c(y)}")
            pen_up = False
            ends[i] = y
        if not d:
            continue
        # isolated points render as round dots (h0 + round cap)
        segs = "".join(d).replace("M", "\x00M").split("\x00")
        d_str = "".join(s + ("h0" if s and "L" not in s else "") for s in segs)
        sw, op = "2", ""
        if emph is not None:
            sw, op = ("2.5", "") if name == emph else ("1.5", ' stroke-opacity="0.55"')
        out.append(
            f'<path d="{d_str}" fill="none" stroke="{_color(i)}" stroke-width="{sw}"{op} '
            f'stroke-linejoin="round" stroke-linecap="round" '
            f'data-series="{escape(name, quote=True)}"/>'
        )

    # --- direct end labels (skip any that would collide; legend carries it) -
    if direct:
        prio = sorted(ends, key=lambda i: (data[i][0] != emph, i))
        placed: list[float] = []
        for i in prio:
            y = ends[i]
            if all(abs(y - p) >= _FONT + 1 for p in placed):
                placed.append(y)
                out.append(_text(left + pw + 6, y + 3.5, data[i][0], fill="--ink"))

    # --- hover layer: native tooltips on invisible columns ------------------
    buckets = min(n, 24) if x_hours else min(n, 48)
    for b in range(buckets):
        j = (b * n) // buckets
        j_next = ((b + 1) * n) // buckets
        xa = sx(x_of_index(j))
        xb = sx(x_of_index(j_next)) if (x_hours or j_next < n) else left + pw
        if not x_hours and j_next >= n:
            xb = left + pw
        when = _hhmm(j * 1440 / n) if x_hours else f"#{j}"
        bits = [when]
        for name, vals in data:
            v = vals[j] if j < len(vals) else None
            bits.append(f"{name} {_fmt(v)} {unit}".strip() if v is not None else f"{name} –")
        out.append(
            f'<rect x="{_c(xa)}" y="{_c(top)}" width="{_c(max(xb - xa, 1))}" height="{_c(ph)}" '
            f'fill="none" pointer-events="all"><title>{escape(" · ".join(bits))}</title></rect>'
        )

    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------------
# horizontal bars


def _bar_path(x0: float, y: float, length: float, h: float, r: float = 4.0) -> str:
    """Bar from baseline x0 rightwards: square at the baseline, rounded data-end."""
    if length <= 0:
        return ""
    r = min(r, length, h / 2)
    x1 = x0 + length
    return (
        f"M{_c(x0)} {_c(y)}H{_c(x1 - r)}Q{_c(x1)} {_c(y)} {_c(x1)} {_c(y + r)}"
        f"V{_c(y + h - r)}Q{_c(x1)} {_c(y + h)} {_c(x1 - r)} {_c(y + h)}H{_c(x0)}Z"
    )


def bars(rows: list[tuple[str, float]], *, unit: str = "", width: int = 480, highlight: str | None = None,
         title: str = "") -> str:
    """Horizontal bar chart <svg> (e.g. model calls: no memory vs Shared Brain). Values labeled at bar
    ends; highlight row uses --c2, others --c1."""
    width = max(int(_num(width) or 480), 160)
    clean: list[tuple[str, float | None]] = []
    for row in rows or []:
        try:
            label, value = row[0], row[1]
        except (TypeError, IndexError, KeyError):
            continue
        clean.append((str(label), _num(value)))
    unit_s = str(unit or "")

    bar_h, gap = 18.0, 10.0
    top = 6.0
    head: list[str] = []
    if title:
        head.append(_text(0, top + 12, title, size=13, fill="--ink", weight="600"))
        top += 24
    label_w = min(max((_text_w(lb) for lb, _ in clean), default=0.0) + 10, width * 0.4)

    def val_label(v: float | None) -> str:
        return "–" if v is None else f"{_fmt(v)} {unit_s}".strip()

    value_w = max((_text_w(val_label(v)) for _, v in clean), default=0.0) + 10
    left = label_w
    pw = max(width - left - value_w, 20.0)
    rows_h = len(clean) * (bar_h + gap) if clean else 30.0
    height = top + rows_h + 4

    vmax = max((v for _, v in clean if v is not None and v > 0), default=0.0)

    summary = [title] if title else []
    summary += [f"{lb} {val_label(v)}" for lb, v in clean] or ["no data"]
    out = [_svg_open(width, height, ". ".join(summary))]
    out.extend(head)
    if not clean:
        out.append(_text(width / 2, top + 18, "No data", anchor="middle"))
        out.append("</svg>")
        return "".join(out)

    out.append(
        f'<path d="M{_c(left)} {_c(top)}V{_c(top + rows_h - gap)}" stroke="var(--grid,currentColor)" '
        f'stroke-width="1"/>'
    )
    for k, (label, v) in enumerate(clean):
        y = top + k * (bar_h + gap)
        length = (max(v, 0.0) / vmax) * pw if (v is not None and vmax > 0) else 0.0
        color = "var(--c2,currentColor)" if (highlight is not None and label == highlight) \
            else "var(--c1,currentColor)"
        tip = f"{label}: {val_label(v)}"
        out.append(f"<g><title>{escape(tip)}</title>")
        out.append(_text(left - 8, y + bar_h / 2 + 4, label, anchor="end", fill="--ink"))
        p = _bar_path(left, y, length, bar_h)
        if p:
            out.append(f'<path d="{p}" fill="{color}" data-row="{escape(label, quote=True)}"/>')
        out.append(
            f'<rect x="0" y="{_c(y - gap / 2)}" width="{_c(width)}" height="{_c(bar_h + gap)}" '
            f'fill="none" pointer-events="all"/>'
        )
        weight = "600" if highlight is not None and label == highlight else None
        out.append(_text(left + length + 6, y + bar_h / 2 + 4, val_label(v), fill="--ink",
                         weight=weight))
        out.append("</g>")
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------------
# sparkline


def sparkline(values: list[float], *, width: int = 160, height: int = 28) -> str:
    """Tiny inline svg line, --c1."""
    width = max(int(_num(width) or 160), 16)
    height = max(int(_num(height) or 28), 8)
    vals = _clean(values)
    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
    if pts:
        lo, hi = min(v for _, v in pts), max(v for _, v in pts)
        label = f"Trend: min {_fmt(lo)}, max {_fmt(hi)}, last {_fmt(pts[-1][1])}"
    else:
        lo = hi = 0.0
        label = "Trend: no data"
    out = [_svg_open(width, height, label)]
    if not pts:
        mid = height / 2
        out.append(
            f'<path d="M2 {_c(mid)}H{_c(width - 2)}" stroke="var(--grid,currentColor)" stroke-width="1"/>'
        )
        out.append("</svg>")
        return "".join(out)
    pad = 3.0
    n = len(vals)
    span = hi - lo

    def sx(i: int) -> float:
        return pad + (i / (n - 1)) * (width - 2 * pad) if n > 1 else width / 2

    def sy(v: float) -> float:
        if span <= 0:
            return height / 2
        return pad + (1 - (v - lo) / span) * (height - 2 * pad)

    d: list[str] = []
    pen_up = True
    for i, v in enumerate(vals):
        if v is None:
            pen_up = True
            continue
        d.append(f"{'M' if pen_up else 'L'}{_c(sx(i))} {_c(sy(v))}")
        pen_up = False
    if len(pts) == 1:
        d.append("h0")
    out.append(
        f'<path d="{"".join(d)}" fill="none" stroke="var(--c1,currentColor)" stroke-width="1.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
    )
    li, lv = pts[-1]
    out.append(f'<circle cx="{_c(sx(li))}" cy="{_c(sy(lv))}" r="2" fill="var(--c1,currentColor)"/>')
    out.append("</svg>")
    return "".join(out)
