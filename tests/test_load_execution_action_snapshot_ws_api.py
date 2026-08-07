from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_action_snapshot_ws_api as snapshot_ws
from custom_components.frakon_energy.const import DOMAIN
from custom_components.frakon_energy.load_action_intent import (
    ACTION_STATE_ALREADY_SATISFIED,
    ACTION_STATE_BLOCKED,
    ACTION_STATE_READY,
    resolve_start_action_intent,
)
from custom_components.frakon_energy.load_execution_action_snapshot import (
    ExecutionActionSnapshot,
    ExecutionActionSnapshotRepository,
)
from custom_components.frakon_energy.load_execution_attempt import (
    ExecutionAttempt,
    ExecutionAttemptRepository,
)
from custom_components.frakon_energy.load_profiles import (
    PROFILE_KIND_EV,
    LoadProfile,
    upsert_profile,
)


class _FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = data


class _FakeEntry:
    domain = DOMAIN
    entry_id = "entry-1"

    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options


class _FakeConfigEntries:
    def __init__(self, entry: _FakeEntry) -> None:
        self.entry = entry

    def async_get_entry(self, entry_id: str) -> _FakeEntry | None:
        return self.entry if entry_id == self.entry.entry_id else None


class _FakeStates:
    def __init__(self, states: dict[str, str]) -> None:
        self._states = states

    def get(self, entity_id: str) -> object | None:
        value = self._states.get(entity_id)
        return SimpleNamespace(state=value) if value is not None else None


class _FakeHass:
    def __init__(self, options: dict[str, Any], state: str = "off") -> None:
        self.data: dict[str, Any] = {}
        self.config_entries = _FakeConfigEntries(_FakeEntry(options))
        self.states = _FakeStates({"switch.enyaq_charging": state})


def _profile(*, entity_id: str = "switch.enyaq_charging", enabled: bool = True) -> LoadProfile:
    return LoadProfile(
        "ev-home",
        "Enyaq",
        PROFILE_KIND_EV,
        120,
        11.0,
        enabled=enabled,
        entity_id=entity_id,
    )


def _attempt() -> ExecutionAttempt:
    return ExecutionAttempt(
        attempt_id="attempt-1",
        entry_id="entry-1",
        profile_id="ev-home",
        entity_id="switch.enyaq_charging",
        approval_id="approval-1",
        approval_fingerprint="a" * 64,
        snapshot_digest="b" * 64,
        intent="execute_load_plan",
        approval_issued_at=100,
        approval_expires_at=220,
        created_at=110,
    ).validated()


def _snapshot(attempt: ExecutionAttempt | None = None) -> ExecutionActionSnapshot:
    current = attempt or _attempt()
    return ExecutionActionSnapshot.from_attempt_and_intent(
        attempt=current,
        intent=resolve_start_action_intent(_profile()),
        created_at=current.created_at,
    )


async def _repositories(
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_attempt: bool = True,
    include_snapshot: bool = True,
):
    attempt_repository = ExecutionAttemptRepository(_FakeStore())
    snapshot_repository = ExecutionActionSnapshotRepository(_FakeStore())
    attempt = _attempt()
    snapshot = _snapshot(attempt)
    if include_attempt:
        await attempt_repository.async_record(attempt)
    if include_snapshot:
        await snapshot_repository.async_record(snapshot)
    monkeypatch.setattr(
        snapshot_ws.consume_ws,
        "_attempt_repository",
        lambda hass, entry_id: attempt_repository,
    )
    monkeypatch.setattr(
        snapshot_ws,
        "action_snapshot_repository",
        lambda hass, entry_id: snapshot_repository,
    )
    return attempt_repository, snapshot_repository, attempt, snapshot


@pytest.mark.asyncio
async def test_snapshot_list_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _FakeHass(upsert_profile({}, _profile()))
    _, _, _, snapshot = await _repositories(monkeypatch)

    result = await snapshot_ws.async_list_execution_action_snapshots(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
    )

    assert result["snapshots"] == [snapshot.as_dict()]
    assert result["read_only"] is True
    assert result["execution_performed"] is False
    assert result["service_call_performed"] is False
    assert result["executor_available"] is False


@pytest.mark.asyncio
async def test_revalidate_live_off_state_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _FakeHass(upsert_profile({}, _profile()), state="off")
    _, _, attempt, _ = await _repositories(monkeypatch)

    result = await snapshot_ws.async_revalidate_execution_action_snapshot(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id=attempt.attempt_id,
    )

    assert result["revalidation"]["status"] == ACTION_STATE_READY
    assert result["revalidation"]["reason"] == "entity_state_allows_start"
    assert result["revalidation"]["attempt_matches"] is True
    assert result["revalidation"]["profile_matches"] is True
    assert result["read_only"] is True
    assert result["service_call_performed"] is False


@pytest.mark.asyncio
async def test_revalidate_live_on_state_is_already_satisfied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(upsert_profile({}, _profile()), state="on")
    _, _, attempt, _ = await _repositories(monkeypatch)

    result = await snapshot_ws.async_revalidate_execution_action_snapshot(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id=attempt.attempt_id,
    )

    assert result["revalidation"]["status"] == ACTION_STATE_ALREADY_SATISFIED
    assert result["revalidation"]["reason"] == "entity_already_in_desired_state"
    assert result["execution_performed"] is False


@pytest.mark.asyncio
async def test_revalidate_unavailable_state_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _FakeHass(upsert_profile({}, _profile()), state="unavailable")
    _, _, attempt, _ = await _repositories(monkeypatch)

    result = await snapshot_ws.async_revalidate_execution_action_snapshot(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id=attempt.attempt_id,
    )

    assert result["revalidation"]["status"] == ACTION_STATE_BLOCKED
    assert result["revalidation"]["reason"] == "entity_state_unavailable"
    assert result["executor_available"] is False


@pytest.mark.asyncio
async def test_revalidate_changed_profile_binding_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _FakeHass(upsert_profile({}, _profile(entity_id="switch.other")), state="off")
    _, _, attempt, _ = await _repositories(monkeypatch)

    result = await snapshot_ws.async_revalidate_execution_action_snapshot(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id=attempt.attempt_id,
    )

    assert result["revalidation"]["status"] == ACTION_STATE_BLOCKED
    assert result["revalidation"]["reason"] == "profile_or_action_mapping_changed"
    assert result["revalidation"]["attempt_matches"] is True
    assert result["revalidation"]["profile_matches"] is False


@pytest.mark.asyncio
async def test_revalidate_missing_profile_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _FakeHass({}, state="off")
    _, _, attempt, _ = await _repositories(monkeypatch)

    result = await snapshot_ws.async_revalidate_execution_action_snapshot(
        hass,  # type: ignore[arg-type]
        entry_id="entry-1",
        attempt_id=attempt.attempt_id,
    )

    assert result["profile"] is None
    assert result["revalidation"]["status"] == ACTION_STATE_BLOCKED
    assert result["revalidation"]["reason"] == "profile_missing"
    assert result["revalidation"]["profile_matches"] is False


@pytest.mark.asyncio
async def test_revalidate_missing_snapshot_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _FakeHass(upsert_profile({}, _profile()))
    _, _, attempt, _ = await _repositories(monkeypatch, include_snapshot=False)

    with pytest.raises(snapshot_ws.ActionSnapshotAuditError, match="snapshot not found"):
        await snapshot_ws.async_revalidate_execution_action_snapshot(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id=attempt.attempt_id,
        )


@pytest.mark.asyncio
async def test_revalidate_missing_attempt_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _FakeHass(upsert_profile({}, _profile()))
    _, _, attempt, _ = await _repositories(monkeypatch, include_attempt=False)

    with pytest.raises(snapshot_ws.ActionSnapshotAuditError, match="attempt not found"):
        await snapshot_ws.async_revalidate_execution_action_snapshot(
            hass,  # type: ignore[arg-type]
            entry_id="entry-1",
            attempt_id=attempt.attempt_id,
        )
