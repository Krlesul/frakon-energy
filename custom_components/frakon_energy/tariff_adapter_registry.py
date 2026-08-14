"""Production registry for FRAKON Energy supplier tariff adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from .providers.cez_tariffs import CezTariffCatalogAdapter
from .providers.eon_tariffs import EonTariffCatalogAdapter
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
