"""Partner glue over Abel's admission ledger (GREENSTOVE_WORLD=abel-admission)."""
from greenstove.scenarios import DEFAULT_REQUIREMENTS, actual_for, lattice_tariffs, scenario
from greenstove.world_adapter import AdmissionWorld

W = AdmissionWorld(DEFAULT_REQUIREMENTS, reoffer=False)       # default: no automatic retiming


def check(name, tariff):
    c = scenario(name)
    p = W.allocate(c.requests, tariff, c, "p1")
    return c, p, [W.verify(W.simulate(p, c, ph), DEFAULT_REQUIREMENTS, c, p)
                  for ph in ("forecast_expected", "forecast_downside")]


def test_some_tariff_passes_every_phase_with_complete_coverage():
    c = scenario("dev-clear-full")
    passing = [t for t in lattice_tariffs(c) if all(v.passed for v in check("dev-clear-full", t)[2])]
    assert passing
    _, p, vs = check("dev-clear-full", passing[0])
    assert all(v.coverage.complete and "pending sign-off" in v.coverage.scope for v in vs)
    actual = W.verify(W.simulate(p, c, "actual", actual_for("dev-clear-full")), DEFAULT_REQUIREMENTS, c, p)
    assert actual.phase == "actual" and "min_battery_kwh" in actual.metrics


def test_refused_starts_become_feedback_and_never_breach_the_floor():
    c, p, vs = check("dev-overcast-low-battery", {"standard": 8.0, "sun": 4.0, "peak": 13.0})
    codes = {f.code for v in vs for f in v.feedback_codes}
    assert not all(v.passed for v in vs) and "BATTERY_RESERVE" in codes
    for v in vs:
        assert v.metrics["min_battery_kwh"] >= c.reserve_kwh - 1e-6          # admission protects the floor


def test_deterministic_and_predawn_infeasibility_proof():
    a = check("dev-clear-full", {"standard": 8.0, "sun": 4.0, "peak": 13.0})[2]
    b = check("dev-clear-full", {"standard": 8.0, "sun": 4.0, "peak": 13.0})[2]
    assert [v.run_id for v in a] == [v.run_id for v in b]
    proof = W.lower_bound(scenario("ho-clear-low-battery"), DEFAULT_REQUIREMENTS)
    assert proof is not None and proof.lower_bound_kwh > proof.available_kwh
    assert W.lower_bound(scenario("dev-clear-full"), DEFAULT_REQUIREMENTS) is None


def test_review_fixes_feedback_requirements_and_world_name(monkeypatch):
    from greenstove.env_protocol import Requirements, load_environment
    c, p, vs = check("dev-overcast-low-battery", {"standard": 8.0, "sun": 4.0, "peak": 13.0})
    for v in vs:                                   # pending approvals are not refusals; zero kWh keeps its limit
        assert all(f.code != "APPROVAL_REQUIRED" for f in v.feedback_codes)
        assert all(f.limit is not None for f in v.feedback_codes if f.code == "BATTERY_RESERVE")
        assert "pending_approval_sessions" in v.metrics
    strict = Requirements(min_service_fraction=0.75, max_shift_hours=1.0)
    trace = W.simulate(p, c, "forecast_expected")    # simulated under other requirements
    v = W.verify(trace, strict, c, p)
    assert v.requirements == strict and v.conditions                 # re-simulated under the given rules
    monkeypatch.setenv("GREENSTOVE_WORLD", "abel_admission")
    import pytest
    with pytest.raises(ValueError):
        load_environment()


def test_admission_variant_has_its_own_hash(monkeypatch):
    from greenstove import cli
    monkeypatch.setenv("GREENSTOVE_WORLD", "abel-admission")
    a, base = cli.context_for("dev-clear-full"), scenario("dev-clear-full")
    assert a.config_hash != base.config_hash and a.scenario_id.endswith("-adm")


# ---- re-offer after DEFER (opt-in GREENSTOVE_REOFFER=1, pending Abel's approval) --------------------------
from greenstove import billing                                       # noqa: E402
from greenstove.env_protocol import Requirements, Reservation        # noqa: E402
from greenstove.world_adapter import MAX_REOFFERS, REOFFER_LABEL, next_offer   # noqa: E402

ON = AdmissionWorld(DEFAULT_REQUIREMENTS, reoffer=True)      # never verified under other rules here
# Found by brute force over the dev lattice: hh-02's booked breakfast start (step 40) is DEFERred for
# stored energy; morning PV makes a later start admissible within its window and budget.
CASE = ("dev-rigid-high-demand", {"standard": 8.0, "sun": 3.0, "peak": 10.0}, "forecast_expected")


def _case(reoffer):
    world, (name, tariff, phase) = AdmissionWorld(DEFAULT_REQUIREMENTS, reoffer=reoffer), CASE
    c = scenario(name)
    p = world.allocate(c.requests, tariff, c, "p1")
    trace = world.simulate(p, c, phase)
    return c, p, trace, world.verify(trace, DEFAULT_REQUIREMENTS, c, p)


def _ran(trace, plan):
    """Reservations (booked or re-offered) that completed, keyed by request."""
    pool = [*plan.reservations, *(Reservation(**x) for x in trace.extra["reoffer_reservations"])]
    return {r.request_id: r for r in pool if trace.reservation_outcomes.get(r.reservation_id) == "completed"}


def test_reoffer_is_opt_in_and_off_finalises_defer_as_unserved(monkeypatch):
    monkeypatch.delenv("GREENSTOVE_REOFFER", raising=False)
    assert AdmissionWorld().reoffer is False
    monkeypatch.setenv("GREENSTOVE_REOFFER", "1")
    assert AdmissionWorld().reoffer is True and REOFFER_LABEL in AdmissionWorld().name
    assert AdmissionWorld(reoffer=False).reoffer is False
    c, p, trace, v = _case(False)
    d = next(d for d in trace.extra["admission"] if d["request_id"] == "req-hh-02")
    # off = admission-glue-1, exactly what Abel's independent verifier replays: DEFER, not started, never retimed
    assert d["status"] == "DEFER" and d["reason"] == "INSUFFICIENT_STORED_ENERGY"
    assert trace.reservation_outcomes["p1:req-hh-02"] == "deferred" and trace.simulator_version == "admission-glue-1"
    assert not any(x["status"] == "REOFFER" for x in trace.extra["admission"]) and not trace.extra["reoffers"]
    assert not v.passed and v.metrics["reoffers"] == 0 and v.metrics["served_fraction"] == 0.625
    assert not next(x for x in v.conditions if x.name == "booked_sessions_admitted").passed
    assert REOFFER_LABEL not in v.coverage.scope and "pending sign-off" in v.coverage.scope
    assert trace.simulation_id != _case(True)[2].simulation_id                  # behaviour change, new identity


def test_deferred_start_is_rescued_by_a_repriced_reoffer():
    c, p, _, off = _case(False)
    _, _, trace, v = _case(True)
    orig = "p1:req-hh-02"
    chain = trace.extra["reoffers"][orig]
    assert trace.reservation_outcomes[orig] == "reoffered" and chain["final"] == "completed"
    winner = chain["completed_by"]
    assert winner.startswith(f"{orig}:reoffer") and trace.reservation_outcomes[winner] == "completed"
    offers = [d for d in trace.extra["admission"] if d["status"] == "REOFFER"]
    assert [d["reservation_id"] for d in offers] == chain["chain"] and all(d["label"] == REOFFER_LABEL for d in offers)
    assert all(d["start_step"] > d["step"] and d["quote"] <= d["max_cost"] for d in offers)   # recorded, not silent
    ran = _ran(trace, p)
    assert ran["req-hh-02"].reservation_id == winner and ran["req-hh-02"].start_step == offers[-1]["start_step"]
    # served fraction, spend and shift follow the reservation that actually ran
    assert v.passed and v.metrics["served_fraction"] == 0.75 > off.metrics["served_fraction"]
    assert v.metrics["rescued_by_reoffer"] == 1 and v.metrics["reoffers"] == len(offers)
    quotes = {q: billing.quote_for(r.power_kw, r.start_step, r.end_step, p.tariff, c) for q, r in ran.items()}
    assert quotes["req-hh-02"] == ran["req-hh-02"].locked_cost
    assert v.metrics["total_spend"] == round(sum(quotes.values()), 2)
    req = {q.request_id: q for q in c.requests}
    assert v.metrics["max_shift_hours"] == max(abs(r.start_step - req[k].preferred_start) * .25 for k, r in ran.items())
    assert next(x for x in v.conditions if x.name == "booked_sessions_admitted").passed
    fb = [f for f in v.feedback_codes if f.request_id == "req-hh-02"]
    assert [f.code for f in fb] == ["REOFFERED"] and "informational" in fb[0].message
    assert REOFFER_LABEL in v.coverage.scope and v.coverage.complete
    assert v.run_id == _case(True)[3].run_id                                    # deterministic


def test_next_offer_respects_window_shift_budget_and_authority():
    c, p, _, _ = _case(True)
    req = next(q for q in c.requests if q.request_id == "req-hh-02")
    booked = next(r for r in p.reservations if r.request_id == "req-hh-02")
    rules = DEFAULT_REQUIREMENTS
    new = next_offer(booked, booked.reservation_id, 1, req, p.tariff, c, rules)
    assert new.start_step == booked.start_step + 1 and new.end_step - new.start_step == req.duration_steps
    assert new.locked_cost == billing.quote_for(req.power_kw, new.start_step, new.end_step, p.tariff, c)
    assert new.accepted and new.tariff_version == booked.tariff_version and new.reservation_id.endswith(":reoffer1")
    assert next_offer(booked, booked.reservation_id, 1, req.model_copy(update={"auto_book": False}),
                      p.tariff, c, rules) is None                             # no authority: no re-offer
    assert next_offer(booked, booked.reservation_id, 1, req.model_copy(update={"max_cost": 0.0}),
                      p.tariff, c, rules) is None                             # every repriced quote over budget
    shift = abs(booked.start_step - req.preferred_start) * .25
    assert next_offer(booked, booked.reservation_id, 1, req, p.tariff, c,
                      Requirements(max_shift_hours=shift)) is None            # any later start shifts further
    last = booked.model_copy(update={"start_step": req.latest_finish - req.duration_steps,
                                     "end_step": req.latest_finish})
    assert next_offer(last, booked.reservation_id, 1, req, p.tariff, c, rules) is None   # window exhausted


def test_reoffers_never_dispatch_twice_breach_the_floor_or_break_household_rules():
    for name in ("dev-overcast-low-battery", "dev-rigid-high-demand", "dev-rainy-afternoon"):
        c = scenario(name)
        req = {q.request_id: q for q in c.requests}
        for tariff in lattice_tariffs(c)[::4]:
            p = ON.allocate(c.requests, tariff, c, "p1")
            for ph in ("forecast_expected", "forecast_downside"):
                trace = ON.simulate(p, c, ph)
                v = ON.verify(trace, DEFAULT_REQUIREMENTS, c, p)
                decisions = trace.extra["admission"]
                admits = [d["request_id"] for d in decisions if d["status"] == "ADMIT"]
                tried = [d["reservation_id"] for d in decisions if d["status"] != "REOFFER"]
                assert len(admits) == len(set(admits)) and len(tried) == len(set(tried))
                assert v.metrics["min_battery_kwh"] >= c.reserve_kwh - 1e-6
                booked = {r.reservation_id: r for r in p.reservations}
                for x in trace.extra["reoffer_reservations"]:
                    r, q = Reservation(**x), req[x["request_id"]]
                    orig = booked[r.reservation_id.rsplit(":reoffer", 1)[0]]
                    assert q.auto_book and r.accepted and r.tariff_version == orig.tariff_version
                    assert orig.start_step < r.start_step and r.end_step <= q.latest_finish
                    assert abs(r.start_step - q.preferred_start) * .25 <= DEFAULT_REQUIREMENTS.max_shift_hours
                    assert r.locked_cost == billing.quote_for(r.power_kw, r.start_step, r.end_step, p.tariff, c)
                    assert r.locked_cost <= q.max_cost
                for chain in trace.extra["reoffers"].values():
                    assert 1 <= len(chain["chain"]) <= MAX_REOFFERS
