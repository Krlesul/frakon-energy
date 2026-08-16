"""Administrator-only proposal/confirmation API for legacy tariff history migration."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import voluptuous as vol
from homeassistant.components import websocket_api
from .ws_auth import ensure_admin
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .legacy_tariff_history import (
    LegacyTariffSnapshot,
    append_legacy_tariff_snapshot,
    confirm_legacy_tariff_snapshot,
    legacy_tariff_fingerprint,
    legacy_tariff_history_from_options,
    legacy_tariff_snapshot_from_options,
)

COMMAND_LEGACY_TARIFF_PROPOSE = "frakon_energy/tariff/legacy/propose"
COMMAND_LEGACY_TARIFF_CONFIRM = "frakon_energy/tariff/legacy/confirm"
_REGISTERED_KEY = "legacy_tariff_migration_websocket_registered"


def _parse_day(value: Any, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"{field} must be an ISO-8601 date") from err


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


def _snapshot_for_fingerprint(
    options: Mapping[str, Any],
    fingerprint: str,
) -> LegacyTariffSnapshot:
    matches = [
        item
        for item in legacy_tariff_history_from_options(options)
        if legacy_tariff_fingerprint(item) == fingerprint
    ]
    if not matches:
        raise LookupError(f"legacy tariff snapshot not found: {fingerprint}")
    if len(matches) != 1:
        raise ValueError("ambiguous legacy tariff snapshot fingerprint")
    return matches[0]


def _snapshot_result(snapshot: LegacyTariffSnapshot) -> dict[str, Any]:
    payload = snapshot.as_dict()
    payload["fingerprint"] = legacy_tariff_fingerprint(snapshot)
    payload["historical_only"] = True
    payload["live_pricing_changed"] = False
    payload["activation_performed"] = False
    return payload


@callback
def async_register_legacy_tariff_migration_websocket(hass: HomeAssistant) -> None:
    """Register explicit historical legacy migration commands once."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_REGISTERED_KEY):
        return

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_LEGACY_TARIFF_PROPOSE,
            vol.Required("entry_id"): str,
            vol.Required("valid_from"): str,
            vol.Required("valid_to"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_legacy_tariff_propose(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        ensure_admin(connection)
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return

        try:
            valid_from = _parse_day(msg["valid_from"], "valid_from")
            valid_to = _parse_day(msg["valid_to"], "valid_to")
            today = dt_util.now().date()
            if valid_to >= today:
                raise ValueError(
                    "legacy tariff migration must end before the current day"
                )
            snapshot = legacy_tariff_snapshot_from_options(
                entry.options,
                valid_from=valid_from,
                valid_to=valid_to,
            )
            updated = append_legacy_tariff_snapshot(entry.options, snapshot)
        except LookupError as err:
            connection.send_error(
                msg["id"],
                "legacy_tariff_prices_unavailable",
                str(err),
            )
            return
        except (TypeError, ValueError) as err:
            connection.send_error(
                msg["id"],
                "invalid_legacy_tariff_migration",
                str(err),
            )
            return

        changed = dict(updated) != dict(entry.options)
        if changed:
            hass.config_entries.async_update_entry(entry, options=updated)
        payload = _snapshot_result(snapshot)
        payload.update(
            {
                "entry_id": entry.entry_id,
                "confirmed": False,
                "proposal_performed": True,
                "persistence_performed": changed,
                "confirmation_performed": False,
            }
        )
        connection.send_result(msg["id"], payload)

    @websocket_api.websocket_command(
        {
            vol.Required("type"): COMMAND_LEGACY_TARIFF_CONFIRM,
            vol.Required("entry_id"): str,
            vol.Required("snapshot_fingerprint"): str,
        }
    )
    @websocket_api.async_response
    async def websocket_legacy_tariff_confirm(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: Mapping[str, Any],
    ) -> None:
        ensure_admin(connection)
        entry = _entry_or_error(hass, connection, msg)
        if entry is None:
            return
        fingerprint = str(msg["snapshot_fingerprint"])

        try:
            staged = _snapshot_for_fingerprint(entry.options, fingerprint)
            if staged.confirmed:
                updated = dict(entry.options)
            else:
                updated = confirm_legacy_tariff_snapshot(entry.options, fingerprint)
            confirmed = _snapshot_for_fingerprint(updated, fingerprint)
            if not confirmed.confirmed:
                raise ValueError("legacy tariff snapshot confirmation did not persist")
        except LookupError as err:
            connection.send_error(
                msg["id"],
                "legacy_tariff_snapshot_not_found",
                str(err),
            )
            return
        except (TypeError, ValueError) as err:
            connection.send_error(
                msg["id"],
                "legacy_tariff_confirmation_failed",
                str(err),
            )
            return

        changed = dict(updated) != dict(entry.options)
        if changed:
            hass.config_entries.async_update_entry(entry, options=updated)
        payload = _snapshot_result(confirmed)
        payload.update(
            {
                "entry_id": entry.entry_id,
                "confirmed": True,
                "proposal_performed": False,
                "persistence_performed": changed,
                "confirmation_performed": changed,
            }
        )
        connection.send_result(msg["id"], payload)

    websocket_api.async_register_command(hass, websocket_legacy_tariff_propose)
    websocket_api.async_register_command(hass, websocket_legacy_tariff_confirm)
    domain_data[_REGISTERED_KEY] = True
