"""Environment-owned bridge: Isaac's day simulator plus independent verification.

env_protocol selects this class without changing the supervisor protocol.
Actual PV is captured from the caller before simulation, never recovered from a trace.
"""
from . import admission_verifier
from .models import ContractError, short_hash, tariff_version
from .world_adapter import AdmissionWorld as SimulationWorld, battery_spec


class AdmissionWorld(SimulationWorld):
    name = "abel-admission (Abel ledger, independently verified energy; synthetic simulation)"

    def __init__(self, requirements=None):
        super().__init__(requirements)
        self._actual_inputs = {}

    def simulate(self, plan, context, phase, actual=None):
        from .acceptance import execute
        if phase == "actual":
            if actual is None:
                raise ContractError("actual phase requires independently supplied ActualScenario")
            pv = actual.actual_pv_kw
        elif phase in ("forecast_expected", "forecast_downside") and actual is None:
            pv = context.forecast_pv_kw if phase == "forecast_expected" else context.downside_pv_kw
        else:
            raise ContractError(f"UNSUPPORTED_PHASE: {phase}")
        authoritative = actual.model_copy(deep=True) if actual is not None else None
        # Share the tested lifecycle loop with the acceptance command. The verifier
        # independently reconstructs it and never calls execute().
        run = execute(plan, context, battery_spec(context), pv_kw=pv, requirements=self.requirements)
        trace = run.trace
        trace.phase = phase
        trace.simulator_version = admission_verifier.SUPPORTED_SIMULATOR_VERSION
        trace.tariff_version = tariff_version(plan.tariff)
        trace.extra["start_step"] = 0
        trace.simulation_id = short_hash({"plan": plan.model_dump(mode="json"), "scenario": context.scenario_hash,
                                         "config": context.config_hash, "phase": phase, "pv": list(pv),
                                         "battery": battery_spec(context).model_dump(),
                                         "req": self.requirements.model_dump(),
                                         "v": trace.simulator_version, "start_step": 0}, 16)
        trace.extra["household_events"] = [
            {"step": e.step, "kind": e.kind, "request_id": e.request_id,
             "messages": [m.model_dump() for m in e.messages if m.request_id == e.request_id]}
            for e in run.events if e.request_id is not None]
        if phase == "actual":
            self._actual_inputs[trace.simulation_id] = authoritative
        return trace

    def verify(self, trace, requirements, context, plan):
        actual = None
        if trace.phase == "actual":
            actual = self._actual_inputs.get(trace.simulation_id)
            if actual is None:
                raise ContractError("no authoritative actual inputs for this simulation; replay with supplied ActualScenario")
        return admission_verifier.verify(trace, requirements, context, plan, battery_spec(context), actual=actual)

    def configuration_record(self, context):
        return admission_verifier.configuration(context, battery_spec(context), self.requirements)
