"""Compatibility exports for tariff source-resolution context.

The canonical type lives in ``tariff_sources`` so isolated supplier-adapter unit
tests do not gain an extra import dependency. Keeping this module as a re-export
preserves existing imports while guaranteeing one dataclass identity everywhere.
"""

from .tariff_sources import (
    TariffSourceResolutionContext,
    normalize_czech_postcode,
    tariff_source_context_fingerprint,
)

__all__ = (
    "TariffSourceResolutionContext",
    "normalize_czech_postcode",
    "tariff_source_context_fingerprint",
)
