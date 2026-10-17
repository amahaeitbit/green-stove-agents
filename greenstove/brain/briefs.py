"""Deterministic brief compiler: curated Shared Brain, raw history, or no memory.

All three modes share one token estimate and one budget. Brief.entry_ids lists the
entries actually supplied in the text, not what a model later claims to cite.
"""
from __future__ import annotations

import math

from greenstove.agent_models import ApprovedObservation, Brief, EvidenceRecord, stable_hash
from greenstove.brain.store import is_current, snapshot_hash_of

# Fixed situation distance for curated ranking (frozen before evaluation):
#   WEATHER_MISMATCH if weather_class differs, plus for each key
#   WEIGHTS[k] * |a - b| / SCALES[k]; keys in RELATIVE first divide |a - b| by max(1, |query|).
#   A descriptor the query has but the entry lacks adds MISSING_PENALTY. Ties break by id.
WEATHER_MISMATCH = 1.0
WEIGHTS = {"battery_frac": 1.0, "demand_kwh": 1.0, "forecast_pv_kwh": 0.5, "flex_hours": 0.5}
SCALES = {"battery_frac": 0.3, "demand_kwh": 0.3, "forecast_pv_kwh": 0.3, "flex_hours": 2.0}
RELATIVE = {"demand_kwh", "forecast_pv_kwh"}
MISSING_PENALTY = 1.0

CURATED_LEGEND = "(id version | situation | prices | phase result codes | outcome | support; forecast != actual)"
CURATED_HINT = ("Use: entries are closest situation first. Prefer prices that PASSED in a similar situation; "
                "avoid or adjust prices that FAILED there, guided by their codes. Cite the ids you rely on.")
HISTORY_LEGEND = "(run day phase scenario | situation | prices | result codes | metrics)"


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def _f(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def situation_distance(query: dict, other: dict) -> float:
    d = 0.0 if query.get("weather_class") == other.get("weather_class") else WEATHER_MISMATCH
    for key, weight in WEIGHTS.items():
        a, b = _f(query.get(key)), _f(other.get(key))
        if a is None:
            continue
        if b is None:
            d += MISSING_PENALTY
            continue
        delta = abs(a - b) / (max(1.0, abs(a)) if key in RELATIVE else 1.0)
        d += weight * delta / SCALES[key]
    return round(d, 9)


def _fmt(v, spec: str) -> str:
    x = _f(v)
    return "?" if x is None else format(x, spec)


def _situation_text(s: dict) -> str:
    return (f"{s.get('weather_class', 'unknown')} batt={_fmt(s.get('battery_frac'), '.2f')} "
            f"demand={_fmt(s.get('demand_kwh'), '.1f')}kWh pv={_fmt(s.get('forecast_pv_kwh'), '.1f')}kWh "
            f"flex={_fmt(s.get('flex_hours'), '.1f')}h")


def _prices_text(tariff: dict[str, float]) -> str:
    return " ".join(f"{k}={v:g}" for k, v in sorted(tariff.items(), key=lambda kv: (kv[1], kv[0])))


def render_observation(e: ApprovedObservation) -> str:
    runs = e.confirmations + 1
    m = e.metrics
    outcome = " ".join(x for x in (
        f"served {m['served_fraction']:.2f}" if "served_fraction" in m else "",
        f"min battery {m['min_battery_kwh']:.2f}kWh" if "min_battery_kwh" in m else "",
        f"max shift {m['max_shift_hours']:.1f}h" if "max_shift_hours" in m else "") if x) or "-"
    support = f"evidence {runs} run{'s' if runs != 1 else ''}" + (f", confirmed {e.confirmations}x" if e.confirmations else "")
    line = (f"{e.id} v{e.version} | {_situation_text(e.situation)} | prices {_prices_text(e.tariff)} | "
            f"{e.phase} {'PASS' if e.outcome_passed else 'FAIL'} {','.join(e.feedback_codes) or '-'} | "
            f"{outcome} | {support}")
    return line + (f" | conflict {e.conflict_group}" if e.conflict_group else "")


def render_record(r: EvidenceRecord) -> str:
    metrics = " ".join(f"{k}={v:g}" for k, v in sorted(r.metrics.items()))
    return (f"{r.run_id} day={r.day} {r.phase} {r.scenario_id} | {_situation_text(r.situation)} | "
            f"prices {_prices_text(r.tariff)} | {'PASS' if r.passed else 'FAIL'} "
            f"{','.join(sorted(r.feedback_codes)) or '-'} | {metrics}")


def _fill(head: list[str], lines: list[str], budget: int, empty: str) -> tuple[str, int]:
    """Header plus as many ranked lines as fit; stops at the first line that would overflow."""
    text = "\n".join(head)
    if estimate_tokens(text) > budget:
        return "", 0
    used = 0
    for line in lines:
        cand = text + "\n" + line
        if estimate_tokens(cand) > budget:
            break
        text, used = cand, used + 1
    if not lines and estimate_tokens(text + "\n" + empty) <= budget:
        text += "\n" + empty
    return text, used


def compile_brief(role: str, situation: dict, store_entries: list[ApprovedObservation],
                  token_budget: int = 600, *, config_hash: str, clock: int) -> Brief:
    """Curated brief: servable entries ranked by fixed situation distance, within budget."""
    snap = snapshot_hash_of(store_entries)
    pool = sorted((e for e in store_entries if is_current(e, config_hash, clock)),
                  key=lambda e: (situation_distance(situation, e.situation), e.id))
    head = [f"SHARED BRAIN (curated, snapshot {snap[:8]})", CURATED_LEGEND, CURATED_HINT]
    text, n = _fill(head, [render_observation(e) for e in pool], token_budget,
                    "(no approved observations match this configuration)")
    chosen = pool[:n]
    return Brief(role=role, mode="curated", entry_ids=[e.id for e in chosen],
                 versions={e.id: e.version for e in chosen}, text=text,
                 tokens=estimate_tokens(text), budget=token_budget, snapshot_hash=snap)


def compile_history_brief(role: str, records: list[EvidenceRecord], token_budget: int = 600, *,
                          config_hash: str, clock: int) -> Brief:
    """Raw-history brief: past same-config records, newest first (day desc, run_id), truncated."""
    snap = stable_hash(sorted((r.model_dump(mode="json") for r in records), key=lambda d: d["run_id"]))
    pool = sorted((r for r in records if r.config_hash == config_hash and r.day < clock),
                  key=lambda r: (-r.day, r.run_id))
    head = [f"RAW HISTORY (newest first, evidence {snap[:8]})", HISTORY_LEGEND]
    text, n = _fill(head, [render_record(r) for r in pool], token_budget,
                    "(no past records for this configuration)")
    return Brief(role=role, mode="history", entry_ids=[r.run_id for r in pool[:n]], text=text,
                 tokens=estimate_tokens(text), budget=token_budget, snapshot_hash=snap)


def empty_brief(role: str) -> Brief:
    return Brief(role=role, mode="off")
