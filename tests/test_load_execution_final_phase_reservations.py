from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.frakon_energy import load_execution_final_phase_recheck as final_phase
from custom_components.frakon_energy.load_execution_phase_capacity_reservation import PhaseCapacityReservation


class _Entry:
    domain = "frakon_energy"
    options: dict[str, Any] = {}


class _ConfigEntries:
    def async_get_entry(self, entry_id: str) -> _Entry:
        return _Entry()


class _Hass:
    config_entries = _ConfigEntries()


class _Capacity:
    configured = True

    def as_dict(self) -> dict[str, Any]:
        return {"configured": True}


class _Lifecycle:
    lifecycle_id = "life-current"
    attempt_id = "attempt-current"
    profile_id = "load-1"

    def validated(self) -> "_Lifecycle":
        return self


class _Value:
    def __init__(self, *, projected: float, planned: float, maximum: float) -> None:
        self.projected_current_a = projected
        self.planned_current_a = planned
        self.max_current_a = maximum


class _Projection:
    def __init__(self) -> None:
        self.phases = {
            "L1": _Value(projected=20.0, planned=8.0, maximum=25.0),
            "L2": _Value(projected=10.0, planned=0.0, maximum=25.0),
            "L3": _Value(projected=10.0, planned=0.0, maximum=25.0),
        }

    def as_dict(self) -> dict[str, Any]:
        return {"status": "within_limit"}


class _Readiness:
    can_start_phase = True
    reason = "phase_capacity_available"

    def as_dict(self) -> dict[str, Any]:
        return {"can_start_phase": True}


class _Repo:
    def __init__(self, active: tuple[PhaseCapacityReservation, ...]) -> None:
        self.active = active
        self.reserve_calls: list[dict[str, Any]] = []

    async def async_active(self, *, now: int) -> tuple[PhaseCapacityReservation, ...]:
        return self.active

    async def async_reserve(self, **kwargs: Any) -> tuple[PhaseCapacityReservation, bool]:
        self.reserve_calls.append(kwargs)
        reservation = PhaseCapacityReservation(
            lifecycle_id=kwargs["lifecycle_id"],
            attempt_id=kwargs["attempt_id"],
            current_l1_a=kwargs["current_l1_a"],
            current_l2_a=kwargs["current_l2_a"],
            current_l3_a=kwargs["current_l3_a"],
            created_at=kwargs["now"],
            expires_at=kwargs["now"] + 300,
        ).validated()
        return reservation, True


def _patch_common(monkeypatch: pytest.MonkeyPatch, repo: _Repo) -> None:
    monkeypatch.setattr(final_phase, "build_site_phase_capacity_status", lambda *args, **kwargs: _Capacity())

    async def records(*args: Any, **kwargs: Any) -> list[_Lifecycle]:
        return [_Lifecycle()]

    monkeypatch.setattr(final_phase, "_dispatching_records", records)
    monkeypatch.setattr(final_phase, "build_load_profile_phase_projection", lambda *args, **kwargs: _Projection())
    monkeypatch.setattr(final_phase, "evaluate_load_phase_readiness", lambda projection: _Readiness())
    monkeypatch.setattr(final_phase, "phase_capacity_reservation_repository", lambda *args, **kwargs: repo)
    monkeypatch.setattr(final_phase.time, "time", lambda: 1_000.0)


@pytest.mark.asyncio
async def test_other_phase_reservation_blocks_second_start_before_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = PhaseCapacityReservation(
        lifecycle_id="life-other",
        attempt_id="attempt-other",
        current_l1_a=6.0,
        current_l2_a=0.0,
        current_l3_a=0.0,
        created_at=900,
        expires_at=1_200,
    ).validated()
    repo = _Repo((existing,))
    _patch_common(monkeypatch, repo)

    result = await final_phase.async_final_phase_recheck(_Hass(), entry_id="entry-1")  # type: ignore[arg-type]

    assert result.can_start is False
    assert result.reason == final_phase.REASON_RESERVED_HEADROOM_INSUFFICIENT
    assert result.blocking_phases == ("L1",)
    assert result.reserved_other_currents_a == {"L1": 6.0, "L2": 0.0, "L3": 0.0}
    assert result.effective_projected_currents_a["L1"] == 26.0
    assert repo.reserve_calls == []


@pytest.mark.asyncio
async def test_allowed_start_reserves_exact_profile_phase_currents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _Repo(())
    _patch_common(monkeypatch, repo)

    result = await final_phase.async_final_phase_recheck(_Hass(), entry_id="entry-1")  # type: ignore[arg-type]

    assert result.can_start is True
    assert result.reservation_performed is True
    assert result.read_only is False
    assert len(repo.reserve_calls) == 1
    assert repo.reserve_calls[0]["current_l1_a"] == 8.0
    assert repo.reserve_calls[0]["current_l2_a"] == 0.0
    assert repo.reserve_calls[0]["current_l3_a"] == 0.0


@pytest.mark.asyncio
async def test_own_replayed_reservation_is_not_double_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    own = PhaseCapacityReservation(
        lifecycle_id="life-current",
        attempt_id="attempt-current",
        current_l1_a=8.0,
        current_l2_a=0.0,
        current_l3_a=0.0,
        created_at=900,
        expires_at=1_200,
    ).validated()
    repo = _Repo((own,))
    _patch_common(monkeypatch, repo)

    result = await final_phase.async_final_phase_recheck(_Hass(), entry_id="entry-1")  # type: ignore[arg-type]

    assert result.can_start is True
    assert result.reserved_other_currents_a == {"L1": 0.0, "L2": 0.0, "L3": 0.0}
    assert result.effective_projected_currents_a["L1"] == 20.0
