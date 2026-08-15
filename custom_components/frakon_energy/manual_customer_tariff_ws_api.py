"""Administrator-only manual supplier-commercial customer tariff proposal flow.

The manual path deliberately exposes proposal staging only. Final confirmation
is shared with the automatic path through ``frakon_energy/tariff/customer/confirm``
so there is exactly one customer-tariff activation boundary.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .all_in_authority import AllInTariffAuthorityMethod
from .const import DOMAIN
from .contracts import ElectricityContract
from .customer_tariff_proposals import stage_customer_tariff_proposal
from .manual_tariff_preview import (
    ManualSupplierCommercialInput,
    build_manual_all_in_tariff_preview,
)
from .regulated_catalog import select_confirmed_regulated_tariff_for_day
from .tariff_candidate_selection import select_tariff_candidate
from .tariff_discovery import async_discover_contract_tariff_candidates
from .tariff_discovery_ws_api import _registry_for_entry, _registry_for_hass
from .tariff_download import ValidatedTariffDownload
from .tariff_fetch import TariffNotModified, build_tariff_fetch_request
from .tariff_http_ha import async_fetch_selected_tariff_document_ha
from .tariff_source_context import (
    TariffSourceResolutionContext,
    tariff_source_context_fingerprint,
)

COMMAND_MANUAL_CUSTOMER_TARIFF_PROPOSE = (
    "frakon_energy/tariff/customer/manual/propose"
)
_REGISTERED_KEY = "manual_customer_tariff_websocket_registered"
_VOL_OPTIONAL = getattr(vol, "Optional", lambda key: key)
_MANUAL_FIELDS = frozenset(
    {
        "high_rate_czk_per_kwh",
        "low_rate_czk_per_kwh",
        "supplier_standing_czk_month",
    }
)
_DECIMAL_RE = re.compile(r"^\d{1,12}(?:\.\d{1,6})?$")


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


def _decimal_string(value: Any, field: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a decimal string")
    normalized = value.strip()
    if not _DECIMAL_RE.fullmatch(normalized):
        raise ValueError(
            f"{field} must use a non-negative plain decimal string"
        )
    try:
        parsed = Decimal(normalized)
    except (InvalidOperation, ValueError) as err:
        raise ValueError(f"{field} must be a decimal string") from err
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return parsed


def _manual_commercial_from_payload(
    value: object,
) -> ManualSupplierCommercialInput:
    if not isinstance(value, Mapping):
        raise ValueError("manual_commercial must be an object")
    keys = set(value)
    missing = _MANUAL_FIELDS - keys
    unexpected = keys - _MANUAL_FIELDS
    if missing:
        raise ValueError(
            "manual_commercial is missing fields: "
            + ", ".join(sorted(missing))
        )
    if unexpected:
        raise ValueError(
            "manual_commercial contains unsupported fields: "
            + ", ".join(sorted(str(item) for item in unexpected))
        )
    return ManualSupplierCommercialInput(
        high_rate_czk_per_kwh=_decimal_string(
            value.get("high_rate_czk_per_kwh"), "high_rate_czk_per_kwh"
        ),
        low_rate_czk_per_kwh=_decimal_string(
            value.get("low_rate_czk_per_kwh"), "low_rate_czk_per_kwh"
        ),
        supplier_standing_czk_month=_decimal_string(
            value.get("supplier_standing_czk_month"),
            "supplier_standing_czk_month",
        ),
    )


@callback
def async_register_manual_customer_tariff_websocket(
    hass: HomeAssistant,
) -> None:
    """Register the manual proposal boundary exactly once per HA runtime."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    active_registry = _registry_for_hass(hass)
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_MANUAL_CUSTOMER_TARIFF_PROPOSE,
            vol.Required("entry_id"): str,
            vol.Required("contract"): dict,
            vol.Required("day"): str,
            vol.Required("candidate_fingerprint"): str,
            vol.Required("manual_commercial"): dict,
            _VOL_OPTIONAL("source_context"): dict,
        }
    )
    @websocket_api.async_response
    async def websocket_manual_customer_tariff_propose(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        connection.require_admin()
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return

        try:
            contract = replace(
                ElectricityContract.from_dict(msg["contract"]),
                customer_confirmed=False,
            )
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
        if not contract.applies_on(discovery_day):
            connection.send_error(
                msg["id"],
                "invalid_discovery_request",
                "contract does not apply on requested discovery day",
            )
            return

        try:
            source_context = TariffSourceResolutionContext.from_value(
                msg.get("source_context")
            )
        except (TypeError, ValueError) as err:
            connection.send_error(
                msg["id"], "invalid_source_context", str(err)
            )
            return

        try:
            manual_commercial = _manual_commercial_from_payload(
                msg.get("manual_commercial")
            )
        except (TypeError, ValueError) as err:
            connection.send_error(
                msg["id"], "invalid_manual_commercial", str(err)
            )
            return

        # Regulated authority must be resolved before supplier discovery/network I/O.
        try:
            regulated_version = select_confirmed_regulated_tariff_for_day(
                entry.options,
                distributor=contract.distributor.value,
                distribution_tariff=contract.distribution_tariff,
                breaker_code=contract.breaker.code,
                day=discovery_day,
            )
        except LookupError as err:
            connection.send_error(
                msg["id"], "regulated_tariff_not_available", str(err)
            )
            return
        except ValueError as err:
            connection.send_error(
                msg["id"], "regulated_tariff_invalid", str(err)
            )
            return

        try:
            request_registry = _registry_for_entry(
                hass,
                entry,
                registry=active_registry,
            )
            candidates = await async_discover_contract_tariff_candidates(
                contract,
                day=discovery_day,
                registry=request_registry,
                source_context=source_context,
            )
        except LookupError as err:
            connection.send_error(
                msg["id"], "supplier_not_supported", str(err)
            )
            return
        except ValueError as err:
            connection.send_error(
                msg["id"], "invalid_discovery_request", str(err)
            )
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
            connection.send_error(
                msg["id"], "candidate_not_found", str(err)
            )
            return
        except ValueError as err:
            connection.send_error(
                msg["id"], "invalid_candidate_selection", str(err)
            )
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
            connection.send_error(
                msg["id"],
                "not_modified_without_cached_document",
                "The selected tariff document was not modified, but no validated "
                "PDF bytes are available for manual customer tariff proposal.",
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
            preview = build_manual_all_in_tariff_preview(
                download=download,
                manual_commercial=manual_commercial,
                contract=contract,
                regulated=regulated_version.bundle,
                regulated_evidence=regulated_version.evidence,
            )
        except LookupError as err:
            connection.send_error(
                msg["id"], "manual_tariff_not_supported", str(err)
            )
            return
        except (TypeError, ValueError) as err:
            connection.send_error(
                msg["id"], "manual_tariff_proposal_failed", str(err)
            )
            return

        if (
            preview.authority_method
            is not AllInTariffAuthorityMethod.MANUAL_USER_ENTRY
            or preview.parsing_performed
            or preview.persistence_performed
            or preview.activation_performed
        ):
            connection.send_error(
                msg["id"],
                "manual_tariff_proposal_failed",
                "Manual preview returned an unsafe authority or side-effect state.",
            )
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
                authority_method=AllInTariffAuthorityMethod.MANUAL_USER_ENTRY,
            )
        except (LookupError, TypeError, ValueError) as err:
            connection.send_error(
                msg["id"], "manual_tariff_proposal_failed", str(err)
            )
            return

        changed = updated != dict(entry.options)
        if changed:
            hass.config_entries.async_update_entry(entry, options=updated)

        connection.send_result(
            msg["id"],
            {
                "entry_id": entry.entry_id,
                "proposal_fingerprint": proposal.fingerprint,
                "contract_fingerprint": proposal.contract_fingerprint,
                "source_context_fingerprint": tariff_source_context_fingerprint(
                    source_context
                ),
                "all_in_tariff_fingerprint": proposal.all_in_tariff_fingerprint,
                "candidate_fingerprint": proposal.candidate_fingerprint,
                "regulated_version_fingerprint": (
                    proposal.regulated_version_fingerprint
                ),
                "proposed_for_day": proposal.proposed_for_day.isoformat(),
                "proposed_at": proposal.proposed_at.isoformat(),
                "checked_at": download.validated_at.isoformat(),
                "source_url": download.document.source_url,
                "document_sha256": download.document.sha256,
                "content_bytes": len(download.content),
                "authority_method": AllInTariffAuthorityMethod.MANUAL_USER_ENTRY.value,
                "manual_entry": True,
                "download_performed": True,
                "parsing_performed": False,
                "all_in_preview_performed": True,
                "persistence_performed": changed,
                "confirmation_performed": False,
                "activation_performed": False,
                "preview": preview.as_dict(),
            },
        )

    websocket_api.async_register_command(
        hass,
        websocket_manual_customer_tariff_propose,
    )
    domain_data[_REGISTERED_KEY] = True
