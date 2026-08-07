"""Read-only timing diagnostics for durable FRAKON Energy execution schedules."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from .load_execution_lifecycle import ExecutionLifecycleRecord
from .load_execution_readiness import DEFAULT_START_GRACE_SECONDS
from .load_execution_schedule import ExecutionSchedule

TIMING_WAITING = "waiting"
TIMING_PREPARE_NOW = "prepare_now"
TIMING_MISSED = "missed"
TIMING_EXPIRED = "expired"
TIMING_LIFECYCLE_EXISTS = "lifecycle_exists"

ACTION_WAIT = "wait_for_start"
ACTION_PREPARE = "prepare_scheduled"
ACTION_REVIEW_MISSED = "manual_review_missed_start"
ACTION_NONE_EXPIRED = "none_expired"
ACTION_NONE_LIFECYCLE = "none_lifecycle_exists"
ACTION_BLOCKED_RECOVERY = "blocked_by_startup_recovery"


class ScheduleDiagnosticError(ValueError):
    """Raised when timing diagnostics cannot be calculated safely."""


@dataclass(frozen=True, slots=True)
class ScheduleTimingDiagnostic:
    schedule_id: str
    attempt_id: str
    profile_id: str
    entity_id: str
    status: str
    next_action: str
    plan_starts_at: str
    plan_ends_at: str
    prepare_deadline: str
    seconds_until_start: int
    seconds_until_prepare_deadline: int
    lifecycle_state: str | None
    recovery_ready: bool
    scheduler_should_prepare: bool
    final_readiness_required: bool = True
    read_only: bool = True
    state_transition_performed: bool = False
    execution_performed: bool = False
    executor_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_schedule_timing(
    schedule: ExecutionSchedule,
    *,
    lifecycle: ExecutionLifecycleRecord | None,
    now: datetime,
    recovery_ready: bool,
    start_grace_seconds: int = DEFAULT_START_GRACE_SECONDS,
) -> ScheduleTimingDiagnostic:
    """Evaluate when an internal scheduler may call the final prepare gate."""
    schedule.validated()
    if lifecycle is not None:
        lifecycle.validated()
        if lifecycle.entry_id != schedule.entry_id or lifecycle.attempt_id != schedule.attempt_id:
            raise ScheduleDiagnosticError("lifecycle scope does not match schedule")
        if lifecycle.plan_digest != schedule.plan_digest:
            raise ScheduleDiagnosticError("lifecycle plan does not match schedule")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ScheduleDiagnosticError("now must be timezone-aware")
    if start_grace_seconds < 0 or start_grace_seconds > 60:
        raise ScheduleDiagnosticError("start_grace_seconds must be between 0 and 60")

    starts = datetime.fromisoformat(schedule.plan.starts_at)
    ends = datetime.fromisoformat(schedule.plan.ends_at)
    deadline = starts + timedelta(seconds=start_grace_seconds)
    seconds_until_start = int(starts.timestamp()) - int(now.timestamp())
    seconds_until_deadline = int(deadline.timestamp()) - int(now.timestamp())

    if lifecycle is not None:
        status = TIMING_LIFECYCLE_EXISTS
        next_action = ACTION_NONE_LIFECYCLE
        should_prepare = False
    elif now < starts:
        status = TIMING_WAITING
        next_action = ACTION_WAIT if recovery_ready else ACTION_BLOCKED_RECOVERY
        should_prepare = False
    elif now < ends and now <= deadline:
        status = TIMING_PREPARE_NOW
        next_action = ACTION_PREPARE if recovery_ready else ACTION_BLOCKED_RECOVERY
        should_prepare = recovery_ready
    elif now >= ends:
        status = TIMING_EXPIRED
        next_action = ACTION_NONE_EXPIRED
        should_prepare = False
    else:
        status = TIMING_MISSED
        next_action = ACTION_REVIEW_MISSED
        should_prepare = False

    return ScheduleTimingDiagnostic(
        schedule_id=schedule.schedule_id,
        attempt_id=schedule.attempt_id,
        profile_id=schedule.profile_id,
        entity_id=schedule.entity_id,
        status=status,
        next_action=next_action,
        plan_starts_at=schedule.plan.starts_at,
        plan_ends_at=schedule.plan.ends_at,
        prepare_deadline=deadline.isoformat(),
        seconds_until_start=seconds_until_start,
        seconds_until_prepare_deadline=seconds_until_deadline,
        lifecycle_state=lifecycle.state if lifecycle is not None else None,
        recovery_ready=recovery_ready,
        scheduler_should_prepare=should_prepare,
    )
