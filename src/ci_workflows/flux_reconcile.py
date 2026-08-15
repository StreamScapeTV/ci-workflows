"""Trusted Flux reconciliation public component facade."""
from . import flux_reconcile_apply as _apply
from .flux_reconcile_apply import plan_summary, reconcile, verify_health
from .flux_reconcile_model import FluxPlan, ResourceRef, WorkloadRef
from .flux_reconcile_plan import resolve_request

# Compatibility for existing focused tests and callers that patch the public
# component module while the implementation remains split into small modules.
shutil = _apply.shutil
subprocess = _apply.subprocess

__all__ = [
    "FluxPlan", "ResourceRef", "WorkloadRef", "plan_summary", "reconcile",
    "resolve_request", "verify_health",
]
