"""Operational tariff-source resolution context.

Location data such as postcode is operational discovery state only. It stays
outside ElectricityContract, tariff provenance and final price fingerprints.
This module is deliberately self-contained because supplier and websocket tests
load FRAKON modules in isolation as well as through the normal package runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

_CZECH_POSTCODE_RE = re.compile(r"^[1-7]\d{4}$")


def normalize_czech_postcode(value: str) -> str:
    """Normalize a Czech PSČ to five digits without inferring location."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("postcode must not be empty")
    normalized = re.sub(r"\s+", "", value)
    if not _CZECH_POSTCODE_RE.fullmatch(normalized):
        raise ValueError("postcode must be a valid five-digit Czech PSČ")
    return normalized


@dataclass(frozen=True, slots=True)
class TariffSourceResolutionContext:
    """Operational lookup context that is never tariff price authority."""

    postcode: str | None = None

    def __post_init__(self) -> None:
        if self.postcode is not None:
            object.__setattr__(self, "postcode", normalize_czech_postcode(self.postcode))

    @property
    def is_empty(self) -> bool:
        return self.postcode is None

    def as_dict(self) -> dict[str, str]:
        return {} if self.postcode is None else {"postcode": self.postcode}

    @classmethod
    def from_value(
        cls,
        value: Mapping[str, Any] | TariffSourceResolutionContext | None,
    ) -> TariffSourceResolutionContext:
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("source_context must be an object")
        unexpected = set(value) - {"postcode"}
        if unexpected:
            raise ValueError(
                "source_context contains unsupported fields: "
                + ", ".join(sorted(str(item) for item in unexpected))
            )
        postcode = value.get("postcode")
        if postcode in (None, ""):
            return cls()
        if not isinstance(postcode, str):
            raise ValueError("postcode must be a string")
        return cls(postcode=postcode)


def tariff_source_context_fingerprint(context: TariffSourceResolutionContext) -> str:
    """Return a stable operational fingerprint, never a price fingerprint."""
    if not isinstance(context, TariffSourceResolutionContext):
        raise ValueError("context must be TariffSourceResolutionContext")
    encoded = json.dumps(
        context.as_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = (
    "TariffSourceResolutionContext",
    "normalize_czech_postcode",
    "tariff_source_context_fingerprint",
)
