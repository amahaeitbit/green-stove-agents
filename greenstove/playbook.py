"""Version N+1: the team playbook, a short versioned text the Pricing Agent reads.

Separate, labeled extension (not the headline A/B/C Shared Brain experiment). A playbook
holds heuristics for the FIRST proposal of a day. It can never change rules: windows,
allowed prices, tier order, requirements and household preferences stay fixed, and the
verifier alone decides pass or fail.

Store layout under <data_dir>/playbook/: v001.md, v002.md, ... plus index.json. v001 is the
human seed (empty, so "before" equals the unchanged Pricing Agent).

Machine-readable directive grammar (the mock Pricing Agent honors these literally):
    - when battery_frac < 0.45: start sun=3 standard=8 peak=13
    - when weather in [overcast, rainy_afternoon] and demand_kwh > 20: start sun=3
Features: battery_frac, weather, forecast_pv_kwh, downside_pv_kwh, demand_kwh, peak_request_kw.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_CHARS = 900
STATUSES = ("active", "candidate", "rejected", "retired")
AUTHORS = ("human-seed", "reviewer-agent")
SEED_TEXT = ""                      # neutral seed: no playbook in the payload at all

# Rule-changing language (same idea as the Keeper's constraint_edit stage, plus a few more).
RULE_PATTERNS = [re.compile(p) for p in (
    r"\brelax", r"\b(lower|raise|reduce|increase|drop|change|skip|move) (the )?(battery )?reserve",
    r"\breserve (change|changes|lower|lowered|drop)",
    r"\b(change|move|shift|extend|widen|narrow) (the )?(tariff )?windows?\b", r"\bwindows? (change|shift)",
    r"\b(increase|raise|exceed) (the )?inverter", r"\bignor", r"\boverrid", r"\bbypass", r"\bwaive",
    r"\bbound", r"\blattice", r"\brequirement", r"\bdisable", r"\bskip (the )?(check|verif)",
    r"\boff-list\b", r"\bnot in the allowed")]
SCENARIO_ID = re.compile(r"\b(ho|dev|demo|fixture|stub)-[a-z0-9]", re.I)


class PlaybookError(ValueError):
    """Playbook text failed validation (reasons in .reasons)."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()[:12]


def _known_scenario_ids() -> set[str]:
    try:
        from greenstove import scenarios
        return {*scenarios.DEV_SCENARIOS, *scenarios.DEMO_SCENARIOS, *scenarios.HELDOUT_SCENARIOS}
    except Exception:  # noqa: BLE001 - validation must not depend on world internals
        return set()


def validate_text(text: str) -> list[str]:
    """Reasons the text is not an acceptable playbook ([] means valid)."""
    reasons = []
    text = text.strip()
    if len(text) > MAX_CHARS:
        reasons.append(f"too_long: {len(text)} > {MAX_CHARS} characters")
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip() and not line.startswith("- "):
            reasons.append(f"not_bullet: line {i} must start with '- '")
    low = text.lower()
    hits = sorted({p.pattern for p in RULE_PATTERNS if p.search(low)})
    if hits:
        reasons.append(f"rule_change: playbooks cannot change rules ({', '.join(hits)})")
    ids = sorted({i for i in _known_scenario_ids() if i in low} | {m.group(0) for m in SCENARIO_ID.finditer(low)})
    if ids:
        reasons.append(f"scenario_id: playbooks cannot name specific scenarios ({', '.join(ids)})")
    return reasons


# ---- directive grammar ----------------------------------------------------------------

_DIRECTIVE = re.compile(r"^-\s*when\s+(?P<conds>.+?)\s*:\s*start\s+(?P<prices>[a-z_]+\s*=\s*\d+(?:\.\d+)?"
                        r"(?:[\s,]+[a-z_]+\s*=\s*\d+(?:\.\d+)?)*)\s*\.?\s*$", re.I)
_COND = re.compile(r"^(?P<feat>[a-z_]+)\s*(?P<op><=|>=|==|!=|<|>|=|\bin\b)\s*(?P<val>.+)$", re.I)
_PRICE = re.compile(r"([a-z_]+)\s*=\s*(\d+(?:\.\d+)?)", re.I)
ALIASES = {"weather": "weather_class", "weather_forecast_class": "weather_class", "battery": "battery_frac"}


@dataclass
class Directive:
    line: str
    conds: list[tuple[str, str, Any]] = field(default_factory=list)
    start: dict[str, float] = field(default_factory=dict)

    def matches(self, features: dict[str, Any]) -> bool:
        for feat, op, val in self.conds:
            x = features.get(feat)
            if x is None:
                return False
            if op == "in":
                if str(x) not in val:
                    return False
                continue
            if isinstance(val, str) or isinstance(x, str):
                if op in ("=", "==") and str(x) == str(val):
                    continue
                if op == "!=" and str(x) != str(val):
                    continue
                return False
            ok = {"<": x < val, "<=": x <= val, ">": x > val, ">=": x >= val, "=": abs(x - val) < 1e-9,
                  "==": abs(x - val) < 1e-9, "!=": abs(x - val) >= 1e-9}[op]
            if not ok:
                return False
        return True


def parse_directives(text: str) -> list[Directive]:
    """Machine-readable `- when ...: start ...` lines; prose bullets are ignored."""
    out = []
    for line in (text or "").splitlines():
        m = _DIRECTIVE.match(line.strip())
        if not m:
            continue
        conds, ok = [], True
        for part in re.split(r"\s+and\s+", m.group("conds").strip(), flags=re.I):
            c = _COND.match(part.strip())
            if not c:
                ok = False
                break
            feat = ALIASES.get(c.group("feat").lower(), c.group("feat").lower())
            op, raw = c.group("op").lower(), c.group("val").strip()
            if op == "in":
                val: Any = {v.strip().strip("'\"") for v in raw.strip("[]() ").split(",") if v.strip()}
            else:
                try:
                    val = float(raw)
                except ValueError:
                    val = raw.strip("'\"")
            conds.append((feat, op, val))
        if ok and conds:
            out.append(Directive(line=line.strip(), conds=conds,
                                 start={k.lower(): float(v) for k, v in _PRICE.findall(m.group("prices"))}))
    return out


def features_from_situation(sit: dict[str, Any]) -> dict[str, Any]:
    return {k: sit[k] for k in ("weather_class", "battery_frac", "forecast_pv_kwh", "downside_pv_kwh",
                                "demand_kwh", "peak_request_kw") if k in sit}


def features_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Same features computed from the Pricing Agent's planner-visible payload."""
    st, wins, reqs = payload.get("station", {}), payload.get("windows", []), payload.get("requests", [])
    cap = st.get("battery_capacity_kwh") or 0
    return {
        "weather_class": payload.get("weather_forecast_class") or "unknown",
        "battery_frac": round(st.get("battery_start_kwh", 0) / cap, 3) if cap else None,
        "forecast_pv_kwh": round(sum(w.get("pv_expected_kwh", 0) for w in wins), 2),
        "downside_pv_kwh": round(sum(w.get("pv_downside_kwh", 0) for w in wins), 2),
        "demand_kwh": round(sum(r["power_kw"] * r["minutes"] / 60 for r in reqs), 2),
        "peak_request_kw": round(max((r["power_kw"] for r in reqs), default=0.0), 2),
    }


def _snap(allowed: list[float], price: float) -> float:
    return min(allowed, key=lambda a: abs(a - price))


def start_from_playbook(text: str, features: dict[str, Any], lattice: dict[str, list[float]],
                        order: list[str]) -> tuple[dict[str, float], str] | None:
    """First matching directive's start tariff (snapped to allowed prices, tier order kept), else None."""
    for d in parse_directives(text):
        if not d.matches(features):
            continue
        prices = {n: (_snap(lattice[n], d.start[n]) if n in d.start else lattice[n][len(lattice[n]) // 2])
                  for n in lattice}
        ordered = [prices[n] for n in order]
        if any(a > b for a, b in zip(ordered, ordered[1:])):
            continue                      # an out-of-order start is never proposed
        return prices, d.line
    return None


# ---- versioned store ------------------------------------------------------------------

@dataclass
class PlaybookVersion:
    id: str
    text: str
    meta: dict[str, Any]


class PlaybookStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index = self.root / "index.json"
        if not self._index.exists():
            self._save({"active": None, "versions": []})
            vid = self.propose(SEED_TEXT, parent=None, author="human-seed")
            self.decide(vid, True, {}, "human seed (empty playbook: the unchanged Pricing Agent)")

    def _load(self) -> dict[str, Any]:
        return json.loads(self._index.read_text())

    def _save(self, data: dict[str, Any]) -> None:
        tmp = self._index.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self._index)

    def history(self) -> list[dict[str, Any]]:
        return self._load()["versions"]

    def get(self, vid: str) -> PlaybookVersion:
        meta = next((v for v in self.history() if v["id"] == vid), None)
        if meta is None:
            raise KeyError(f"unknown playbook version {vid!r}")
        return PlaybookVersion(vid, (self.root / f"{vid}.md").read_text().strip(), meta)

    def active(self) -> PlaybookVersion:
        return self.get(self._load()["active"])

    def seed(self) -> PlaybookVersion:
        return self.get(self.history()[0]["id"])

    def propose(self, text: str, parent: str | None, author: str) -> str:
        if author not in AUTHORS:
            raise ValueError(f"author must be one of {AUTHORS}")
        text = text.strip()
        reasons = validate_text(text)
        if reasons:
            raise PlaybookError(reasons)
        data = self._load()
        vid = f"v{len(data['versions']) + 1:03d}"
        (self.root / f"{vid}.md").write_text(text + ("\n" if text else ""))
        data["versions"].append({"id": vid, "parent": parent, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "author": author, "hash": text_hash(text), "chars": len(text),
                                 "status": "candidate", "dev_score": None, "reason": ""})
        self._save(data)
        return vid

    def decide(self, vid: str, keep: bool, scores: dict[str, Any], reason: str) -> None:
        data = self._load()
        entry = next((v for v in data["versions"] if v["id"] == vid), None)
        if entry is None:
            raise KeyError(f"unknown playbook version {vid!r}")
        if entry["status"] != "candidate":
            raise ValueError(f"{vid} is {entry['status']}, only candidates can be decided")
        entry["dev_score"], entry["reason"] = scores, reason
        entry["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if keep:
            for v in data["versions"]:
                if v["status"] == "active":
                    v["status"] = "retired"
            entry["status"], data["active"] = "active", vid
        else:
            entry["status"] = "rejected"
        self._save(data)
