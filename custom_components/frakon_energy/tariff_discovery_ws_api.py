"""Administrator-only read-only tariff discovery websocket API."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from .ws_auth import ensure_admin
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .contracts import ElectricityContract, contract_fingerprint
from .tariff_adapter_registry import (
    build_default_tariff_adapter_registry,
    build_entry_tariff_adapter_registry,
)
from .tariff_discovery import async_discover_contract_tariff_review
from .tariff_sources import (
    TariffAdapterRegistry,
    TariffSourceResolutionContext,
    tariff_source_context_fingerprint,
)

COMMAND_TARIFF_DISCOVER = "frakon_energy/tariff/discover"
_REGISTERED_KEY = "tariff_discovery_websocket_registered"
_REGISTRY_KEY = "tariff_adapter_registry"
_REGISTRY_EXPLICIT_KEY = "tariff_adapter_registry_explicit"
_VOL_OPTIONAL = getattr(vol, "Optional", lambda key: key)


def _entry_or_error(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: Mapping[str, Any]):
    entry = hass.config_entries.async_get_entry(str(msg["entry_id"]))
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], "entry_not_found", "FRAKON Energy config entry was not found.")
        return None
    return entry


def _registry_for_hass(hass: HomeAssistant, *, registry: TariffAdapterRegistry | None = None) -> TariffAdapterRegistry:
    """Return the shared base registry and remember whether it was injected.

    An explicitly supplied registry is test/custom authority and must never be
    silently replaced by entry-derived production adapters. A registry already
    present without the origin marker is treated as explicit fail-closed state,
    which also makes partial hot reloads safe.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    existing = domain_data.get(_REGISTRY_KEY)
    if existing is not None:
        if not isinstance(existing, TariffAdapterRegistry):
            raise ValueError("stored tariff adapter registry is invalid")
        if _REGISTRY_EXPLICIT_KEY not in domain_data:
            domain_data[_REGISTRY_EXPLICIT_KEY] = True
        if registry is not None and registry is not existing:
            raise ValueError("tariff adapter registry is already configured")
        return existing
    if registry is None:
        registry = build_default_tariff_adapter_registry()
        explicit = False
    elif not isinstance(registry, TariffAdapterRegistry):
        raise ValueError("registry must be TariffAdapterRegistry")
    else:
        explicit = True
    domain_data[_REGISTRY_KEY] = registry
    domain_data[_REGISTRY_EXPLICIT_KEY] = explicit
    return registry


def _registry_for_entry(
    hass: HomeAssistant,
    entry: object,
    *,
    registry: TariffAdapterRegistry | None = None,
) -> TariffAdapterRegistry:
    """Return request registry with MND authority isolated to one config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    active_registry = _registry_for_hass(hass) if registry is None else registry
    if not isinstance(active_registry, TariffAdapterRegistry):
        raise ValueError("registry must be TariffAdapterRegistry")
    stored = domain_data.get(_REGISTRY_KEY)
    if stored is not active_registry:
        raise ValueError("tariff adapter registry is not the configured base registry")
    if domain_data.get(_REGISTRY_EXPLICIT_KEY, True):
        return active_registry

    options = getattr(entry, "options", None)
    if not isinstance(options, Mapping):
        raise ValueError("config entry options must be a mapping")
    return build_entry_tariff_adapter_registry(options)


@callback
def async_register_tariff_discovery_websocket(hass: HomeAssistant, *, registry: TariffAdapterRegistry | None = None) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    active_registry = _registry_for_hass(hass, registry=registry)
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command({
        vol.Required("type"): COMMAND_TARIFF_DISCOVER,
        vol.Required("entry_id"): str,
        vol.Required("contract"): dict,
        vol.Required("day"): str,
        _VOL_OPTIONAL("source_context"): dict,
    })
    @websocket_api.async_response
    async def websocket_tariff_discover(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: Mapping[str, Any]) -> None:
        ensure_admin(connection)
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return
        try:
            contract = ElectricityContract.from_dict(msg["contract"])
        except (TypeError, ValueError) as err:
            connection.send_error(msg["id"], "invalid_contract", str(err))
            return
        try:
            discovery_day = date.fromisoformat(str(msg["day"]))
        except ValueError:
            connection.send_error(msg["id"], "invalid_day", "day must be an ISO-8601 date")
            return
        try:
            source_context = TariffSourceResolutionContext.from_value(msg.get("source_context"))
        except (TypeError, ValueError) as err:
            connection.send_error(msg["id"], "invalid_source_context", str(err))
            return
        try:
            request_registry = _registry_for_entry(
                hass,
                entry,
                registry=active_registry,
            )
            review = await async_discover_contract_tariff_review(
                contract,
                day=discovery_day,
                registry=request_registry,
                source_context=source_context,
            )
        except LookupError as err:
            connection.send_error(msg["id"], "supplier_not_supported", str(err))
            return
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_discovery_request", str(err))
            return
        connection.send_result(msg["id"], {
            "entry_id": entry.entry_id,
            "contract_fingerprint": contract_fingerprint(contract),
            "source_context_fingerprint": tariff_source_context_fingerprint(source_context),
            "day": discovery_day.isoformat(),
            "supported_suppliers": list(request_registry.supported_suppliers()),
            "candidates": [item.as_dict() for item in review],
            "download_performed": False,
            "parsing_performed": False,
            "persistence_performed": False,
            "activation_performed": False,
        })

    websocket_api.async_register_command(hass, websocket_tariff_discover)
    domain_data[_REGISTERED_KEY] = True
