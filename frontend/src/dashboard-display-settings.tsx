import React from "react";
import { callHomeAssistantWs, type HomeAssistant } from "./home-assistant";

export type DashboardDisplayKey =
  | "show_hdo"
  | "show_hdo_plan"
  | "show_spot_prices"
  | "show_daily_consumption"
  | "show_monthly_consumption"
  | "show_billing_estimate"
  | "show_technical_measurements"
  | "show_technology_overview"
  | "show_photovoltaics"
  | "show_energy_flow";

export type DashboardDisplaySettings = Record<DashboardDisplayKey, boolean>;

export const DEFAULT_DASHBOARD_DISPLAY_SETTINGS: DashboardDisplaySettings = {
  show_hdo: true,
  show_hdo_plan: true,
  show_spot_prices: true,
  show_daily_consumption: true,
  show_monthly_consumption: true,
  show_billing_estimate: true,
  show_technical_measurements: true,
  show_technology_overview: true,
  show_photovoltaics: true,
  show_energy_flow: true,
};

export async function loadDashboardDisplaySettings(
  hass: HomeAssistant,
  entryId: string,
): Promise<DashboardDisplaySettings> {
  return callHomeAssistantWs<DashboardDisplaySettings>(hass, {
    type: "frakon_energy/dashboard_display_settings/get",
    entry_id: entryId,
  });
}

export async function saveDashboardDisplaySetting(
  hass: HomeAssistant,
  entryId: string,
  key: DashboardDisplayKey,
  enabled: boolean,
): Promise<DashboardDisplaySettings> {
  return callHomeAssistantWs<DashboardDisplaySettings>(hass, {
    type: "frakon_energy/dashboard_display_settings/set",
    entry_id: entryId,
    key,
    enabled,
  });
}

const OPTIONS: Array<{ key: DashboardDisplayKey; label: string; description: string }> = [
  { key: "show_hdo", label: "HDO · aktuální tarif", description: "Aktuální NT/VT, cena a ověřený čas další změny." },
  { key: "show_hdo_plan", label: "HDO · denní plán", description: "Časová osa dnešních intervalů nízkého tarifu." },
  { key: "show_spot_prices", label: "Spotové ceny", description: "Aktuální a nadcházející spotové ceny elektřiny." },
  { key: "show_daily_consumption", label: "Denní spotřeba a náklady", description: "Spotřeba a cena elektřiny za dnešní den." },
  { key: "show_monthly_consumption", label: "Měsíční spotřeba", description: "Průběžná spotřeba v aktuálním měsíci." },
  { key: "show_billing_estimate", label: "Odhad vyúčtování", description: "Přeplatek, nedoplatek, zálohy a průběžné náklady." },
  { key: "show_technical_measurements", label: "Technická měření", description: "Registry elektroměru VT/NT a kvalita dat." },
  { key: "show_technology_overview", label: "Technologie domu", description: "Karty aktivních energetických technologií." },
  { key: "show_photovoltaics", label: "Fotovoltaika", description: "FVE karta v přehledu technologií, pokud je FVE nastavena." },
  { key: "show_energy_flow", label: "Energetické toky", description: "Živý tok mezi sítí, domem, FVE, baterií a spotřebiči." },
];

export function applyDashboardDisplayClasses(settings: DashboardDisplaySettings): void {
  const root = document.documentElement;
  Object.entries(settings).forEach(([key, visible]) => {
    const name = key.replace(/^show_/, "").replaceAll("_", "-");
    root.classList.toggle(`frakon-hide-${name}`, !visible);
  });
  window.dispatchEvent(new Event("frakon-energy-dashboard-display-changed"));
}

export function DashboardDisplaySettingsCard({
  settings,
  disabled,
  status,
  onChange,
}: {
  settings: DashboardDisplaySettings;
  disabled: boolean;
  status: string;
  onChange: (key: DashboardDisplayKey, enabled: boolean) => void;
}) {
  return <article className="chart-card dashboard-display-settings">
    <span className="eyebrow">Zobrazení přehledu</span>
    <h2>Co se má zobrazovat</h2>
    <p className="settings-copy">Vypnuté části se v přehledu FRAKON Energy nezobrazí. Nastavení je uložené v Home Assistantu a platí na všech zařízeních.</p>
    <div className="display-toggle-list">
      {OPTIONS.map((option) => <label className="display-toggle" key={option.key}>
        <span className="display-toggle__copy"><b>{option.label}</b><small>{option.description}</small></span>
        <span className="display-switch">
          <input
            type="checkbox"
            checked={settings[option.key]}
            disabled={disabled}
            onChange={(event) => onChange(option.key, event.target.checked)}
          />
          <i aria-hidden="true" />
        </span>
      </label>)}
    </div>
    <small className="display-settings-status">{status}</small>
  </article>;
}
