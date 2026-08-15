"""Operational source-resolution context kept outside tariff price authority."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

_CZECH_POSTCODE_RE = re.compile(r"^[1-7]\d{4}$")


def normalize_czech_postcode(value: str) -> str:
    """Normalize a Czech PSČ to five digits without making location inferences."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("postcode must not be empty")
    normalized = re.sub(r"\s+", "", value)
    if not _CZECH_POSTCODE_RE.fullmatch(normalized):
        raise ValueError("postcode must be a valid five-digit Czech PSČ")
    return normalized


@dataclass(frozen=True, slots=True)
class TariffSourceResolutionContext:
    """Non-price context needed only to resolve an official supplier document.

    This object is deliberately separate from ``ElectricityContract`` and from
    tariff provenance. A postcode may choose an official supplier document, but
    it must never become part of commodity-price authority or an all-in price.
    """

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
        value: Mapping[str, Any] | None,
    ) -> TariffSourceResolutionContext:
        if value is None:
            return cls()
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
        return cls(postcode=postcode)


def tariff_source_context_fingerprint(context: TariffSourceResolutionContext) -> str:
    """Return an operational fingerprint; this is never a price fingerprint."""
    if not isinstance(context, TariffSourceResolutionContext):
        raise ValueError("context must be TariffSourceResolutionContext")
    encoded = json.dumps(
        context.as_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
