# Crusoe in Green-Stove Agents

Every model call in the project goes through Crusoe Managed Inference
(`https://api.inference.crusoecloud.com/v1`, OpenAI-compatible, `Authorization: Bearer`).
This page lists the Crusoe features the agent layer uses, where, and how to show them.

## Features used

| Crusoe feature | Where | What it does for the project |
| --- | --- | --- |
| Serverless inference with open Nemotron models | `greenstove/llm.py` | Pricing Agent, Note-writer and forecast extractor all run on Nemotron; the same open weights could later run on a station controller |
| Model catalog `GET /v1/models` | `gsa models`, `preflight`, `llm.ensure_catalog()` | Exact case-sensitive ids, `supported_parameters` and pricing come from the live catalog, never from memory; saved to `data/crusoe/models.json` |
| Per-role model routing | `GREENSTOVE_MODEL_<ROLE>` | For example Super for pricing, Nano for notes and forecast extraction. Routing is identical across conditions A/B/C, so the memory comparison stays fair |
| Structured output (`response_format`) | `llm._response_format` | `json_schema` when the catalog lists structured outputs, else `json_object`, else plain text; a model that rejects it falls back once and is remembered. Every answer is still validated by pydantic |
| Thinking control (`chat_template_kwargs.enable_thinking`) | `GREENSTOVE_THINKING_<ROLE>` | Off by default for JSON tasks so hidden reasoning cannot eat the output budget; can be turned on for the Pricing Agent (budget raised to 2048 tokens), reasoning tokens recorded |
| Prompt caching | message layout + `CallRecord.cached_tokens` | System prompt and stable facts come first, feedback and repairs last, so the second and third attempts share a byte-identical prefix; cached tokens are reported per call and per condition |
| Usage and pricing | `CallRecord.cost_usd`, `gsa usage`, A/B/C summary | Cost per call from catalog pricing (or an env override), cost per day per condition in `agent-summary.md`: the plan's "token cost per day" metric, measured |
| Rate limits (429/503, `x-ratelimit-remaining-*`) | `llm._send` | Exponential backoff with jitter; remaining requests and tokens recorded on each call; every retry counts against the run budget |
| Console metrics | https://console.crusoecloud.com/foundry/metrics | Cross-check of the local `usage.jsonl` totals after live runs |

## Commands

```bash
gsa models                                   # live catalog: Nemotron first, supported parameters, USD per 1M tokens
gsa bench --mode live --n 3                  # same Pricing Agent task on each Nemotron text model:
                                             #   valid outputs, p50 latency, tokens, cached tokens, cost
gsa usage                                    # calls, outcomes, tokens, cache hit rate, p50/p95 latency, cost by role and model
gsa preflight --mode crusoe                  # key, spend flag, model id in the catalog
```

Model choice follows PLAN.md B1: measure with `bench`, pin the winner in `GREENSTOVE_MODEL`
(and optional per-role models), and keep it fixed through warm-up, freeze and the A/B/C
evaluation. The pinned ids, thinking and structured-output settings are recorded in
`manifest-partner.json`.

## What to show judges

1. `gsa models`: the exact Nemotron ids served by Crusoe right now.
2. `gsa bench --mode live`: why this Nemotron model, by measurement.
3. The live demo: each call's request id, tokens and latency in the banner and records.
4. `gsa usage` after the run: total cost, cache hit rate and latency, then the same numbers in
   the Crusoe console.

## Limits

- No live Crusoe call has been made from this repository yet (no key at the time of writing);
  the live path is tested against a simulated endpoint (`tests/test_crusoe_live.py`).
- Catalog pricing units are read as USD per token, or per 1M tokens when the value is above
  0.01; set `GREENSTOVE_PRICE_IN_PER_MTOK`/`OUT` if the catalog format differs.
- Cached-token discounts are not modeled in `cost_usd`; cached tokens are reported separately.
