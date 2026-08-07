"""Deadline-enforcing transition gateway for bounded stop lifecycles.

All runtime stop dispatch/no-op paths must enter the stop state machine through
this module. It adds a second, model-adjacent deadline invariant on top of the
read-only due gate so an internal caller cannot transition a stop before the
immutable persisted ``ends_at`` deadline.
"""

from __future__ import annotations

from datetime import datetime

from .load_execution_stop_lifecycle import (
    ExecutionStopLifecycleRecord,
    StopLifecycleError,
    begin_stop_dispatch,
    satisfy_stop_without_dispatch,
)


def _ends_at_timestamp(record: ExecutionStopLifecycleRecord) -> int:
    record.validated()
    try:
        ends_at = datetime.fromisoformat(record.ends_at)
    except (TypeError, ValueError) as err:
        raise StopLifecycleError("ends_at must be an ISO-8601 datetime") from err
    if ends_at.tzinfo is None or ends_at.utcoffset() is None:
        raise StopLifecycleError("ends_at must include timezone offset")
    return int(ends_at.timestamp())


def assert_stop_deadline_reached(
    record: ExecutionStopLifecycleRecord,
    *,
    now: int,
) -> None:
    """Reject any stop transition before the exact immutable plan deadline."""
    if now < _ends_at_timestamp(record):
        raise StopLifecycleError("stop transition cannot occur before ends_at")


def begin_due_stop_dispatch(
    record: ExecutionStopLifecycleRecord,
    *,
    now: int,
) -> ExecutionStopLifecycleRecord:
    """Begin crash-safe stop dispatch only at or after persisted ``ends_at``."""
    assert_stop_deadline_reached(record, now=now)
    return begin_stop_dispatch(record, now=now)


def satisfy_due_stop_without_dispatch(
    record: ExecutionStopLifecycleRecord,
    *,
    current_state: str | None,
    now: int,
) -> ExecutionStopLifecycleRecord:
    """Complete an already-off stop only at or after persisted ``ends_at``."""
    assert_stop_deadline_reached(record, now=now)
    return satisfy_stop_without_dispatch(
        record,
        current_state=current_state,
        now=now,
    )
