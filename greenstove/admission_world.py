"""Environment-owned bridge: Isaac's day simulator plus independent verification.

Isaac can select this class in env_protocol without changing the supervisor protocol.
Actual PV is captured from the caller before simulation, never recovered from a trace.
"""
from . import admission_verifier
from .models import ContractError
from .world_adapter import AdmissionWorld as SimulationWorld, battery_spec


class AdmissionWorld(SimulationWorld):
    name = "abel-admission (Abel ledger, independently verified energy; synthetic simulation)"

    def __init__(self, requirements=None):
        super().__init__(requirements)
        self._actual_inputs = {}

    def simulate(self, plan, context, phase, actual=None):
        authoritative = actual.model_copy(deep=True) if actual is not None else None
        trace = super().simulate(plan, context, phase, authoritative)
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
