"""Synthetic at-home cooking requests; no calendar or model integration."""
from __future__ import annotations

from .models import HouseholdRequest, ScenarioContext


def household_requests(context: ScenarioContext) -> list[HouseholdRequest]:
    """Return detached requests so a caller cannot mutate the scenario via this list."""
    return [HouseholdRequest.model_validate(r.model_dump()) for r in context.requests]
