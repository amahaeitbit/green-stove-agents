# Response to Abel's integration checkpoint review

From Isaac (partner), against Abel's `docs/integration-checkpoint-review.md` (which reviewed
`agents-memory@8ee143f`). Current branch: `agents-memory` (this commit). Everything below was
run on the merged branch, which contains all of `environment-evidence` through `b6d8689`.

## Commands and results (requested)

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q` (includes your 22 `test_admission_independent` tests) | 306 passed, 21 subtests passed |
| `python -m unittest discover -s tests` | Ran 102 tests, OK |
| `scripts/c0_check.py` · `allocator_check.py` · `admission_check.py` | C0 OK · ALLOCATOR OK · ADMISSION OK |
| `gsa acceptance` (your joint acceptance run, through the app) | ACCEPTANCE PASS, 7/7 conditions |
| `gsa e2e --mode mock` (stub world) | 19/19 steps ok · 458 simulations checked · 0 unmodeled phases |
| `GREENSTOVE_WORLD=abel gsa e2e --mode mock` | 19/19 ok · 231 checked · 74 phases your C0 world cannot model (labeled partial) |
| `GREENSTOVE_WORLD=abel-admission gsa e2e --mode mock` (after `ca42c1a`: your independent verifier) | 19/19 ok · 375 checked · 0 unmodeled phases · 0 verifier mismatches |

All runs are mock-model runs (labeled MOCK everywhere); no live Crusoe key yet.

## Item by item

| Item | Status | Where |
| --- | --- | --- |
| A1 admission not wired | **Wired.** `GREENSTOVE_WORLD=abel-admission` runs `can_start` at each booked start and `advance_interval` every interval, one ledger per phase, single owner (the world's day loop) | `greenstove/world_adapter.py` `AdmissionWorld` |
| A1 `execute_day` (yours) | Not yet in `admission.py`; when it lands, `AdmissionWorld.simulate` calls it once instead of its own loop | — |
| A2 downside/actual unreachable | **Option (a), now independently verified:** your ledger runs for expected, downside and actual PV in the partner simulation (`admission-glue-1`), and since your `ca42c1a` the `abel-admission` world is your bridge `greenstove/admission_world.py`, so every verdict comes from your `admission_verifier.py` replay (never from the simulator's own numbers). Your two hooks are applied: `env_protocol` imports your bridge; `cli.admission_variant` uses your `configuration_hash(ctx, battery_spec(ctx), DEFAULT_REQUIREMENTS)` on the base context | `admission_world.py`, `admission_verifier.py` |
| A3 per-household routing | **Done.** `household_outcomes(context, plan, trace)` from `context.requests`, one message per household, own data only (tested) | `greenstove/household_messages.py` |
| A4 shift limit dropped | **Fixed** in both worlds: `allocate(..., max_shift_hours=requirements.max_shift_hours)` | `world_adapter.py` |
| A5 acceptance fixture | Yours; meanwhile `gsa acceptance` reproduces your hand-calculated run from `fixtures/admission/commitment-example.json` (efficiency 1.0, start step 92) | `greenstove/acceptance.py` |
| B1 stale snapshots | One loop carries the state; a duplicate booking is refused; tested | `tests/test_acceptance.py` |
| B2 DEFER final outcome | **No automatic retiming by default:** re-offer off reproduces `admission-glue-1` exactly (DEFER = not started, never retimed), which your verifier replays. The opt-in re-offer (`GREENSTOVE_REOFFER=1`) emits `admission-glue-2-reoffer`, which your verifier correctly refuses until you approve it | `AdmissionWorld` |
| B3 BatterySpec in identity | Partner side: the admission scenarios' station hash includes the BatterySpec. Your scenario config/hash and manifest: yours | `cli.admission_variant` |
| B4 stub behind contract | **Fixed:** two-pass order (auto-book first), only `UNSERVED_CAPACITY` / `UNSERVED_BUDGET`; duplicate request ids raise | `greenstove/stub_env.py` |
| B5 memory sees plans only | **Fixed:** `EvidenceRecord.outcomes` (per request: completed / deferred / unserved / pending / reoffered) from the trace; admitted, deferred and pending counts in metrics; Note-writer input includes them | `supervisor._outcomes`, `note_writer.summarize` |
| B6 essential load dropped | Kept on `abel-admission` (only the C0 world uses the essential-free variant, labeled) | — |
| B7 stale docs | env_protocol docstring and end-to-end status updated; `checkpoints.md` sign-offs are for both of us | — |

## Also added since your review

- `gsa compare`: six-condition table (A/B/C plus D last tariff, E nearest-context reuse, F3/Ffull
  search) on the same frozen evidence, requirements and verifier. Partner implementation of your
  baselines for review; it reports honestly when search beats the agents.
- `gsa report`: results page from an e2e run; mock copy in `docs/evidence/results-mock.md`.
- Spec Agent (definition of done may only tighten; frozen for A/B/C), drift detection and a
  decision trail, `gsa tracks` scorecard (17/17 in mock).

## What we still need from you

1. Confirm the BatterySpec numbers (power = inverter kW, efficiencies 0.95 are placeholders);
   the partner's own `AdmissionWorld.verify` is no longer used on `abel-admission`.
2. Decide on the opt-in re-offer (keep off, or approve).
3. `execute_day` when ready; A5 fixture; BatterySpec in your station config.
4. Sign `docs/checkpoints.md` with Isaac; then merge to `main`.

## Merge to main

Merged `agents-memory` into `main` on 2026-10-17 at about 15:40 PT at Isaac's direction (Isaac
reports that Abel agreed). The `docs/checkpoints.md` sign-off lines are still for both of you to
fill in; they were not signed on anyone's behalf.
