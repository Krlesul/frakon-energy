"""Administrator-only selected tariff document download preview websocket API."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .contracts import ElectricityContract, contract_fingerprint
from .tariff_candidate_selection import select_tariff_candidate
from .tariff_discovery import async_discover_contract_tariff_candidates
from .tariff_discovery_ws_api import _registry_for_entry, _registry_for_hass
from .tariff_fetch import TariffNotModified, build_tariff_fetch_request
from .tariff_http_ha import async_fetch_selected_tariff_document_ha
from .tariff_source_context import (
    TariffSourceResolutionContext,
    tariff_source_context_fingerprint,
)

COMMAND_TARIFF_DOWNLOAD_PREVIEW = "frakon_energy/tariff/download_preview"
_REGISTERED_KEY = "tariff_download_preview_websocket_registered"
_VOL_OPTIONAL = getattr(vol, "Optional", lambda key: key)


def _entry_or_error(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: Mapping[str, Any],
):
    entry = hass.config_entries.async_get_entry(str(msg["entry_id"]))
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(
            msg["id"],
            "entry_not_found",
            "FRAKON Energy config entry was not found.",
        )
        return None
    return entry


@callback
def async_register_tariff_download_preview_websocket(hass: HomeAssistant) -> None:
    """Register exact-candidate download preview command once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    registry = _registry_for_hass(hass)
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_TARIFF_DOWNLOAD_PREVIEW,
            vol.Required("entry_id"): str,
            vol.Required("contract"): dict,
            vol.Required("day"): str,
            vol.Required("candidate_fingerprint"): str,
            _VOL_OPTIONAL("source_context"): dict,
        }
    )
    @websocket_api.async_response
    async def websocket_tariff_download_preview(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        connection.require_admin()
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
            connection.send_error(
                msg["id"],
                "invalid_day",
                "day must be an ISO-8601 date",
            )
            return

        try:
            source_context = TariffSourceResolutionContext.from_value(
                msg.get("source_context")
            )
        except (TypeError, ValueError) as err:
            connection.send_error(msg["id"], "invalid_source_context", str(err))
            return

        try:
            request_registry = _registry_for_entry(
                hass,
                entry,
                registry=registry,
            )
            candidates = await async_discover_contract_tariff_candidates(
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

        try:
            selected_fingerprint = str(msg["candidate_fingerprint"])
            candidate = select_tariff_candidate(
                candidates,
                fingerprint=selected_fingerprint,
            )
            request = build_tariff_fetch_request(
                candidate,
                selected_fingerprint=selected_fingerprint,
            )
        except LookupError as err:
            connection.send_error(msg["id"], "candidate_not_found", str(err))
            return
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_candidate_selection", str(err))
            return

        checked_at = dt_util.now()
        try:
            result = await async_fetch_selected_tariff_document_ha(
                hass,
                candidate=candidate,
                request=request,
                checked_at=checked_at,
            )
        except Exception as err:
            connection.send_error(msg["id"], "download_failed", str(err))
            return

        context_fingerprint = tariff_source_context_fingerprint(source_context)
        if isinstance(result, TariffNotModified):
            connection.send_result(
                msg["id"],
                {
                    "entry_id": entry.entry_id,
                    "contract_fingerprint": contract_fingerprint(contract),
                    "source_context_fingerprint": context_fingerprint,
                    "candidate_fingerprint": selected_fingerprint,
                    "source_url": result.source_url,
                    "checked_at": result.checked_at.isoformat(),
                    "document_sha256": candidate.document.sha256,
                    "etag": result.etag,
                    "last_modified": result.last_modified,
                    "content_bytes": 0,
                    "body_downloaded": False,
                    "download_performed": False,
                    "parser_authorized": False,
                    "parsing_performed": False,
                    "persistence_performed": False,
                    "activation_performed": False,
                },
            )
            return

        connection.send_result(
            msg["id"],
            {
                "entry_id": entry.entry_id,
                "contract_fingerprint": contract_fingerprint(contract),
                "source_context_fingerprint": context_fingerprint,
                "candidate_fingerprint": result.selected_fingerprint,
                "source_url": result.document.source_url,
                "checked_at": result.validated_at.isoformat(),
                "document_sha256": result.document.sha256,
                "etag": result.document.etag,
                "last_modified": result.document.last_modified,
                "content_bytes": len(result.content),
                "body_downloaded": True,
                "download_performed": True,
                "parser_authorized": result.parser_authorized,
                "parsing_performed": False,
                "persistence_performed": result.persistence_performed,
                "activation_performed": result.activation_performed,
            },
        )

    websocket_api.async_register_command(hass, websocket_tariff_download_preview)
    domain_data[_REGISTERED_KEY] = True
