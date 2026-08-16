import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { findFrakonEnergyEntryId, type HomeAssistant } from "./home-assistant";

type ConfigEntry = { entry_id: string };
type WsConnection = { sendMessagePromise?: <T>(message: Record<string, unknown>) => Promise<T> };
type SiteCapacityStatus = {
  entry_id: string;
  status: "not_configured" | "topology_not_ready" | "source_unavailable" | "source_stale" | "within_limit" | "over_limit" | string;
  configured: boolean;
  topology_ready: boolean;
  source_available: boolean;
  source_fresh: boolean;
  source_age_seconds: number | null;
  max_source_age_seconds: number;
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
type PhaseCapacityValue = {
  phase: string;
  current_a: number | null;
  max_current_a: number | null;
  headroom_a: number | null;
  over_limit_a: number | null;
  utilization_percent: number | null;
  over_limit: boolean;
  source_entity_id: string | null;
  source_available: boolean;
  source_fresh: boolean;
  reason: string;
};
type SitePhaseCapacityStatus = {
  entry_id: string;
  status: "not_configured" | "source_not_ready" | "within_limit" | "over_limit" | string;
  configured: boolean;
  max_phase_current_a: number | null;
  phase_current_status: string;
  source_ready: boolean;
  phases: Record<string, PhaseCapacityValue>;
  worst_phase: string | null;
  max_utilization_percent: number | null;
  any_phase_over_limit: boolean;
  reason: string;
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
type PhaseCapacityReservation = {
  lifecycle_id: string;
  attempt_id: string;
  current_l1_a: number;
  current_l2_a: number;
  current_l3_a: number;
  created_at: number;
  expires_at: number;
};
type PhaseMap = { L1: number | null; L2: number | null; L3: number | null };
type PhaseCapacityReservationSummary = {
  storage_healthy: boolean;
  last_error: string | null;
  active_count: number | null;
  reserved_current_a: PhaseMap;
  next_expiry_at: number | null;
  reservations: PhaseCapacityReservation[];
  capacity_healthy: boolean;
  capacity_error: string | null;
  effective_current_a: PhaseMap;
  effective_headroom_a: PhaseMap;
  effective_over_limit_a: PhaseMap;
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
  site_phase_capacity_reservations?: PhaseCapacityReservationSummary;
};

const PROFILE_CHANGED_EVENT = "frakon-energy-technology-profile-changed";

function formatKw(value: number | null): string {
  if (value === null) return "—";
  return `${value.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} kW`;
}

function formatA(value: number | null): string {
  if (value === null) return "—";
  return `${value.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} A`;
}

function formatPercent(value: number | null): string {
  if (value === null) return "—";
  return `${value.toLocaleString("cs-CZ", { maximumFractionDigits: 1 })} %`;
}

function formatExpiry(value: number | null): string {
  if (value === null) return "—";
  const remaining = Math.max(0, value - Math.floor(Date.now() / 1000));
  if (remaining < 60) return `${remaining} s`;
  return `${Math.ceil(remaining / 60)} min`;
}

function formatAge(value: number | null): string {
  if (value === null) return "—";
  return `${Math.round(value)} s`;
}

async function callWs<T>(hass: HomeAssistant, message: Record<string, unknown>): Promise<T> {
  const connection = hass.connection as WsConnection | undefined;
  if (!connection?.sendMessagePromise) throw new Error("WebSocket Home Assistantu není dostupný.");
  return connection.sendMessagePromise<T>(message);
}

async function findEntry(hass: HomeAssistant): Promise<ConfigEntry | null> {
  const entryId = await findFrakonEnergyEntryId(hass);
  return entryId ? { entry_id: entryId } : null;
}

function errorMessage(reason: unknown, fallback: string): string {
  if (reason instanceof Error && reason.message) return reason.message;
  if (typeof reason === "object" && reason !== null && "message" in reason) {
    const message = String((reason as { message?: unknown }).message ?? "");
    if (message) return message;
  }
  return fallback;
}

function statusLabel(status: SiteCapacityStatus["status"]): string {
  if (status === "within_limit") return "V limitu";
  if (status === "over_limit") return "Limit překročen";
  if (status === "topology_not_ready") return "Topologie není připravená";
  if (status === "source_unavailable") return "Měření není dostupné";
  if (status === "source_stale") return "Měření je zastaralé";
  if (status === "not_configured") return "Limit není nastaven";
  return status;
}

export function SiteCapacitySettings({ hass }: { hass?: HomeAssistant }) {
  const [entryId, setEntryId] = useState<string | null>(null);
  const [status, setStatus] = useState<SiteCapacityStatus | null>(null);
  const [phaseCapacity, setPhaseCapacity] = useState<SitePhaseCapacityStatus | null>(null);
  const [safety, setSafety] = useState<ExecutionSafetyStatus | null>(null);
  const [safetyError, setSafetyError] = useState<string | null>(null);
  const [limitInput, setLimitInput] = useState("");
  const [phaseLimitInput, setPhaseLimitInput] = useState("");
  const [guardEnabled, setGuardEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [phaseBusy, setPhaseBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [phaseError, setPhaseError] = useState<string | null>(null);
  const lastSourceFingerprint = useRef<string | null>(null);
  const lastPhaseFingerprint = useRef<string | null>(null);

  const load = useCallback(async () => {
    if (!hass) return;
    try {
      const entry = await findEntry(hass);
      if (!entry) throw new Error("Nebyla nalezena položka integrace FRAKON Energy.");
      const [value, phaseValue] = await Promise.all([
        callWs<SiteCapacityStatus>(hass, { type: "frakon_energy/site_capacity/status", entry_id: entry.entry_id }),
        callWs<SitePhaseCapacityStatus>(hass, { type: "frakon_energy/site_phase_capacity/status", entry_id: entry.entry_id }),
      ]);
      setEntryId(entry.entry_id);
      setStatus(value);
      setLimitInput(value.max_grid_import_kw === null ? "" : String(value.max_grid_import_kw));
      setGuardEnabled(value.execution_guard_active);
      setPhaseCapacity(phaseValue);
      setPhaseLimitInput(phaseValue.max_phase_current_a === null ? "" : String(phaseValue.max_phase_current_a));
      setError(null);
      setPhaseError(null);

      try {
        const safetyValue = await callWs<ExecutionSafetyStatus>(hass, {
          type: "frakon_energy/load_execution/safety_status",
          entry_id: entry.entry_id,
        });
        setSafety(safetyValue);
        setSafetyError(null);
      } catch (reason) {
        setSafety(null);
        setSafetyError(errorMessage(reason, "Bezpečnostní stav rezervací se nepodařilo načíst."));
      }
    } catch (reason) {
      setError(errorMessage(reason, "Kapacitu přívodu se nepodařilo načíst."));
    }
  }, [hass?.connection]);

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

  const phaseFingerprint = useMemo(() => {
    if (!hass || !phaseCapacity) return "";
    return ["L1", "L2", "L3"].map((phase) => {
      const entityId = phaseCapacity.phases[phase]?.source_entity_id;
      const source = entityId ? hass.states[entityId] : undefined;
      return `${phase}:${entityId ?? "none"}:${source?.state ?? "missing"}:${String(source?.attributes.unit_of_measurement ?? "")}`;
    }).join("|");
  }, [hass, phaseCapacity]);

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

  useEffect(() => {
    if (!phaseFingerprint) return;
    if (lastPhaseFingerprint.current === null) {
      lastPhaseFingerprint.current = phaseFingerprint;
      return;
    }
    if (lastPhaseFingerprint.current === phaseFingerprint) return;
    lastPhaseFingerprint.current = phaseFingerprint;
    void load();
  }, [load, phaseFingerprint]);

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

  const savePhaseLimit = async (clear = false) => {
    if (!hass || !entryId) return;
    let parsed: number | null = null;
    if (!clear) {
      const candidate = Number(phaseLimitInput.replace(",", "."));
      if (!Number.isFinite(candidate) || candidate <= 0) {
        setPhaseError("Maximální proud fáze musí být kladné číslo v A.");
        return;
      }
      parsed = candidate;
    }
    setPhaseBusy(true);
    setPhaseError(null);
    try {
      const value = await callWs<SitePhaseCapacityStatus>(hass, {
        type: "frakon_energy/site_phase_capacity/set",
        entry_id: entryId,
        max_phase_current_a: parsed,
      });
      setPhaseCapacity(value);
      setPhaseLimitInput(value.max_phase_current_a === null ? "" : String(value.max_phase_current_a));
      window.dispatchEvent(new CustomEvent(PROFILE_CHANGED_EVENT));
    } catch (reason) {
      setPhaseError(reason instanceof Error ? reason.message : "Proudový limit fází se nepodařilo uložit.");
    } finally {
      setPhaseBusy(false);
    }
  };

  const reservations = safety?.site_capacity_reservations;
  const phaseReservations = safety?.site_phase_capacity_reservations;
  const guard = safety?.site_capacity_guard;

  return <article className="chart-card technology-settings site-capacity-settings">
    <div className="technology-settings__header">
      <div><span className="eyebrow">Kapacita přívodu</span><h2>Rezerva odběru ze sítě</h2></div>
      <span className={`entity-badge ${status?.status === "over_limit" || status?.status === "source_stale" || guard?.currently_blocks_all_new_starts ? "warn" : ""}`}>{status ? statusLabel(status.status) : "Načítám…"}</span>
    </div>
    <p className="settings-copy">Nastavený limit slouží vždy jako diagnostika rezervy přívodu. Blokování řízených startů je samostatná volba. Po zapnutí execution guard vyžaduje čerstvé hlavní měření, započítává rezervace právě startujících spotřebičů a při nedostatečné kapacitě nový start bezpečně odmítne.</p>

    <div className="role-list">
      <div className="role-row">
        <div className="role-row__label"><b>Maximální odběr ze sítě · kW</b><small>Musí odpovídat hlavnímu měření celého domu; může obsahovat i vlastní bezpečnostní rezervu.</small></div>
        <input type="number" min="0.1" step="0.1" value={limitInput} disabled={busy || !hass} placeholder="např. 15.0" onChange={(event) => setLimitInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void saveLimit(false); }} />
      </div>
      <div className="role-row">
        <div className="role-row__label"><b>Vynucovat limit při řízených startech</b><small>Zapni pouze tehdy, když je zdroj odběru skutečně hlavní měření celého domu. Existující instalace s dříve aktivním limitem zůstávají po aktualizaci chráněné.</small></div>
        <label><input type="checkbox" checked={guardEnabled} disabled={busy || !hass || !limitInput.trim()} onChange={(event) => setGuardEnabled(event.target.checked)} /> {guardEnabled ? "Execution guard aktivní" : "Pouze diagnostika"}</label>
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
      <span>Čerstvost měření <b>{status.source_fresh ? "OK" : "nevyhovuje"}</b></span>
      <span>Stáří měření <b>{formatAge(status.source_age_seconds)}</b></span>
      <span>Guard <b>{status.execution_guard_active ? "aktivní" : "vypnutý"}</b></span>
      <span>Rezervováno starty <b>{formatKw(reservations?.reserved_power_kw ?? null)}</b></span>
      <span>Aktivní rezervace <b>{reservations?.active_count ?? "—"}</b></span>
      <span>Nejbližší expirace <b>{formatExpiry(reservations?.next_expiry_at ?? null)}</b></span>
    </div> : null}

    <div className="technology-settings__header">
      <div><span className="eyebrow">Třífázová ochrana</span><h2>Proudová rezerva L1 / L2 / L3</h2></div>
      <span className={`entity-badge ${phaseCapacity?.status === "over_limit" || phaseCapacity?.status === "source_not_ready" ? "warn" : ""}`}>{phaseCapacity?.status ?? "Načítám…"}</span>
    </div>
    <p className="settings-copy">Po nastavení proudového limitu je třífázová ochrana součástí bounded i finální fyzické startovací hranice. FRAKON vyžaduje čerstvé potvrzené L1/L2/L3, explicitní fázovou topologii profilu a započítává také durable rezervace právě startujících spotřebičů. Chybějící fáze ani proudy se nikdy neodhadují z celkového výkonu.</p>
    <div className="role-list">
      <div className="role-row">
        <div className="role-row__label"><b>Maximální proud jedné fáze · A</b><small>Zadej skutečný limit jedné fáze přípojky/jističe. Hodnota není automaticky odvozena z celkových kW.</small></div>
        <input type="number" min="0.1" step="0.1" value={phaseLimitInput} disabled={phaseBusy || !hass} placeholder="např. 25" onChange={(event) => setPhaseLimitInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void savePhaseLimit(false); }} />
        <div className="role-actions"><button disabled={phaseBusy || !hass} onClick={() => void savePhaseLimit(false)}>Uložit proudový limit</button>{phaseCapacity?.configured ? <button disabled={phaseBusy} onClick={() => void savePhaseLimit(true)}>Zrušit proudový limit</button> : null}</div>
      </div>
    </div>
    {phaseError ? <div className="settings-error">{phaseError}</div> : null}
    {phaseCapacity ? <div className="discovery-summary">
      <span>Limit fáze <b>{formatA(phaseCapacity.max_phase_current_a)}</b></span>
      <span>Zdroj L1/L2/L3 <b>{phaseCapacity.source_ready ? "připraven" : phaseCapacity.phase_current_status}</b></span>
      <span>Nejzatíženější fáze <b>{phaseCapacity.worst_phase ?? "—"}</b></span>
      <span>Max. využití <b>{formatPercent(phaseCapacity.max_utilization_percent)}</b></span>
      <span>Phase guard <b>{phaseCapacity.execution_guard_active ? "aktivní" : "vypnutý"}</b></span>
      <span>Rezervováno L1 <b>{formatA(phaseReservations?.reserved_current_a.L1 ?? null)}</b></span>
      <span>Rezervováno L2 <b>{formatA(phaseReservations?.reserved_current_a.L2 ?? null)}</b></span>
      <span>Rezervováno L3 <b>{formatA(phaseReservations?.reserved_current_a.L3 ?? null)}</b></span>
      <span>Efektivní rezerva L1 <b>{formatA(phaseReservations?.effective_headroom_a.L1 ?? null)}</b></span>
      <span>Efektivní rezerva L2 <b>{formatA(phaseReservations?.effective_headroom_a.L2 ?? null)}</b></span>
      <span>Efektivní rezerva L3 <b>{formatA(phaseReservations?.effective_headroom_a.L3 ?? null)}</b></span>
      <span>Aktivní fázové rezervace <b>{phaseReservations?.active_count ?? "—"}</b></span>
      <span>Nejbližší expirace <b>{formatExpiry(phaseReservations?.next_expiry_at ?? null)}</b></span>
    </div> : null}
    {phaseCapacity ? <div className="role-list">
      {["L1", "L2", "L3"].map((phase) => {
        const key = phase as "L1" | "L2" | "L3";
        const item = phaseCapacity.phases[phase];
        if (!item) return null;
        const reserved = phaseReservations?.reserved_current_a[key] ?? null;
        const effectiveCurrent = phaseReservations?.effective_current_a[key] ?? null;
        const effectiveHeadroom = phaseReservations?.effective_headroom_a[key] ?? null;
        const effectiveOver = phaseReservations?.effective_over_limit_a[key] ?? null;
        return <div className="role-row" key={phase}>
          <div className="role-row__label"><b>{phase} · {formatA(item.current_a)}</b><small>{item.source_entity_id ?? "měření není přiřazeno"} · {item.reason}</small></div>
          <div className="discovery-summary">
            <span>Rezerva měření <b>{formatA(item.headroom_a)}</b></span>
            <span>Rezervováno starty <b>{formatA(reserved)}</b></span>
            <span>Efektivní proud <b>{formatA(effectiveCurrent)}</b></span>
            <span>Pro další start zbývá <b>{formatA(effectiveHeadroom)}</b></span>
            <span>Efektivní překročení <b>{formatA(effectiveOver)}</b></span>
            <span>Využití měření <b>{formatPercent(item.utilization_percent)}</b></span>
          </div>
        </div>;
      })}
    </div> : null}
    {phaseCapacity ? <p className="missing-reason">{phaseCapacity.reason} Phase execution guard: {phaseCapacity.execution_guard_active ? "aktivní" : "vypnutý"}.</p> : null}

    {reservations && !reservations.storage_healthy ? <div className="settings-error">Stav rezervací není důvěryhodný: {reservations.last_error ?? "neznámá chyba"}</div> : null}
    {phaseReservations && !phaseReservations.storage_healthy ? <div className="settings-error">Stav fázových rezervací není důvěryhodný: {phaseReservations.last_error ?? "neznámá chyba"}</div> : null}
    {phaseReservations?.storage_healthy && !phaseReservations.capacity_healthy ? <div className="settings-error">Efektivní fázovou rezervu nelze autoritativně spočítat: {phaseReservations.capacity_error ?? "kapacita fází není připravená"}</div> : null}
    {safetyError ? <p className="missing-reason">Rozšířená bezpečnostní diagnostika není dostupná: {safetyError}</p> : null}
    {status ? <p className="missing-reason">{status.reason}{status.source_entity_id ? ` Zdroj: ${status.source_entity_id}.` : ""} Execution guard: {status.execution_guard_active ? "aktivní" : "vypnutý"}{guard?.blocking_reason ? ` · blokuje nové starty: ${guard.blocking_reason}` : ""}.</p> : null}
    {reservations?.reservations?.length ? <div className="role-list">
      {reservations.reservations.map((reservation) => <div className="role-row" key={reservation.lifecycle_id}>
        <div className="role-row__label"><b>Rezervace {formatKw(reservation.power_kw)}</b><small>{reservation.lifecycle_id} · {reservation.attempt_id} · expirace za {formatExpiry(reservation.expires_at)}</small></div>
      </div>)}
    </div> : null}
    {phaseReservations?.reservations?.length ? <div className="role-list">
      {phaseReservations.reservations.map((reservation) => <div className="role-row" key={`phase-${reservation.lifecycle_id}`}>
        <div className="role-row__label"><b>Fázová rezervace · L1 {formatA(reservation.current_l1_a)} · L2 {formatA(reservation.current_l2_a)} · L3 {formatA(reservation.current_l3_a)}</b><small>{reservation.lifecycle_id} · {reservation.attempt_id} · expirace za {formatExpiry(reservation.expires_at)}</small></div>
      </div>)}
    </div> : null}
  </article>;
}
