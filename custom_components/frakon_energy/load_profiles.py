"""Persistent flexible-load profiles for FRAKON Energy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

OPTION_LOAD_PROFILES = "load_profiles"
PROFILE_KIND_EV = "ev"
PROFILE_KIND_BOILER = "boiler"
PROFILE_KIND_BATTERY = "battery"
PROFILE_KIND_GENERIC = "generic"
PROFILE_KINDS = (PROFILE_KIND_EV, PROFILE_KIND_BOILER, PROFILE_KIND_BATTERY, PROFILE_KIND_GENERIC)


@dataclass(frozen=True, slots=True)
class LoadProfile:
    """Reusable planning defaults for one flexible energy load."""

    profile_id: str
    name: str
    kind: str
    duration_minutes: int
    power_kw: float
    enabled: bool = True

    def validated(self) -> "LoadProfile":
        if not self.profile_id.strip():
            raise ValueError("profile_id is required")
        if not self.name.strip():
            raise ValueError("profile name is required")
        if self.kind not in PROFILE_KINDS:
            raise ValueError(f"unsupported profile kind: {self.kind}")
        if self.duration_minutes <= 0 or self.duration_minutes % 15 != 0:
            raise ValueError("duration_minutes must be a positive multiple of 15")
        if self.power_kw <= 0:
            raise ValueError("power_kw must be positive")
        return self

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LoadProfile":
        return cls(
            profile_id=str(value.get("profile_id", "")),
            name=str(value.get("name", "")),
            kind=str(value.get("kind", PROFILE_KIND_GENERIC)),
            duration_minutes=int(value.get("duration_minutes", 0)),
            power_kw=float(value.get("power_kw", 0)),
            enabled=bool(value.get("enabled", True)),
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
