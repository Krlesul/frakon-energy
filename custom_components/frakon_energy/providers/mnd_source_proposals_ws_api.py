"""Administrator-only two-phase MND source proposal/confirmation websocket API."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from ..ws_auth import ensure_admin
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..contracts import Distributor
from ..tariff_candidate_selection import tariff_candidate_selection_fingerprint
from ..tariff_download import ValidatedTariffDownload
from ..tariff_fetch import TariffNotModified, build_tariff_fetch_request
from ..tariff_http_ha import async_fetch_selected_tariff_document_ha
from ..tariff_source_context import (
    TariffSourceResolutionContext,
    tariff_source_context_fingerprint,
)
from ..tariff_sources import (
    PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    OfficialTariffDocument,
    TariffDocumentCandidate,
)
from .mnd_confirmed_source_resolver import (
    confirmed_mnd_source_resolution_fingerprint,
)
from .mnd_source_proposals import (
    MndSourceProposal,
    append_mnd_source_proposal,
    confirm_mnd_source_proposal,
)
from .mnd_tariffs import (
    MND_SUPPLIER,
    MndResolvedTariffSource,
    mnd_product_definition,
)

COMMAND_MND_SOURCE_PROPOSE = "frakon_energy/tariff/mnd/source/propose"
COMMAND_MND_SOURCE_CONFIRM = "frakon_energy/tariff/mnd/source/confirm"
_REGISTERED_KEY = "mnd_source_proposals_websocket_registered"
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


def _date(value: Any, field: str, *, optional: bool = False) -> date | None:
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 date") from err


def _proposal_candidate(
    *,
    source_context: TariffSourceResolutionContext,
    product_name: str,
    distributor: str,
    contract_kind: str,
    source_url: str,
    valid_from: date,
    valid_to: date | None,
    discovered_at,
) -> tuple[TariffDocumentCandidate, str]:
    """Build an untrusted-source candidate only after exact MND identity validation."""
    if source_context.postcode is None:
        raise ValueError("MND source proposal requires an explicit postcode context")
    try:
        distributor_value = Distributor(distributor).value
    except (TypeError, ValueError) as err:
        raise ValueError("unsupported MND distribution territory") from err

    product = mnd_product_definition(product_name, contract_kind)
    if product is None:
        raise ValueError("MND source proposal must match exactly one verified product")
    if product.advertised_valid_to is not None and valid_to != product.advertised_valid_to:
        raise ValueError("MND source validity end does not match public product evidence")
    if product.advertised_valid_to is None and valid_to is not None:
        raise ValueError("indefinite MND product cannot have a validity end")

    validated_source = MndResolvedTariffSource(
        product_name=product.product_name,
        distributor=distributor_value,
        contract_kind=product.contract_kind,
        source_url=source_url,
        valid_from=valid_from,
        valid_to=valid_to,
        document_date=None,
        discovered_at=discovered_at,
        sha256=None,
    )
    candidate = TariffDocumentCandidate(
        document=OfficialTariffDocument(
            supplier=MND_SUPPLIER,
            source_url=validated_source.source_url,
            discovered_at=discovered_at,
            document_date=None,
            content_type="application/pdf",
        ),
        product_name=product.product_name,
        valid_from=valid_from,
        valid_to=valid_to,
        match_score=100,
        match_reasons=(
            "manual MND source proposal awaiting explicit confirmation",
            "exact verified MND product and contract kind",
            "official MND /documents/view source URL",
            "public MND fixed-term validity boundary match",
            "no price authority before PDF parsing",
        ),
        price_scope=PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )
    return candidate, distributor_value


@callback
def async_register_mnd_source_proposals_websocket(hass: HomeAssistant) -> None:
    """Register explicit MND source proposal/confirmation commands exactly once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_MND_SOURCE_PROPOSE,
            vol.Required("entry_id"): str,
            vol.Required("source_context"): dict,
            vol.Required("product_name"): str,
            vol.Required("distributor"): str,
            vol.Required("contract_kind"): str,
            vol.Required("source_url"): str,
            vol.Required("valid_from"): str,
            _VOL_OPTIONAL("valid_to"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_mnd_source_propose(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        ensure_admin(connection)
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return

        proposed_at = dt_util.now()
        try:
            source_context = TariffSourceResolutionContext.from_value(
                msg.get("source_context")
            )
            valid_from = _date(msg.get("valid_from"), "valid_from")
            if valid_from is None:
                raise ValueError("valid_from must be an ISO-8601 date")
            valid_to = _date(msg.get("valid_to"), "valid_to", optional=True)
            candidate, distributor = _proposal_candidate(
                source_context=source_context,
                product_name=str(msg.get("product_name", "")),
                distributor=str(msg.get("distributor", "")),
                contract_kind=str(msg.get("contract_kind", "")),
                source_url=str(msg.get("source_url", "")),
                valid_from=valid_from,
                valid_to=valid_to,
                discovered_at=proposed_at,
            )
            selected_fingerprint = tariff_candidate_selection_fingerprint(candidate)
            request = build_tariff_fetch_request(
                candidate,
                selected_fingerprint=selected_fingerprint,
            )
        except (TypeError, ValueError) as err:
            connection.send_error(msg["id"], "invalid_mnd_source_proposal", str(err))
            return

        try:
            download = await async_fetch_selected_tariff_document_ha(
                hass,
                candidate=candidate,
                request=request,
                checked_at=proposed_at,
            )
        except Exception as err:
            connection.send_error(msg["id"], "mnd_source_download_failed", str(err))
            return
        if isinstance(download, TariffNotModified) or not isinstance(
            download, ValidatedTariffDownload
        ):
            connection.send_error(
                msg["id"],
                "mnd_source_download_failed",
                "MND source proposal requires a newly validated PDF body.",
            )
            return
        if download.document.sha256 is None:
            connection.send_error(
                msg["id"],
                "mnd_source_download_failed",
                "validated MND source document is missing SHA-256",
            )
            return

        try:
            proposal = MndSourceProposal(
                source_context_fingerprint=tariff_source_context_fingerprint(
                    source_context
                ),
                product_name=candidate.product_name,
                distributor=distributor,
                contract_kind=str(msg.get("contract_kind", "")),
                source_url=download.document.source_url,
                valid_from=candidate.valid_from,
                valid_to=candidate.valid_to,
                document_date=download.document.document_date,
                document_sha256=download.document.sha256,
                proposed_at=proposed_at,
            )
            updated = append_mnd_source_proposal(entry.options, proposal)
        except (TypeError, ValueError) as err:
            connection.send_error(msg["id"], "invalid_mnd_source_proposal", str(err))
            return

        changed = updated != dict(entry.options)
        if changed:
            hass.config_entries.async_update_entry(entry, options=updated)

        connection.send_result(
            msg["id"],
            {
                "entry_id": entry.entry_id,
                "proposal_fingerprint": proposal.fingerprint,
                "source_context_fingerprint": proposal.source_context_fingerprint,
                "product_name": proposal.product_name,
                "distributor": proposal.distributor,
                "contract_kind": proposal.contract_kind,
                "source_url": proposal.source_url,
                "valid_from": proposal.valid_from.isoformat(),
                "valid_to": (
                    proposal.valid_to.isoformat()
                    if proposal.valid_to is not None
                    else None
                ),
                "document_sha256": proposal.document_sha256,
                "checked_at": download.validated_at.isoformat(),
                "content_bytes": len(download.content),
                "download_performed": True,
                "parsing_performed": False,
                "persistence_performed": changed,
                "confirmation_performed": False,
                "activation_performed": False,
                "proposal": proposal.as_dict(),
            },
        )

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_MND_SOURCE_CONFIRM,
            vol.Required("entry_id"): str,
            vol.Required("proposal_fingerprint"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_mnd_source_confirm(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        ensure_admin(connection)
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return

        try:
            updated, resolution = confirm_mnd_source_proposal(
                entry.options,
                str(msg["proposal_fingerprint"]),
                confirmed_at=dt_util.now(),
            )
        except LookupError as err:
            connection.send_error(msg["id"], "mnd_source_proposal_not_found", str(err))
            return
        except (TypeError, ValueError) as err:
            connection.send_error(msg["id"], "mnd_source_confirmation_failed", str(err))
            return

        changed = updated != dict(entry.options)
        if changed:
            hass.config_entries.async_update_entry(entry, options=updated)

        connection.send_result(
            msg["id"],
            {
                "entry_id": entry.entry_id,
                "proposal_fingerprint": str(msg["proposal_fingerprint"]),
                "confirmed_resolution_fingerprint": (
                    confirmed_mnd_source_resolution_fingerprint(resolution)
                ),
                "document_sha256": resolution.document_sha256,
                "confirmed": True,
                "download_performed": False,
                "parsing_performed": False,
                "persistence_performed": changed,
                "confirmation_performed": changed,
                "activation_performed": False,
            },
        )

    websocket_api.async_register_command(hass, websocket_mnd_source_propose)
    websocket_api.async_register_command(hass, websocket_mnd_source_confirm)
    domain_data[_REGISTERED_KEY] = True
