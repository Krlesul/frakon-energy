import React, { useCallback, useEffect, useMemo, useState } from "react";
import { callHomeAssistantWs, type HomeAssistant } from "./home-assistant";
import "./load-execution-field-acceptance-panel.css";

type SafetyItem = {
  attempt_id: string;
  lifecycle_id: string;
  lifecycle_state: string;
  entity_id: string;
  current_state: string | null;
};

type SafetyResponse = {
  execution_armed: boolean;
  execution_arm: { storage_healthy: boolean };
  items: SafetyItem[];
};

type CommissioningPreflight = {
  attempt_id: string;
  status: "ready_for_arm" | "blocked" | "no_start_needed" | "already_armed";
  reasons: string[];
  commissioning_target: {
    class: "home_assistant_helper" | "physical_capable_target";
    direct_hardware_service: boolean;
    home_assistant_helper: boolean;
    indirect_automation_side_effects_assessed: false;
    recommended_first_field_test_target: boolean;
    requires_downstream_automation_review: boolean;
  };
  immutable_start_action: {
    service_domain: string;
    service_name: string;
    entity_id: string;
    service_data: Record<string, never>;
  };
  immutable_stop_action: {
    service_domain: string;
    service_name: string;
    entity_id: string;
    service_data: Record<string, never>;
    ends_at: string;
  } | null;
  durable_stop_lease_present: boolean;
  durable_stop_lease_matches: boolean;
  dry_run: true;
  service_call_performed: false;
  execution_performed: false;
};

function formatTime(value: string | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString("cs-CZ", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
}

export function LoadExecutionFieldAcceptancePanel({ hass, entryId }: { hass?: HomeAssistant; entryId: string | null }) {
  const [safety, setSafety] = useState<SafetyResponse | null>(null);
  const [result, setResult] = useState<CommissioningPreflight | null>(null);
  const [status, setStatus] = useState("Načítám commissioning kandidáty…");
  const [busyAttemptId, setBusyAttemptId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!hass || !entryId) {
      setSafety(null);
      setResult(null);
      setStatus("Čekám na Home Assistant");
      return;
    }
    try {
      const response = await callHomeAssistantWs<SafetyResponse>(hass, {
        type: "frakon_energy/load_execution/safety_status",
        entry_id: entryId,
      });
      setSafety(response);
      if (response.execution_armed) {
        setResult(null);
        setStatus("Field acceptance preflight je určený pro DISARMED stav.");
      } else if (!response.execution_arm.storage_healthy) {
        setResult(null);
        setStatus("ARM storage není důvěryhodný; commissioning zůstává fail-closed.");
      } else {
        setStatus("Vyber připravený input_boolean helper pro první field test.");
      }
    } catch (error) {
      setSafety(null);
      setResult(null);
      setStatus(`Field acceptance status nelze načíst: ${String(error)}`);
    }
  }, [entryId, hass]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const helperCandidates = useMemo(
    () => (safety?.items ?? []).filter(
      (item) => item.lifecycle_state === "prepared" && item.entity_id.startsWith("input_boolean."),
    ),
    [safety],
  );

  const runPreflight = async (item: SafetyItem) => {
    if (!hass || !entryId) return;
    setBusyAttemptId(item.attempt_id);
    setResult(null);
    setStatus(`Ověřuji software-helper target ${item.entity_id}…`);
    try {
      const response = await callHomeAssistantWs<CommissioningPreflight>(hass, {
        type: "frakon_energy/load_execution/commissioning_preflight",
        entry_id: entryId,
        attempt_id: item.attempt_id,
      });
      setResult(response);
      if (response.commissioning_target.class !== "home_assistant_helper") {
        setStatus("Backend target neklasifikoval jako HA helper; tento kandidát není vhodný jako první software test.");
      } else if (response.status === "ready_for_arm") {
        setStatus("Software-helper preflight prošel. Před ARM ještě ověř downstream automatizace tohoto helperu.");
      } else {
        setStatus(`Preflight není připravený k ARM: ${response.status}.`);
      }
    } catch (error) {
      setResult(null);
      setStatus(`Field acceptance preflight selhal: ${String(error)}`);
    } finally {
      setBusyAttemptId(null);
    }
  };

  return <section className="field-acceptance-panel">
    <div className="field-acceptance-header">
      <div>
        <span className="eyebrow">Field Acceptance</span>
        <h3>První reálný test přes Home Assistant helper</h3>
        <p>Nejbezpečnější první průchod produkční execution pipeline je profil svázaný s novým <code>input_boolean</code>. FRAKON stále používá stejné approval, stop lease, ARM, start a stop brány; mění se pouze přímý HA target.</p>
      </div>
      <span className="field-acceptance-badge">software helper</span>
    </div>

    <div className="field-acceptance-warning">
      <strong>Důležitá hranice</strong>
      <span><code>input_boolean.turn_on/off</code> samo nevolá device service, ale helper může být triggerem jiné automatizace, která hardware ovládá. Tento preflight downstream automatizace neanalyzuje — před ARM použij nový helper bez navázaných automací.</span>
    </div>

    <div className="field-acceptance-candidates">
      {helperCandidates.length === 0 ? <div className="field-acceptance-empty">
        <strong>Zatím není připravený helper kandidát</strong>
        <span>V Home Assistantu vytvoř nový pomocník Přepínač, například <code>input_boolean.frakon_execution_test</code>, nepoužívej ho v žádné jiné automatizaci a svaž s ním testovací profil FRAKON. Potom projdi normální approval → naplánování; zde se objeví až durable <code>prepared</code> lifecycle.</span>
      </div> : helperCandidates.map((item) => <div className="field-acceptance-candidate" key={item.lifecycle_id}>
        <div><strong>{item.entity_id}</strong><span>{item.lifecycle_state} · live {item.current_state ?? "—"}</span><code>{item.attempt_id}</code></div>
        <button className="secondary-action" disabled={busyAttemptId !== null || safety?.execution_armed === true || safety?.execution_arm.storage_healthy !== true} onClick={() => void runPreflight(item)}>{busyAttemptId === item.attempt_id ? "Kontroluji…" : "Ověřit field test"}</button>
      </div>)}
    </div>

    {result ? <div className={`field-acceptance-result is-${result.status}`}>
      <div className="field-acceptance-result__head"><div><span>Target class</span><strong>{result.commissioning_target.class === "home_assistant_helper" ? "Home Assistant helper" : "Fyzicky schopný target"}</strong></div><b>{result.status}</b></div>
      <div className="field-acceptance-grid">
        <div><span>Přímý hardware service</span><b>{result.commissioning_target.direct_hardware_service ? "ANO" : "ne"}</b></div>
        <div><span>Downstream automation audit</span><b>{result.commissioning_target.indirect_automation_side_effects_assessed ? "ověřen" : "NEPROVEDEN"}</b></div>
        <div><span>Stop lease</span><b>{result.durable_stop_lease_present && result.durable_stop_lease_matches ? "exact match" : "BLOCKED"}</b></div>
        <div><span>Doporučený první target</span><b>{result.commissioning_target.recommended_first_field_test_target ? "ano" : "ne"}</b></div>
      </div>
      <div className="field-acceptance-actions">
        <div><span>Start</span><code>{result.immutable_start_action.service_domain}.{result.immutable_start_action.service_name}</code><b>{result.immutable_start_action.entity_id}</b></div>
        <div><span>Stop</span>{result.immutable_stop_action ? <><code>{result.immutable_stop_action.service_domain}.{result.immutable_stop_action.service_name}</code><b>{result.immutable_stop_action.entity_id}</b><small>{formatTime(result.immutable_stop_action.ends_at)}</small></> : <b>není dostupný</b>}</div>
      </div>
      {result.reasons.length > 0 ? <small>{result.reasons.join(" · ")}</small> : null}
      <p>{result.status === "ready_for_arm" && result.commissioning_target.home_assistant_helper
        ? "Backend potvrzuje přesný helper target a všechny současné suché execution brány. ARM proveď až po kontrole, že helper není triggerem žádné jiné automatizace; po ARM se všechny autoritativní brány znovu vyhodnotí."
        : "Tento výsledek není oprávnění k fyzickému startu. Odstraň blokující důvod a spusť preflight znovu."}</p>
      <small>dry_run={String(result.dry_run)} · service_call_performed={String(result.service_call_performed)} · execution_performed={String(result.execution_performed)}</small>
    </div> : null}

    <div className="field-acceptance-footer"><span>{status}</span><button className="secondary-action" onClick={() => void refresh()}>Obnovit kandidáty</button></div>
  </section>;
}
