import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { HomeAssistant } from "./home-assistant";
import "./energy-flow-summary.css";

type ConfigEntry = { entry_id: string; domain?: string };
type WsConnection = { sendMessagePromise?: <T>(message: Record<string, unknown>) => Promise<T> };
type FlowDirection = "into-house" | "out-of-house" | "house-load" | "bidirectional";
type FlowQuality = "complete" | "partial" | "needs_setup";
type PowerReading = {
  entity_id: string | null;
  value_kw: number | null;
  state: string | null;
  unit: string | null;
  available: boolean;
  reason: string;
};
type ServerEnergyFlowSnapshot = {
  entry_id: string;
  quality: FlowQuality;
  quality_label: string;
  reasons: string[];
  house_load_kw: number | null;
  pv_generation_kw: number | null;
  grid_import_kw: number | null;
  grid_export_kw: number | null;
  battery_charge_kw: number | null;
  battery_discharge_kw: number | null;
  known_load_kw: number | null;
  known_load_quality: string;
  known_load_reason: string;
  topology: Record<string, string>;
  entities: Record<string, PowerReading>;
  read_only: true;
  service_call_performed: false;
  execution_performed: false;
};
type FlowNode = {
  id: string;
  label: string;
  entityId: string | null;
  numeric: number | null;
  active: boolean;
  direction: FlowDirection;
  directionLabel: string;
  unavailableReason: string | null;
};

const HOST_ID = "frakon-energy-flow-host";
const PROFILE_CHANGED_EVENT = "frakon-energy-technology-profile-changed";
let root: Root | null = null;

function currentHass(): HomeAssistant | undefined { return window.__FRAKON_ENERGY_HASS__ ?? window.hass; }

async function callWs<T>(hass: HomeAssistant, message: Record<string, unknown>): Promise<T> {
  const connection = hass.connection as WsConnection | undefined;
  if (!connection?.sendMessagePromise) throw new Error("WebSocket Home Assistantu není dostupný.");
  return connection.sendMessagePromise<T>(message);
}

async function findEntry(hass: HomeAssistant): Promise<ConfigEntry | null> {
  const entries = await callWs<ConfigEntry[]>(hass, { type: "config_entries/get" });
  return entries.find((item) => item.domain === "frakon_energy") ?? null;
}

async function loadServerFlow(hass: HomeAssistant, entryId: string): Promise<ServerEnergyFlowSnapshot> {
  return callWs<ServerEnergyFlowSnapshot>(hass, {
    type: "frakon_energy/energy_flow/status",
    entry_id: entryId,
  });
}

function isOverviewVisible(): boolean {
  const label = document.querySelector<HTMLElement>(".view-header > span");
  return label?.textContent?.trim() === "Přehled";
}

function overviewAnchor(): HTMLElement | null {
  if (!isOverviewVisible()) return null;
  return document.getElementById("frakon-technology-overview-host") ?? document.querySelector<HTMLElement>(".hdo-plan-card") ?? document.querySelector<HTMLElement>(".metrics-grid");
}

function formatKw(value: number | null): string {
  if (value === null) return "—";
  const abs = Math.abs(value);
  return `${abs.toLocaleString("cs-CZ", { maximumFractionDigits: abs >= 10 ? 1 : 2 })} kW`;
}

function qualityClass(quality: FlowQuality): string {
  return quality === "needs_setup" ? "needs-setup" : quality;
}

function readingNode(
  id: string,
  label: string,
  reading: PowerReading | undefined,
  direction: FlowDirection,
  directionLabel: string,
): FlowNode {
  return {
    id,
    label,
    entityId: reading?.entity_id ?? null,
    numeric: reading?.value_kw ?? null,
    active: Boolean(reading?.entity_id),
    direction,
    directionLabel,
    unavailableReason: reading && !reading.available ? reading.reason : null,
  };
}

function batteryNode(flow: ServerEnergyFlowSnapshot): FlowNode | null {
  const reading = flow.entities.battery;
  if (!reading?.entity_id) return null;
  if ((flow.battery_charge_kw ?? 0) > 0.03) {
    return readingNode("battery", "Baterie", reading, "house-load", "nabíjení baterie");
  }
  if ((flow.battery_discharge_kw ?? 0) > 0.03) {
    return readingNode("battery", "Baterie", reading, "into-house", "vybíjení do domu");
  }
  return readingNode("battery", "Baterie", reading, "bidirectional", reading.available ? "baterie je téměř v klidu" : "výkon baterie není dostupný");
}

function batterySummary(flow: ServerEnergyFlowSnapshot): string {
  if (flow.battery_charge_kw === null && flow.battery_discharge_kw === null) return "—";
  if ((flow.battery_charge_kw ?? 0) > 0.03) return `nabíjení ${formatKw(flow.battery_charge_kw)}`;
  if ((flow.battery_discharge_kw ?? 0) > 0.03) return `vybíjení ${formatKw(flow.battery_discharge_kw)}`;
  return "0 kW";
}

function EnergyFlow({ hass }: { hass: HomeAssistant }) {
  const [entryId, setEntryId] = useState<string | null>(null);
  const [flow, setFlow] = useState<ServerEnergyFlowSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const lastSourceFingerprint = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const entry = await findEntry(hass);
        if (!active) return;
        if (!entry) {
          setEntryId(null);
          setFlow(null);
          setError(null);
          return;
        }
        const value = await loadServerFlow(hass, entry.entry_id);
        if (!active) return;
        setEntryId(entry.entry_id);
        setFlow(value);
        setError(null);
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "Energetický tok se nepodařilo načíst.");
      }
    };
    void refresh();
    window.addEventListener(PROFILE_CHANGED_EVENT, refresh);
    return () => {
      active = false;
      window.removeEventListener(PROFILE_CHANGED_EVENT, refresh);
    };
  }, [hass.connection]);

  const sourceFingerprint = useMemo(() => {
    if (!flow) return "";
    return Object.values(flow.entities)
      .filter((reading) => reading.entity_id !== null)
      .map((reading) => {
        const source = reading.entity_id ? hass.states[reading.entity_id] : undefined;
        return `${reading.entity_id}:${source?.state ?? "missing"}:${String(source?.attributes.unit_of_measurement ?? "")}`;
      })
      .sort()
      .join("|");
  }, [flow, hass.states]);

  useEffect(() => {
    if (!entryId || !sourceFingerprint) return;
    if (lastSourceFingerprint.current === null) {
      lastSourceFingerprint.current = sourceFingerprint;
      return;
    }
    if (lastSourceFingerprint.current === sourceFingerprint) return;
    lastSourceFingerprint.current = sourceFingerprint;
    let active = true;
    loadServerFlow(hass, entryId)
      .then((value) => {
        if (!active) return;
        setFlow(value);
        setError(null);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "Energetický tok se nepodařilo obnovit.");
      });
    return () => { active = false; };
  }, [entryId, hass, sourceFingerprint]);

  const nodes = useMemo<FlowNode[]>(() => {
    if (!flow) return [];
    const result: FlowNode[] = [
      readingNode("pv", "FVE", flow.entities.pv, "into-house", "do domu"),
      readingNode("grid-in", "Síť · odběr", flow.entities.grid_import, "into-house", "ze sítě do domu"),
      readingNode("grid-out", "Síť · přetok", flow.entities.grid_export, "out-of-house", "z domu do sítě"),
      readingNode("wallbox", "Wallbox", flow.entities.wallbox, "house-load", "spotřeba domu"),
      readingNode("ev", "Elektromobil", flow.entities.ev, "house-load", "spotřeba při nabíjení"),
      readingNode("heatpump", "Tepelné čerpadlo", flow.entities.heat_pump, "house-load", "spotřeba domu"),
      readingNode("boiler", "Elektrický bojler", flow.entities.electric_boiler, "house-load", "spotřeba domu"),
      readingNode("hot-water", "Ohřev vody", flow.entities.hot_water_tank, "house-load", "spotřeba domu"),
      readingNode("electric-heating", "Elektrické vytápění", flow.entities.electric_heating, "house-load", "spotřeba domu"),
      readingNode("submeters", "Podružné měření", flow.entities.submeters, "house-load", "známá spotřeba"),
    ];
    const battery = batteryNode(flow);
    if (battery) result.splice(3, 0, battery);
    return result.filter((item) => item.active);
  }, [flow]);

  if (error) return <section className="energy-flow energy-flow--error">{error}</section>;
  if (!flow || nodes.length < 2) return null;

  const activePower = nodes.filter((item) => item.numeric !== null && Math.abs(item.numeric) > 0.03).length;
  const tone = qualityClass(flow.quality);
  const primaryReason = flow.reasons[0] ?? "Serverový energetický model nemá další diagnostiku.";

  return <section className="energy-flow">
    <div className="energy-flow__heading">
      <div><span className="eyebrow">Živý energetický tok</span><h2>Kam právě teče energie</h2></div>
      <div className="energy-flow__heading-meta">
        <span className={`energy-flow__quality energy-flow__quality--${tone}`}>{flow.quality_label}</span>
        <small>{activePower > 0 ? `${activePower} aktivní toky` : "Toky jsou právě téměř nulové"}</small>
      </div>
    </div>
    <div className="energy-flow__map">
      <div className={`energy-flow__hub energy-flow__hub--${tone}`}><span>Dům</span><strong>{flow.house_load_kw !== null ? formatKw(flow.house_load_kw) : "FRAKON"}</strong><small>{flow.house_load_kw !== null ? "aktuální spotřeba · server model" : flow.quality_label}</small></div>
      {nodes.map((item, index) => {
        const live = item.numeric !== null && Math.abs(item.numeric) > 0.03;
        return <article className={`energy-flow__node energy-flow__node--${item.direction}${live ? " energy-flow__node--live" : " energy-flow__node--idle"}`} key={item.id} style={{ "--flow-index": index } as React.CSSProperties}>
          <span>{item.label}</span><strong>{item.numeric !== null ? formatKw(item.numeric) : "Bez dat"}</strong><small className="energy-flow__direction">{item.unavailableReason ? `${item.directionLabel} · ${item.unavailableReason}` : item.directionLabel}</small><i aria-hidden="true" />
        </article>;
      })}
    </div>
    <div className="energy-flow__balance-status">
      <div><span>Kvalita bilance</span><strong>{flow.quality_label}</strong></div>
      <p>{flow.reasons.join(" ")}</p>
    </div>
    <div className="energy-flow__balance" aria-label="Serverový souhrn energetických toků">
      <div><span>Spotřeba domu</span><strong>{formatKw(flow.house_load_kw)}</strong><small>{primaryReason}</small></div>
      <div><span>Výroba FVE</span><strong>{formatKw(flow.pv_generation_kw)}</strong><small>normalizovaný serverový výkon</small></div>
      <div><span>Odběr ze sítě</span><strong>{formatKw(flow.grid_import_kw)}</strong><small>potvrzený vstup hlavního elektroměru</small></div>
      <div><span>Přetok do sítě</span><strong>{formatKw(flow.grid_export_kw)}</strong><small>potvrzený export hlavního elektroměru</small></div>
      <div><span>Známé spotřeby</span><strong>{formatKw(flow.known_load_kw)}</strong><small>{flow.known_load_reason}</small></div>
      <div><span>Baterie</span><strong>{batterySummary(flow)}</strong><small>{flow.entities.battery?.entity_id ? "směr určuje potvrzená serverová topologie" : "baterie není součástí potvrzené topologie"}</small></div>
    </div>
    <p className="energy-flow__note">Dashboard už bilanci nepřepočítává. Spotřebu domu, kvalitu, jednotky, směr baterie i deduplikaci EV/wallboxu přebírá z autoritativního serverového modelu FRAKON Energy, který používají také nativní Home Assistant entity.</p>
  </section>;
}

function mount(): void {
  const anchor = overviewAnchor();
  const stale = document.getElementById(HOST_ID);
  if (!anchor) { stale?.remove(); root = null; return; }
  let host = stale;
  if (!host) { host = document.createElement("section"); host.id = HOST_ID; anchor.insertAdjacentElement("afterend", host); root = createRoot(host); }
  const hass = currentHass();
  if (hass) root?.render(<EnergyFlow hass={hass} />);
}

const observer = new MutationObserver(mount);
observer.observe(document.documentElement, { childList: true, subtree: true });
window.addEventListener("frakon-energy-hass-updated", mount);
window.addEventListener("load", mount);
mount();
