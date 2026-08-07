import pytest

from custom_components.frakon_energy.load_execution_approval import ExecutionApproval
from custom_components.frakon_energy.load_execution_attempt import ExecutionAttempt


def _approval() -> ExecutionApproval:
    return ExecutionApproval(
        approval_id="approval-validity",
        intent="execute_load_plan",
        snapshot_digest="a" * 64,
        issued_at=100,
        expires_at=220,
        signature="signature",
    )


@pytest.mark.parametrize("created_at", [99, 220, 221])
def test_attempt_created_at_must_be_inside_approval_validity_window(created_at: int) -> None:
    with pytest.raises(ValueError, match="within the approval validity window"):
        ExecutionAttempt.from_consumed_approval(
            entry_id="entry-1",
            profile_id="ev-home",
            entity_id="switch.enyaq_charging",
            approval=_approval(),
            created_at=created_at,
        )


def test_attempt_created_at_accepts_approval_issue_time_and_last_valid_second() -> None:
    issued = ExecutionAttempt.from_consumed_approval(
        entry_id="entry-1",
        profile_id="ev-home",
        entity_id="switch.enyaq_charging",
        approval=_approval(),
        created_at=100,
    )
    last_valid = ExecutionAttempt.from_consumed_approval(
        entry_id="entry-1",
        profile_id="ev-home",
        entity_id="switch.enyaq_charging",
        approval=_approval(),
        created_at=219,
    )

    assert issued.created_at == 100
    assert last_valid.created_at == 219
    assert issued.execution_performed is False
    assert last_valid.executor_available is False
