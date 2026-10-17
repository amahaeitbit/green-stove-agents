"""Crusoe tooling: model catalog, model benchmark for the Pricing Agent, usage and cost report.

Model choice is measured, not assumed (PLAN.md B1): `bench` runs the same fixed pricing task
on each candidate model and reports valid-output rate, latency, tokens and cost. The chosen
model is then pinned for A/B/C. Console metrics: https://console.crusoecloud.com/foundry/metrics
"""
from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

from greenstove import pricing_agent
from greenstove.agent_models import Brief, CallRecord
from greenstove.llm import LLMClient, ModelError, parse_pricing


def catalog_rows(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for m in sorted(models, key=lambda m: (("nemotron" not in m.get("id", "").lower()), m.get("id", ""))):
        pin, pout = parse_pricing(m)
        arch = m.get("architecture") or {}
        rows.append({"id": m.get("id"), "nemotron": "nemotron" in m.get("id", "").lower(),
                     "modality": arch.get("modality") or m.get("modality") or "",
                     "context": m.get("context_length") or m.get("max_model_len") or "",
                     "supported": ", ".join(m.get("supported_parameters") or [])[:80],
                     "usd_per_mtok_in": round(pin * 1e6, 4) if pin is not None else None,
                     "usd_per_mtok_out": round(pout * 1e6, 4) if pout is not None else None})
    return rows


def save_catalog(models: list[dict[str, Any]], root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "models.json"
    path.write_text(json.dumps({"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "data": models}, indent=1))
    return path


def bench(llm: LLMClient, model_ids: list[str], context, n: int = 3) -> list[dict[str, Any]]:
    """Same pricing payload (brain off, no feedback) for each model; every call counts."""
    rows = []
    for mid in model_ids:
        client = LLMClient(replace(llm.s, role_models={**llm.s.role_models, "pricing": mid}, max_calls=n * 4),
                           llm.mock_handlers, usage_path=llm.usage_path, transport=llm.transport, catalog=llm.catalog)
        valid, errors = 0, []
        for i in range(n):
            try:
                prop, errs, _ = pricing_agent.propose(context, Brief(role="pricing", mode="off"), [], client, 1, f"bench-{i}")
            except ModelError as exc:
                errors.append(str(exc)[:80])
                continue
            valid += prop is not None
            errors += errs[:1]
        calls = [c for c in client.calls if c.outcome != "budget"]
        ok = [c for c in calls if c.outcome in ("ok", "repaired")]
        costs = [c.cost_usd for c in calls if c.cost_usd is not None]
        rows.append({"model": mid, "valid": f"{valid}/{n}", "calls": len(calls),
                     "p50_latency_ms": int(statistics.median([c.latency_ms for c in ok])) if ok else None,
                     "mean_tokens": int(statistics.mean([c.prompt_tokens + c.completion_tokens for c in ok])) if ok else None,
                     "cached_tokens": sum(c.cached_tokens for c in calls),
                     "cost_usd": round(sum(costs), 6) if costs else None,
                     "response_format": next((c.response_format for c in ok if c.response_format), None),
                     "errors": errors[:2]})
    return rows


def usage_rows(path: Path) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[CallRecord]] = defaultdict(list)
    for line in path.read_text().splitlines():
        if line.strip():
            c = CallRecord.model_validate_json(line)
            groups[(c.llm_mode, c.role, c.model)].append(c)
    rows = []
    for (mode, role, model), cs in sorted(groups.items()):
        lat = sorted(c.latency_ms for c in cs if c.outcome in ("ok", "repaired", "invalid"))
        costs = [c.cost_usd for c in cs if c.cost_usd is not None]
        out = defaultdict(int)
        for c in cs:
            out[c.outcome] += 1
        rows.append({"mode": mode, "role": role, "model": model, "calls": len(cs),
                     "outcomes": " ".join(f"{k}={v}" for k, v in sorted(out.items())),
                     "prompt_tokens": sum(c.prompt_tokens for c in cs), "completion_tokens": sum(c.completion_tokens for c in cs),
                     "cached_tokens": sum(c.cached_tokens for c in cs),
                     "cache_hit": round(sum(c.cached_tokens for c in cs) / max(1, sum(c.prompt_tokens for c in cs)), 3),
                     "p50_ms": lat[len(lat) // 2] if lat else None,
                     "p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
                     "cost_usd": round(sum(costs), 6) if costs else None})
    return rows
