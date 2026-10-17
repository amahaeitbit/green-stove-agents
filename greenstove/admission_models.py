"""Immutable records for battery-backed admission; separate from C0 verdicts."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LedgerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class BatterySpec(LedgerModel):
    capacity_kwh: float = Field(gt=0)
    floor_kwh: float = Field(ge=0)
    max_charge_kw: float = Field(ge=0)  # input power before charging losses
    max_discharge_kw: float = Field(gt=0)  # delivered load power, before storage withdrawal
    charge_efficiency: float = Field(gt=0, le=1)
    discharge_efficiency: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def valid_floor(self):
        if self.floor_kwh > self.capacity_kwh:
            raise ValueError("floor exceeds capacity")
        return self


class Commitment(LedgerModel):
    reservation_id: str
    request_id: str
    start_step: int = Field(ge=0, lt=96)
    end_step: int = Field(gt=0, le=96)
    power_kw: float = Field(gt=0)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end_step <= self.start_step:
            raise ValueError("commitment end must follow its start")
        return self


class AdmissionState(LedgerModel):
    scope_id: str
    step: int = Field(ge=0, le=96)
    battery_kwh: float = Field(ge=0)
    commitments: tuple[Commitment, ...] = ()
    admitted_request_ids: tuple[str, ...] = ()
    admitted_reservation_ids: tuple[str, ...] = ()
    completed_request_ids: tuple[str, ...] = ()
    delivered_cooking_kwh: float = Field(default=0, ge=0)
    unserved_essential_kwh: float = Field(default=0, ge=0)


class AdmissionDecision(LedgerModel):
    status: Literal["ADMIT", "DEFER", "UNSERVED"]
    reason: str
    request_id: str
    reservation_id: str
    step: int
    required_storage_kwh: float = Field(ge=0)
    available_storage_kwh: float = Field(ge=0)
    state: AdmissionState


class IntervalResult(LedgerModel):
    step: int
    state: AdmissionState
    served_essential_kwh: float
    delivered_cooking_kwh: float
    unserved_essential_kwh: float
    pv_to_load_kwh: float
    battery_withdrawal_kwh: float
    battery_charge_kwh: float
    curtailed_pv_kwh: float
