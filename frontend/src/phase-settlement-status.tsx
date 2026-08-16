import React, { useCallback, useEffect, useState } from "react";
import { findFrakonEnergyEntryId, type HomeAssistant } from "./home-assistant";

type WsConnection = { sendMessagePromise?: <T>(message: Record<string, unknown>) => Promise<T> };

type SettlementLifecycleStatus = {
  lifecycle_id: string;
  status: "waiting" | "confirmed" | "released" | "error" | string;
  last_checked_at: number;
  confirmation_status: string | null;
  release_status: string | null;
  last_error: string | null;
};

type SettlementRuntimeStatus = {
  started: boolean;
  healthy: boolean;
  last_error: string | null;
  statuses: SettlementLifecycleStatus[];
  poll_seconds: number;
  read_only: true;
  service_call_performed: false;
  execution_performed: false;
};

type ExecutionSafetyStatus = {
  phase_settlement_runtime?: SettlementRuntimeStatus;
};

async function callWs<T>(hass: HomeAssistant, message: Record<string, unknown>): Promise<T> {
  const connection = hass.connection as WsConnection | undefined;
  if (!connection?.sendMessagePromise) throw new Error("WebSocket Home Assistantu není dostupný.");
  return connection.sendMessagePromise<T>(message);
}

function errorMessage(reason: unknown, fallback: string): string {
  if (reason instanceof Error && reason.message) return reason.message;
  if (typeof reason === "object" && reason !== null && "message" in reason) {
    const message = String((reason as { message?: unknown }).message ?? "");
    if (message) return message;
  }
  return fallback;
}

function statusLabel(status: SettlementLifecycleStatus["status"]): string {
  if (status === "waiting") return "Čeká na důkaz";
  if (status === "confirmed") return "Telemetrie potvrzena";
  if (status === "released") return "Rezervace uvolněna";
  if (status === "error") return "Chyba settlementu";
  return status;
}

function formatChecked(timestamp: number): string {
  if (!timestamp) return "—";
  return new Date(timestamp * 1000).toLocaleTimeString("cs-CZ", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function PhaseSettlementStatus({ hass }: { hass?: HomeAssistant }) {
  const [runtime, setRuntime] = useState<SettlementRuntimeStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!hass) return;
    try {
      const entryId = await findFrakonEnergyEntryId(hass);
      if (!entryId) throw new Error("Nebyla nalezena aktivní VisionQ položka FRAKON Energy.");
      const safety = await callWs<ExecutionSafetyStatus>(hass, {
        type: "frakon_energy/load_execution/safety_status",
        entry_id: entryId,
      });
      setRuntime(safety.phase_settlement_runtime ?? null);
      setError(null);
    } catch (reason) {
      setRuntime(null);
      setError(errorMessage(reason, "Settlement runtime se nepodařilo načíst."));
    }
  }, [hass?.connection]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  const unhealthy = runtime ? !runtime.started || !runtime.healthy : false;

  return <article className="chart-card technology-settings site-capacity-settings">
    <div className="technology-settings__header">
      <div>
        <span className="eyebrow">Settlement rezervací</span>
        <h2>Potvrzení propsání startu do L1 / L2 / L3</h2>
      </div>
      <span className={`entity-badge ${unhealthy ? "warn" : ""}`}>
        {runtime ? (runtime.started && runtime.healthy ? "Healthy" : "Vyžaduje kontrolu") : "Načítám…"}
      </span>
    </div>

    <p className="settings-copy">
      Runtime pouze sleduje již existující fázové rezervace. Uvolnění je možné až po verified lifecycle,
      dvou nezávislých pozitivních vzorcích telemetrie a závěrečném serverovém proof rechecku. Při chybě
      rezervace zůstává až do konzervativní TTL expirace.
    </p>

    {runtime ? <div className="discovery-summary">
      <span>Runtime <b>{runtime.started ? "spuštěn" : "zastaven"}</b></span>
      <span>Zdraví <b>{runtime.healthy ? "OK" : "chyba"}</b></span>
      <span>Kontrola každých <b>{runtime.poll_seconds} s</b></span>
      <span>Sledované lifecycle <b>{runtime.statuses.length}</b></span>
    </div> : null}

    {runtime?.last_error ? <div className="settings-error">Settlement runtime: {runtime.last_error}</div> : null}
    {error ? <div className="settings-error">{error}</div> : null}

    {runtime?.statuses.length ? <div className="role-list">
      {runtime.statuses.map((item) => <div className="role-row" key={item.lifecycle_id}>
        <div className="role-row__label">
          <b>{statusLabel(item.status)}</b>
          <small>{item.lifecycle_id} · poslední kontrola {formatChecked(item.last_checked_at)}</small>
        </div>
        <div className="discovery-summary">
          <span>Confirmation <b>{item.confirmation_status ?? "—"}</b></span>
          <span>Release <b>{item.release_status ?? "—"}</b></span>
        </div>
        {item.last_error ? <div className="settings-error">{item.last_error}</div> : null}
      </div>)}
    </div> : runtime ? <p className="missing-reason">Žádná aktivní fázová rezervace právě nečeká na settlement.</p> : null}
  </article>;
}
