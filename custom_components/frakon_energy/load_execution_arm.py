"""Persistent global execution arming interlock for FRAKON Energy.

The interlock is an additional fail-closed prerequisite for every new physical
bounded start. It never authorizes work by itself and it never disables a
previously-owned stop obligation. First use is deliberately DISARMED.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

EXECUTION_ARM_STORAGE_VERSION = 1
EXECUTION_ARM_SCHEMA_VERSION = 1
EXECUTION_ARM_CONFIRMATION = "ARM"

_REPOSITORIES_KEY = "load_execution_arm_repositories_by_entry"
_GUARD_LOCKS_KEY = "load_execution_arm_guard_locks_by_entry"


class ExecutionArmError(RuntimeError):
    """Raised when execution arming state cannot be trusted."""


class ExecutionDisarmedError(ExecutionArmError):
    """Raised when a new physical start is blocked by DISARM."""


class ExecutionArmStore(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


def execution_arm_storage_key(entry_id: str) -> str:
    if not entry_id:
        raise ExecutionArmError("entry_id is required")
    digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:20]
    return f"{DOMAIN}.load_execution_arm.{digest}"


@dataclass(frozen=True, slots=True)
class ExecutionArmState:
    """Durable global start-execution interlock state."""

    armed: bool = False
    revision: int = 0
    changed_at: int = 0
    changed_by: str | None = None

    def validated(self) -> "ExecutionArmState":
        if not isinstance(self.armed, bool):
            raise ExecutionArmError("armed must be boolean")
        if self.revision < 0:
            raise ExecutionArmError("revision must be non-negative")
        if self.changed_at < 0:
            raise ExecutionArmError("changed_at must be non-negative")
        if self.revision == 0:
            if self.armed:
                raise ExecutionArmError("initial execution arm state must be disarmed")
            if self.changed_at != 0 or self.changed_by is not None:
                raise ExecutionArmError("initial execution arm state must be pristine")
        elif self.changed_at <= 0:
            raise ExecutionArmError("changed_at is required after first arm state change")
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionArmState":
        try:
            raw_armed = value["armed"]
            if not isinstance(raw_armed, bool):
                raise ExecutionArmError("armed must be boolean")
            raw_changed_by = value.get("changed_by")
            return cls(
                armed=raw_armed,
                revision=int(value["revision"]),
                changed_at=int(value["changed_at"]),
                changed_by=(str(raw_changed_by) if raw_changed_by is not None else None),
            ).validated()
        except (KeyError, TypeError, ValueError) as err:
            if isinstance(err, ExecutionArmError):
                raise
            raise ExecutionArmError("invalid persisted execution arm state") from err


@dataclass(frozen=True, slots=True)
class ExecutionArmUpdateResult:
    state: ExecutionArmState
    changed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "execution_arm": self.state.as_dict(),
            "changed": self.changed,
            "state_transition_performed": self.changed,
            "service_call_performed": False,
            "execution_performed": False,
        }


class ExecutionArmRepository:
    """Transactional persistent arm state with rollback-on-save-failure semantics."""

    def __init__(self, store: ExecutionArmStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._state: ExecutionArmState | None = None

    async def _async_state(self) -> ExecutionArmState:
        if self._state is None:
            raw = await self._store.async_load()
            if raw is None:
                self._state = ExecutionArmState().validated()
            else:
                if not isinstance(raw, dict):
                    raise ExecutionArmError("execution arm storage must be an object")
                if raw.get("schema_version") != EXECUTION_ARM_SCHEMA_VERSION:
                    raise ExecutionArmError("unsupported execution arm storage schema")
                state_value = raw.get("state")
                if not isinstance(state_value, dict):
                    raise ExecutionArmError("execution arm storage state must be an object")
                self._state = ExecutionArmState.from_dict(state_value)
        return self._state

    async def async_get(self) -> ExecutionArmState:
        async with self._lock:
            return await self._async_state()

    async def async_set(
        self,
        *,
        armed: bool,
        changed_at: int,
        changed_by: str | None,
    ) -> ExecutionArmUpdateResult:
        if not isinstance(armed, bool):
            raise ExecutionArmError("armed must be boolean")
        if changed_at <= 0:
            raise ExecutionArmError("changed_at must be positive")
        async with self._lock:
            current = await self._async_state()
            if current.armed is armed:
                return ExecutionArmUpdateResult(current, changed=False)
            candidate = ExecutionArmState(
                armed=armed,
                revision=current.revision + 1,
                changed_at=changed_at,
                changed_by=changed_by,
            ).validated()
            await self._store.async_save(
                {
                    "schema_version": EXECUTION_ARM_SCHEMA_VERSION,
                    "state": candidate.as_dict(),
                }
            )
            self._state = candidate
            return ExecutionArmUpdateResult(candidate, changed=True)


def home_assistant_execution_arm_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionArmRepository:
    return ExecutionArmRepository(
        Store(
            hass,
            EXECUTION_ARM_STORAGE_VERSION,
            execution_arm_storage_key(entry_id),
        )
    )


def execution_arm_repository(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionArmRepository:
    if not entry_id:
        raise ExecutionArmError("entry_id is required")
    domain_data = hass.data.setdefault(DOMAIN, {})
    repositories = domain_data.get(_REPOSITORIES_KEY)
    if not isinstance(repositories, dict):
        repositories = {}
        domain_data[_REPOSITORIES_KEY] = repositories
    repository = repositories.get(entry_id)
    if isinstance(repository, ExecutionArmRepository):
        return repository
    repository = home_assistant_execution_arm_repository(hass, entry_id)
    repositories[entry_id] = repository
    return repository


def execution_arm_guard(hass: HomeAssistant, entry_id: str) -> asyncio.Lock:
    """Serialize ARM/DISARM changes with the final physical start boundary."""
    if not entry_id:
        raise ExecutionArmError("entry_id is required")
    domain_data = hass.data.setdefault(DOMAIN, {})
    locks = domain_data.get(_GUARD_LOCKS_KEY)
    if not isinstance(locks, dict):
        locks = {}
        domain_data[_GUARD_LOCKS_KEY] = locks
    lock = locks.get(entry_id)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        locks[entry_id] = lock
    return lock


async def async_require_execution_armed(
    hass: HomeAssistant,
    entry_id: str,
) -> ExecutionArmState:
    """Fail closed unless the durable interlock explicitly says ARMED."""
    try:
        state = await execution_arm_repository(hass, entry_id).async_get()
    except Exception as err:
        raise ExecutionArmError(f"execution arm state unavailable: {err}") from err
    if not state.armed:
        raise ExecutionDisarmedError("physical start execution is DISARMED")
    return state


async def async_set_execution_armed(
    hass: HomeAssistant,
    *,
    entry_id: str,
    armed: bool,
    changed_at: int,
    changed_by: str | None,
) -> ExecutionArmUpdateResult:
    """Persist ARM/DISARM while serialized against the physical start boundary."""
    async with execution_arm_guard(hass, entry_id):
        try:
            return await execution_arm_repository(hass, entry_id).async_set(
                armed=armed,
                changed_at=changed_at,
                changed_by=changed_by,
            )
        except Exception as err:
            if isinstance(err, ExecutionArmError):
                raise
            raise ExecutionArmError(f"execution arm state could not be persisted: {err}") from err


async def async_execution_arm_status(
    hass: HomeAssistant,
    entry_id: str,
) -> dict[str, Any]:
    """Return fail-closed diagnostic status without mutating the interlock."""
    try:
        state = await execution_arm_repository(hass, entry_id).async_get()
    except Exception as err:
        return {
            "entry_id": entry_id,
            "armed": False,
            "storage_healthy": False,
            "last_error": str(err),
            "revision": None,
            "changed_at": None,
            "changed_by": None,
            "required_arm_confirmation": EXECUTION_ARM_CONFIRMATION,
            "fail_closed": True,
        }
    return {
        "entry_id": entry_id,
        "armed": state.armed,
        "storage_healthy": True,
        "last_error": None,
        "revision": state.revision,
        "changed_at": state.changed_at,
        "changed_by": state.changed_by,
        "required_arm_confirmation": EXECUTION_ARM_CONFIRMATION,
        "fail_closed": True,
    }
