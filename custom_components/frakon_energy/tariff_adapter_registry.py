"""Production registry for FRAKON Energy supplier tariff adapters."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Callable

from .providers.cez_tariffs import CezTariffCatalogAdapter
from .providers.eon_tariffs import EonTariffCatalogAdapter
from .providers.mnd_confirmed_source_resolver import (
    mnd_confirmed_source_resolver_from_options,
)
from .providers.mnd_tariffs import MndTariffCatalogAdapter, MndTariffSourceResolver
from .providers.pre_tariffs import PreTariffCatalogAdapter
from .tariff_sources import TariffAdapterRegistry


def build_default_tariff_adapter_registry(
    *,
    mnd_resolver: MndTariffSourceResolver | None = None,
    clock: Callable[[], datetime] | None = None,
) -> TariffAdapterRegistry:
    """Build the production supplier registry with the four mandatory adapters.

    ČEZ, E.ON and PRE use immutable verified official catalogs and share an
    optional clock only to make discovery timestamps deterministic in tests.
    MND is always registered too, but remains fail-closed until an exact
    postcode-/territory-aware document resolver is supplied.
    """
    registry = TariffAdapterRegistry()
    registry.register(CezTariffCatalogAdapter(clock=clock))
    registry.register(EonTariffCatalogAdapter(clock=clock))
    registry.register(PreTariffCatalogAdapter(clock=clock))
    registry.register(MndTariffCatalogAdapter(resolver=mnd_resolver))
    return registry


def build_entry_tariff_adapter_registry(
    options: Mapping[str, Any],
    *,
    clock: Callable[[], datetime] | None = None,
) -> TariffAdapterRegistry:
    """Build a request registry whose MND authority belongs to one config entry.

    Confirmed MND source resolutions are config-entry options, not global tariff
    authority. Rebuilding the small adapter registry from only the current entry's
    options prevents a source confirmed in one FRAKON Energy entry from silently
    authorizing discovery in another entry. The resolver reads no raw postcode;
    matching remains against the hashed operational source context.
    """
    if not isinstance(options, Mapping):
        raise ValueError("entry options must be a mapping")
    return build_default_tariff_adapter_registry(
        mnd_resolver=mnd_confirmed_source_resolver_from_options(
            options,
            clock=clock,
        ),
        clock=clock,
    )
