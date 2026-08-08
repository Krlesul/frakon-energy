import React, { useCallback, useEffect, useMemo, useState } from "react";
import { callHomeAssistantWs, type HomeAssistant } from "./home-assistant";
import "./load-execution-pending-run-panel.css";

type PendingRunSchedulerItem = {
  pending_run_id: string;
  attempt_id: string;
  entity_id: string;
  status: "scheduled" | "preparing" | "retrying_stop_lease" | "no_start_needed" | "prepared_with_stop_lease" | "delegated_to_start_scheduler" | "existing_lifecycle" | "missed_start_window" | "blocked" | "error" | string;
  starts_at: string;
  ends_at: string;
  next_wake_at: string | null;
  last_processed_at: string | null;
  last_error: string | null;
  timer_active: boolean;
  lifecycle_prepared: boolean;
  stop_lease_prepared: boolean;
  retry_count: number;
  service_call_performed: false;
  execution_performed: false;
  executor_available: false;
};

type PendingRunSchedulerResponse = {
  entry_id: string;
  started: boolean;
  healthy: boolean;
  last_error: string | null;
  statuses: PendingRunSchedulerItem[];
  creates_authority: false;
  calls_home_assistant_services_directly: false;
  delegates_only_to_existing_prepare_flows: true;
  read_only: true;
  state_transition_performed: false;
  service_call_performed: false;
  execution_performed: false;
};

const STATUS_LABELS: Record<string, string> = {
  scheduled: "Čeká na start",
  preparing: "Připravuji bezpečný lifecycle",
  retrying_stop_lease: "Opakuji pouze stop lease",
  no_start_needed: "Cíl už byl splněný · bez startu",
  prepared_with_stop_lease: "Prepared + stop lease",
  delegated_to_start_scheduler: "Předáno start scheduleru",
  existing_lifecycle: "Lifecycle už existuje",
  missed_start_window: "Start window minul",
  blocked: "Blokováno",
  error: "Chyba",
};

function formatIso(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("cs-CZ", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
}

function statusTone(status: string): string {
  if (["no_start_needed", "prepared_with_stop_lease", "delegated_to_start_scheduler"].includes(status)) return "ok";
  if (["missed_start_window", "blocked", "error"].includes(status)) return "danger";
  if (["preparing", "retrying_stop_lease"].includes(status)) return "working";
  return "waiting";
}

export function LoadExecutionPendingRunPanel({ hass, entryId }: { hass?: HomeAssistant; entryId: string | null }) {
  const [runtime, setRuntime] = useState<PendingRunSchedulerResponse | null>(null);
  const [status, setStatus] = useState("Načítám pending scheduler…");
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async (quiet = false) => {
    if (!hass || !entryId) {
      setRuntime(null);
      setStatus("Čekám na Home Assistant");
      return;
    }
    if (!quiet) setRefreshing(true);
    try {
      const response = await callHomeAssistantWs<PendingRunSchedulerResponse>(hass, {
        type: "frakon_energy/load_execution_pending_run/scheduler",
        entry_id: entryId,
      });
      setRuntime(response);
      if (!response.started) setStatus("Pending scheduler není spuštěný.");
      else if (!response.healthy) setStatus(`Pending scheduler je fail-closed: ${response.last_error ?? "neznámá chyba"}`);
      else setStatus(response.statuses.length > 0 ? `${response.statuses.length} durable pending runů v auditu.` : "Žádný durable pending run.");
    } catch (error) {
      setRuntime(null);
      setStatus(`Pending scheduler nelze načíst: ${String(error)}`);
    } finally {
      if (!quiet) setRefreshing(false);
    }
  }, [entryId, hass]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(true), 15_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const items = useMemo(
    () => [...(runtime?.statuses ?? [])].sort((a, b) => b.starts_at.localeCompare(a.starts_at)).slice(0, 10),
    [runtime],
  );

  return <section className={`pending-run-panel ${runtime?.healthy === false ? "has-error" : ""}`}>
    <div className="pending-run-header">
      <div><span className="eyebrow">Pending Run Scheduler</span><h3>Restart-safe budoucí běhy</h3><p>Vrstva mezi spotřebovaným approval a krátkým start window. Drží pouze exact plan a timer; sama nikdy nevolá Home Assistant service.</p></div>
      <div className={`pending-run-health ${runtime?.started && runtime?.healthy ? "ok" : "danger"}`}><span>Runtime</span><strong>{runtime ? (runtime.started ? (runtime.healthy ? "HEALTHY" : "FAIL-CLOSED") : "STOPPED") : "—"}</strong></div>
    </div>

    {runtime?.last_error ? <div className="pending-run-alert"><strong>Scheduler chyba</strong><span>{runtime.last_error}</span></div> : null}

    <div className="pending-run-list">
      {items.length === 0 ? <div className="pending-run-empty">Zatím není uložen žádný budoucí execution run.</div> : items.map((item) => <article className={`pending-run-item is-${statusTone(item.status)}`} key={item.pending_run_id}>
        <div className="pending-run-item__identity"><strong>{item.entity_id}</strong><span>{STATUS_LABELS[item.status] ?? item.status}</span><code>{item.pending_run_id}</code></div>
        <div><span>Start</span><b>{formatIso(item.starts_at)}</b></div>
        <div><span>Stop</span><b>{formatIso(item.ends_at)}</b></div>
        <div><span>Next wake</span><b>{item.timer_active ? formatIso(item.next_wake_at) : "bez aktivního timeru"}</b></div>
        <div><span>Lifecycle</span><b>{item.lifecycle_prepared ? "prepared" : item.status === "no_start_needed" ? "nebyl potřeba" : "zatím ne"}</b></div>
        <div><span>Stop lease</span><b>{item.stop_lease_prepared ? "prepared" : item.status === "no_start_needed" ? "nebyl potřeba" : item.retry_count > 0 ? `retry ${item.retry_count}` : "zatím ne"}</b></div>
        {item.last_error ? <small>{item.last_error}</small> : null}
      </article>)}
    </div>

    <div className="pending-run-footer"><div><span>{status}</span><small>creates_authority={String(runtime?.creates_authority ?? false)} · direct_service_calls={String(runtime?.calls_home_assistant_services_directly ?? false)} · service_call_performed={String(runtime?.service_call_performed ?? false)}</small></div><button className="secondary-action" disabled={refreshing} onClick={() => void refresh()}>{refreshing ? "Obnovuji…" : "Obnovit scheduler"}</button></div>
  </section>;
}
