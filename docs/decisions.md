# Partner decisions: reusing the built agents against the team diagram

Source: the team's "Person B (Partner): agents + memory" diagram, PLAN.md section 7 and
docs/contracts.md. The agents built on `agents-memory` are reused as they are; this page
records where they already match the diagram, what changed to match it, and what is left
for Abel to confirm. Status: decided by the partner, pending Abel's sign-off at C2.

## What the agents decide each day

```
facts + household requests + brief (A none · B raw history · C approved observations)
        │
        ▼
Pricing Agent (Nemotron) ── 3 prices from the allowed list, cites brief entries
        │  gate: every price in the lattice, tier order, all windows; citations outside the brief are dropped
        ▼
households choose → allocator books → simulator + verifier on expected AND downside forecasts
        │
        ├─ both pass ──────────► PLAN_VERIFIED → actual simulated day → EXECUTION_COMPLETE (pass/fail)
        ├─ fail ───────────────► feedback codes → next attempt (max 3)
        ├─ same tariff again ──► NO_VERIFIED_PLAN (repeated proposal)
        ├─ 3 attempts used ────► NO_VERIFIED_PLAN (budget)
        ├─ energy bound ───────► INFEASIBLE before any model call (deterministic proof)
        └─ model/network/budget► ERROR (never reported as infeasible or as a pass)
        │
        ▼
decision.md for operators · evidence in runs/<run_id>/ · Note-writer → Keeper → approved.jsonl (development only)
```

`gsa decide --scenario <name> --condition C` prints the operator decision record:
today's tariff and why, the memory relied on, both forecast checks recomputed by code,
bookings, households that still have to confirm, unserved requests, rejected
alternatives, cost, and what the model was not allowed to decide.

## Diagram element by element

| Diagram | Built | Decision |
| --- | --- | --- |
| `cli.py / demo.py`: `gsa run --mode mock\|live --condition A\|B\|C` | `gsa run`, `gsa decide`, `--mode live` (alias of `crusoe`), plus `plan`, `warmup`, `freeze`, `eval-agents`, `e2e`, `preflight`, `brain` | Matched; extra commands kept for the end-to-end run |
| `gsa demo replay` | `gsa demo --replay latest\|<file>`, labeled RECORDED | Matched |
| `models.py / contracts.md` (Abel owns PlanningFacts, TariffProposal, Observation, NoteProposal) | Provisional copies in `env_protocol.py`; `NoteProposal` alias added | Switch to Abel's `models.py` when it lands; partner requests changes, never edits |
| `supervisor.py`: ≤3 attempts, repeat stop, feedback list, `status.json` | Same, plus `ERROR` kept separate | Matched; `status.json` written per run |
| INFEASIBLE comes from Abel's bound | `env.lower_bound` is called first; the stub's bound is a stand-in | Matched; Abel's bound replaces the stub's |
| `runs/<run_id>/`: requests, tariff_N, reservations_N, verdict_N, status | Same files, plus `verdict_actual`, `run.json`, `decision.md` | Matched; the Keeper only trusts these records |
| `allocator.py + verifier.py`: identical in all conditions | Called through one protocol; same functions for A/B/C | Matched (Abel's code plugs in through `world_adapter.py`) |
| `pricing_agent.py`: gate prices ∈ lattice, cites ⊆ brief | Lattice, tier order and window coverage are validation errors; citations outside the brief are dropped and not counted as memory use | Decided: dropping keeps a good tariff from wasting an attempt; ask Abel if he prefers a hard reject |
| `note_writer.py`: NoteProposal {situation, tariff, phase, claimed_result, evidence[], duplicates, conflicts}; dev only, eval → quarantine | Added `claimed_tariff`, `claimed_phase`, `claimed_passed` to the proposal; dev only; evaluation output quarantined or disabled | Matched |
| `llm.py`: pinned model, mock\|live, timeout, JSON schema, 1 repair retry, call + token budgets, `usage.jsonl` (no keys, prompt hash) | Same; usage lines written once the outcome is final | Matched |
| Keeper order: evidence_unknown → evidence_expired (config hash) → result_mismatch → scope → constraint_edit → duplicate → conflict; first failure rejects with that code | Same order; `AdmissionResult.code` is the first failing stage, details follow | Matched. A duplicate does not create a new entry; independent new evidence confirms the existing one (version and expiry), code `duplicate` |
| `store.py`: `approved.jsonl` (cap 20, successes + failures, expiry), `audit.jsonl` (never served), frozen at C4 | Same files, plus `meta.json` (frozen flag, snapshot hash) | Matched |
| `briefs.py`: deterministic match on weather class, SOC bucket, demand, flexibility, config hash; 600-token cap; logs ids/versions; A [] · B raw rows · C approved obs | Same; distance weights are constants in `briefs.py` | Matched |
| Crusoe: same model across A/B/C | Model per role from `.env` (default one model for all roles), chosen with `gsa bench`, recorded in every call and in the manifest; catalog features (structured output, thinking, caching, pricing) used as described in [crusoe.md](crusoe.md) | Matched; routing never differs between A, B and C |
| Stretch: NemoClaw / OpenShell sandbox | Not built | Only after C3 passes, per PLAN.md |
| You.com (PLAN.md: Abel, stretch) | Built by the partner at the user's request: `gsa forecast` (You.com Web Search → Nemotron extraction → verified quote → PV profile) and `--forecast` on `plan`/`run`/`decide` | Forecast records are inputs to a day, saved and frozen; evaluation never runs a live search; Abel to review |

## Extra decisions not in the diagram

- **A forecast pass needs both forecasts.** The Keeper rejects a "pass" note that cites
  only one forecast scenario (`result_mismatch`), matching PLAN_VERIFIED.
- **Conflicts are kept, not overwritten.** Same situation and tariff with opposite results
  are stored as two entries in one conflict group; recency is not proof.
- **Infeasibility is checked before spending calls.** A deterministic bound costs nothing
  and stops the day honestly.
- **The demo starts from a fresh memory.** What the Keeper admits in the memory scene is
  what the fresh agent reads in the handoff scene; `--dev-brain` uses warm-up memory.
- **Held-out runs rotate condition order** each day to reduce order effects, use frozen
  memory and history, and count stale entries served (must be 0).

## For Abel to confirm

1. Names in your `models.py`; I map them in the adapter.
2. Citations outside the brief: drop (current) or reject the proposal.
3. Feedback metric names (`served_fraction`, `min_battery_kwh`, …); I map them if yours
   differ.
4. Whether a `Plan` can carry `INVALID_TARIFF`; today the Pricing Agent validates before
   allocation, so the allocator never sees an invalid tariff.

## Weather forecast and solar generation (You.com)

```
gsa forecast --place "Asmara, Eritrea" --lat 15.3229 --lon 38.9251 --date 2026-10-18 --mode live
  You.com Web Search (POST ydc-index.io/v1/search, highlights) for the place and date
  → Nemotron on Crusoe extracts cloud cover %, rain chance %, condition, source_url, verbatim quote
  → code keeps a value only if the quote is in that source's retrieved text and every number is in the quote
  → PV profile: NOAA solar position, Haurwitz clear sky, Kasten-Czeplak cloud factor, 3 kWp × 0.8
  → downside: cloud +0.25 (rain ≥ 50%: lighter morning, heavier afternoon)
  → data/forecast/<place>-<date>-<mode>.json  →  gsa decide --forecast <file>
```

- You.com is a search service, not a weather provider: the record keeps the source url,
  page age, query and quote, and says `NO_VERIFIED_FORECAST` (conservative cloud 0.6) when
  nothing passes the check. Mock mode uses a fixture at `example.invalid`, labeled.
- A forecast supports an input assumption; it does not calibrate or validate the simulator.
- Only the place and date go to web search; no household data.
- Default site is a placeholder (Asmara); set the pilot's real coordinates with `--lat/--lon`.

## Spec Agent (Done Means Done)

PLAN.md says a Spec Agent is unnecessary for the Shared Brain MVP and that no relaxation can
turn a failed headline case into success. The event's Done Means Done track asks agents to
write their own definition of done, so the partner added one under Abel's rule: the agent's
checks are validated against the verifier's metric vocabulary and can only be stricter than
the committee's requirements (`code_tightened`, `code_added`). The definition is cached per
goal + committee + model and frozen into `manifest-partner.json`, so A, B and C use the same
one. `gsa run --no-spec` reproduces committee-only behavior. Abel to confirm.
