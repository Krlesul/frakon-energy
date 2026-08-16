"""Administrator-only server-verified customer tariff proposal workflow."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from .ws_auth import ensure_admin
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .contracts import ElectricityContract
from .customer_tariff_proposals import (
    confirm_customer_tariff_proposal,
    stage_customer_tariff_proposal,
)
from .regulated_catalog import (
    ConfirmedRegulatedTariffVersion,
    select_confirmed_regulated_tariff_for_day,
)
from .tariff_all_in_preview import AllInTariffPreview, build_all_in_tariff_preview
from .tariff_candidate_selection import select_tariff_candidate
from .tariff_discovery import async_discover_contract_tariff_candidates
from .tariff_discovery_ws_api import _registry_for_hass
from .tariff_download import ValidatedTariffDownload
from .tariff_fetch import TariffNotModified, build_tariff_fetch_request
from .tariff_http_ha import async_fetch_selected_tariff_document_ha
from .tariff_parser_preview import parse_supplier_tariff_preview
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

COMMAND_CUSTOMER_TARIFF_PROPOSE = "frakon_energy/tariff/customer/propose"
COMMAND_CUSTOMER_TARIFF_CONFIRM = "frakon_energy/tariff/customer/confirm"
_REGISTERED_KEY = "customer_tariff_proposals_websocket_registered"
_VOL_OPTIONAL = getattr(vol, "Optional", lambda key: key)


def _entry_or_error(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: Mapping[str, Any],
):
    entry = hass.config_entries.async_get_entry(str(msg["entry_id"]))
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], "entry_not_found", "FRAKON Energy config entry was not found.")
        return None
    return entry


def _extract_parse_and_assemble(download: ValidatedTariffDownload, contract: ElectricityContract, regulated_version: ConfirmedRegulatedTariffVersion) -> AllInTariffPreview:
    extracted = extract_validated_tariff_pdf_text(download)
    parsed = parse_supplier_tariff_preview(download, extracted, contract)
    return build_all_in_tariff_preview(
        download=download,
        parsed=parsed,
        contract=contract,
        regulated=regulated_version.bundle,
        regulated_evidence=regulated_version.evidence,
    )


async def _discover_candidates(
    contract: ElectricityContract,
    *,
    discovery_day: date,
    registry: object,
    source_context: TariffSourceResolutionContext,
):
    """Preserve the legacy call shape when there is no operational context."""
    if source_context.is_empty:
        return await async_discover_contract_tariff_candidates(
            contract,
            day=discovery_day,
            registry=registry,
        )
    return await async_discover_contract_tariff_candidates(
        contract,
        day=discovery_day,
        registry=registry,
        source_context=source_context,
    )


@callback
def async_register_customer_tariff_proposals_websocket(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return
    registry = _registry_for_hass(hass)

    @websocket_api.websocket_command({
        vol.Required("type"): COMMAND_CUSTOMER_TARIFF_PROPOSE,
        vol.Required("entry_id"): str,
        vol.Required("contract"): dict,
        vol.Required("day"): str,
        vol.Required("candidate_fingerprint"): str,
        _VOL_OPTIONAL("source_context"): dict,
    })
    @websocket_api.async_response
    async def websocket_customer_tariff_propose(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: Mapping[str, Any]) -> None:
        ensure_admin(connection)
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return
        try:
            contract = replace(ElectricityContract.from_dict(msg["contract"]), customer_confirmed=False)
        except (TypeError, ValueError) as err:
            connection.send_error(msg["id"], "invalid_contract", str(err))
            return
        if not supplier_parser_supported(contract.supplier):
            connection.send_error(msg["id"], "parser_not_supported", f"supplier parser preview is not implemented: {contract.supplier.value}")
            return
        try:
            discovery_day = date.fromisoformat(str(msg["day"]))
        except ValueError:
            connection.send_error(msg["id"], "invalid_day", "day must be an ISO-8601 date")
            return
        if not contract.applies_on(discovery_day):
            connection.send_error(msg["id"], "invalid_discovery_request", "contract does not apply on requested discovery day")
            return
        try:
            source_context = TariffSourceResolutionContext.from_value(msg.get("source_context"))
        except (TypeError, ValueError) as err:
            connection.send_error(msg["id"], "invalid_source_context", str(err))
            return

        try:
            regulated_version = select_confirmed_regulated_tariff_for_day(
                entry.options,
                distributor=contract.distributor.value,
                distribution_tariff=contract.distribution_tariff,
                breaker_code=contract.breaker.code,
                day=discovery_day,
            )
        except LookupError as err:
            connection.send_error(msg["id"], "regulated_tariff_not_available", str(err))
            return
        except ValueError as err:
            connection.send_error(msg["id"], "regulated_tariff_invalid", str(err))
            return

        try:
            candidates = await _discover_candidates(
                contract,
                discovery_day=discovery_day,
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
            candidate = select_tariff_candidate(candidates, fingerprint=selected_fingerprint)
            request = build_tariff_fetch_request(candidate, selected_fingerprint=selected_fingerprint)
        except LookupError as err:
            connection.send_error(msg["id"], "candidate_not_found", str(err))
            return
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_candidate_selection", str(err))
            return

        try:
            download = await async_fetch_selected_tariff_document_ha(
                hass,
                candidate=candidate,
                request=request,
                checked_at=dt_util.now(),
            )
        except Exception as err:
            connection.send_error(msg["id"], "download_failed", str(err))
            return
        if isinstance(download, TariffNotModified):
            connection.send_error(msg["id"], "not_modified_without_cached_document", "The selected tariff document was not modified, but no validated PDF bytes are available for customer tariff proposal.")
            return
        if not isinstance(download, ValidatedTariffDownload):
            connection.send_error(msg["id"], "download_failed", "Tariff download returned an unsupported result.")
            return

        try:
            preview = await hass.async_add_executor_job(_extract_parse_and_assemble, download, contract, regulated_version)
        except LookupError as err:
            connection.send_error(msg["id"], "parser_not_supported", str(err))
            return
        except Exception as err:
            connection.send_error(msg["id"], "customer_tariff_proposal_failed", str(err))
            return
        try:
            updated, proposal = stage_customer_tariff_proposal(
                entry.options,
                contract=contract,
                assembly=preview.assembly,
                candidate_fingerprint=download.selected_fingerprint,
                regulated_version_fingerprint=regulated_version.fingerprint,
                proposed_for_day=discovery_day,
                proposed_at=dt_util.now(),
            )
        except (LookupError, TypeError, ValueError) as err:
            connection.send_error(msg["id"], "customer_tariff_proposal_failed", str(err))
            return
        changed = updated != dict(entry.options)
        if changed:
            hass.config_entries.async_update_entry(entry, options=updated)
        connection.send_result(msg["id"], {
            "entry_id": entry.entry_id,
            "proposal_fingerprint": proposal.fingerprint,
            "contract_fingerprint": proposal.contract_fingerprint,
            "source_context_fingerprint": tariff_source_context_fingerprint(source_context),
            "all_in_tariff_fingerprint": proposal.all_in_tariff_fingerprint,
            "candidate_fingerprint": proposal.candidate_fingerprint,
            "regulated_version_fingerprint": proposal.regulated_version_fingerprint,
            "proposed_for_day": proposal.proposed_for_day.isoformat(),
            "proposed_at": proposal.proposed_at.isoformat(),
            "checked_at": download.validated_at.isoformat(),
            "source_url": download.document.source_url,
            "document_sha256": download.document.sha256,
            "content_bytes": len(download.content),
            "download_performed": True,
            "parsing_performed": True,
            "all_in_preview_performed": True,
            "persistence_performed": changed,
            "confirmation_performed": False,
            "activation_performed": False,
            "preview": preview.as_dict(),
        })

    @websocket_api.websocket_command({
        vol.Required("type"): COMMAND_CUSTOMER_TARIFF_CONFIRM,
        vol.Required("entry_id"): str,
        vol.Required("proposal_fingerprint"): str,
    })
    @websocket_api.async_response
    async def websocket_customer_tariff_confirm(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: Mapping[str, Any]) -> None:
        ensure_admin(connection)
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return
        try:
            updated, proposal = confirm_customer_tariff_proposal(entry.options, str(msg["proposal_fingerprint"]))
        except LookupError as err:
            connection.send_error(msg["id"], "customer_tariff_proposal_not_found", str(err))
            return
        except (TypeError, ValueError) as err:
            connection.send_error(msg["id"], "customer_tariff_confirmation_failed", str(err))
            return
        changed = updated != dict(entry.options)
        if changed:
            hass.config_entries.async_update_entry(entry, options=updated)
        connection.send_result(msg["id"], {
            "entry_id": entry.entry_id,
            "proposal_fingerprint": proposal.fingerprint,
            "contract_fingerprint": proposal.contract_fingerprint,
            "all_in_tariff_fingerprint": proposal.all_in_tariff_fingerprint,
            "regulated_version_fingerprint": proposal.regulated_version_fingerprint,
            "confirmed": True,
            "persistence_performed": changed,
            "confirmation_performed": changed,
            "activation_performed": changed,
        })

    websocket_api.async_register_command(hass, websocket_customer_tariff_propose)
    websocket_api.async_register_command(hass, websocket_customer_tariff_confirm)
    domain_data[_REGISTERED_KEY] = True
