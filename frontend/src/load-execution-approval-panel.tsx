import React, { useEffect, useMemo, useState } from "react";
import { callHomeAssistantWs, type HomeAssistant } from "./home-assistant";
import "./load-execution-approval-panel.css";

export type ApprovalProfile = {
  profile_id: string;
  name: string;
  power_kw: number;
  duration_minutes: number;
  enabled: boolean;
  entity_id?: string | null;
};

type ApprovalStatus = "pending" | "approved" | "used" | "revoked" | "expired";
type ApprovalScope = {
  entry_id: string;
  profile_id: string;
  entity_id: string;
  plan_starts_at: string;
  plan_ends_at: string;
  plan_power_kw: number;
  plan_duration_minutes: number;
  plan_average_czk_kwh: number;
  plan_estimated_cost_czk: number;
  policy_mode: string;
  policy_max_power_kw: number;
  policy_max_duration_minutes: number;
};
type Approval = {
  approval_id: string;
  scope: ApprovalScope;
  scope_hash: string;
  created_at: string;
  expires_at: string;
  status: ApprovalStatus;
  stored_status: ApprovalStatus;
  approved_at: string | null;
  approved_by: string | null;
  revoked_at: string | null;
  used_at: string | null;
  runtime_only: boolean;
  survives_restart: boolean;
  execution_performed: boolean;
  executor_available: boolean;
  can_execute: boolean;
};
type ApprovalListResponse = {
  approvals: Approval[];
  runtime_only: boolean;
  survives_restart: boolean;
  executor_available: boolean;
  execution_performed: boolean;
  can_execute: boolean;
};
type Evaluation = {
  status: "blocked" | "approval_required";
  reasons: string[];
  plan: { starts_at: string; ends_at: string; estimated_cost_czk: number } | null;
};
type RequestResponse = {
  created: boolean;
  approval: Approval | null;
  evaluation: Evaluation;
  executor_available: boolean;
  execution_performed: boolean;
  can_execute: boolean;
};
type ValidationResponse = {
  approval: Approval;
  validation: { valid: boolean; status: ApprovalStatus; reasons: string[] };
  evaluation: Evaluation;
  executor_available: boolean;
  execution_performed: boolean;
  can_execute: boolean;
};

const STATUS_LABELS: Record<ApprovalStatus, string> = {
  pending: "Čeká na schválení",
  approved: "Schváleno · inertní",
  used: "Použito",
  revoked: "Zrušeno",
  expired: "Vypršelo",
};
const VALIDATION_LABELS: Record<string, string> = {
  not_approved: "Žádost zatím není schválená.",
  expired: "Platnost schválení vypršela.",
  scope_changed: "Profil, plán, entita nebo policy se od vytvoření žádosti změnily.",
  plan_started: "Plánovaný interval už začal.",
  already_used: "Jednorázové schválení už bylo použito.",
  revoked: "Schválení bylo zrušeno.",
};
const EVALUATION_LABELS: Record<string, string> = {
  policy_disabled: "Policy je vypnutá.",
  profile_disabled: "Profil je vypnutý.",
  entity_binding_required: "Chybí vazba na Home Assistant entitu.",
  entity_unavailable: "Navázaná Home Assistant entita není dostupná.",
  power_limit_exceeded: "Plán překračuje limit výkonu.",
  duration_limit_exceeded: "Plán překračuje limit délky.",
  plan_unavailable: "Pro zadané časové okno není dostupný plán.",
};

function toIso(value: string): string | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}
function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("cs-CZ", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function shortId(value: string): string { return value.length > 14 ? `${value.slice(0, 7)}…${value.slice(-5)}` : value; }

export function LoadExecutionApprovalPanel({ hass, entryId, profiles, earliestStart, deadline }: { hass?: HomeAssistant; entryId: string | null; profiles: ApprovalProfile[]; earliestStart: string; deadline: string }) {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [ttlSeconds, setTtlSeconds] = useState(300);
  const [status, setStatus] = useState("Načítám žádosti…");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [lastEvaluation, setLastEvaluation] = useState<Evaluation | null>(null);
  const [lastValidation, setLastValidation] = useState<ValidationResponse | null>(null);

  const selectedProfile = useMemo(() => profiles.find((item) => item.profile_id === selectedProfileId) ?? null, [profiles, selectedProfileId]);

  const refresh = async () => {
    if (!entryId || !hass) return;
    const response = await callHomeAssistantWs<ApprovalListResponse>(hass, { type: "frakon_energy/load_execution_approvals/list", entry_id: entryId });
    setApprovals(response.approvals);
  };

  useEffect(() => {
    if (!profiles.length) { setSelectedProfileId(""); return; }
    if (!profiles.some((item) => item.profile_id === selectedProfileId)) setSelectedProfileId(profiles[0].profile_id);
  }, [profiles, selectedProfileId]);

  useEffect(() => {
    if (!entryId || !hass) { setStatus("Čekám na Home Assistant"); return; }
    refresh().then(() => setStatus("Runtime žádosti načteny")).catch((error) => setStatus(`Chyba: ${String(error)}`));
  }, [entryId, hass]);

  const requestApproval = async () => {
    if (!entryId || !hass || !selectedProfile) return;
    setRequesting(true);
    setLastValidation(null);
    setStatus("Vytvářím scope a znovu kontroluji execution policy…");
    try {
      const message: Record<string, unknown> = {
        type: "frakon_energy/load_execution_approvals/request",
        entry_id: entryId,
        profile_id: selectedProfile.profile_id,
        ttl_seconds: ttlSeconds,
      };
      const earliest = toIso(earliestStart);
      const latest = toIso(deadline);
      if (earliest) message.earliest_start = earliest;
      if (latest) message.deadline = latest;
      const response = await callHomeAssistantWs<RequestResponse>(hass, message);
      setLastEvaluation(response.evaluation);
      await refresh();
      setStatus(response.created ? "Žádost vytvořena. Zatím není schválená a nic nespustí." : "Žádost nevznikla, protože aktuální policy plán zablokovala.");
    } catch (error) {
      setStatus(`Chyba: ${String(error)}`);
    } finally {
      setRequesting(false);
    }
  };

  const approve = async (approval: Approval) => {
    if (!entryId || !hass) return;
    setBusyId(approval.approval_id);
    setStatus("Před schválením znovu ověřuji neměnný scope…");
    try {
      await callHomeAssistantWs(hass, { type: "frakon_energy/load_execution_approvals/approve", entry_id: entryId, approval_id: approval.approval_id });
      await refresh();
      setStatus("Schváleno. Approval je stále inertní: executor neexistuje a nic nebylo spuštěno.");
    } catch (error) {
      setStatus(`Schválení odmítnuto: ${String(error)}`);
    } finally {
      setBusyId(null);
    }
  };

  const revoke = async (approval: Approval) => {
    if (!entryId || !hass) return;
    setBusyId(approval.approval_id);
    try {
      await callHomeAssistantWs(hass, { type: "frakon_energy/load_execution_approvals/revoke", entry_id: entryId, approval_id: approval.approval_id });
      await refresh();
      setStatus("Approval zrušen.");
    } catch (error) {
      setStatus(`Chyba: ${String(error)}`);
    } finally {
      setBusyId(null);
    }
  };

  const validate = async (approval: Approval) => {
    if (!entryId || !hass) return;
    setBusyId(approval.approval_id);
    try {
      const response = await callHomeAssistantWs<ValidationResponse>(hass, { type: "frakon_energy/load_execution_approvals/validate", entry_id: entryId, approval_id: approval.approval_id });
      setLastValidation(response);
      await refresh();
      setStatus(response.validation.valid ? "Approval je stále platný pro přesně stejný scope. Nic nebylo spuštěno." : "Approval už není platný pro aktuální scope.");
    } catch (error) {
      setStatus(`Chyba: ${String(error)}`);
    } finally {
      setBusyId(null);
    }
  };

  return <section className="load-approval-panel">
    <div className="load-approval-header"><div><span className="eyebrow">Approval Guard</span><h3>Krátkodobé jednorázové schválení</h3></div><div className="load-approval-safety"><span>Executor</span><b>není implementován</b><small>can_execute = false</small></div></div>
    <p>Approval je navázaný na přesný profil, entitu, spotový plán a policy snapshot. Platí maximálně 15 minut, nejdéle do začátku plánu, po restartu Home Assistantu/FRAKONu zmizí a <b>samotné schválení nikdy nic nespustí</b>.</p>

    <div className="load-approval-request">
      <label>Profil<select value={selectedProfileId} onChange={(e) => setSelectedProfileId(e.target.value)} disabled={!profiles.length}>{profiles.length ? profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.name}</option>) : <option value="">Žádný profil</option>}</select></label>
      <label>Platnost žádosti<select value={ttlSeconds} onChange={(e) => setTtlSeconds(Number(e.target.value))}><option value={60}>1 minuta</option><option value={300}>5 minut</option><option value={600}>10 minut</option><option value={900}>15 minut</option></select></label>
      <button className="primary-action" onClick={requestApproval} disabled={!selectedProfile || requesting || !entryId || !hass}>{requesting ? "Kontroluji…" : "Vytvořit žádost o schválení"}</button>
    </div>
    <div className="load-approval-status">{status}</div>

    {lastEvaluation?.status === "blocked" ? <div className="load-approval-blocked"><b>Žádost nevznikla</b>{lastEvaluation.reasons.map((reason) => <span key={reason}>{EVALUATION_LABELS[reason] ?? reason}</span>)}</div> : null}

    <div className="load-approval-list">
      {approvals.length === 0 ? <div className="load-approval-empty">Žádná runtime žádost. Po restartu je tento seznam vždy prázdný.</div> : approvals.slice().reverse().map((approval) => <article className={`load-approval-item load-approval-item--${approval.status}`} key={approval.approval_id}>
        <div className="load-approval-item__top"><div><span>{STATUS_LABELS[approval.status]}</span><strong>{profiles.find((p) => p.profile_id === approval.scope.profile_id)?.name ?? approval.scope.profile_id}</strong><small>{shortId(approval.approval_id)} · scope {approval.scope_hash.slice(0, 10)}…</small></div><div><b>{approval.scope.plan_power_kw.toLocaleString("cs-CZ")} kW</b><span>{approval.scope.plan_duration_minutes} min</span></div></div>
        <div className="load-approval-scope"><div><span>Entita</span><b>{approval.scope.entity_id}</b></div><div><span>Plán</span><b>{formatTime(approval.scope.plan_starts_at)} → {formatTime(approval.scope.plan_ends_at)}</b></div><div><span>Odhad</span><b>{approval.scope.plan_estimated_cost_czk.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} Kč</b></div><div><span>Vyprší</span><b>{formatTime(approval.expires_at)}</b></div></div>
        {approval.approved_by ? <div className="load-approval-approved-by">Schválil: <b>{approval.approved_by}</b></div> : null}
        <div className="load-approval-actions">
          {approval.status === "pending" ? <button onClick={() => approve(approval)} disabled={busyId === approval.approval_id}>Schválit tento scope</button> : null}
          {approval.status === "approved" ? <button onClick={() => validate(approval)} disabled={busyId === approval.approval_id}>Ověřit platnost</button> : null}
          {(approval.status === "pending" || approval.status === "approved") ? <button className="danger-action" onClick={() => revoke(approval)} disabled={busyId === approval.approval_id}>Zrušit</button> : null}
        </div>
      </article>)}
    </div>

    {lastValidation ? <div className={`load-approval-validation ${lastValidation.validation.valid ? "is-valid" : "is-invalid"}`}><b>{lastValidation.validation.valid ? "Scope je stále platný" : "Scope už není platný"}</b>{lastValidation.validation.reasons.length ? lastValidation.validation.reasons.map((reason) => <span key={reason}>{VALIDATION_LABELS[reason] ?? reason}</span>) : <span>Profil, entita, plán i policy odpovídají schválenému snapshotu.</span>}<small>execution_performed = false · executor_available = false · can_execute = false</small></div> : null}
  </section>;
}
