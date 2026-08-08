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
type CapacityReservation = {
  lifecycle_id: string;
  attempt_id: string;
  power_kw: number;
  created_at: number;
  expires_at: number;
};
type CapacityReservationSummary = {
  storage_healthy: boolean;
  last_error: string | null;
  active_count: number | null;
  reserved_power_kw: number | null;
  next_expiry_at: number | null;
  reservations: CapacityReservation[];
};
type ExecutionSafetyStatus = {
  site_capacity_guard?: {
    configured: boolean;
    guard_active: boolean;
    data_ready: boolean;
    currently_blocks_all_new_starts: boolean;
    blocking_reason: string | null;
  };
  site_capacity_reservations?: CapacityReservationSummary;
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

function formatExpiry(value: number | null): string {
  if (value === null) return "—";
  const remaining = Math.max(0, value - Math.floor(Date.now() / 1000));
  if (remaining < 60) return `${remaining} s`;
  const minutes = Math.ceil(remaining / 60);
  return `${minutes} min`;
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
  const [safety, setSafety] = useState<ExecutionSafetyStatus | null>(null);
  const [safetyError, setSafetyError] = useState<string | null>(null);
  const [limitInput, setLimitInput] = useState("");
  const [guardEnabled, setGuardEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastSourceFingerprint = useRef<string | null>(null);

  const load = useCallback(async () => {
    if (!hass) return;
    try {
      const entry = await findEntry(hass);
      if (!entry) throw new Error("Nebyla nalezena položka integrace FRAKON Energy.");
      const value = await callWs<SiteCapacityStatus>(hass, {
        type: "frakon_energy/site_capacity/status",
        entry_id: entry.entry_id,
      });
      setEntryId(entry.entry_id);
      setStatus(value);
      setLimitInput(value.max_grid_import_kw === null ? "" : String(value.max_grid_import_kw));
      setGuardEnabled(value.execution_guard_active);
      setError(null);

      try {
        const safetyValue = await callWs<ExecutionSafetyStatus>(hass, {
          type: "frakon_energy/load_execution/safety_status",
          entry_id: entry.entry_id,
        });
        setSafety(safetyValue);
        setSafetyError(null);
      } catch (reason) {
        setSafety(null);
        setSafetyError(reason instanceof Error ? reason.message : "Bezpečnostní stav rezervací se nepodařilo načíst.");
      }
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
    if (lastSourceFingerprint.current === null) {
      lastSourceFingerprint.current = sourceFingerprint;
      return;
    }
    if (lastSourceFingerprint.current === sourceFingerprint) return;
    lastSourceFingerprint.current = sourceFingerprint;
    void load();
  }, [load, sourceFingerprint]);

  const saveLimit = async (clear = false) => {
    if (!hass || !entryId) return;
    let parsed: number | null = null;
    if (!clear) {
      const candidate = Number(limitInput.replace(",", "."));
      if (!Number.isFinite(candidate) || candidate <= 0) {
        setError("Maximální odběr musí být kladné číslo v kW.");
        return;
      }
      parsed = candidate;
    }
    setBusy(true);
    setError(null);
    try {
      const value = await callWs<SiteCapacityStatus>(hass, {
        type: "frakon_energy/site_capacity/set",
        entry_id: entryId,
        max_grid_import_kw: parsed,
        execution_guard_enabled: clear ? false : guardEnabled,
      });
      setStatus(value);
      setLimitInput(value.max_grid_import_kw === null ? "" : String(value.max_grid_import_kw));
      setGuardEnabled(value.execution_guard_active);
      window.dispatchEvent(new CustomEvent(PROFILE_CHANGED_EVENT));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Nastavení kapacity přívodu se nepodařilo uložit.");
    } finally {
      setBusy(false);
    }
  };

  const reservations = safety?.site_capacity_reservations;
  const guard = safety?.site_capacity_guard;

  return <article className="chart-card technology-settings site-capacity-settings">
    <div className="technology-settings__header">
      <div><span className="eyebrow">Kapacita přívodu</span><h2>Rezerva odběru ze sítě</h2></div>
      <span className={`entity-badge ${status?.status === "over_limit" || guard?.currently_blocks_all_new_starts ? "warn" : ""}`}>{status ? statusLabel(status.status) : "Načítám…"}</span>
    </div>
    <p className="settings-copy">Nastavený limit slouží vždy jako diagnostika rezervy přívodu. Samotné blokování řízených startů je samostatná volba: po zapnutí execution guard kontroluje živý odběr, započítává krátkodobé rezervace právě startujících spotřebičů a při nedostatečné kapacitě nový start bezpečně odmítne.</p>

    <div className="role-list">
      <div className="role-row">
        <div className="role-row__label"><b>Maximální odběr ze sítě · kW</b><small>Musí odpovídat hlavnímu měření celého domu; může obsahovat i vlastní bezpečnostní rezervu.</small></div>
        <input
          type="number"
          min="0.1"
          step="0.1"
          value={limitInput}
          disabled={busy || !hass}
          placeholder="např. 15.0"
          onChange={(event) => setLimitInput(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") void saveLimit(false); }}
        />
      </div>
      <div className="role-row">
        <div className="role-row__label"><b>Vynucovat limit při řízených startech</b><small>Zapni pouze tehdy, když je zdroj odběru skutečně hlavní měření celého domu. Existující instalace s dříve aktivním limitem zůstávají po aktualizaci chráněné.</small></div>
        <label>
          <input
            type="checkbox"
            checked={guardEnabled}
            disabled={busy || !hass || !limitInput.trim()}
            onChange={(event) => setGuardEnabled(event.target.checked)}
          /> {guardEnabled ? "Execution guard aktivní" : "Pouze diagnostika"}
        </label>
        <div className="role-actions"><button disabled={busy || !hass} onClick={() => void saveLimit(false)}>Uložit</button>{status?.configured ? <button disabled={busy} onClick={() => void saveLimit(true)}>Zrušit limit</button> : null}</div>
      </div>
    </div>

    {error ? <div className="settings-error">{error}</div> : null}
    {status ? <div className="discovery-summary">
      <span>Aktuální odběr <b>{formatKw(status.current_grid_import_kw)}</b></span>
      <span>Nastavený limit <b>{formatKw(status.max_grid_import_kw)}</b></span>
      <span>Rezerva měření <b>{formatKw(status.grid_headroom_kw)}</b></span>
      <span>Překročení <b>{formatKw(status.grid_over_limit_kw)}</b></span>
      <span>Využití <b>{formatPercent(status.utilization_percent)}</b></span>
      <span>Guard <b>{status.execution_guard_active ? "aktivní" : "vypnutý"}</b></span>
      <span>Rezervováno starty <b>{formatKw(reservations?.reserved_power_kw ?? null)}</b></span>
      <span>Aktivní rezervace <b>{reservations?.active_count ?? "—"}</b></span>
      <span>Nejbližší expirace <b>{formatExpiry(reservations?.next_expiry_at ?? null)}</b></span>
    </div> : null}
    {reservations && !reservations.storage_healthy ? <div className="settings-error">Stav rezervací není důvěryhodný: {reservations.last_error ?? "neznámá chyba"}</div> : null}
    {safetyError ? <p className="missing-reason">Rozšířená bezpečnostní diagnostika není dostupná: {safetyError}</p> : null}
    {status ? <p className="missing-reason">{status.reason}{status.source_entity_id ? ` Zdroj: ${status.source_entity_id}.` : ""} Execution guard: {status.execution_guard_active ? "aktivní" : "vypnutý"}{guard?.blocking_reason ? ` · blokuje nové starty: ${guard.blocking_reason}` : ""}.</p> : null}
    {reservations?.reservations?.length ? <div className="role-list">
      {reservations.reservations.map((reservation) => <div className="role-row" key={reservation.lifecycle_id}>
        <div className="role-row__label">
          <b>Rezervace {formatKw(reservation.power_kw)}</b>
          <small>{reservation.lifecycle_id} · {reservation.attempt_id} · expirace za {formatExpiry(reservation.expires_at)}</small>
        </div>
      </div>)}
    </div> : null}
  </article>;
}