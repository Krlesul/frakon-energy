"""Persistent flexible-load profiles for FRAKON Energy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re
from typing import Any, Mapping

OPTION_LOAD_PROFILES = "load_profiles"
PROFILE_KIND_EV = "ev"
PROFILE_KIND_BOILER = "boiler"
PROFILE_KIND_BATTERY = "battery"
PROFILE_KIND_GENERIC = "generic"
PROFILE_KINDS = (PROFILE_KIND_EV, PROFILE_KIND_BOILER, PROFILE_KIND_BATTERY, PROFILE_KIND_GENERIC)

PHASE_TOPOLOGY_UNKNOWN = "unknown"
PHASE_TOPOLOGY_SINGLE = "single_phase"
PHASE_TOPOLOGY_THREE = "three_phase"
PHASE_TOPOLOGIES = (PHASE_TOPOLOGY_UNKNOWN, PHASE_TOPOLOGY_SINGLE, PHASE_TOPOLOGY_THREE)

_ENTITY_ID_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


def _optional_positive_current(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("phase current must be a finite positive number")
    current = float(value)
    if not math.isfinite(current) or current <= 0:
        raise ValueError("phase current must be a finite positive number")
    return current


@dataclass(frozen=True, slots=True)
class LoadProfile:
    """Reusable planning defaults for one flexible energy load."""

    profile_id: str
    name: str
    kind: str
    duration_minutes: int
    power_kw: float
    enabled: bool = True
    entity_id: str | None = None
    phase_topology: str = PHASE_TOPOLOGY_UNKNOWN
    phase_current_l1_a: float | None = None
    phase_current_l2_a: float | None = None
    phase_current_l3_a: float | None = None

    def validated(self) -> "LoadProfile":
        if not self.profile_id.strip():
            raise ValueError("profile_id is required")
        if not self.name.strip():
            raise ValueError("profile name is required")
        if self.kind not in PROFILE_KINDS:
            raise ValueError(f"unsupported profile kind: {self.kind}")
        if self.duration_minutes <= 0 or self.duration_minutes % 15 != 0:
            raise ValueError("duration_minutes must be a positive multiple of 15")
        if not math.isfinite(float(self.power_kw)) or self.power_kw <= 0:
            raise ValueError("power_kw must be a finite positive number")
        if self.entity_id is not None and not _ENTITY_ID_PATTERN.fullmatch(self.entity_id):
            raise ValueError("entity_id must be a valid Home Assistant entity ID")
        if self.phase_topology not in PHASE_TOPOLOGIES:
            raise ValueError(f"unsupported phase topology: {self.phase_topology}")

        currents = (
            _optional_positive_current(self.phase_current_l1_a),
            _optional_positive_current(self.phase_current_l2_a),
            _optional_positive_current(self.phase_current_l3_a),
        )
        configured = sum(value is not None for value in currents)
        if self.phase_topology == PHASE_TOPOLOGY_UNKNOWN and configured:
            raise ValueError("unknown phase topology cannot contain phase currents")
        if self.phase_topology == PHASE_TOPOLOGY_SINGLE and configured != 1:
            raise ValueError("single_phase topology requires exactly one phase current")
        if self.phase_topology == PHASE_TOPOLOGY_THREE and configured != 3:
            raise ValueError("three_phase topology requires L1, L2 and L3 phase currents")
        return self

    @property
    def phase_model_ready(self) -> bool:
        return self.phase_topology in (PHASE_TOPOLOGY_SINGLE, PHASE_TOPOLOGY_THREE)

    def phase_currents_a(self) -> dict[str, float | None]:
        return {
            "L1": self.phase_current_l1_a,
            "L2": self.phase_current_l2_a,
            "L3": self.phase_current_l3_a,
        }

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["phase_model_ready"] = self.phase_model_ready
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LoadProfile":
        raw_entity_id = value.get("entity_id")
        entity_id = str(raw_entity_id).strip() if raw_entity_id is not None else ""
        return cls(
            profile_id=str(value.get("profile_id", "")),
            name=str(value.get("name", "")),
            kind=str(value.get("kind", PROFILE_KIND_GENERIC)),
            duration_minutes=int(value.get("duration_minutes", 0)),
            power_kw=float(value.get("power_kw", 0)),
            enabled=bool(value.get("enabled", True)),
            entity_id=entity_id or None,
            phase_topology=str(value.get("phase_topology", PHASE_TOPOLOGY_UNKNOWN)),
            phase_current_l1_a=_optional_positive_current(value.get("phase_current_l1_a")),
            phase_current_l2_a=_optional_positive_current(value.get("phase_current_l2_a")),
            phase_current_l3_a=_optional_positive_current(value.get("phase_current_l3_a")),
        ).validated()


def profiles_from_options(options: Mapping[str, Any]) -> tuple[LoadProfile, ...]:
    """Load validated profiles from config-entry options."""
    raw = options.get(OPTION_LOAD_PROFILES, [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("load_profiles must be a list")

    profiles: list[LoadProfile] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("each load profile must be an object")
        profile = LoadProfile.from_dict(item)
        if profile.profile_id in seen:
            raise ValueError(f"duplicate profile_id: {profile.profile_id}")
        seen.add(profile.profile_id)
        profiles.append(profile)
    return tuple(profiles)


def profile_by_id(options: Mapping[str, Any], profile_id: str) -> LoadProfile:
    for profile in profiles_from_options(options):
        if profile.profile_id == profile_id:
            return profile
    raise ValueError(f"load profile not found: {profile_id}")


def upsert_profile(options: Mapping[str, Any], profile: LoadProfile) -> dict[str, Any]:
    """Return config-entry options with one profile inserted or replaced."""
    profile.validated()
    profiles = list(profiles_from_options(options))
    for index, existing in enumerate(profiles):
        if existing.profile_id == profile.profile_id:
            profiles[index] = profile
            break
    else:
        profiles.append(profile)
    updated = dict(options)
    updated[OPTION_LOAD_PROFILES] = [item.as_dict() for item in profiles]
    return updated


def delete_profile(options: Mapping[str, Any], profile_id: str) -> dict[str, Any]:
    """Return config-entry options without the selected profile."""
    profiles = list(profiles_from_options(options))
    if not any(item.profile_id == profile_id for item in profiles):
        raise ValueError(f"load profile not found: {profile_id}")
    updated = dict(options)
    updated[OPTION_LOAD_PROFILES] = [item.as_dict() for item in profiles if item.profile_id != profile_id]
    return updated
