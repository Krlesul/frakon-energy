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

type CommissioningAction = {
  service_domain: string;
  service_name: string;
  entity_id: string;
  service_data: Record<string, never>;
  ends_at?: string;
};

type CommissioningPreflightResponse = {
  entry_id: string;
  attempt_id: string;
  status: "ready_for_arm" | "blocked" | "no_start_needed" | "already_armed";
  reasons: string[];
  commissioning_window_safe: boolean;
  can_arm_to_execute: boolean;
  arm_is_only_remaining_interlock: boolean;
  execution_arm: ExecutionArmStatus;
  runtime: {
    start_recovery_ready: boolean;
    stop_recovery_ready: boolean;
    start_scheduler_ready: boolean;
    stop_scheduler_ready: boolean;
  };
  bounded_dispatch_gate: {
    status: string;
    reason: string;
    stop_lease_matches?: boolean;
    dispatch_gate_matches?: boolean;
  };
  immutable_start_action: CommissioningAction;
  immutable_stop_action: CommissioningAction | null;
  durable_stop_lease_present: boolean;
  durable_stop_lease_matches: boolean;
  client_supplied_action_fields: false;
  preflight_snapshot_reserves_execution: false;
  gates_rechecked_after_arm: true;
  dry_run: true;
  read_only: true;
  state_transition_performed: false;
  service_call_performed: false;
  execution_performed: false;
};

type PreflightView = {
  response: CommissioningPreflightResponse;
  checkedAt: number;
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

function formatIso(value: string | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
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

function preflightLabel(status: CommissioningPreflightResponse["status"]): string {
  if (status === "ready_for_arm") return "READY FOR ARM";
  if (status === "no_start_needed") return "START NENÍ POTŘEBA";
  if (status === "already_armed") return "UŽ ARMED";
  return "BLOCKED";
}

function preflightStatusMessage(response: CommissioningPreflightResponse): string {
  if (response.status === "ready_for_arm") {
    return "Suchý preflight je zelený. ARM je nyní jediný commissioning interlock; skutečný dispatcher po ARM všechny brány znovu ověří.";
  }
  if (response.status === "no_start_needed") {
    return "Preflight zjistil, že požadovaný stav už je splněný. Fyzický start není potřeba.";
  }
  if (response.status === "already_armed") {
    return "Preflight pro commissioning vyžaduje DISARMED stav. Nejdřív zablokuj nové fyzické starty.";
  }
  return `Suchý preflight je blokovaný${response.reasons.length > 0 ? `: ${response.reasons.join(", ")}` : "."}`;
}

export function LoadExecutionRuntimePanel({ hass, entryId }: { hass?: HomeAssistant; entryId: string | null }) {
  const [safety, setSafety] = useState<ExecutionSafetyStatus | null>(null);
  const [status, setStatus] = useState("Načítám execution runtime…");
  const [busy, setBusy] = useState<"refresh" | "arm" | "disarm" | null>(null);
  const [preflightBusyAttemptId, setPreflightBusyAttemptId] = useState<string | null>(null);
  const [preflightView, setPreflightView] = useState<PreflightView | null>(null);
  const [armConfirmation, setArmConfirmation] = useState("");

  const refresh = useCallback(async (quiet = false) => {
    if (!hass || !entryId) {
      setSafety(null);
      setPreflightView(null);
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
      if (response.execution_armed) setPreflightView(null);
      setStatus(response.execution_arm.storage_healthy
        ? (response.execution_armed ? "Execution runtime je ARMED." : "Execution runtime je bezpečně DISARMED.")
        : "Execution ARM storage není důvěryhodný. Nové starty jsou fail-closed zablokované.");
    } catch (error) {
      setSafety(null);
      setPreflightView(null);
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
      setPreflightView(null);
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

  const runPreflight = async (item: SafetyItem) => {
    if (!hass || !entryId) return;
    if (safety?.execution_armed) {
      setStatus("Suchý commissioning preflight lze spustit pouze při DISARMED execution runtime.");
      return;
    }
    setPreflightBusyAttemptId(item.attempt_id);
    setStatus(`Provádím read-only preflight pro ${item.entity_id}…`);
    try {
      const response = await callHomeAssistantWs<CommissioningPreflightResponse>(hass, {
        type: "frakon_energy/load_execution/commissioning_preflight",
        entry_id: entryId,
        attempt_id: item.attempt_id,
      });
      setPreflightView({ response, checkedAt: Date.now() });
      setStatus(preflightStatusMessage(response));
    } catch (error) {
      setPreflightView(null);
      setStatus(`Commissioning preflight selhal: ${String(error)}`);
    } finally {
      setPreflightBusyAttemptId(null);
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
      <div className="execution-runtime-audit__header"><div><span className="eyebrow">Durable audit</span><h4>Poslední execution lifecycle</h4></div><button className="secondary-action" disabled={busy !== null || preflightBusyAttemptId !== null} onClick={() => void refresh()}>{busy === "refresh" ? "Obnovuji…" : "Obnovit stav"}</button></div>
      {activeItems.length === 0 ? <div className="execution-runtime-empty">Žádný aktivní nebo ověřený execution lifecycle.</div> : <div className="execution-runtime-list">{activeItems.map((item) => {
        const matchingPreflight = preflightView?.response.attempt_id === item.attempt_id ? preflightView : null;
        const canPreflight = item.lifecycle_state === "prepared" && !armed && armHealthy;
        return <div className={`execution-runtime-item ${item.safety_status === "unsafe" ? "is-unsafe" : ""}`} key={item.lifecycle_id}>
          <div><strong>{item.entity_id}</strong><span>{item.lifecycle_state} · live {item.current_state ?? "—"}</span></div>
          <div><span>Stop ownership</span><b>{item.stop_ownership_required ? (item.stop_ownership_ready ? "ready" : "CHYBÍ") : "není vyžadován"}</b></div>
          <div><span>Stop lifecycle</span><b>{item.stop_lifecycle_state ?? "—"}</b></div>
          <div><span>Call evidence</span><b>{evidenceLabel(item.service_call_performed)}</b></div>
          {item.lifecycle_state === "prepared" ? <div className="execution-runtime-item__actions">
            <button className="execution-preflight-button" disabled={!canPreflight || busy !== null || preflightBusyAttemptId !== null} onClick={() => void runPreflight(item)}>{preflightBusyAttemptId === item.attempt_id ? "Kontroluji…" : "Suchý commissioning preflight"}</button>
            <span>{armed ? "Nejdřív DISARM." : armHealthy ? "Bez service callu · přesný bounded gate + stop lease." : "ARM storage není důvěryhodný."}</span>
          </div> : null}
          {matchingPreflight ? <div className={`execution-preflight-card is-${matchingPreflight.response.status}`}>
            <div className="execution-preflight-card__header">
              <div><span className="eyebrow">Commissioning preflight</span><strong>{matchingPreflight.response.status === "ready_for_arm" ? "Všechny suché kontroly prošly" : matchingPreflight.response.status === "no_start_needed" ? "Start není potřeba" : matchingPreflight.response.status === "already_armed" ? "Runtime už je ARMED" : "Preflight je blokovaný"}</strong></div>
              <span className="execution-preflight-badge">{preflightLabel(matchingPreflight.response.status)}</span>
            </div>
            <div className="execution-preflight-grid">
              <div><span>Start recovery</span><b>{matchingPreflight.response.runtime.start_recovery_ready ? "ready" : "BLOCKED"}</b></div>
              <div><span>Stop recovery</span><b>{matchingPreflight.response.runtime.stop_recovery_ready ? "ready" : "BLOCKED"}</b></div>
              <div><span>Start scheduler</span><b>{matchingPreflight.response.runtime.start_scheduler_ready ? "ready" : "BLOCKED"}</b></div>
              <div><span>Stop scheduler</span><b>{matchingPreflight.response.runtime.stop_scheduler_ready ? "ready" : "BLOCKED"}</b></div>
              <div><span>Bounded gate</span><b>{matchingPreflight.response.bounded_dispatch_gate.status}</b></div>
              <div><span>Stop lease</span><b>{matchingPreflight.response.durable_stop_lease_present && matchingPreflight.response.durable_stop_lease_matches ? "exact match" : "BLOCKED"}</b></div>
            </div>
            <div className="execution-preflight-actions">
              <div><span>Immutable start</span><code>{matchingPreflight.response.immutable_start_action.service_domain}.{matchingPreflight.response.immutable_start_action.service_name}</code><b>{matchingPreflight.response.immutable_start_action.entity_id}</b><small>service_data = {"{}"}</small></div>
              <div><span>Durable stop</span>{matchingPreflight.response.immutable_stop_action ? <><code>{matchingPreflight.response.immutable_stop_action.service_domain}.{matchingPreflight.response.immutable_stop_action.service_name}</code><b>{matchingPreflight.response.immutable_stop_action.entity_id}</b><small>ends_at · {formatIso(matchingPreflight.response.immutable_stop_action.ends_at)}</small></> : <b>není potřeba / není dostupný</b>}</div>
            </div>
            {matchingPreflight.response.reasons.length > 0 ? <div className="execution-preflight-reasons">{matchingPreflight.response.reasons.map((reason) => <span key={reason}>{reason}</span>)}</div> : null}
            <p>{preflightStatusMessage(matchingPreflight.response)}</p>
            <small>Kontrola: {new Date(matchingPreflight.checkedAt).toLocaleString("cs-CZ")} · dry_run={String(matchingPreflight.response.dry_run)} · service_call_performed={String(matchingPreflight.response.service_call_performed)} · execution_performed={String(matchingPreflight.response.execution_performed)}. Snapshot nic nerezervuje a po ARM se všechny autoritativní brány znovu vyhodnotí.</small>
          </div> : null}
          {item.unsafe_reason ? <small>{item.unsafe_reason}</small> : null}
        </div>;
      })}</div>}
      <small>ARM změněn: {formatEpoch(safety.execution_arm.changed_at)}{safety.execution_arm.changed_by ? ` · user ${safety.execution_arm.changed_by}` : ""}. Read-only safety status ani commissioning preflight nikdy neprovádí service call.</small>
    </div> : null}

    <div className="execution-runtime-footer"><span>{status}</span><small>DISARM není emergency stop. Bezpečnostní turn_off pro už vlastněnou stop povinnost zůstává vždy dostupný.</small></div>
  </section>;
}
