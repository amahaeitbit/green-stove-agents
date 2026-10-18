"""Limit-label layout in line_chart: de-duplication, collision avoidance, legible tiles, narrow mode."""
import math
import re
import xml.etree.ElementTree as ET

from greenstove.replay_charts import line_chart

SVG_NS = "{http://www.w3.org/2000/svg}"

PV = [round(max(0.0, math.sin((i - 24) / 48 * math.pi)) * 1.14, 4) for i in range(96)]
DEMAND = [2.8 if 70 <= i < 76 else 0.12 for i in range(96)]
BATT = [2.0 + 0.03 * i for i in range(96)]
UI_SERIES = {"PV (kW)": PV, "demand (kW)": DEMAND, "battery (kWh)": BATT}
UI_LIMITS = {"inverter 3 kW": 3.0, "reserve 1.2 kWh": 1.2}


def _texts(svg: str) -> dict[str, list[float]]:
    """label text -> y positions of every <text> carrying it."""
    out: dict[str, list[float]] = {}
    for t in ET.fromstring(svg).iter(f"{SVG_NS}text"):
        out.setdefault(t.text or "", []).append(float(t.get("y")))
    return out


def _tiles(svg: str) -> list[ET.Element]:
    return [r for r in ET.fromstring(svg).iter(f"{SVG_NS}rect") if r.get("fill-opacity")]


def test_duplicate_limit_label_is_drawn_once():
    svg = line_chart({"Battery": BATT}, limits={"inverter 3 kW": 3.0, "inverter": 3.0}, y_unit="kW")
    root = ET.fromstring(svg)
    dashed = {p.get("data-limit") for p in root.iter(f"{SVG_NS}path") if p.get("stroke-dasharray")}
    assert dashed == {"inverter 3 kW", "inverter"}  # both reference lines exist
    assert len(_texts(svg).get("inverter 3 kW", [])) == 1  # but the label appears once
    assert len(_tiles(svg)) == 1
    # the accessible summary does not repeat a value the name already carries
    assert "inverter 3 kW 3" not in root.get("aria-label")


def test_close_limits_get_distinct_non_overlapping_label_positions():
    svg = line_chart({"Battery": BATT}, limits={"Reserve 1.2 kWh": 1.2, "Floor 1.3 kWh": 1.3}, y_unit="kWh")
    texts = _texts(svg)
    (ya,), (yb,) = texts["Reserve 1.2 kWh"], texts["Floor 1.3 kWh"]
    assert abs(ya - yb) >= 12
    tiles = _tiles(svg)
    assert len(tiles) == 2
    ys = sorted(float(r.get("y")) for r in tiles)
    assert ys[1] - ys[0] >= 12
    # labels stay inside the plot: below the header, above the x-axis tick labels
    root = ET.fromstring(svg)
    height = float(root.get("viewBox").split()[3])
    for y in (ya, yb):
        assert 20 < y < height - 20
    for r in tiles:
        assert "var(--panel" in r.get("fill")


def test_narrow_chart_uses_short_limit_labels_and_the_legend():
    wide = line_chart(UI_SERIES, limits=UI_LIMITS, y_unit="kW / kWh", emphasis="demand (kW)")
    narrow = line_chart(UI_SERIES, limits=UI_LIMITS, y_unit="kW / kWh", emphasis="demand (kW)", width=330)
    wt, nt = _texts(wide), _texts(narrow)
    assert "inverter 3 kW" in wt and "reserve 1.2 kWh" in wt
    assert "3 kW" in nt and "1.2 kWh" in nt
    assert "inverter 3 kW" not in nt and "reserve 1.2 kWh" not in nt
    # series names come from the legend exactly once (no direct end labels at this width)
    for name in UI_SERIES:
        assert len(nt[name]) == 1
    assert ET.fromstring(narrow).get("viewBox").startswith("0 0 330 ")
    # single series: narrow charts still name it (legend), wide ones use the direct end label
    assert "Battery" in _texts(line_chart({"Battery": BATT}, width=300))


def test_limit_labels_are_drawn_after_series_lines():
    svg = line_chart(UI_SERIES, limits=UI_LIMITS, y_unit="kW / kWh")
    last_series = max(m.end() for m in re.finditer(r'data-series="[^"]*"', svg))
    assert svg.index(">inverter 3 kW</text>") > last_series


def test_output_has_no_scripts_or_handlers():
    evil = '<script>alert(1)</script> onload="x" 3 kW'
    for svg in (
        line_chart(UI_SERIES, limits=UI_LIMITS, y_unit="kW / kWh"),
        line_chart(UI_SERIES, limits=UI_LIMITS, y_unit="kW / kWh", width=330),
        line_chart({evil: PV}, limits={evil: 3.0}, title=evil, width=300),
    ):
        root = ET.fromstring(svg)
        assert "<script" not in svg
        assert not any(a.lower().startswith("on") for el in root.iter() for a in el.attrib)
        assert "href" not in svg and "url(" not in svg
