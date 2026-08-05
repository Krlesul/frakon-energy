from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class SettingsSection(StrEnum):
    CONNECTION = "connection"
    METERING = "metering"
    BILLING = "billing"
    CONTRACT = "contract"
    HDO = "hdo"
    DOCUMENTS = "documents"
    DIAGNOSTICS = "diagnostics"
    UPDATES = "updates"


@dataclass(frozen=True, slots=True)
class SettingsSectionDefinition:
    key: SettingsSection
    title_cs: str
    description_cs: str
    contains_secrets: bool = False


SETTINGS_SECTIONS: Final[tuple[SettingsSectionDefinition, ...]] = (
    SettingsSectionDefinition(
        SettingsSection.CONNECTION,
        "Připojení",
        "VisionQ, výběr zařízení, změna přihlašovacích údajů a opětovné přihlášení.",
        contains_secrets=True,
    ),
    SettingsSectionDefinition(
        SettingsSection.METERING,
        "Měření a elektroměry",
        "Počáteční stavy, výměny elektroměrů a zdroje měřených hodnot.",
    ),
    SettingsSectionDefinition(
        SettingsSection.BILLING,
        "Vyúčtování a zálohy",
        "Zúčtovací období, zálohy, termíny a kontrolní výpočty.",
    ),
    SettingsSectionDefinition(
        SettingsSection.CONTRACT,
        "Smlouva a ceník",
        "Dodavatel, distributor, produkt, fixace, sazba, jistič a cenová období.",
    ),
    SettingsSectionDefinition(
        SettingsSection.HDO,
        "HDO a tarify",
        "Zdroj HDO, intervaly nízkého tarifu a kvalita dat.",
    ),
    SettingsSectionDefinition(
        SettingsSection.DOCUMENTS,
        "Dokumenty",
        "Nahrané smlouvy, ceníky PDF a odkazy na oficiální dokumenty.",
    ),
    SettingsSectionDefinition(
        SettingsSection.DIAGNOSTICS,
        "Diagnostika",
        "Stav připojení, zdroje dat, chyby, poslední aktualizace a kvalita rozpoznání.",
    ),
    SettingsSectionDefinition(
        SettingsSection.UPDATES,
        "Aktualizace",
        "Verze FRAKON Energy, dostupné aktualizace a historie změn.",
    ),
)


def public_settings_payload() -> list[dict[str, object]]:
    """Return frontend-safe settings navigation metadata.

    This payload intentionally contains no credentials or secret values.
    """
    return [
        {
            "key": section.key.value,
            "title": section.title_cs,
            "description": section.description_cs,
            "contains_secrets": section.contains_secrets,
        }
        for section in SETTINGS_SECTIONS
    ]


def redact_connection_data(data: dict[str, object]) -> dict[str, object]:
    """Return connection data that is safe to expose to the dashboard."""
    redacted = dict(data)
    for key in ("password", "token", "access_token", "refresh_token"):
        redacted.pop(key, None)
    if "username" in redacted:
        redacted["username_configured"] = bool(redacted.pop("username"))
    redacted["credentials_configured"] = bool(
        data.get("username") and (data.get("password") or data.get("token"))
    )
    return redacted
