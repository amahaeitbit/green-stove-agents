# Environment correctness handoff to Isaac

Abel's environment branch now contains your agent code through `1c5ef6f` plus an
independent admission verifier. Merge `environment-evidence` into `agents-memory`
before changing the hooks below. No agent-owned implementation files were edited.

## Implemented on Abel's side

- `greenstove/admission_verifier.py`: independent replay of the full day, including
  zero-future-solar admission, outstanding sessions, essential demand, discharge/charge
  limits and efficiencies, delivered cooking, battery balance and completion. It never
  calls `can_start`, `advance_interval` or the simulator to verify their outputs.
- Full plan validation: tariff/quote provenance, household authority, windows/budgets,
  duplicate request/reservation IDs, unserved partition and pending capacity holds.
- Trace/config/phase identities, separate authoritative actual PV, requirements and
  BatterySpec matching. A different requirements snapshot raises ContractError; explicitly
  re-simulate the candidate under the new fixed requirements instead of replacing evidence.
- Informational UNSERVED_* remains informational. Prevented starts use ADMISSION_REFUSED
  with a reason, rather than implying an actual inverter or reserve violation occurred.
- `configuration()` and `configuration_hash()` include BatterySpec, policy version,
  simulator version, independent verifier version and optional fixed requirements.
- `greenstove/admission_world.py`: a bridge inheriting your existing day simulation but
  using the independent verifier. It captures actual inputs separately, in application
  memory, so the verifier never takes actual PV from an untrusted trace. Saved traces
  can also be verified directly by passing the corresponding ActualScenario explicitly.
- Tests include the six-completions/zero-energy attack, 15 development/phase combinations,
  essential shortages, forged records, compatibility hashes, detached inputs and a real
  supervisor run with a deterministic mock proposal reaching actual execution.

## Isaac: two small hooks before the joint merge

1. In `greenstove/env_protocol.py`, change only the import in the `abel-admission`
   branch of `load_environment()`:

```python
from greenstove.admission_world import AdmissionWorld
return AdmissionWorld()
```

   This replaces the import from `world_adapter`. Keep the original class in
   world_adapter: the independent bridge uses it for simulation. Do not make the
   original world_adapter class import/subclass the bridge (that creates a cycle).

2. In `cli.admission_variant`, replace the literal policy-name hash with the fingerprint
   from the base context, before changing that context's config_hash:

```python
from greenstove.admission_verifier import configuration_hash
from greenstove.world_adapter import battery_spec
from greenstove.scenarios import DEFAULT_REQUIREMENTS

fingerprint = configuration_hash(ctx, battery_spec(ctx), DEFAULT_REQUIREMENTS)
# In the existing model_copy update:
# "config_hash": fingerprint
```

   Use this same recipe for development, held-out and forecast contexts. Requirements
   are also a compatibility dimension: when using a different definition of done,
   apply the corresponding requirements fingerprint consistently or exclude those notes.
   Avoid hashing an already transformed admission context a second time. Include
   `env.configuration_record(base_context)` and the effective requirements/spec hash
   in the frozen manifest. Existing memory produced under the old literal salt should
   not be reused; regenerate development memory and freeze it again.

## Isaac: lifecycle/message items still yours

- Save phase traces in the evidence folder before verification, linked to verdict IDs.
- Emit only actual household-specific start/wait/completed events from an authoritative
  request-to-household map. Forecast admissions are predictions, not start commands.
- Finalize accepted sessions that never start as UNSERVED at the deadline/day end;
  preserve the original DEFER event. Pending offers need an explicit not-approved final
  outcome. No silent rebooking or authority change.
- The current bridge deliberately verifies the current version-1 trace format. If you
  change outcomes or add terminal events, coordinate the trace contract/version and
  update both simulation and independent replay. Do not bypass verification to pass tests.
- Teach feedback consumers that ADMISSION_REFUSED means a start was prevented; consult
  its reason to distinguish energy, power or authority. It is not a physical reserve trip.
- Emit canonical `models.tariff_version(plan.tariff)` in new traces. The verifier accepts
  both the legacy version-1 and canonical tariff recipes during this handoff.

## Joint acceptance

Validation of this increment: **273 pytest tests passed (21 subtests), 102 unittest
tests passed**, and admission_check.py, allocator_check.py and c0_check.py passed.
The new tests exercise the bridge through the real supervisor with mock inference,
including actual execution; the full default CLI switch still needs the hook above.

Run both test suites and all checkers, then a mock e2e using `GREENSTOVE_WORLD=abel-admission`.
Confirm actual verdicts identify `independent-admission-1`, not `admission-glue-1`.
The default CLI remains on the old glue until Isaac applies the import hook above.
Do not treat green tests of the new verifier as proof that the default CLI has switched.

Run the hand-calculated step-92 fixture through the actual event path after message
routing is connected. Existing admission_check.py continues to prove the environment's
fixture arithmetic; it does not prove household message delivery.

These fixes do not establish calibrated village behavior, live device control, live
partner-tool operation or a memory advantage. C0 simulator/verifier and fixtures remain
power-only and unchanged.
