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
  can_execute?: boolean;
};
type RuntimeApprovalRecord = {
  entry_id: string;
  profile_id: string;
  approved_by: string;
  status: "approved" | "expired" | "revoked";
  approval: {
    approval_id: string;
    intent: string;
    snapshot_digest: string;
    issued_at: number;
    expires_at: number;
  };
  plan_starts_at: string;
  plan_ends_at: string;
  revoked: boolean;
  runtime_only: boolean;
  survives_restart: boolean;
  execution_performed: boolean;
  executor_available: boolean;
  can_execute: boolean;
};
type ApprovalListResponse = {
  approvals: RuntimeApprovalRecord[];
  runtime_only: boolean;
  survives_restart: boolean;
  execution_performed: boolean;
  executor_available: boolean;
  can_execute: boolean;
};
type ApprovalIssueResponse = {
  approval_issued: boolean;
  approval_id: string;
  signature: string;
  snapshot_digest: string;
  expected_snapshot_digest: string;
  issued_at: number;
  expires_at: number;
  ttl_seconds: number;
  approved_by: string;
  execution_performed: boolean;
  executor_available: boolean;
  consumed: boolean;
  can_execute: boolean;
};
type ApprovalVerifyResponse = {
  record: RuntimeApprovalRecord;
  verification: {
    valid: boolean;
    reason: string;
    approval_id: string;
    snapshot_digest: string;
    consumed: boolean;
    execution_performed: boolean;
  };
  evaluation: EvaluationResponse;
  execution_performed: boolean;
  executor_available: boolean;
  can_execute: boolean;
};
type PolicyForm = {
  mode: PolicyMode;
  max_power_kw: string;
  max_duration_minutes: string;
};

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
const VERIFY_LABELS: Record<string, string> = {
  ok: "Podpis i aktuální snapshot jsou platné.",
  unknown_approval: "Approval už tento runtime nezná, například po restartu.",
  replayed: "Jednorázový approval už byl spotřebován.",
  revoked: "Approval byl revokován.",
  expired: "Approval vypršel.",
  not_yet_valid: "Approval ještě není časově platný.",
  invalid_signature: "HMAC podpis je neplatný nebo byl artifact změněn.",
  snapshot_mismatch: "Profil, plán, policy nebo vazba se od schválení změnily.",
  policy_not_eligible: "Aktuální execution policy už kandidáta nepovoluje.",
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

function formatUnix(value: number): string {
  const parsed = new Date(value * 1000);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("cs-CZ", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function shortHash(value: string): string {
  return value.length > 24 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value;
}

export function LoadExecutionPolicyPanel({ hass, entryId, profiles, earliestStart, deadline }: { hass?: HomeAssistant; entryId: string | null; profiles: PolicyProfile[]; earliestStart: string; deadline: string }) {
  const [policies, setPolicies] = useState<ExecutionPolicy[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [form, setForm] = useState<PolicyForm>({ mode: "disabled", max_power_kw: "", max_duration_minutes: "" });
  const [status, setStatus] = useState("Načítám policy…");
  const [evaluation, setEvaluation] = useState<EvaluationResponse | null>(null);
  const [approvalPreview, setApprovalPreview] = useState<ApprovalPreviewResponse | null>(null);
  const [approvalTtl, setApprovalTtl] = useState(120);
  const [approvals, setApprovals] = useState<RuntimeApprovalRecord[]>([]);
  const [approvalRuntimeStatus, setApprovalRuntimeStatus] = useState("Načítám admin approval runtime…");
  const [approvalVerification, setApprovalVerification] = useState<ApprovalVerifyResponse | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const [previewingApproval, setPreviewingApproval] = useState(false);
  const [issuingApproval, setIssuingApproval] = useState(false);
  const [approvalBusyId, setApprovalBusyId] = useState<string | null>(null);
  const [executorAvailable, setExecutorAvailable] = useState(false);

  const selectedProfile = useMemo(() => profiles.find((profile) => profile.profile_id === selectedProfileId) ?? null, [profiles, selectedProfileId]);
  const explicitPolicy = useMemo(() => policies.find((policy) => policy.profile_id === selectedProfileId) ?? null, [policies, selectedProfileId]);
  const selectedApprovals = useMemo(() => approvals.filter((record) => record.profile_id === selectedProfileId).slice().reverse(), [approvals, selectedProfileId]);

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

  const refreshApprovals = async () => {
    if (!entryId || !hass) return;
    try {
      const response = await callHomeAssistantWs<ApprovalListResponse>(hass, { type: "frakon_energy/load_execution/approval_list", entry_id: entryId });
      setApprovals(response.approvals);
      setApprovalRuntimeStatus("Admin approval runtime načten");
    } catch (error) {
      setApprovals([]);
      setApprovalRuntimeStatus(`Signed approval management vyžaduje administrátora: ${String(error)}`);
    }
  };

  useEffect(() => {
    void refreshApprovals();
  }, [entryId, hass]);

  useEffect(() => {
    if (!selectedProfile) return;
    setForm({
      mode: explicitPolicy?.mode ?? "disabled",
      max_power_kw: String(explicitPolicy?.max_power_kw ?? selectedProfile.power_kw),
      max_duration_minutes: String(explicitPolicy?.max_duration_minutes ?? selectedProfile.duration_minutes),
    });
    setEvaluation(null);
    setApprovalPreview(null);
    setApprovalVerification(null);
  }, [selectedProfile, explicitPolicy]);

  useEffect(() => {
    setApprovalPreview(null);
    setApprovalVerification(null);
  }, [approvalTtl, earliestStart, deadline]);

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
      setEvaluation(null);
      setApprovalPreview(null);
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
    const message = buildTimeWindowMessage("frakon_energy/load_execution/evaluate_profile");
    if (!message) return;
    setEvaluating(true);
    setApprovalPreview(null);
    setApprovalVerification(null);
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
    const message = buildTimeWindowMessage("frakon_energy/load_execution/approval_preview");
    if (!message) return;
    message.ttl_seconds = approvalTtl;
    setPreviewingApproval(true);
    setApprovalVerification(null);
    setStatus("Počítám přesný rozsah schválení…");
    try {
      const response = await callHomeAssistantWs<ApprovalPreviewResponse>(hass, message);
      setApprovalPreview(response);
      setStatus(response.eligible ? "Snapshot je připravený k explicitnímu admin schválení. Artifact zatím nebyl vydán." : "Rozsah schválení nelze vytvořit, protože policy kandidáta blokuje.");
    } catch (error) {
      setApprovalPreview(null);
      setStatus(`Chyba: ${String(error)}`);
    } finally {
      setPreviewingApproval(false);
    }
  };

  const issueApproval = async () => {
    if (!hass || !approvalPreview?.eligible || !approvalPreview.snapshot_digest) return;
    const message = buildTimeWindowMessage("frakon_energy/load_execution/approval_issue");
    if (!message) return;
    message.expected_snapshot_digest = approvalPreview.snapshot_digest;
    message.ttl_seconds = approvalTtl;
    setIssuingApproval(true);
    setStatus("Znovu přepočítávám scope a vydávám HMAC-signed approval…");
    try {
      const response = await callHomeAssistantWs<ApprovalIssueResponse>(hass, message);
      await refreshApprovals();
      setStatus(`Approval ${response.approval_id} vydán. Nic nebylo spuštěno; executor stále neexistuje.`);
    } catch (error) {
      setStatus(`Approval nevydán: ${String(error)}. Obnov rozsah schválení a zkus znovu.`);
    } finally {
      setIssuingApproval(false);
    }
  };

  const verifyApproval = async (record: RuntimeApprovalRecord) => {
    if (!hass || !entryId) return;
    setApprovalBusyId(record.approval.approval_id);
    try {
      const response = await callHomeAssistantWs<ApprovalVerifyResponse>(hass, { type: "frakon_energy/load_execution/approval_verify", entry_id: entryId, approval_id: record.approval.approval_id });
      setApprovalVerification(response);
      await refreshApprovals();
      setStatus(response.verification.valid ? "Signed approval je stále kryptograficky i obsahově platný. Nic nebylo spuštěno." : `Approval není platný: ${VERIFY_LABELS[response.verification.reason] ?? response.verification.reason}`);
    } catch (error) {
      setStatus(`Chyba ověření approvalu: ${String(error)}`);
    } finally {
      setApprovalBusyId(null);
    }
  };

  const revokeApproval = async (record: RuntimeApprovalRecord) => {
    if (!hass || !entryId) return;
    setApprovalBusyId(record.approval.approval_id);
    try {
      await callHomeAssistantWs(hass, { type: "frakon_energy/load_execution/approval_revoke", entry_id: entryId, approval_id: record.approval.approval_id });
      setApprovalVerification(null);
      await refreshApprovals();
      setStatus("Signed approval byl revokován a už neprojde Verify.");
    } catch (error) {
      setStatus(`Chyba revokace approvalu: ${String(error)}`);
    } finally {
      setApprovalBusyId(null);
    }
  };

  if (profiles.length === 0) return <section className="load-policy-panel"><span className="eyebrow">Execution Guard</span><h3>Nejdřív vytvoř profil zátěže</h3><p>Policy se váže na uložený profil. Bez profilu není co vyhodnocovat.</p></section>;

  return <section className="load-policy-panel">
    <div className="load-policy-header"><div><span className="eyebrow">Execution Guard</span><h3>Fail-closed policy před budoucím řízením</h3></div><div className="load-policy-executor"><span>Executor</span><b>{executorAvailable ? "dostupný" : "není implementován"}</b></div></div>
    <p>Žádný režim Automatic neexistuje. Výsledek policy může být pouze <b>Blocked</b> nebo <b>Approval required</b>. Administrátor může podepsat přesný snapshot, ale signed approval <b>není spuštění zařízení</b> a v této vrstvě neexistuje Execute ani Consume endpoint.</p>

    <div className="load-policy-grid">
      <label>Profil<select value={selectedProfileId} onChange={(e) => setSelectedProfileId(e.target.value)}>{profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.name}</option>)}</select></label>
      <label>Režim<select value={form.mode} onChange={(e) => setForm((current) => ({ ...current, mode: e.target.value as PolicyMode }))}><option value="disabled">Disabled</option><option value="approval_required">Vyžadovat schválení</option></select></label>
      <label>Max. výkon · kW<input type="number" min="0.001" step="0.1" disabled={form.mode === "disabled"} value={form.max_power_kw} onChange={(e) => setForm((current) => ({ ...current, max_power_kw: e.target.value }))} /></label>
      <label>Max. délka · min<input type="number" min="15" step="15" disabled={form.mode === "disabled"} value={form.max_duration_minutes} onChange={(e) => setForm((current) => ({ ...current, max_duration_minutes: e.target.value }))} /></label>
    </div>

    {selectedProfile ? <div className="load-policy-binding"><span>Vazba</span><b>{selectedProfile.entity_id ?? "žádná HA entita"}</b><small>Policy v UI vždy vyžaduje existující i dostupnou entitu.</small></div> : null}
    <div className="load-policy-actions"><button className="primary-action" onClick={savePolicy} disabled={!entryId || !hass}>Uložit policy</button><button className="secondary-action" onClick={evaluate} disabled={!entryId || !hass || evaluating}>{evaluating ? "Vyhodnocuji…" : "Vyhodnotit aktuální plán"}</button><button className="secondary-action" onClick={previewApprovalScope} disabled={!entryId || !hass || previewingApproval}>{previewingApproval ? "Počítám scope…" : "Zobrazit rozsah schválení"}</button><span>{status}</span></div>

    {evaluation ? <div className={`load-policy-result load-policy-result--${evaluation.status}`}><div><span>Verdikt</span><strong>{evaluation.status === "approval_required" ? "Approval required" : "Blocked"}</strong></div>{evaluation.plan ? <div className="load-policy-result__plan"><span>{formatTime(evaluation.plan.starts_at)} → {formatTime(evaluation.plan.ends_at)}</span><b>{evaluation.plan.power_kw.toLocaleString("cs-CZ")} kW · {evaluation.plan.duration_minutes} min · ~{evaluation.plan.estimated_cost_czk.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} Kč</b></div> : null}<div className="load-policy-reasons">{evaluation.reasons.length === 0 ? <span>Všechny kontrolované podmínky policy jsou splněné. K provedení je stále nutná další oddělená schvalovací/exekuční cesta.</span> : evaluation.reasons.map((reason) => <span key={reason}>{REASON_LABELS[reason] ?? reason}</span>)}</div><small>execution_performed={String(evaluation.execution_performed)} · executor_available={String(evaluation.executor_available)} · entity_available={String(evaluation.entity_available)}</small></div> : null}

    <div className="load-approval-config"><label>Platnost signed approvalu<select value={approvalTtl} onChange={(e) => setApprovalTtl(Number(e.target.value))}><option value={60}>60 sekund</option><option value={120}>120 sekund</option><option value={300}>300 sekund · maximum</option></select></label><small>Skutečná expirace je vždy omezena i začátkem konkrétního plánovaného okna.</small></div>

    {approvalPreview ? <div className={`load-approval-scope ${approvalPreview.eligible ? "load-approval-scope--eligible" : "load-approval-scope--blocked"}`}><div className="load-approval-scope__header"><div><span className="eyebrow">Approval scope</span><h4>{approvalPreview.eligible ? "Přesný snapshot k explicitnímu podpisu" : "Kandidát není způsobilý ke schválení"}</h4></div><span className="load-approval-scope__badge">{approvalPreview.eligible ? "scope ready" : "blocked"}</span></div>{approvalPreview.plan ? <div className="load-approval-scope__plan"><span>{formatTime(approvalPreview.plan.starts_at)} → {formatTime(approvalPreview.plan.ends_at)}</span><b>{approvalPreview.entity_id ?? "bez entity"} · {approvalPreview.plan.power_kw.toLocaleString("cs-CZ")} kW · {approvalPreview.plan.duration_minutes} min</b></div> : null}{approvalPreview.snapshot_digest ? <div className="load-approval-scope__digest"><span>SHA-256 snapshot digest</span><code>{approvalPreview.snapshot_digest}</code></div> : <div className="load-policy-reasons">{approvalPreview.reasons.map((reason) => <span key={reason}>{REASON_LABELS[reason] ?? reason}</span>)}</div>}<div className="load-approval-scope__meta"><div><span>Intent</span><b>{approvalPreview.intent}</b></div><div><span>Schema</span><b>v{approvalPreview.schema_version}</b></div><div><span>TTL</span><b>{approvalPreview.ttl_seconds} s / max {approvalPreview.max_ttl_seconds} s</b></div><div><span>Artifact</span><b>{approvalPreview.approval_issued ? "vydán" : "nevydán"}</b></div></div><p>Po stisku Schválit server znovu přepočítá aktuální plán, policy i stav entity. Signed artifact vydá pouze tehdy, když nový SHA-256 digest přesně odpovídá tomuto zobrazenému digestu.</p>{approvalPreview.eligible && approvalPreview.snapshot_digest ? <div className="load-approval-issue"><button className="primary-action" onClick={issueApproval} disabled={issuingApproval}>{issuingApproval ? "Podepisuji…" : "Schválit přesně tento snapshot"}</button><span>Admin-only · HMAC-signed · runtime-only · žádné spuštění</span></div> : null}<small>approval_issued={String(approvalPreview.approval_issued)} · execution_performed={String(approvalPreview.execution_performed)} · executor_available={String(approvalPreview.executor_available)} · preview_only={String(approvalPreview.preview_only)} · can_execute={String(approvalPreview.can_execute ?? false)}</small></div> : null}

    <div className="load-approval-runtime"><div className="load-approval-runtime__header"><div><span className="eyebrow">Signed approval runtime</span><h4>Vydané approvaly pro vybraný profil</h4></div><button className="secondary-action" onClick={() => void refreshApprovals()} disabled={!entryId || !hass}>Obnovit</button></div><p>{approvalRuntimeStatus}. Runtime seznam i HMAC autorita se po restartu zahodí fail-closed.</p>{selectedApprovals.length === 0 ? <div className="load-approval-runtime__empty">Pro tento profil není žádný runtime signed approval.</div> : <div className="load-approval-runtime__list">{selectedApprovals.map((record) => <article className={`load-approval-record load-approval-record--${record.status}`} key={record.approval.approval_id}><div className="load-approval-record__top"><div><span>{record.status === "approved" ? "Signed approval" : record.status === "revoked" ? "Revokovaný approval" : "Vypršelý approval"}</span><strong>{record.approval.approval_id}</strong></div><b>{formatUnix(record.approval.expires_at)}</b></div><div className="load-approval-record__meta"><div><span>Digest</span><code>{shortHash(record.approval.snapshot_digest)}</code></div><div><span>Schválil</span><b>{record.approved_by}</b></div><div><span>Intent</span><b>{record.approval.intent}</b></div><div><span>Plán start</span><b>{formatTime(record.plan_starts_at)}</b></div></div>{record.status === "approved" ? <div className="load-approval-record__actions"><button onClick={() => verifyApproval(record)} disabled={approvalBusyId === record.approval.approval_id}>{approvalBusyId === record.approval.approval_id ? "Pracuji…" : "Verify"}</button><button className="danger-action" onClick={() => revokeApproval(record)} disabled={approvalBusyId === record.approval.approval_id}>Revokovat</button></div> : null}<small>runtime_only={String(record.runtime_only)} · survives_restart={String(record.survives_restart)} · execution_performed={String(record.execution_performed)} · can_execute={String(record.can_execute)}</small></article>)}</div>}</div>

    {approvalVerification ? <div className={`load-approval-verification ${approvalVerification.verification.valid ? "load-approval-verification--valid" : "load-approval-verification--invalid"}`}><div><span className="eyebrow">Verify signed approvalu</span><strong>{approvalVerification.verification.valid ? "Platný" : "Neplatný"}</strong></div><p>{VERIFY_LABELS[approvalVerification.verification.reason] ?? approvalVerification.verification.reason}</p><small>consumed={String(approvalVerification.verification.consumed)} · execution_performed={String(approvalVerification.execution_performed)} · executor_available={String(approvalVerification.executor_available)} · can_execute={String(approvalVerification.can_execute)}</small></div> : null}
  </section>;
}
