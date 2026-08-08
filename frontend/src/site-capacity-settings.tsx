import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { HomeAssistant } from "./home-assistant";

type ConfigEntry = { entry_id: string; domain?: string };
type WsConnection = { sendMessagePromise?: <T>(message: Record<string, unknown>) => Promise<T> };
type SiteCapacityStatus = {
  entry_id: string;
  status: "not_configured" | "topology_not_ready" | "source_unavailable" | "within_limit" | "over_limit" | string;
  configured: boolean;
  topology_ready: boolean;
  source_available: boolean;
  max_grid_import_kw: number | null;
  current_grid_import_kw: number | null;
  grid_headroom_kw: number | null;
  grid_over_limit_kw: number | null;
  utilization_percent: number | null;
  source_entity_id: string | null;
  reason: string;
  read_only: true;
  service_call_performed: false;
  execution_performed: false;
  execution_guard_active: boolean;
};

const PROFILE_CHANGED_EVENT = "frakon-energy-technology-profile-changed";

function formatKw(value: number | null): string {
  if (value === null) return "—";
  return `${value.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} kW`;
}
function formatPercent(value: number | null): string {
  if (value === null) return "—";
  return `${value.toLocaleString("cs-CZ", { maximumFractionDigits: 1 })} %`;
}
async function callWs<T>(hass: HomeAssistant, message: Record<string, unknown>): Promise<T> {
  const connection = hass.connection as WsConnection | undefined;
  if (!connection?.sendMessagePromise) throw new Error("WebSocket Home Assistantu není dostupný.");
  return connection.sendMessagePromise<T>(message);
}
async function findEntry(hass: HomeAssistant): Promise<ConfigEntry | null> {
  const entries = await callWs<ConfigEntry[]>(hass, { type: "config_entries/get" });
  return entries.find((entry) => entry.domain === "frakon_energy") ?? null;
}
function statusLabel(status: SiteCapacityStatus["status"]): string {
  if (status === "within_limit") return "V limitu";
  if (status === "over_limit") return "Limit překročen";
  if (status === "topology_not_ready") return "Topologie není připravená";
  if (status === "source_unavailable") return "Měření není dostupné";
  if (status === "not_configured") return "Limit není nastaven";
  return status;
}

export function SiteCapacitySettings({ hass }: { hass?: HomeAssistant }) {
  const [entryId, setEntryId] = useState<string | null>(null);
  const [status, setStatus] = useState<SiteCapacityStatus | null>(null);
  const [limitInput, setLimitInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastSourceFingerprint = useRef<string | null>(null);

  const load = useCallback(async () => {
    if (!hass) return;
    try {
      const entry = await findEntry(hass);
      if (!entry) throw new Error("Nebyla nalezena položka integrace FRAKON Energy.");
      const value = await callWs<SiteCapacityStatus>(hass, { type: "frakon_energy/site_capacity/status", entry_id: entry.entry_id });
      setEntryId(entry.entry_id);
      setStatus(value);
      setLimitInput(value.max_grid_import_kw === null ? "" : String(value.max_grid_import_kw));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Kapacitu přívodu se nepodařilo načíst.");
    }
  }, [hass]);

  useEffect(() => {
    void load();
    window.addEventListener(PROFILE_CHANGED_EVENT, load);
    return () => window.removeEventListener(PROFILE_CHANGED_EVENT, load);
  }, [load]);

  const sourceFingerprint = useMemo(() => {
    if (!hass || !status?.source_entity_id) return "";
    const source = hass.states[status.source_entity_id];
    return `${status.source_entity_id}:${source?.state ?? "missing"}:${String(source?.attributes.unit_of_measurement ?? "")}`;
  }, [hass, status?.source_entity_id]);

  useEffect(() => {
    if (!sourceFingerprint) return;
    if (lastSourceFingerprint.current === null) { lastSourceFingerprint.current = sourceFingerprint; return; }
    if (lastSourceFingerprint.current === sourceFingerprint) return;
    lastSourceFingerprint.current = sourceFingerprint;
    void load();
  }, [load, sourceFingerprint]);

  const applySettings = async (message: Record<string, unknown>) => {
    if (!hass || !entryId) return;
    setBusy(true);
    setError(null);
    try {
      const value = await callWs<SiteCapacityStatus>(hass, { type: "frakon_energy/site_capacity/set", entry_id: entryId, ...message });
      setStatus(value);
      setLimitInput(value.max_grid_import_kw === null ? "" : String(value.max_grid_import_kw));
      window.dispatchEvent(new CustomEvent(PROFILE_CHANGED_EVENT));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Nastavení kapacity přívodu se nepodařilo uložit.");
    } finally {
      setBusy(false);
    }
  };

  const saveLimit = async (clear = false) => {
    let parsed: number | null = null;
    if (!clear) {
      const candidate = Number(limitInput.replace(",", "."));
      if (!Number.isFinite(candidate) || candidate <= 0) {
        setError("Maximální odběr musí být kladné číslo v kW.");
        return;
      }
      parsed = candidate;
    }
    await applySettings({ max_grid_import_kw: parsed });
  };

  const setGuard = async (enabled: boolean) => {
    if (enabled && !status?.configured) {
      setError("Nejdřív ulož maximální odběr ze sítě.");
      return;
    }
    await applySettings({ execution_guard_enabled: enabled });
  };

  const guardActive = status?.execution_guard_active ?? false;

  return <article className={`chart-card technology-settings site-capacity-settings ${guardActive ? "is-guarded" : ""}`}>
    <div className="technology-settings__header">
      <div><span className="eyebrow">Kapacita přívodu</span><h2>Rezerva odběru ze sítě</h2></div>
      <span className={`entity-badge ${status?.status === "over_limit" ? "warn" : ""}`}>{status ? statusLabel(status.status) : "Načítám…"}</span>
    </div>
    <p className="settings-copy">Zadej skutečný maximální odběr v kW. Samotné nastavení limitu je diagnostické. Teprve explicitně zapnutý Execution guard použije limit v bounded gate, posledním fyzickém rechecku a durable reservation accountingu.</p>
    <div className="role-list">
      <div className="role-row">
        <div className="role-row__label"><b>Maximální odběr ze sítě · kW</b><small>Musí odpovídat hlavnímu měření celého domu; může obsahovat vlastní bezpečnostní rezervu.</small></div>
        <input type="number" min="0.1" step="0.1" value={limitInput} disabled={busy || !hass} placeholder="např. 15.0" onChange={(event) => setLimitInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void saveLimit(false); }} />
        <div className="role-actions"><button disabled={busy || !hass} onClick={() => void saveLimit(false)}>Uložit</button>{status?.configured ? <button disabled={busy || guardActive} title={guardActive ? "Nejdřív vypni execution guard." : undefined} onClick={() => void saveLimit(true)}>Zrušit limit</button> : null}</div>
      </div>
      <div className="role-row">
        <div className="role-row__label"><b>Execution guard kapacity</b><small>{guardActive ? "Nový turn_on musí projít kapacitní kontrolou. Bezpečnostní stop povinnosti zůstávají nezávislé." : "Vypnuto: limit i jeho senzory jsou pouze diagnostické a nové starty neblokují ani nevytvářejí kapacitní rezervace."}</small></div>
        <label className="technology-toggle"><input type="checkbox" checked={guardActive} disabled={busy || !status?.configured} onChange={(event) => void setGuard(event.target.checked)} /><span>{guardActive ? "Aktivní" : "Vypnutý"}</span></label>
        <div className="role-actions" />
      </div>
    </div>
    {error ? <div className="settings-error">{error}</div> : null}
    {status ? <div className="discovery-summary"><span>Aktuální odběr <b>{formatKw(status.current_grid_import_kw)}</b></span><span>Nastavený limit <b>{formatKw(status.max_grid_import_kw)}</b></span><span>Rezerva <b>{formatKw(status.grid_headroom_kw)}</b></span><span>Překročení <b>{formatKw(status.grid_over_limit_kw)}</b></span><span>Využití <b>{formatPercent(status.utilization_percent)}</b></span><span>Execution guard <b>{guardActive ? "aktivní" : "vypnutý"}</b></span></div> : null}
    {status ? <p className="missing-reason">{status.reason}{status.source_entity_id ? ` Zdroj: ${status.source_entity_id}.` : ""} execution_guard_active={String(status.execution_guard_active)}</p> : null}
  </article>;
}
