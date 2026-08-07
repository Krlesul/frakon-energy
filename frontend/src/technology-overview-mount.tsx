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
type TrendPoint = { updated: string; value: number };

const HOST_ID = "frakon-technology-overview-host";
const PROFILE_CHANGED_EVENT = "frakon-energy-technology-profile-changed";
const TREND_POINTS = 30;
let root: Root | null = null;
let cachedSnapshot: DiscoverySnapshot | null = null;
const trendCache = new Map<string, TrendPoint[]>();

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

const TECHNOLOGY_COPY: Record<string, string> = {
  photovoltaics: "Výroba ze slunce a aktuální energetický tok.",
  home_battery: "Stav úložiště, výkon a dostupná energie.",
  electric_vehicle: "Baterie vozu, nabíjení a dostupné provozní údaje.",
  wallbox: "Aktuální nabíjecí výkon a stav domácího nabíjení.",
  heat_pump: "Provoz, příkon a dostupné hodnoty vytápění.",
  energy_export: "Přetoky a energie odeslaná do distribuční sítě.",
  hdo: "Nízký tarif a aktivní časová okna distributora.",
  dynamic_tariff: "Dynamická cena elektřiny a aktuální tarifní signál.",
};

const PRIMARY_ROLE_ORDER: Record<string, string[]> = {
  photovoltaics: ["pv_power", "energy_total", "grid_export", "grid_import"],
  home_battery: ["battery_level", "power", "energy_total"],
  electric_vehicle: ["battery_level", "power", "range", "charging_state", "charge_limit"],
  wallbox: ["power", "charging_state", "energy_total"],
  heat_pump: ["power", "energy_total"],
  energy_export: ["grid_export", "energy_total", "power"],
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

function captureTrend(hass: HomeAssistant, entityId?: string | null): number[] {
  if (!entityId) return [];
  const entity = hass.states[entityId];
  if (!entity) return [];
  const value = Number(entity.state.replace(",", "."));
  if (!Number.isFinite(value)) return [];
  const updated = String((entity as unknown as { last_updated?: string; last_changed?: string }).last_updated
    ?? (entity as unknown as { last_changed?: string }).last_changed
    ?? entity.state);
  const current = trendCache.get(entityId) ?? [];
  if (current[current.length - 1]?.updated !== updated) {
    current.push({ updated, value });
    if (current.length > TREND_POINTS) current.splice(0, current.length - TREND_POINTS);
    trendCache.set(entityId, current);
  }
  return current.map((point) => point.value);
}

function sparklinePath(values: number[], width = 160, height = 36): string | null {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  return values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - ((value - min) / range) * height;
    return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");
}

function trendLabel(values: number[]): string | null {
  if (values.length < 2) return null;
  const first = values[0];
  const last = values[values.length - 1];
  const tolerance = Math.max(Math.abs(first), Math.abs(last), 1) * 0.01;
  if (Math.abs(last - first) <= tolerance) return "Stabilní";
  return last > first ? "Roste" : "Klesá";
}

function pickPrimary<T extends { role: CandidateRole; value: string | null }>(technology: string, mapped: T[]): T | null {
  const preferred = PRIMARY_ROLE_ORDER[technology] ?? [];
  for (const role of preferred) {
    const match = mapped.find((item) => item.role.role === role && item.value);
    if (match) return match;
  }
  return mapped.find((item) => item.value) ?? mapped[0] ?? null;
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
      const primary = pickPrimary(technology.technology, mapped);
      const secondary = mapped.filter((item) => item !== primary).slice(0, 3);
      const trendValues = captureTrend(hass, primary?.entityId ?? null);
      const path = sparklinePath(trendValues);
      const direction = trendLabel(trendValues);
      return <article className="technology-overview__card" data-technology={technology.technology} key={technology.technology}>
        <div className="technology-overview__card-head">
          <div className="technology-overview__identity">
            <span className="technology-overview__icon" aria-hidden="true">{TECHNOLOGY_ICONS[technology.technology] ?? "•"}</span>
            <div><span className={`technology-overview__state${complete ? " technology-overview__state--ready" : ""}`}>{complete ? "Připraveno" : "Aktivní"}</span><h3>{technology.label ?? technology.technology}</h3></div>
          </div>
          <b>{technology.configured_roles ?? mapped.length}/{technology.total_roles ?? roles.length}</b>
        </div>
        <p className="technology-overview__copy">{TECHNOLOGY_COPY[technology.technology] ?? "Živé hodnoty technologie z Home Assistantu."}</p>
        {primary ? <div className="technology-overview__primary">
          <div className="technology-overview__primary-head"><span>{primary.role.label ?? primary.role.role}</span>{direction ? <em>{direction}</em> : null}</div>
          <strong>{primary.value ?? "Bez dat"}</strong>
          {path ? <svg className="technology-overview__sparkline" viewBox="0 0 160 36" preserveAspectRatio="none" role="img" aria-label={`Krátkodobý trend: ${direction ?? "beze změny"}`}><path d={path} /></svg> : <div className="technology-overview__sparkline-empty">Trend se začne kreslit z živých změn.</div>}
          <small>{primary.entityId}</small>
        </div> : null}
        {secondary.length > 0 ? <div className="technology-overview__values">{secondary.map(({ role, entityId, value }) => <div key={`${technology.technology}-${role.role}`}>
          <span>{role.label ?? role.role}</span>
          <strong>{value ?? "Bez dat"}</strong>
          <small>{entityId}</small>
        </div>)}</div> : null}
        {mapped.length === 0 ? <p>Zatím není potvrzená žádná entita. Nastav ji v části Technologie domu.</p> : null}
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
