import pytest

from custom_components.frakon_energy.load_execution_lifecycle_core import (
    STATUS_CANCELLED,
    STATUS_DISPATCHING,
    STATUS_FAILED,
    STATUS_PREPARED,
    STATUS_RECOVERY_REQUIRED,
    STATUS_VERIFIED,
    STATUS_VERIFYING,
    InvalidLifecycleTransition,
    allowed_next_statuses,
    is_terminal,
    require_transition,
    validate_status,
)


def test_all_allowed_transitions_are_accepted() -> None:
    allowed = {
        STATUS_PREPARED: {STATUS_DISPATCHING, STATUS_CANCELLED},
        STATUS_DISPATCHING: {STATUS_VERIFYING, STATUS_RECOVERY_REQUIRED},
        STATUS_VERIFYING: {STATUS_VERIFIED, STATUS_FAILED, STATUS_RECOVERY_REQUIRED},
        STATUS_RECOVERY_REQUIRED: {STATUS_VERIFYING, STATUS_FAILED},
        STATUS_VERIFIED: set(),
        STATUS_FAILED: set(),
        STATUS_CANCELLED: set(),
    }

    for current, targets in allowed.items():
        assert allowed_next_statuses(current) == frozenset(targets)
        for target in targets:
            require_transition(current, target)


def test_unlisted_transition_is_rejected() -> None:
    with pytest.raises(InvalidLifecycleTransition, match="not allowed"):
        require_transition(STATUS_PREPARED, STATUS_VERIFIED)


def test_same_state_transition_is_rejected() -> None:
    with pytest.raises(InvalidLifecycleTransition, match="not allowed"):
        require_transition(STATUS_VERIFYING, STATUS_VERIFYING)


@pytest.mark.parametrize(
    "status",
    [STATUS_VERIFIED, STATUS_FAILED, STATUS_CANCELLED],
)
def test_terminal_states_are_terminal(status: str) -> None:
    assert is_terminal(status) is True
    assert allowed_next_statuses(status) == frozenset()


@pytest.mark.parametrize(
    "status",
    [STATUS_PREPARED, STATUS_DISPATCHING, STATUS_VERIFYING, STATUS_RECOVERY_REQUIRED],
)
def test_non_terminal_states_are_not_terminal(status: str) -> None:
    assert is_terminal(status) is False


def test_unknown_status_is_rejected_fail_closed() -> None:
    with pytest.raises(ValueError, match="unsupported execution lifecycle status"):
        validate_status("automatic")

    with pytest.raises(ValueError, match="unsupported execution lifecycle status"):
        allowed_next_statuses("unknown")

    with pytest.raises(ValueError, match="unsupported execution lifecycle status"):
        require_transition(STATUS_PREPARED, "executed")
