"""Best-effort terminal fallback for an interrupted broker-dispatched Actions job."""
from __future__ import annotations

import os
import urllib.request
from typing import Any, Mapping

from .ci_broker_action import BrokerActionError, _finish, _read_state


def fail_if_active(
    environment: Mapping[str, str] = os.environ,
    opener: Any = urllib.request.urlopen,
) -> None:
    ci_run_id = _read_state(environment)
    if ci_run_id is None:
        return
    try:
        # Omit log mutation in the fallback. If the primary executor already
        # recorded its R2 result, a same-status retry preserves that metadata.
        _finish(
            environment=environment,
            ci_run_id=ci_run_id,
            status="failed",
            error_summary="central_runner_failed",
            logs_status=None,  # type: ignore[arg-type]
            opener=opener,
        )
    except BrokerActionError:
        return


__all__ = ("fail_if_active",)
