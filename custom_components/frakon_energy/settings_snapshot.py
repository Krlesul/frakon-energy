from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .design import DesignStudioPreferences, default_design_preferences, design_payload
from .overview import OverviewPreferences, default_overview_preferences, overview_payload
from .settings import public_settings_payload, redact_connection_data


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    """Frontend-safe configuration snapshot for the FRAKON Energy panel."""

    sections: list[dict[str, object]]
    connection: dict[str, object]
    metering: dict[str, object]
    billing: dict[str, object]
    contract: dict[str, object]
    hdo: dict[str, object]
    documents: dict[str, object]
    design: dict[str, object]
    overview: dict[str, object]
    diagnostics: dict[str, object]
    updates: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "sections": self.sections,
            "connection": self.connection,
            "metering": self.metering,
            "billing": self.billing,
            "contract": self.contract,
            "hdo": self.hdo,
            "documents": self.documents,
            "design": self.design,
            "overview": self.overview,
            "diagnostics": self.diagnostics,
            "updates": self.updates,
        }


def _copy_mapping(value: Mapping[str, Any] | None) -> dict[str, object]:
    return dict(value or {})


def build_settings_snapshot(
    *,
    connection: Mapping[str, Any] | None = None,
    metering: Mapping[str, Any] | None = None,
    billing: Mapping[str, Any] | None = None,
    contract: Mapping[str, Any] | None = None,
    hdo: Mapping[str, Any] | None = None,
    documents: Mapping[str, Any] | None = None,
    design: DesignStudioPreferences | None = None,
    overview: OverviewPreferences | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    updates: Mapping[str, Any] | None = None,
) -> SettingsSnapshot:
    """Build the single settings payload consumed by the dashboard.

    Secret connection values are removed before the payload leaves the backend.
    Other sections are copied to prevent the caller from mutating the snapshot.
    Design and Overview preferences are serialized through validated models.
    """

    return SettingsSnapshot(
        sections=public_settings_payload(),
        connection=redact_connection_data(_copy_mapping(connection)),
        metering=_copy_mapping(metering),
        billing=_copy_mapping(billing),
        contract=_copy_mapping(contract),
        hdo=_copy_mapping(hdo),
        documents=_copy_mapping(documents),
        design=design_payload(design or default_design_preferences()),
        overview=overview_payload(overview or default_overview_preferences()),
        diagnostics=_copy_mapping(diagnostics),
        updates=_copy_mapping(updates),
    )


def settings_completion(snapshot: SettingsSnapshot) -> dict[str, bool]:
    """Return completion flags used by the settings overview cards."""

    return {
        "connection": bool(snapshot.connection.get("credentials_configured")),
        "metering": bool(snapshot.metering.get("configured")),
        "billing": bool(snapshot.billing.get("configured")),
        "contract": bool(snapshot.contract.get("configured")),
        "hdo": bool(snapshot.hdo.get("configured")),
        "documents": bool(snapshot.documents.get("count", 0)),
        "design": bool(snapshot.design.get("active_layout")),
        "overview": bool(snapshot.overview.get("widgets")),
        "diagnostics": True,
        "updates": bool(snapshot.updates.get("version")),
    }
