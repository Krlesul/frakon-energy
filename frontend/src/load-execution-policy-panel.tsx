import React, { useEffect, useMemo, useState } from "react";
import { callHomeAssistantWs, type HomeAssistant } from "./home-assistant";
import "./load-execution-policy-panel.css";

export type PolicyProfile = {
  profile_id: string;
  name: string;
  power_kw: number;
  duration_minutes: number;
  enabled: boolean;
  entity_id?: string | null;
};

type PolicyMode = "disabled" | "approval_required";
type ExecutionPolicy = {
  profile_id: string;
  mode: PolicyMode;
  max_power_kw: number | null;
  max_duration_minutes: number | null;
  require_entity_binding: boolean;
  require_entity_available: boolean;
};
type PoliciesResponse = {
  entry_id: string;
  policies: ExecutionPolicy[];
  modes: PolicyMode[];
  default_mode: "disabled";
  executor_available: boolean;
};
type EvaluationPlan = {
  starts_at: string;
  ends_at: string;
  power_kw: number;
  duration_minutes: number;
  estimated_cost_czk: number;
};
type EvaluationResponse = {
  status: "blocked" | "approval_required";
  profile_id: string;
  entity_id: string | null;
  reasons: string[];
  entity_available: boolean | null;
  execution_performed: boolean;
  executor_available: boolean;
  plan: EvaluationPlan | null;
};
type ApprovalPreviewResponse = {
  eligible: boolean;
  status: "blocked" | "approval_required";
  reasons: string[];
  intent: string;
  schema_version: number;
  snapshot_digest: string | null;
  profile: PolicyProfile | null;
  policy: ExecutionPolicy | null;
  plan: EvaluationPlan | null;
  entity_id: string | null;
  entity_available: boolean | null;
  ttl_seconds: number;
  max_ttl_seconds: number;
  approval_issued: boolean;
  approval_id: null;
  signature: null;
  execution_performed: boolean;
  executor_available: boolean;
  preview_only: boolean;
};
type IssuedApprovalResponse = {
  approval_issued: boolean;
  approval: {
    approval_id: string;
    intent: string;
    snapshot_digest: string;
    issued_at: number;
    expires_at: number;
    signature: string;
  };
  approval_id: string;
  signature: string;
  snapshot_digest: string;
  expected_snapshot_digest: string;
  intent: string;
  schema_version: number;
  issued_at: number;
  expires_at: number;
  ttl_seconds: number;
  entry_id: string;
  profile: PolicyProfile;
  policy: ExecutionPolicy;
  plan: EvaluationPlan;
  entity_id: string | null;
  entity_available: boolean | null;
  execution_performed: boolean;
  executor_available: boolean;
  consumed: boolean;
};
type PolicyForm = {
  mode: PolicyMode;
  max_power_kw: string;
  max_duration_minutes: string;
};

const CONFIRM_PHRASE = "SCHVÁLIT";

const REASON_LABELS: Record<string, string> = {
  policy_disabled: "Policy je vypnutá.",
  policy_profile_mismatch: "Policy patří jinému profilu.",
  plan_profile_mismatch: "Plán nepatří vybranému profilu.",
  profile_disabled: "Profil je vypnutý.",
  entity_binding_required: "Chybí vazba na Home Assistant entitu.",
  entity_unavailable: "Svázaná Home Assistant entita není dostupná.",
  power_limit_exceeded: "Plán překračuje povolený výkon.",
  duration_limit_exceeded: "Plán překračuje povolenou délku běhu.",
  plan_unavailable: "Pro zadané časové okno nevznikl žádný plán.",
};

function toIso(value: string): string | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}

function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("cs-CZ", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function formatEpoch(seconds: number): string {
  const parsed = new Date(seconds * 1000);
  return Number.isNaN(parsed.getTime()) ? String(seconds) : parsed.toLocaleString("cs-CZ", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function LoadExecutionPolicyPanel({ hass, entryId, profiles, earliestStart, deadline }: { hass?: HomeAssistant; entryId: string | null; profiles: PolicyProfile[]; earliestStart: string; deadline: string }) {
  const [policies, setPolicies] = useState<ExecutionPolicy[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [form, setForm] = useState<PolicyForm>({ mode: "disabled", max_power_kw: "", max_duration_minutes: "" });
  const [status, setStatus] = useState("Načítám policy…");
  const [evaluation, setEvaluation] = useState<EvaluationResponse | null>(null);
  const [approvalPreview, setApprovalPreview] = useState<ApprovalPreviewResponse | null>(null);
  const [issuedApproval, setIssuedApproval] = useState<IssuedApprovalResponse | null>(null);
  const [approvalConfirm, setApprovalConfirm] = useState("");
  const [evaluating, setEvaluating] = useState(false);
  const [previewingApproval, setPreviewingApproval] = useState(false);
  const [issuingApproval, setIssuingApproval] = useState(false);
  const [executorAvailable, setExecutorAvailable] = useState(false);

  const selectedProfile = useMemo(() => profiles.find((profile) => profile.profile_id === selectedProfileId) ?? null, [profiles, selectedProfileId]);
  const explicitPolicy = useMemo(() => policies.find((policy) => policy.profile_id === selectedProfileId) ?? null, [policies, selectedProfileId]);
  const policyDirty = useMemo(() => {
    if (!selectedProfile) return false;
    const persistedMode: PolicyMode = explicitPolicy?.mode ?? "disabled";
    if (form.mode !== persistedMode) return true;
    if (form.mode === "disabled") return false;
    return Number(form.max_power_kw) !== explicitPolicy?.max_power_kw || Number(form.max_duration_minutes) !== explicitPolicy?.max_duration_minutes;
  }, [selectedProfile, explicitPolicy, form]);
  const confirmationMatches = approvalConfirm.trim().toLocaleUpperCase("cs-CZ") === CONFIRM_PHRASE;

  const clearCandidateState = () => {
    setEvaluation(null);
    setApprovalPreview(null);
    setIssuedApproval(null);
    setApprovalConfirm("");
  };

  const updateForm = (update: Partial<PolicyForm>) => {
    setForm((current) => ({ ...current, ...update }));
    clearCandidateState();
  };

  useEffect(() => {
    if (profiles.length === 0) {
      setSelectedProfileId("");
      return;
    }
    if (!profiles.some((profile) => profile.profile_id === selectedProfileId)) setSelectedProfileId(profiles[0].profile_id);
  }, [profiles, selectedProfileId]);

  useEffect(() => {
    if (!entryId || !hass) {
      setStatus("Čekám na Home Assistant");
      return;
    }
    callHomeAssistantWs<PoliciesResponse>(hass, { type: "frakon_energy/load_execution_policies/list", entry_id: entryId })
      .then((response) => {
        setPolicies(response.policies);
        setExecutorAvailable(response.executor_available);
        setStatus(response.policies.length > 0 ? "Policy načteny" : "Všechny profily jsou ve výchozím režimu Disabled");
      })
      .catch((error) => setStatus(`Chyba: ${String(error)}`));
  }, [entryId, hass]);

  useEffect(() => {
    if (!selectedProfile) return;
    setForm({
      mode: explicitPolicy?.mode ?? "disabled",
      max_power_kw: String(explicitPolicy?.max_power_kw ?? selectedProfile.power_kw),
      max_duration_minutes: String(explicitPolicy?.max_duration_minutes ?? selectedProfile.duration_minutes),
    });
    clearCandidateState();
  }, [selectedProfile, explicitPolicy]);

  useEffect(() => {
    clearCandidateState();
  }, [earliestStart, deadline]);

  const savePolicy = async () => {
    if (!entryId || !hass || !selectedProfile) return;
    const message: Record<string, unknown> = {
      type: "frakon_energy/load_execution_policies/upsert",
      entry_id: entryId,
      profile_id: selectedProfile.profile_id,
      mode: form.mode,
      require_entity_binding: true,
      require_entity_available: true,
    };
    if (form.mode === "approval_required") {
      const maxPower = Number(form.max_power_kw);
      const maxDuration = Number(form.max_duration_minutes);
      if (!(maxPower > 0) || !(maxDuration > 0) || maxDuration % 15 !== 0) {
        setStatus("Pro režim Vyžadovat schválení musí být výkon kladný a délka násobek 15 minut.");
        return;
      }
      message.max_power_kw = maxPower;
      message.max_duration_minutes = maxDuration;
    }
    setStatus("Ukládám policy…");
    try {
      const response = await callHomeAssistantWs<PoliciesResponse>(hass, message);
      setPolicies(response.policies);
      setExecutorAvailable(response.executor_available);
      setStatus(form.mode === "disabled" ? "Policy uložena jako Disabled" : "Policy uložena · stále vyžaduje samostatné schválení");
      clearCandidateState();
    } catch (error) {
      setStatus(`Chyba: ${String(error)}`);
    }
  };

  const buildTimeWindowMessage = (type: string): Record<string, unknown> | null => {
    if (!entryId || !selectedProfile) return null;
    const message: Record<string, unknown> = {
      type,
      entry_id: entryId,
      profile_id: selectedProfile.profile_id,
    };
    const earliest = toIso(earliestStart);
    const latest = toIso(deadline);
    if (earliest) message.earliest_start = earliest;
    if (latest) message.deadline = latest;
    return message;
  };

  const evaluate = async () => {
    if (!hass) return;
    if (policyDirty) {
      setStatus("Nejdřív ulož změny policy. Vyhodnocení vždy používá uloženou backend policy.");
      return;
    }
    const message = buildTimeWindowMessage("frakon_energy/load_execution/evaluate_profile");
    if (!message) return;
    setEvaluating(true);
    setApprovalPreview(null);
    setIssuedApproval(null);
    setApprovalConfirm("");
    setStatus("Vyhodnocuji policy nad aktuálním spotovým plánem…");
    try {
      const response = await callHomeAssistantWs<EvaluationResponse>(hass, message);
      setEvaluation(response);
      setStatus(response.status === "approval_required" ? "Plán splňuje policy, ale stále vyžaduje explicitní schválení." : "Plán je zablokován policy.");
    } catch (error) {
      setEvaluation(null);
      setStatus(`Chyba: ${String(error)}`);
    } finally {
      setEvaluating(false);
    }
  };

  const previewApprovalScope = async () => {
    if (!hass) return;
    if (policyDirty) {
      setStatus("Nejdřív ulož změny policy. Scope se vždy počítá z uložené backend policy.");
      return;
    }
    const message = buildTimeWindowMessage("frakon_energy/load_execution/approval_preview");
    if (!message) return;
    message.ttl_seconds = 120;
    setPreviewingApproval(true);
    setIssuedApproval(null);
    setApprovalConfirm("");
    setStatus("Počítám přesný rozsah schválení…");
    try {
      const response = await callHomeAssistantWs<ApprovalPreviewResponse>(hass, message);
      setApprovalPreview(response);
      setStatus(response.eligible ? "Scope je způsobilý. Pro vydání krátkodobého artifactu je ještě nutné explicitní potvrzení." : "Rozsah schválení nelze vytvořit, protože policy kandidáta blokuje.");
    } catch (error) {
      setApprovalPreview(null);
      setStatus(`Chyba: ${String(error)}`);
    } finally {
      setPreviewingApproval(false);
    }
  };

  const issueApproval = async () => {
    if (!hass || !approvalPreview?.eligible || !approvalPreview.snapshot_digest) return;
    if (policyDirty) {
      setStatus("Policy má neuložené změny. Vydání approval je zablokováno.");
      return;
    }
    if (!confirmationMatches) {
      setStatus(`Pro vydání napiš přesně ${CONFIRM_PHRASE}.`);
      return;
    }
    const message = buildTimeWindowMessage("frakon_energy/load_execution/approval_issue");
    if (!message) return;
    message.expected_snapshot_digest = approvalPreview.snapshot_digest;
    message.ttl_seconds = approvalPreview.ttl_seconds;
    setIssuingApproval(true);
    setStatus("Server znovu přepočítává scope a vydává krátkodobý approval artifact…");
    try {
      const response = await callHomeAssistantWs<IssuedApprovalResponse>(hass, message);
      if (!response.approval_issued || response.snapshot_digest !== approvalPreview.snapshot_digest) {
        throw new Error("Server vrátil neočekávaný approval scope.");
      }
      setIssuedApproval(response);
      setApprovalConfirm("");
      setStatus("Approval artifact vydán. Nic nebylo spuštěno a executor stále neexistuje.");
    } catch (error) {
      setIssuedApproval(null);
      setApprovalPreview(null);
      setApprovalConfirm("");
      setStatus(`Vydání approval selhalo: ${String(error)}. Scope je nutné načíst znovu.`);
    } finally {
      setIssuingApproval(false);
    }
  };

  if (profiles.length === 0) return <section className="load-policy-panel"><span className="eyebrow">Execution Guard</span><h3>Nejdřív vytvoř profil zátěže</h3><p>Policy se váže na uložený profil. Bez profilu není co vyhodnocovat.</p></section>;

  return <section className="load-policy-panel">
    <div className="load-policy-header"><div><span className="eyebrow">Execution Guard</span><h3>Fail-closed policy před budoucím řízením</h3></div><div className="load-policy-executor"><span>Executor</span><b>{executorAvailable ? "dostupný" : "není implementován"}</b></div></div>
    <p>Žádný režim Automatic neexistuje. Výsledek policy může být pouze <b>Blocked</b> nebo <b>Approval required</b>. Tato obrazovka může administrátorovi vydat krátkodobý approval artifact, ale nemá consume endpoint ani možnost plán spustit.</p>

    <div className="load-policy-grid">
      <label>Profil<select value={selectedProfileId} onChange={(e) => setSelectedProfileId(e.target.value)}>{profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.name}</option>)}</select></label>
      <label>Režim<select value={form.mode} onChange={(e) => updateForm({ mode: e.target.value as PolicyMode })}><option value="disabled">Disabled</option><option value="approval_required">Vyžadovat schválení</option></select></label>
      <label>Max. výkon · kW<input type="number" min="0.001" step="0.1" disabled={form.mode === "disabled"} value={form.max_power_kw} onChange={(e) => updateForm({ max_power_kw: e.target.value })} /></label>
      <label>Max. délka · min<input type="number" min="15" step="15" disabled={form.mode === "disabled"} value={form.max_duration_minutes} onChange={(e) => updateForm({ max_duration_minutes: e.target.value })} /></label>
    </div>

    {policyDirty ? <div className="load-policy-dirty"><b>Neuložené změny policy</b><span>Dokud policy neuložíš, vyhodnocení, scope preview i vydání approval zůstávají zablokované.</span></div> : null}
    {selectedProfile ? <div className="load-policy-binding"><span>Vazba</span><b>{selectedProfile.entity_id ?? "žádná HA entita"}</b><small>Policy v UI vždy vyžaduje existující i dostupnou entitu.</small></div> : null}
    <div className="load-policy-actions"><button className="primary-action" onClick={savePolicy} disabled={!entryId || !hass}>Uložit policy</button><button className="secondary-action" onClick={evaluate} disabled={!entryId || !hass || evaluating || policyDirty}>{evaluating ? "Vyhodnocuji…" : "Vyhodnotit aktuální plán"}</button><button className="secondary-action" onClick={previewApprovalScope} disabled={!entryId || !hass || previewingApproval || policyDirty}>{previewingApproval ? "Počítám scope…" : "Zobrazit rozsah schválení"}</button><span>{status}</span></div>

    {evaluation ? <div className={`load-policy-result load-policy-result--${evaluation.status}`}><div><span>Verdikt</span><strong>{evaluation.status === "approval_required" ? "Approval required" : "Blocked"}</strong></div>{evaluation.plan ? <div className="load-policy-result__plan"><span>{formatTime(evaluation.plan.starts_at)} → {formatTime(evaluation.plan.ends_at)}</span><b>{evaluation.plan.power_kw.toLocaleString("cs-CZ")} kW · {evaluation.plan.duration_minutes} min · ~{evaluation.plan.estimated_cost_czk.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} Kč</b></div> : null}<div className="load-policy-reasons">{evaluation.reasons.length === 0 ? <span>Všechny podmínky policy jsou splněné. Kandidát může projít samostatným issuance flow, ale žádná exekuce se neprovádí.</span> : evaluation.reasons.map((reason) => <span key={reason}>{REASON_LABELS[reason] ?? reason}</span>)}</div><small>execution_performed={String(evaluation.execution_performed)} · executor_available={String(evaluation.executor_available)} · entity_available={String(evaluation.entity_available)}</small></div> : null}

    {approvalPreview ? <div className={`load-approval-scope ${approvalPreview.eligible ? "load-approval-scope--eligible" : "load-approval-scope--blocked"}`}><div className="load-approval-scope__header"><div><span className="eyebrow">Approval scope · preview</span><h4>{approvalPreview.eligible ? "Přesný snapshot schválení" : "Kandidát není způsobilý ke schválení"}</h4></div><span className="load-approval-scope__badge">{approvalPreview.eligible ? "scope ready" : "blocked"}</span></div>{approvalPreview.plan ? <div className="load-approval-scope__plan"><span>{formatTime(approvalPreview.plan.starts_at)} → {formatTime(approvalPreview.plan.ends_at)}</span><b>{approvalPreview.entity_id ?? "bez entity"} · {approvalPreview.plan.power_kw.toLocaleString("cs-CZ")} kW · {approvalPreview.plan.duration_minutes} min</b></div> : null}{approvalPreview.snapshot_digest ? <div className="load-approval-scope__digest"><span>SHA-256 snapshot digest</span><code>{approvalPreview.snapshot_digest}</code></div> : <div className="load-policy-reasons">{approvalPreview.reasons.map((reason) => <span key={reason}>{REASON_LABELS[reason] ?? reason}</span>)}</div>}<div className="load-approval-scope__meta"><div><span>Intent</span><b>{approvalPreview.intent}</b></div><div><span>Schema</span><b>v{approvalPreview.schema_version}</b></div><div><span>TTL preview</span><b>{approvalPreview.ttl_seconds} s / max {approvalPreview.max_ttl_seconds} s</b></div><div><span>Artifact</span><b>nevydán</b></div></div><p>Tento digest je pouze náhled scope. Vydání artifactu vždy znovu přepočítá aktuální plán, policy i entitu a odmítne změněný digest.</p><small>approval_issued={String(approvalPreview.approval_issued)} · approval_id={String(approvalPreview.approval_id)} · signature={String(approvalPreview.signature)} · execution_performed={String(approvalPreview.execution_performed)} · executor_available={String(approvalPreview.executor_available)} · preview_only={String(approvalPreview.preview_only)}</small>{approvalPreview.eligible && approvalPreview.snapshot_digest ? <div className="load-approval-issue"><div><b>Explicitní vydání approval artifactu</b><p>Backend tuto mutaci povolí pouze administrátorovi Home Assistantu. Artifact je krátkodobý bearer token pro přesný digest výše; jeho vydání samo o sobě nic nezapne ani nespustí.</p></div><label>Pro potvrzení napiš <strong>{CONFIRM_PHRASE}</strong><input value={approvalConfirm} autoComplete="off" spellCheck={false} onChange={(e) => setApprovalConfirm(e.target.value)} placeholder={CONFIRM_PHRASE} /></label><button className="load-approval-issue__button" onClick={issueApproval} disabled={!confirmationMatches || issuingApproval || issuedApproval !== null || policyDirty}>{issuingApproval ? "Vydávám artifact…" : "Vydat krátkodobý approval artifact"}</button></div> : null}</div> : null}

    {issuedApproval ? <div className="load-issued-approval"><div className="load-issued-approval__header"><div><span className="eyebrow">Approval artifact · vydán</span><h4>Krátkodobé schválení scope</h4></div><span>bez exekuce</span></div><div className="load-issued-approval__grid"><div><span>Approval ID</span><code>{issuedApproval.approval_id}</code></div><div><span>Platnost</span><b>{formatEpoch(issuedApproval.issued_at)} → {formatEpoch(issuedApproval.expires_at)}</b></div><div><span>TTL</span><b>{issuedApproval.ttl_seconds} s</b></div><div><span>HMAC podpis</span><b>vydán · skrytý v UI</b></div></div><div className="load-approval-scope__digest"><span>Svázaný snapshot digest</span><code>{issuedApproval.snapshot_digest}</code></div><p>Artifact je pouze schválení přesného scope. V systému stále neexistuje consume WebSocket endpoint ani executor, takže tímto krokem nelze zařízení fyzicky spustit.</p><small>approval_issued={String(issuedApproval.approval_issued)} · consumed={String(issuedApproval.consumed)} · execution_performed={String(issuedApproval.execution_performed)} · executor_available={String(issuedApproval.executor_available)}</small></div> : null}
  </section>;
}
