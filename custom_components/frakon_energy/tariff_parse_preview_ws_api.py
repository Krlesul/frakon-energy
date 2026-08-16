"""Administrator-only end-to-end tariff parser preview websocket API."""

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
from .tariff_discovery_ws_api import _registry_for_hass
from .tariff_download import ValidatedTariffDownload
from .tariff_fetch import TariffNotModified, build_tariff_fetch_request
from .tariff_http_ha import async_fetch_selected_tariff_document_ha
from .tariff_parser_preview import (
    SupplierTariffParsePreview,
    parse_supplier_tariff_preview,
)
try:
    from .tariff_parser_preview import supplier_parser_supported
except ImportError:
    def supplier_parser_supported(supplier: object) -> bool:
        return getattr(supplier, "value", supplier) == "cez"

from .tariff_pdf_text import extract_validated_tariff_pdf_text
from .tariff_source_context import (
    TariffSourceResolutionContext,
    tariff_source_context_fingerprint,
)

COMMAND_TARIFF_PARSE_PREVIEW = "frakon_energy/tariff/parse_preview"
_REGISTERED_KEY = "tariff_parse_preview_websocket_registered"
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


def _extract_and_parse(
    download: ValidatedTariffDownload,
    contract: ElectricityContract,
) -> SupplierTariffParsePreview:
    """Run bounded PDF extraction and pure parser off the Home Assistant event loop."""
    extracted = extract_validated_tariff_pdf_text(download)
    return parse_supplier_tariff_preview(download, extracted, contract)


@callback
def async_register_tariff_parse_preview_websocket(hass: HomeAssistant) -> None:
    """Register read-only selected-document parser preview command once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return
    registry = _registry_for_hass(hass)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_TARIFF_PARSE_PREVIEW,
            vol.Required("entry_id"): str,
            vol.Required("contract"): dict,
            vol.Required("day"): str,
            vol.Required("candidate_fingerprint"): str,
            _VOL_OPTIONAL("source_context"): dict,
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def websocket_tariff_parse_preview(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return

        try:
            contract = ElectricityContract.from_dict(msg["contract"])
        except (TypeError, ValueError) as err:
            connection.send_error(msg["id"], "invalid_contract", str(err))
            return

        if not supplier_parser_supported(contract.supplier):
            connection.send_error(
                msg["id"],
                "parser_not_supported",
                f"supplier parser preview is not implemented: {contract.supplier.value}",
            )
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
            candidates = await async_discover_contract_tariff_candidates(
                contract,
                day=discovery_day,
                registry=registry,
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
            download = await async_fetch_selected_tariff_document_ha(
                hass,
                candidate=candidate,
                request=request,
                checked_at=checked_at,
            )
        except Exception as err:
            connection.send_error(msg["id"], "download_failed", str(err))
            return

        if isinstance(download, TariffNotModified):
            connection.send_error(
                msg["id"],
                "not_modified_without_cached_document",
                "The selected tariff document was not modified, but no validated PDF bytes are available for parser preview.",
            )
            return
        if not isinstance(download, ValidatedTariffDownload):
            connection.send_error(
                msg["id"],
                "download_failed",
                "Tariff download returned an unsupported result.",
            )
            return

        try:
            preview = await hass.async_add_executor_job(
                _extract_and_parse,
                download,
                contract,
            )
        except LookupError as err:
            connection.send_error(msg["id"], "parser_not_supported", str(err))
            return
        except ValueError as err:
            connection.send_error(msg["id"], "parse_failed", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "parse_failed", str(err))
            return

        connection.send_result(
            msg["id"],
            {
                "entry_id": entry.entry_id,
                "contract_fingerprint": contract_fingerprint(contract),
                "source_context_fingerprint": tariff_source_context_fingerprint(
                    source_context
                ),
                "candidate_fingerprint": download.selected_fingerprint,
                "checked_at": download.validated_at.isoformat(),
                "source_url": download.document.source_url,
                "document_sha256": download.document.sha256,
                "content_bytes": len(download.content),
                "download_performed": True,
                "parsing_performed": preview.parsing_performed,
                "persistence_performed": preview.persistence_performed,
                "activation_performed": preview.activation_performed,
                "preview": preview.as_dict(),
            },
        )

    websocket_api.async_register_command(hass, websocket_tariff_parse_preview)
    domain_data[_REGISTERED_KEY] = True
