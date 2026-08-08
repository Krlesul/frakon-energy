import React, { useCallback, useEffect, useMemo, useState } from "react";
import { callHomeAssistantWs, type HomeAssistant } from "./home-assistant";
import "./load-execution-runtime-panel.css";

type ExecutionArmStatus = {
  entry_id: string;
  armed: boolean;
  storage_healthy: boolean;
  last_error: string | null;
  revision: number | null;
  changed_at: number | null;
  changed_by: string | null;
  required_arm_confirmation: string;
  fail_closed: boolean;
};

type SchedulerStatus = {
  started: boolean;
  healthy: boolean;
  last_error: string | null;
  statuses: Array<Record<string, unknown>>;
};

type SafetyItem = {
  attempt_id: string;
  lifecycle_id: string;
  lifecycle_state: string;
  entity_id: string;
  current_state: string | null;
  service_call_performed: boolean | null;
  stop_ownership_required: boolean;
  stop_ownership_ready: boolean;
  stop_ownership_reason: string;
  stop_lifecycle_state: string | null;
  stop_scheduler_status: string | null;
  safety_status: "safe" | "unsafe";
  unsafe_reason: string | null;
};

type ExecutionSafetyStatus = {
  entry_id: string;
  execution_arm: ExecutionArmStatus;
  start_recovery: { status: string };
  stop_recovery: { status: string };
  start_scheduler: SchedulerStatus;
  stop_scheduler: SchedulerStatus;
  start_runtime_ready: boolean;
  stop_runtime_ready: boolean;
  autonomous_start_runtime_ready: boolean;
  execution_armed: boolean;
  explicit_start_executor_available: boolean;
  explicit_stop_executor_available: boolean;
  autonomous_stop_enabled: boolean;
  autonomous_start_enabled: boolean;
  unsafe_start_lifecycles: string[];
  items: SafetyItem[];
  read_only: boolean;
  state_transition_performed: boolean;
  service_call_performed: boolean;
  execution_performed: boolean;
};

type ArmMutationResponse = {
  execution_arm: ExecutionArmStatus;
  changed: boolean;
  state_transition_performed: boolean;
  service_call_performed: false;
  execution_performed: false;
  status: ExecutionArmStatus;
  new_physical_starts_allowed: boolean;
  stop_obligations_remain_enabled: true;
};

const ARM_CONFIRMATION = "ARM";

function formatEpoch(seconds: number | null): string {
  if (!seconds) return "nikdy";
  const date = new Date(seconds * 1000);
  return Number.isNaN(date.getTime())
    ? String(seconds)
    : date.toLocaleString("cs-CZ", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
}

function runtimeLabel(started: boolean, healthy: boolean): string {
  if (!started) return "zastavený";
  return healthy ? "zdravý" : "porucha";
}

function evidenceLabel(value: boolean | null): string {
  if (value === true) return "potvrzeno";
  if (value === false) return "neprovedeno";
  return "neznámý výsledek";
}

export function LoadExecutionRuntimePanel({ hass, entryId }: { hass?: HomeAssistant; entryId: string | null }) {
  const [safety, setSafety] = useState<ExecutionSafetyStatus | null>(null);
  const [status, setStatus] = useState("Načítám execution runtime…");
  const [busy, setBusy] = useState<"refresh" | "arm" | "disarm" | null>(null);
  const [armConfirmation, setArmConfirmation] = useState("");

  const refresh = useCallback(async (quiet = false) => {
    if (!hass || !entryId) {
      setSafety(null);
      setStatus("Čekám na Home Assistant");
      return;
    }
    if (!quiet) setBusy("refresh");
    try {
      const response = await callHomeAssistantWs<ExecutionSafetyStatus>(hass, {
        type: "frakon_energy/load_execution/safety_status",
        entry_id: entryId,
      });
      setSafety(response);
      setStatus(response.execution_arm.storage_healthy
        ? (response.execution_armed ? "Execution runtime je ARMED." : "Execution runtime je bezpečně DISARMED.")
        : "Execution ARM storage není důvěryhodný. Nové starty jsou fail-closed zablokované.");
    } catch (error) {
      setSafety(null);
      setStatus(`Execution status nelze načíst: ${String(error)}. Ovládání je dostupné pouze administrátorovi Home Assistantu.`);
    } finally {
      if (!quiet) setBusy(null);
    }
  }, [entryId, hass]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(true), 15_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const mutateArm = async (armed: boolean) => {
    if (!hass || !entryId) return;
    if (armed && armConfirmation !== ARM_CONFIRMATION) {
      setStatus(`Pro ARM napiš přesně ${ARM_CONFIRMATION}.`);
      return;
    }
    setBusy(armed ? "arm" : "disarm");
    setStatus(armed ? "Aktivuji fyzické starty…" : "Blokuji všechny nové fyzické starty…");
    try {
      const response = await callHomeAssistantWs<ArmMutationResponse>(hass, {
        type: armed ? "frakon_energy/load_execution/arm" : "frakon_energy/load_execution/disarm",
        entry_id: entryId,
        ...(armed ? { confirmation: ARM_CONFIRMATION } : {}),
      });
      setArmConfirmation("");
      setStatus(armed
        ? "ARM potvrzen. Již připravená a stále platná práce může nyní projít všemi bezpečnostními branami."
        : "DISARM potvrzen. Nové turn_on jsou zablokované; existující stop povinnosti zůstávají aktivní.");
      if (!response.stop_obligations_remain_enabled) {
        setStatus("Backend nevrátil potvrzení zachování stop povinností. Stav znovu ověřuji fail-closed.");
      }
      await refresh(true);
    } catch (error) {
      setStatus(`${armed ? "ARM" : "DISARM"} selhal: ${String(error)}`);
      await refresh(true);
    } finally {
      setBusy(null);
    }
  };

  const unsafeCount = safety?.unsafe_start_lifecycles.length ?? 0;
  const activeItems = useMemo(
    () => (safety?.items ?? []).filter((item) => !["cancelled", "failed"].includes(item.lifecycle_state)).slice(-8).reverse(),
    [safety],
  );
  const armHealthy = safety?.execution_arm.storage_healthy ?? false;
  const armed = safety?.execution_armed ?? false;
  const confirmationMatches = armConfirmation === ARM_CONFIRMATION;

  return <section className={`execution-runtime-panel ${armed ? "is-armed" : "is-disarmed"} ${unsafeCount > 0 ? "has-unsafe" : ""}`}>
    <div className="execution-runtime-header">
      <div>
        <span className="eyebrow">Execution Runtime</span>
        <h3>Commissioning interlock a bezpečnost běhu</h3>
        <p>ARM pouze povolí novým startům pokračovat přes existující policy, approval, lifecycle a stop-ownership brány. Nikdy sám zařízení nezapne bez platné připravené práce.</p>
      </div>
      <div className={`execution-arm-badge ${armed ? "is-armed" : "is-disarmed"} ${!armHealthy ? "is-error" : ""}`}>
        <span>Physical start</span>
        <strong>{!armHealthy ? "FAIL-CLOSED" : armed ? "ARMED" : "DISARMED"}</strong>
      </div>
    </div>

    {safety ? <div className="execution-runtime-metrics">
      <div><span>Autonomní start</span><b className={safety.autonomous_start_enabled ? "ok" : "blocked"}>{safety.autonomous_start_enabled ? "povolen" : "blokován"}</b></div>
      <div><span>Autonomní stop</span><b className={safety.autonomous_stop_enabled ? "ok" : "danger"}>{safety.autonomous_stop_enabled ? "aktivní" : "nedostupný"}</b></div>
      <div><span>Start scheduler</span><b className={safety.start_scheduler.healthy ? "ok" : "danger"}>{runtimeLabel(safety.start_scheduler.started, safety.start_scheduler.healthy)}</b></div>
      <div><span>Stop scheduler</span><b className={safety.stop_scheduler.healthy ? "ok" : "danger"}>{runtimeLabel(safety.stop_scheduler.started, safety.stop_scheduler.healthy)}</b></div>
      <div><span>Start recovery</span><b>{safety.start_recovery.status}</b></div>
      <div><span>Stop recovery</span><b>{safety.stop_recovery.status}</b></div>
      <div><span>Unsafe lifecycle</span><b className={unsafeCount === 0 ? "ok" : "danger"}>{unsafeCount}</b></div>
      <div><span>ARM revize</span><b>{safety.execution_arm.revision ?? "—"}</b></div>
    </div> : null}

    {!armHealthy && safety ? <div className="execution-runtime-alert execution-runtime-alert--danger">
      <strong>ARM storage není důvěryhodný</strong>
      <span>{safety.execution_arm.last_error ?? "Neznámá chyba storage."} Nové fyzické starty zůstávají blokované. Bezpečnostní stop zůstává nezávislá.</span>
    </div> : null}

    {unsafeCount > 0 ? <div className="execution-runtime-alert execution-runtime-alert--danger">
      <strong>Nalezen start bez prokázaného bounded stop ownership</strong>
      <span>{unsafeCount} lifecycle vyžaduje kontrolu. Autonomní redispatch neproběhne.</span>
    </div> : null}

    <div className="execution-arm-control">
      <div className="execution-arm-control__copy">
        <strong>{armed ? "Fyzické starty jsou povolené" : "Fyzické starty jsou zablokované"}</strong>
        <p>{armed
          ? "DISARM zablokuje všechny další turn_on. Již běžící bounded zátěž nevypne okamžitě; její durable stop povinnost se provede v uloženém ends_at."
          : "Pro commissioning nebo testování nech DISARMED. ARM může okamžitě uvolnit již schválenou a stále platnou připravenou práci, pokud projde všemi ostatními branami."}</p>
      </div>
      {armed ? <button className="execution-disarm-button" disabled={busy !== null} onClick={() => void mutateArm(false)}>{busy === "disarm" ? "DISARM…" : "DISARM · blokovat nové starty"}</button> : <div className="execution-arm-confirm">
        <label>Pro aktivaci napiš přesně <strong>{ARM_CONFIRMATION}</strong><input value={armConfirmation} disabled={busy !== null || !armHealthy} autoComplete="off" spellCheck={false} onChange={(event) => setArmConfirmation(event.target.value)} placeholder={ARM_CONFIRMATION} /></label>
        <button className="execution-arm-button" disabled={!confirmationMatches || busy !== null || !armHealthy} onClick={() => void mutateArm(true)}>{busy === "arm" ? "ARM…" : "ARM · povolit fyzické starty"}</button>
      </div>}
    </div>

    {safety ? <div className="execution-runtime-audit">
      <div className="execution-runtime-audit__header"><div><span className="eyebrow">Durable audit</span><h4>Poslední execution lifecycle</h4></div><button className="secondary-action" disabled={busy !== null} onClick={() => void refresh()}>{busy === "refresh" ? "Obnovuji…" : "Obnovit stav"}</button></div>
      {activeItems.length === 0 ? <div className="execution-runtime-empty">Žádný aktivní nebo ověřený execution lifecycle.</div> : <div className="execution-runtime-list">{activeItems.map((item) => <div className={`execution-runtime-item ${item.safety_status === "unsafe" ? "is-unsafe" : ""}`} key={item.lifecycle_id}>
        <div><strong>{item.entity_id}</strong><span>{item.lifecycle_state} · live {item.current_state ?? "—"}</span></div>
        <div><span>Stop ownership</span><b>{item.stop_ownership_required ? (item.stop_ownership_ready ? "ready" : "CHYBÍ") : "není vyžadován"}</b></div>
        <div><span>Stop lifecycle</span><b>{item.stop_lifecycle_state ?? "—"}</b></div>
        <div><span>Call evidence</span><b>{evidenceLabel(item.service_call_performed)}</b></div>
        {item.unsafe_reason ? <small>{item.unsafe_reason}</small> : null}
      </div>)}</div>}
      <small>ARM změněn: {formatEpoch(safety.execution_arm.changed_at)}{safety.execution_arm.changed_by ? ` · user ${safety.execution_arm.changed_by}` : ""}. Read-only safety status nikdy neprovádí service call.</small>
    </div> : null}

    <div className="execution-runtime-footer"><span>{status}</span><small>DISARM není emergency stop. Bezpečnostní turn_off pro už vlastněnou stop povinnost zůstává vždy dostupný.</small></div>
  </section>;
}
