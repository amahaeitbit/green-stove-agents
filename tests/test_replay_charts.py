import math
import re
import xml.etree.ElementTree as ET

from greenstove.replay_charts import bars, line_chart, sparkline

SVG_NS = "{http://www.w3.org/2000/svg}"


def _day(f):
    return [f(i) for i in range(96)]


PV = _day(lambda i: round(max(0.0, math.sin((i - 24) / 48 * math.pi)) * 1.14, 4))
DEMAND = _day(lambda i: 2.8 if 70 <= i < 76 else 0.12)
BATT = _day(lambda i: 2.0 + 0.03 * i)


def _parse(svg: str) -> ET.Element:
    root = ET.fromstring(svg)  # well-formed XML or this raises
    assert root.tag == f"{SVG_NS}svg"
    return root


def _assert_valid(svg: str) -> ET.Element:
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    root = _parse(svg)
    assert re.fullmatch(r"0 0 [\d.]+ [\d.]+", root.get("viewBox", ""))
    assert root.get("role") == "img"
    assert root.get("aria-label")
    assert root.find(f"{SVG_NS}title") is not None
    # no external assets, no hard-coded colors
    assert "href" not in svg and "url(" not in svg
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", svg)
    assert "rgb(" not in svg
    return root


def test_line_chart_valid_svg_with_96_point_paths_and_legend():
    svg = line_chart({"PV": PV, "Demand": DEMAND}, title="Forecast downside", emphasis="Demand")
    root = _assert_valid(svg)
    paths = [p for p in root.iter(f"{SVG_NS}path") if p.get("data-series")]
    assert [p.get("data-series") for p in paths] == ["PV", "Demand"]  # emphasis drawn last
    for p in paths:
        assert len(re.findall(r"[ML]", p.get("d"))) == 96
        assert "var(--c" in p.get("stroke")
    assert "var(--c1" in svg and "var(--c2" in svg
    texts = [t.text for t in root.iter(f"{SVG_NS}text")]
    for tick in ("00", "03", "06", "12", "21", "24"):
        assert tick in texts
    assert texts.count("PV") >= 1 and "Forecast downside" in texts
    assert "Forecast downside" in root.get("aria-label")
    assert "peak" in root.get("aria-label")
    # hover layer: native tooltips per hour
    tips = [r.find(f"{SVG_NS}title").text for r in root.iter(f"{SVG_NS}rect")]
    assert len(tips) == 24 and tips[0].startswith("00:00") and "PV" in tips[12]


def test_limits_are_dashed_and_labeled():
    svg = line_chart({"Battery": BATT}, limits={"Reserve": 0.8, "Capacity": 5.0}, y_unit="kWh")
    root = _assert_valid(svg)
    dashed = [p for p in root.iter(f"{SVG_NS}path") if p.get("stroke-dasharray")]
    assert {p.get("data-limit") for p in dashed} == {"Reserve", "Capacity"}
    assert all("var(--limit" in p.get("stroke") for p in dashed)
    texts = [t.text for t in root.iter(f"{SVG_NS}text")]
    assert "Reserve 0.8 kWh" in texts and "Capacity 5 kWh" in texts
    # grid lines are never dashed
    grid = [p for p in root.iter(f"{SVG_NS}path") if "--grid" in (p.get("stroke") or "")]
    assert grid and not any(p.get("stroke-dasharray") for p in grid)
    # single series: no legend line-key, title names it
    assert sum(1 for t in texts if t == "Battery") == 1  # direct end label only


def test_empty_zero_none_nan_inputs_are_safe():
    for svg in (
        line_chart({}),
        line_chart({"PV": []}),
        line_chart({"PV": [0.0] * 96}),
        line_chart({"PV": [None] * 96}),
        line_chart({"PV": [float("nan"), float("inf"), None, 1.0, "x"]}),
        line_chart({"PV": [3.0]}, limits={"Bad": float("nan")}),
        line_chart({"A": [1, 2, 3]}, x_hours=False),
        bars([]),
        bars([("a", 0), ("b", 0)]),
        bars([("a", None), ("b", float("nan")), ("c", -2)]),
        sparkline([]),
        sparkline([None, float("nan")]),
        sparkline([5, 5, 5]),
        sparkline([1]),
    ):
        _assert_valid(svg)
        assert "nan" not in svg.lower().replace("nanosecond", "")
        assert "inf" not in svg.lower()
    assert "No data" in line_chart({})
    assert "Trend: no data" in sparkline([])


def test_none_gaps_split_the_path():
    vals = [1.0] * 10 + [None] * 5 + [2.0] * 10
    root = _parse(line_chart({"PV": vals}))
    d = next(p for p in root.iter(f"{SVG_NS}path") if p.get("data-series")).get("d")
    assert d.count("M") == 2 and d.count("L") == 18


def test_deterministic_and_compact_coordinates():
    a = line_chart({"PV": PV, "Demand": DEMAND}, limits={"Inverter": 3.0})
    b = line_chart({"PV": PV, "Demand": DEMAND}, limits={"Inverter": 3.0})
    assert a == b
    assert bars([("x", 1)]) == bars([("x", 1)])
    assert sparkline(PV) == sparkline(PV)
    for svg in (a, sparkline(PV), bars([("No memory", 12.3456), ("Shared Brain", 7.891)])):
        assert not re.search(r"\d\.\d{2,}", re.sub(r"<title>.*?</title>|aria-label=\"[^\"]*\"|>[^<]*</text>", "", svg))


def test_text_is_escaped():
    evil = '<script>alert("x")</script> & "q"'
    for svg in (
        line_chart({evil: [1, 2], "ok": [2, 1]}, title=evil, limits={evil: 1.0}, y_unit="<u>"),
        bars([(evil, 3.0)], title=evil, unit="<u>", highlight=evil),
    ):
        _assert_valid(svg)
        assert "<script" not in svg and "<u>" not in svg
        assert "&lt;script&gt;" in svg


def test_bars_values_at_ends_and_highlight():
    svg = bars([("No memory", 14), ("Shared Brain", 6)], unit="calls", highlight="Shared Brain",
               title="Model calls")
    root = _assert_valid(svg)
    fills = {p.get("data-row"): p.get("fill") for p in root.iter(f"{SVG_NS}path") if p.get("data-row")}
    assert "var(--c1" in fills["No memory"] and "var(--c2" in fills["Shared Brain"]
    texts = [t.text for t in root.iter(f"{SVG_NS}text")]
    assert "14 calls" in texts and "6 calls" in texts and "Model calls" in texts
    assert "No memory 14 calls" in root.get("aria-label")


def test_sparkline_basic():
    svg = sparkline(BATT, width=120, height=24)
    root = _assert_valid(svg)
    assert root.get("viewBox") == "0 0 120 24"
    path = root.find(f"{SVG_NS}path")
    assert "var(--c1" in path.get("stroke") and len(re.findall(r"[ML]", path.get("d"))) == 96
