"""UI-safe canonical product catalog derived from verified supplier adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .providers.cez_tariffs import CEZ_2026_COMMERCIAL_CATALOG
from .providers.eon_tariffs import EON_2026_ELECTRICITY_CATALOG
from .providers.mnd_tariffs import MND_CURRENT_ELECTRICITY_PRODUCTS
from .providers.pre_tariffs import PRE_CURRENT_COMMERCIAL_CATALOG
from .tariff_sources import PRICE_SCOPE_SUPPLIER_COMMERCIAL

SOURCE_RESOLUTION_STATIC = "static_catalog"
SOURCE_RESOLUTION_DYNAMIC = "dynamic_resolver"
SOURCE_RESOLUTIONS = (SOURCE_RESOLUTION_STATIC, SOURCE_RESOLUTION_DYNAMIC)

_SUPPLIER_ORDER = {"cez": 0, "eon": 1, "pre": 2, "mnd": 3}


@dataclass(frozen=True, slots=True)
class TariffProductOption:
    """One canonical supplier/product choice suitable for the setup wizard."""

    supplier: str
    product_name: str
    contract_kind: str
    source_resolution: str
    price_scope: str = PRICE_SCOPE_SUPPLIER_COMMERCIAL

    def __post_init__(self) -> None:
        for field_name in ("supplier", "product_name", "contract_kind"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
        if self.source_resolution not in SOURCE_RESOLUTIONS:
            raise ValueError("unsupported source_resolution")
        if self.price_scope != PRICE_SCOPE_SUPPLIER_COMMERCIAL:
            raise ValueError("wizard product options must be supplier-commercial")

    @property
    def requires_document_resolver(self) -> bool:
        return self.source_resolution == SOURCE_RESOLUTION_DYNAMIC

    def as_dict(self) -> dict[str, Any]:
        return {
            "supplier": self.supplier,
            "product_name": self.product_name,
            "contract_kind": self.contract_kind,
            "source_resolution": self.source_resolution,
            "requires_document_resolver": self.requires_document_resolver,
            "price_scope": self.price_scope,
        }


def _deduplicated_options(
    *,
    supplier: str,
    entries: Iterable[object],
    source_resolution: str,
) -> tuple[TariffProductOption, ...]:
    options: dict[tuple[str, str], TariffProductOption] = {}
    for entry in entries:
        product_name = getattr(entry, "product_name", None)
        contract_kind = getattr(entry, "contract_kind", None)
        if not isinstance(product_name, str) or not product_name.strip():
            raise ValueError(f"{supplier} catalog entry has invalid product_name")
        if not isinstance(contract_kind, str) or not contract_kind.strip():
            raise ValueError(f"{supplier} catalog entry has invalid contract_kind")
        key = (product_name.strip(), contract_kind.strip())
        options.setdefault(
            key,
            TariffProductOption(
                supplier=supplier,
                product_name=key[0],
                contract_kind=key[1],
                source_resolution=source_resolution,
            ),
        )
    return tuple(options.values())


def default_tariff_product_options() -> tuple[TariffProductOption, ...]:
    """Return canonical wizard choices directly from current verified catalogs."""
    options = (
        *_deduplicated_options(
            supplier="cez",
            entries=CEZ_2026_COMMERCIAL_CATALOG,
            source_resolution=SOURCE_RESOLUTION_STATIC,
        ),
        *_deduplicated_options(
            supplier="eon",
            entries=EON_2026_ELECTRICITY_CATALOG,
            source_resolution=SOURCE_RESOLUTION_STATIC,
        ),
        *_deduplicated_options(
            supplier="pre",
            entries=PRE_CURRENT_COMMERCIAL_CATALOG,
            source_resolution=SOURCE_RESOLUTION_STATIC,
        ),
        *_deduplicated_options(
            supplier="mnd",
            entries=MND_CURRENT_ELECTRICITY_PRODUCTS,
            source_resolution=SOURCE_RESOLUTION_DYNAMIC,
        ),
    )
    seen: set[tuple[str, str, str]] = set()
    for option in options:
        identity = (option.supplier, option.product_name, option.contract_kind)
        if identity in seen:
            raise ValueError(f"duplicate tariff product option: {identity!r}")
        seen.add(identity)
    return tuple(
        sorted(
            options,
            key=lambda option: (
                _SUPPLIER_ORDER.get(option.supplier, 999),
                option.product_name.casefold(),
                option.contract_kind,
            ),
        )
    )


def tariff_product_catalog_payload() -> dict[str, Any]:
    """Return deterministic JSON-safe catalog metadata for frontend clients."""
    options = default_tariff_product_options()
    suppliers: dict[str, list[dict[str, Any]]] = {}
    for option in options:
        suppliers.setdefault(option.supplier, []).append(option.as_dict())
    return {
        "suppliers": [
            {
                "supplier": supplier,
                "products": products,
            }
            for supplier, products in suppliers.items()
        ],
        "price_scope": PRICE_SCOPE_SUPPLIER_COMMERCIAL,
        "activation_performed": False,
    }
