import React, { useEffect, useMemo, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { HomeAssistant } from "./home-assistant";
import "./technology-overview.css";

type CandidateRole = {
  role: string;
  label?: string;
  selected_entity_id?: string | null;
  confirmed_entity_id?: string | null;
};

type TechnologySuggestion = {
  technology: string;
  label?: string;
  enabled?: boolean;
  configured_roles?: number;
  total_roles?: number;
  roles?: CandidateRole[];
};

type DiscoverySnapshot = { technologies?: TechnologySuggestion[] };
type ConfigEntry = { entry_id: string; domain?: string };
type WsConnection = { sendMessagePromise?: <T>(message: Record<string, unknown>) => Promise<T> };

const HOST_ID = "frakon-technology-overview-host";
const PROFILE_CHANGED_EVENT = "frakon-energy-technology-profile-changed";
let root: Root | null = null;
let cachedSnapshot: DiscoverySnapshot | null = null;

const TECHNOLOGY_ICONS: Record<string, string> = {
  photovoltaics: "☀",
  home_battery: "▰",
  electric_vehicle: "◆",
  wallbox: "⚡",
  heat_pump: "↻",
  electric_boiler: "♨",
  hot_water_tank: "◉",
  electric_heating: "♨",
  gas_heating: "♨",
  solid_fuel_heating: "♨",
  chp: "⚙",
  generator: "⏻",
  smart_meter: "⌁",
  submeters: "⌁",
  dynamic_tariff: "↕",
  hdo: "◷",
  energy_export: "⇢",
};

function currentHass(): HomeAssistant | undefined {
  return window.__FRAKON_ENERGY_HASS__ ?? window.hass;
}

async function callWs<T>(hass: HomeAssistant, message: Record<string, unknown>): Promise<T> {
  const connection = hass.connection as WsConnection | undefined;
  if (!connection?.sendMessagePromise) throw new Error("WebSocket Home Assistantu není dostupný.");
  return connection.sendMessagePromise<T>(message);
}

async function loadSnapshot(hass: HomeAssistant): Promise<DiscoverySnapshot> {
  const entries = await callWs<ConfigEntry[]>(hass, { type: "config_entries/get" });
  const entry = entries.find((item) => item.domain === "frakon_energy");
  if (!entry) return {};
  return callWs<DiscoverySnapshot>(hass, {
    type: "frakon_energy/entity_discovery/get",
    entry_id: entry.entry_id,
  });
}

function applyModuleVisibility(snapshot: DiscoverySnapshot): void {
  const enabled = new Set(
    (snapshot.technologies ?? [])
      .filter((item) => Boolean(item.enabled))
      .map((item) => item.technology),
  );
  const rootElement = document.documentElement;
  const flags: Record<string, boolean> = {
    tariff: enabled.has("hdo") || enabled.has("dynamic_tariff"),
    photovoltaics: enabled.has("photovoltaics"),
    battery: enabled.has("home_battery"),
    ev: enabled.has("electric_vehicle"),
    wallbox: enabled.has("wallbox"),
    heatpump: enabled.has("heat_pump"),
    export: enabled.has("energy_export"),
  };
  Object.entries(flags).forEach(([name, active]) => {
    rootElement.classList.toggle(`frakon-has-${name}`, active);
    rootElement.classList.toggle(`frakon-no-${name}`, !active);
  });
  rootElement.dataset.frakonTechnologies = [...enabled].sort().join(",");
}

function isOverviewVisible(): boolean {
  const label = document.querySelector<HTMLElement>(".view-header > span");
  return label?.textContent?.trim() === "Přehled";
}

function overviewAnchor(): HTMLElement | null {
  if (!isOverviewVisible()) return null;
  const metrics = document.querySelectorAll<HTMLElement>(".metrics-grid");
  return metrics[0] ?? document.querySelector<HTMLElement>(".hero-grid");
}

function entityValue(hass: HomeAssistant, entityId?: string | null): string | null {
  if (!entityId) return null;
  const entity = hass.states[entityId];
  if (!entity || ["unknown", "unavailable", "none", "null", ""].includes(entity.state.toLowerCase())) return null;
  const unit = String(entity.attributes.unit_of_measurement ?? "").trim();
  return unit ? `${entity.state} ${unit}` : entity.state;
}

function TechnologyOverview({ hass }: { hass: HomeAssistant }) {
  const [snapshot, setSnapshot] = useState<DiscoverySnapshot | null>(cachedSnapshot);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const refreshProfile = () => loadSnapshot(hass)
      .then((value) => {
        if (!active) return;
        cachedSnapshot = value;
        applyModuleVisibility(value);
        setSnapshot(value);
        setError(null);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "Technologie se nepodařilo načíst.");
      });

    if (cachedSnapshot) applyModuleVisibility(cachedSnapshot);
    else void refreshProfile();

    window.addEventListener(PROFILE_CHANGED_EVENT, refreshProfile);
    return () => {
      active = false;
      window.removeEventListener(PROFILE_CHANGED_EVENT, refreshProfile);
    };
  }, [hass.connection]);

  const technologies = useMemo(
    () => (snapshot?.technologies ?? []).filter((item) => Boolean(item.enabled)),
    [snapshot],
  );

  if (error) return <section className="technology-overview technology-overview--error">{error}</section>;
  if (!snapshot || technologies.length === 0) return null;

  return <section className="technology-overview">
    <div className="technology-overview__heading">
      <div><span className="eyebrow">Technologie domu</span><h2>Aktivní energetické technologie</h2></div>
      <small>Živé hodnoty z již existujících entit Home Assistantu.</small>
    </div>
    <div className="technology-overview__grid">{technologies.map((technology) => {
      const roles = technology.roles ?? [];
      const mapped = roles.flatMap((role) => {
        const entityId = role.selected_entity_id ?? role.confirmed_entity_id ?? null;
        const value = entityValue(hass, entityId);
        return entityId ? [{ role, entityId, value }] : [];
      });
      const complete = (technology.configured_roles ?? mapped.length) >= (technology.total_roles ?? roles.length) && roles.length > 0;
      return <article className="technology-overview__card" data-technology={technology.technology} key={technology.technology}>
        <div className="technology-overview__card-head">
          <div className="technology-overview__identity">
            <span className="technology-overview__icon" aria-hidden="true">{TECHNOLOGY_ICONS[technology.technology] ?? "•"}</span>
            <div><span className={`technology-overview__state${complete ? " technology-overview__state--ready" : ""}`}>{complete ? "Připraveno" : "Aktivní"}</span><h3>{technology.label ?? technology.technology}</h3></div>
          </div>
          <b>{technology.configured_roles ?? mapped.length}/{technology.total_roles ?? roles.length}</b>
        </div>
        {mapped.length > 0 ? <div className="technology-overview__values">{mapped.slice(0, 4).map(({ role, entityId, value }) => <div key={`${technology.technology}-${role.role}`}>
          <span>{role.label ?? role.role}</span>
          <strong>{value ?? "Bez dat"}</strong>
          <small>{entityId}</small>
        </div>)}</div> : <p>Zatím není potvrzená žádná entita. Nastav ji v části Technologie domu.</p>}
      </article>;
    })}</div>
  </section>;
}

function mount(): void {
  const anchor = overviewAnchor();
  const stale = document.getElementById(HOST_ID);
  if (!anchor) {
    if (stale) stale.remove();
    root = null;
    return;
  }

  let host = stale;
  if (!host) {
    host = document.createElement("section");
    host.id = HOST_ID;
    anchor.insertAdjacentElement("afterend", host);
    root = createRoot(host);
  }

  const hass = currentHass();
  if (hass) root?.render(<TechnologyOverview hass={hass} />);
}

const observer = new MutationObserver(mount);
observer.observe(document.documentElement, { childList: true, subtree: true });
window.addEventListener("frakon-energy-hass-updated", mount);
window.addEventListener("load", mount);
mount();
