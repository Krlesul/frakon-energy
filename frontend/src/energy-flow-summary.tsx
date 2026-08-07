import React, { useEffect, useMemo, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { HomeAssistant } from "./home-assistant";
import "./energy-flow-summary.css";

type CandidateRole = { role: string; selected_entity_id?: string | null; confirmed_entity_id?: string | null };
type TechnologySuggestion = { technology: string; enabled?: boolean; roles?: CandidateRole[] };
type DiscoverySnapshot = { technologies?: TechnologySuggestion[] };
type BatteryPowerSign = "unknown" | "positive_is_charge" | "positive_is_discharge";
type GridMeterScope = "unknown" | "whole_site";
type PvPowerScope = "unknown" | "ac_output";
type EvWallboxRelation = "unknown" | "same_flow" | "separate";
type EnergyFlowSettings = { battery_power_sign: BatteryPowerSign; grid_meter_scope: GridMeterScope; pv_power_scope: PvPowerScope; ev_wallbox_relation: EvWallboxRelation };
type ConfigEntry = { entry_id: string; domain?: string };
type WsConnection = { sendMessagePromise?: <T>(message: Record<string, unknown>) => Promise<T> };
type FlowDirection = "into-house" | "out-of-house" | "house-load" | "bidirectional";
type FlowNode = { id: string; label: string; entityId: string | null; value: string | null; numeric: number | null; active: boolean; direction: FlowDirection; directionLabel: string };

const HOST_ID = "frakon-energy-flow-host";
const PROFILE_CHANGED_EVENT = "frakon-energy-technology-profile-changed";
const DEFAULT_FLOW_SETTINGS: EnergyFlowSettings = { battery_power_sign: "unknown", grid_meter_scope: "unknown", pv_power_scope: "unknown", ev_wallbox_relation: "unknown" };
let root: Root | null = null;
let cachedSnapshot: DiscoverySnapshot | null = null;
let cachedFlowSettings: EnergyFlowSettings = DEFAULT_FLOW_SETTINGS;

function currentHass(): HomeAssistant | undefined { return window.__FRAKON_ENERGY_HASS__ ?? window.hass; }
async function callWs<T>(hass: HomeAssistant, message: Record<string, unknown>): Promise<T> { const connection = hass.connection as WsConnection | undefined; if (!connection?.sendMessagePromise) throw new Error("WebSocket Home Assistantu není dostupný."); return connection.sendMessagePromise<T>(message); }
async function findEntry(hass: HomeAssistant): Promise<ConfigEntry | null> { const entries = await callWs<ConfigEntry[]>(hass, { type: "config_entries/get" }); return entries.find((item) => item.domain === "frakon_energy") ?? null; }
async function loadData(hass: HomeAssistant): Promise<{ snapshot: DiscoverySnapshot; settings: EnergyFlowSettings }> { const entry = await findEntry(hass); if (!entry) return { snapshot: {}, settings: DEFAULT_FLOW_SETTINGS }; const [snapshot, settings] = await Promise.all([callWs<DiscoverySnapshot>(hass, { type: "frakon_energy/entity_discovery/get", entry_id: entry.entry_id }), callWs<EnergyFlowSettings>(hass, { type: "frakon_energy/energy_flow/get", entry_id: entry.entry_id })]); return { snapshot, settings: { ...DEFAULT_FLOW_SETTINGS, ...settings } }; }
function isOverviewVisible(): boolean { const label = document.querySelector<HTMLElement>(".view-header > span"); return label?.textContent?.trim() === "Přehled"; }
function overviewAnchor(): HTMLElement | null { if (!isOverviewVisible()) return null; return document.getElementById("frakon-technology-overview-host") ?? document.querySelector<HTMLElement>(".hdo-plan-card") ?? document.querySelector<HTMLElement>(".metrics-grid"); }
function roleEntity(snapshot: DiscoverySnapshot, technology: string, role: string): string | null { const item = (snapshot.technologies ?? []).find((entry) => entry.technology === technology && entry.enabled); const mapped = item?.roles?.find((entry) => entry.role === role); return mapped?.selected_entity_id ?? mapped?.confirmed_entity_id ?? null; }
function powerValue(hass: HomeAssistant, entityId: string | null): { text: string | null; numeric: number | null } { if (!entityId) return { text: null, numeric: null }; const entity = hass.states[entityId]; if (!entity) return { text: null, numeric: null }; const raw = Number(entity.state.replace(",", ".")); if (!Number.isFinite(raw)) return { text: null, numeric: null }; const unit = String(entity.attributes.unit_of_measurement ?? "").trim(); let kw = raw; if (unit === "W") kw = raw / 1000; else if (unit === "MW") kw = raw * 1000; else if (unit && unit !== "kW") return { text: `${entity.state} ${unit}`, numeric: null }; const abs = Math.abs(kw); return { text: `${abs.toLocaleString("cs-CZ", { maximumFractionDigits: abs >= 10 ? 1 : 2 })} kW`, numeric: kw }; }
function formatKw(value: number | null): string { if (value === null) return "—"; const abs = Math.abs(value); return `${abs.toLocaleString("cs-CZ", { maximumFractionDigits: abs >= 10 ? 1 : 2 })} kW`; }
function sumUnique(nodes: FlowNode[], directions: FlowDirection[], excludeIds: Set<string> = new Set()): number | null { const seen = new Set<string>(); let total = 0; let count = 0; for (const node of nodes) { if (excludeIds.has(node.id) || !directions.includes(node.direction) || node.numeric === null || !node.entityId || seen.has(node.entityId)) continue; seen.add(node.entityId); total += Math.abs(node.numeric); count += 1; } return count > 0 ? total : null; }
function batteryDirection(value: number | null, sign: BatteryPowerSign): { direction: FlowDirection; label: string } { if (value === null || Math.abs(value) <= 0.03 || sign === "unknown") return { direction: "bidirectional", label: sign === "unknown" ? "směr není nastaven" : "baterie je téměř v klidu" }; const charging = sign === "positive_is_charge" ? value > 0 : value < 0; return charging ? { direction: "house-load", label: "nabíjení baterie" } : { direction: "into-house", label: "vybíjení do domu" }; }

function EnergyFlow({ hass }: { hass: HomeAssistant }) {
  const [snapshot, setSnapshot] = useState<DiscoverySnapshot | null>(cachedSnapshot);
  const [flowSettings, setFlowSettings] = useState<EnergyFlowSettings>(cachedFlowSettings);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { let active = true; const refresh = () => loadData(hass).then(({ snapshot: value, settings }) => { if (!active) return; cachedSnapshot = value; cachedFlowSettings = settings; setSnapshot(value); setFlowSettings(settings); setError(null); }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Energetický tok se nepodařilo načíst."); }); if (!cachedSnapshot) void refresh(); window.addEventListener(PROFILE_CHANGED_EVENT, refresh); return () => { active = false; window.removeEventListener(PROFILE_CHANGED_EVENT, refresh); }; }, [hass.connection]);

  const nodes = useMemo<FlowNode[]>(() => {
    if (!snapshot) return [];
    const batteryEntity = roleEntity(snapshot, "home_battery", "power"); const batteryValue = powerValue(hass, batteryEntity); const batteryFlow = batteryDirection(batteryValue.numeric, flowSettings.battery_power_sign);
    const specs = [
      ["pv", "FVE", roleEntity(snapshot, "photovoltaics", "pv_power"), "into-house", "do domu"],
      ["grid-in", "Síť · odběr", roleEntity(snapshot, "smart_meter", "grid_import") ?? roleEntity(snapshot, "photovoltaics", "grid_import"), "into-house", "ze sítě do domu"],
      ["grid-out", "Síť · přetok", roleEntity(snapshot, "smart_meter", "grid_export") ?? roleEntity(snapshot, "energy_export", "grid_export") ?? roleEntity(snapshot, "photovoltaics", "grid_export"), "out-of-house", "z domu do sítě"],
      ["wallbox", "Wallbox", roleEntity(snapshot, "wallbox", "power"), "house-load", "spotřeba domu"],
      ["ev", "Elektromobil", roleEntity(snapshot, "electric_vehicle", "power"), "house-load", flowSettings.ev_wallbox_relation === "same_flow" ? "stejný tok jako wallbox" : "spotřeba při nabíjení"],
      ["heatpump", "Tepelné čerpadlo", roleEntity(snapshot, "heat_pump", "power"), "house-load", "spotřeba domu"],
    ] as const;
    const mapped = specs.map(([id, label, entityId, direction, directionLabel]) => { const value = powerValue(hass, entityId); return { id, label, entityId, value: value.text, numeric: value.numeric, active: Boolean(entityId), direction, directionLabel } as FlowNode; });
    if (batteryEntity) mapped.splice(3, 0, { id: "battery", label: "Baterie", entityId: batteryEntity, value: batteryValue.text, numeric: batteryValue.numeric, active: true, direction: batteryFlow.direction, directionLabel: batteryFlow.label });
    return mapped.filter((node) => node.active);
  }, [snapshot, hass.states, flowSettings.battery_power_sign, flowSettings.ev_wallbox_relation]);

  if (error) return <section className="energy-flow energy-flow--error">{error}</section>;
  if (!snapshot || nodes.length < 2) return null;

  const activePower = nodes.filter((node) => node.numeric !== null && Math.abs(node.numeric) > 0.03).length;
  const duplicateLoadIds = new Set<string>();
  if (flowSettings.ev_wallbox_relation !== "separate" && nodes.some((node) => node.id === "wallbox")) duplicateLoadIds.add("ev");
  const knownSources = sumUnique(nodes, ["into-house"]);
  const knownLoads = sumUnique(nodes, ["house-load"], duplicateLoadIds);
  const knownExport = sumUnique(nodes, ["out-of-house"]);
  const batteryNode = nodes.find((node) => node.id === "battery" && node.numeric !== null);
  const battery = batteryNode?.numeric ?? null;
  const batteryKnown = flowSettings.battery_power_sign !== "unknown";

  const pv = nodes.find((node) => node.id === "pv")?.numeric ?? null;
  const gridIn = nodes.find((node) => node.id === "grid-in")?.numeric ?? null;
  const gridOut = nodes.find((node) => node.id === "grid-out")?.numeric ?? null;
  const batteryBlocksBalance = Boolean(batteryNode) && !batteryKnown;
  const topologyReady = flowSettings.grid_meter_scope === "whole_site" && flowSettings.pv_power_scope === "ac_output" && pv !== null && gridIn !== null && gridOut !== null && !batteryBlocksBalance;
  let derivedHouseLoad: number | null = null;
  if (topologyReady) {
    const batteryDischarge = batteryNode?.direction === "into-house" ? Math.abs(batteryNode.numeric ?? 0) : 0;
    const batteryCharge = batteryNode?.direction === "house-load" ? Math.abs(batteryNode.numeric ?? 0) : 0;
    derivedHouseLoad = Math.max(0, Math.abs(pv ?? 0) + Math.abs(gridIn ?? 0) + batteryDischarge - Math.abs(gridOut ?? 0) - batteryCharge);
  }

  return <section className="energy-flow">
    <div className="energy-flow__heading"><div><span className="eyebrow">Živý energetický tok</span><h2>Kam právě teče energie</h2></div><small>{activePower > 0 ? `${activePower} aktivní toky` : "Toky jsou právě téměř nulové"}</small></div>
    <div className="energy-flow__map"><div className="energy-flow__hub"><span>Dům</span><strong>{derivedHouseLoad !== null ? formatKw(derivedHouseLoad) : "FRAKON"}</strong><small>{derivedHouseLoad !== null ? "dopočítaná spotřeba" : "živý stav"}</small></div>{nodes.map((node, index) => { const live = node.numeric !== null && Math.abs(node.numeric) > 0.03; return <article className={`energy-flow__node energy-flow__node--${node.direction}${live ? " energy-flow__node--live" : " energy-flow__node--idle"}`} key={node.id} style={{ "--flow-index": index } as React.CSSProperties}><span>{node.label}</span><strong>{node.value ?? "Bez dat"}</strong><small className="energy-flow__direction">{node.directionLabel}</small><i aria-hidden="true" /></article>; })}</div>
    <div className="energy-flow__balance" aria-label="Souhrn dostupných energetických toků">
      <div><span>Spotřeba celého domu</span><strong>{formatKw(derivedHouseLoad)}</strong><small>{topologyReady ? "dopočítáno z potvrzené topologie" : "čeká na potvrzení topologie"}</small></div>
      <div><span>Zdroje do domu</span><strong>{formatKw(knownSources)}</strong><small>FVE + síť + známé vybíjení baterie</small></div>
      <div><span>Známé spotřeby</span><strong>{formatKw(knownLoads)}</strong><small>{flowSettings.ev_wallbox_relation === "same_flow" ? "EV není duplikováno s wallboxem" : "jen samostatně potvrzené spotřebiče"}</small></div>
      <div><span>Přetok do sítě</span><strong>{formatKw(knownExport)}</strong><small>potvrzené měření exportu</small></div>
    </div>
    <p className="energy-flow__note">FRAKON dopočítá spotřebu celého domu pouze tehdy, když je potvrzeno, že smart meter měří celý připojovací bod, FVE poskytuje AC výkon a případná baterie má známou znaménkovou konvenci. Do té doby zůstává výpočet záměrně vypnutý.</p>
  </section>;
}

function mount(): void { const anchor = overviewAnchor(); const stale = document.getElementById(HOST_ID); if (!anchor) { stale?.remove(); root = null; return; } let host = stale; if (!host) { host = document.createElement("section"); host.id = HOST_ID; anchor.insertAdjacentElement("afterend", host); root = createRoot(host); } const hass = currentHass(); if (hass) root?.render(<EnergyFlow hass={hass} />); }
const observer = new MutationObserver(mount); observer.observe(document.documentElement, { childList: true, subtree: true }); window.addEventListener("frakon-energy-hass-updated", mount); window.addEventListener("load", mount); mount();
