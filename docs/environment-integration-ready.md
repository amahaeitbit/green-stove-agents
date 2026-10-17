# Reconciled environment and agent integration

Updated after merging Isaac's `agents-memory@3c86604` into Abel's environment branch.
This supersedes the previous instructions to apply two loader/fingerprint hooks: those
hooks are now implemented. Agent-owned integration files were updated as part of Abel's
explicit request to reconcile the two implementations.

## What is now connected

- `GREENSTOVE_WORLD=abel-admission` selects `admission_world.AdmissionWorld`, with
  `independent-admission-2` verification and `admission-execution-2` simulation identities.
- Normal simulation and the acceptance rehearsal use Isaac's same `acceptance.execute`
  ledger loop. Independent verification reconstructs that loop without calling it.
- The verifier supports an explicitly supplied start step for the last-hour fixture,
  while normal days start at zero. A late start cannot be inferred from untrusted metadata.
- Accepted sessions deferred for energy are finalized as unserved at their last valid
  start, with no automatic retiming or authorization changes. Pending offers remain
  approval-needed and are never admitted or counted as delivered.
- The supervisor saves expected/downside/actual traces, verdicts, final household notices
  and actual-execution event records. Events are reconstructed from verified decisions
  and outcomes, rather than trusted from arbitrary trace metadata. Each event includes
  only its recipient's own information. These are in-process simulated notices, not a
  broker, network delivery, calendar integration or real device commands.
- Contract inconsistencies now stop supervisor/comparator execution. Only explicit
  UNSUPPORTED_PHASE/UNSUPPORTED_PROFILE errors are labeled as unmodeled coverage.
- Memory configuration fingerprints include the battery specification and policy,
  simulator and verifier versions. The CLI manifest records the admission configuration
  and the actual scenario configuration hash. The definition of done/effective requirements
  remain explicit in the frozen manifest; do not mix memory across different definitions.
- The mock pricing adapter understands the informational UNSERVED_* and ADMISSION_REFUSED
  signals. Preventing a start does not report an actual inverter or battery-floor trip.

## Validation

Final full pytest suite: **308 passed, 21 subtests passed**. This includes regressions
for the actual loader, private execution events, terminal defer outcomes, saved traces,
independent acceptance verification and refusing to downgrade tampered evidence.
The 102-test unittest suite, all three fixture checkers and the acceptance CLI also passed.

The full mock e2e with two A/B/C evaluation days completed, including compare, acceptance
and report. The summary counted 443 nested simulations and zero unmodeled phases. Those
counts include nested track exercises; they are pipeline evidence, not a model-quality
measurement. All 40 verdict files in the top-level development evidence folder identified
`independent-admission-2`. The hand-calculated acceptance fixture passed every condition
and deliberately returned NO_VERIFIED_PLAN because its service target was not met.

Run the current suite and checkers:

```bash
python -m pytest -q
python -m unittest discover -s tests -q
python scripts/admission_check.py
python scripts/allocator_check.py
python scripts/c0_check.py
GREENSTOVE_WORLD=abel-admission python -m greenstove acceptance
GREENSTOVE_WORLD=abel-admission python -m greenstove e2e --mode mock --days 2
```

## Next joint action

Isaac should merge `origin/environment-evidence` into `agents-memory`, run the checks,
and inspect one actual trace, household outbox and independent verdict together with Abel.
Then merge the combined branch into main. No force push or separate cherry-picking of
shared files is needed. Human checkpoint sign-offs have not been fabricated.

Regenerate development memory and freeze it again: fingerprints and verdict identities
changed. Do not compare the old recorded mock results page with a new run as if they shared
the same environment. Official held-out evaluation must use frozen settings and fresh
records. Live Crusoe/Nemotron evidence, partner integrations and a memory advantage remain
separate claims requiring their own evidence. Original C0 fixtures stay power-only.
