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
let root: Root | null = null;

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
  const [snapshot, setSnapshot] = useState<DiscoverySnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = () => loadSnapshot(hass)
      .then((value) => { if (active) { setSnapshot(value); setError(null); } })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Technologie se nepodařilo načíst."); });
    void load();
    const onHass = () => void load();
    window.addEventListener("frakon-energy-hass-updated", onHass);
    return () => { active = false; window.removeEventListener("frakon-energy-hass-updated", onHass); };
  }, [hass]);

  const technologies = useMemo(
    () => (snapshot?.technologies ?? []).filter((item) => Boolean(item.enabled)),
    [snapshot],
  );

  if (error) return <section className="technology-overview technology-overview--error">{error}</section>;
  if (!snapshot || technologies.length === 0) return null;

  return <section className="technology-overview">
    <div className="technology-overview__heading">
      <div><span className="eyebrow">Technologie domu</span><h2>Aktivní energetické technologie</h2></div>
      <small>Zobrazuje se pouze to, co je zapnuté v Nastavení.</small>
    </div>
    <div className="technology-overview__grid">{technologies.map((technology) => {
      const roles = technology.roles ?? [];
      const mapped = roles.flatMap((role) => {
        const entityId = role.selected_entity_id ?? role.confirmed_entity_id ?? null;
        const value = entityValue(hass, entityId);
        return entityId ? [{ role, entityId, value }] : [];
      });
      return <article className="technology-overview__card" key={technology.technology}>
        <div className="technology-overview__card-head">
          <div><span className="technology-overview__state">Aktivní</span><h3>{technology.label ?? technology.technology}</h3></div>
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
