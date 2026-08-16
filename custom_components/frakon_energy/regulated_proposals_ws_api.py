"""Administrator-only regulated tariff proposal and confirmation websocket API."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .cz_regulated_2026_catalog import official_2026_regulated_inputs
from .regulated_proposals import (
    RegulatedTariffProposal,
    append_regulated_tariff_proposal,
    confirm_regulated_tariff_proposal,
    regulated_tariff_proposal_from_payload,
)

COMMAND_REGULATED_TARIFF_PROPOSE = "frakon_energy/tariff/regulated/propose"
COMMAND_REGULATED_TARIFF_OFFICIAL_PROPOSE = "frakon_energy/tariff/regulated/official_propose"
COMMAND_REGULATED_TARIFF_CONFIRM = "frakon_energy/tariff/regulated/confirm"
_REGISTERED_KEY = "regulated_tariff_proposals_websocket_registered"


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


def _day_from_message(value: Any) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("day must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise ValueError("day must be an ISO-8601 date") from err


@callback
def async_register_regulated_tariff_proposals_websocket(hass: HomeAssistant) -> None:
    """Register explicit regulator proposal/confirmation commands exactly once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_REGULATED_TARIFF_PROPOSE,
            vol.Required("entry_id"): str,
            vol.Required("bundle"): dict,
            vol.Required("evidence"): list,
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def websocket_regulated_tariff_propose(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return

        try:
            proposal = regulated_tariff_proposal_from_payload(
                msg["bundle"],
                msg["evidence"],
                proposed_at=dt_util.now(),
            )
            updated = append_regulated_tariff_proposal(entry.options, proposal)
        except (TypeError, ValueError) as err:
            connection.send_error(msg["id"], "invalid_regulated_proposal", str(err))
            return

        changed = updated != dict(entry.options)
        if changed:
            hass.config_entries.async_update_entry(entry, options=updated)

        connection.send_result(
            msg["id"],
            {
                "entry_id": entry.entry_id,
                "proposal_fingerprint": proposal.fingerprint,
                "proposed_at": proposal.proposed_at.isoformat(),
                "proposal": proposal.as_dict(),
                "persistence_performed": changed,
                "confirmation_performed": False,
                "activation_performed": False,
            },
        )

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_REGULATED_TARIFF_OFFICIAL_PROPOSE,
            vol.Required("entry_id"): str,
            vol.Required("distributor"): str,
            vol.Required("distribution_tariff"): str,
            vol.Required("breaker_code"): str,
            vol.Required("day"): str,
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def websocket_regulated_tariff_official_propose(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        """Create a server-authored proposal from frozen official 2026 evidence.

        The client supplies only tariff identity fields. Monetary values, source
        URLs, checksums and validity are selected on the server and remain
        unconfirmed until the separate fingerprint-only confirmation command.
        """
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return

        try:
            requested_day = _day_from_message(msg["day"])
            inputs = official_2026_regulated_inputs(
                distributor=str(msg["distributor"]),
                distribution_tariff=str(msg["distribution_tariff"]),
                breaker_code=str(msg["breaker_code"]),
                day=requested_day,
            )
            proposal = RegulatedTariffProposal(
                bundle=inputs.to_bundle(confirmed=False),
                evidence=inputs.regulated_evidence(),
                proposed_at=dt_util.now(),
            )
            updated = append_regulated_tariff_proposal(entry.options, proposal)
        except LookupError as err:
            connection.send_error(msg["id"], "official_regulated_tariff_not_available", str(err))
            return
        except (TypeError, ValueError) as err:
            connection.send_error(msg["id"], "invalid_official_regulated_request", str(err))
            return

        changed = updated != dict(entry.options)
        if changed:
            hass.config_entries.async_update_entry(entry, options=updated)

        connection.send_result(
            msg["id"],
            {
                "entry_id": entry.entry_id,
                "requested_day": requested_day.isoformat(),
                "proposal_fingerprint": proposal.fingerprint,
                "proposed_at": proposal.proposed_at.isoformat(),
                "proposal": proposal.as_dict(),
                "server_authored": True,
                "source_authority": "official_2026_frozen",
                "persistence_performed": changed,
                "confirmation_performed": False,
                "activation_performed": False,
            },
        )

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_REGULATED_TARIFF_CONFIRM,
            vol.Required("entry_id"): str,
            vol.Required("proposal_fingerprint"): str,
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    async def websocket_regulated_tariff_confirm(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return

        try:
            updated, version = confirm_regulated_tariff_proposal(
                entry.options,
                str(msg["proposal_fingerprint"]),
            )
        except LookupError as err:
            connection.send_error(msg["id"], "regulated_proposal_not_found", str(err))
            return
        except (TypeError, ValueError) as err:
            connection.send_error(msg["id"], "regulated_confirmation_failed", str(err))
            return

        changed = updated != dict(entry.options)
        if changed:
            hass.config_entries.async_update_entry(entry, options=updated)

        connection.send_result(
            msg["id"],
            {
                "entry_id": entry.entry_id,
                "proposal_fingerprint": str(msg["proposal_fingerprint"]),
                "regulated_version_fingerprint": version.fingerprint,
                "confirmed": True,
                "persistence_performed": changed,
                "confirmation_performed": changed,
                "activation_performed": False,
            },
        )

    websocket_api.async_register_command(hass, websocket_regulated_tariff_propose)
    websocket_api.async_register_command(hass, websocket_regulated_tariff_official_propose)
    websocket_api.async_register_command(hass, websocket_regulated_tariff_confirm)
    domain_data[_REGISTERED_KEY] = True
