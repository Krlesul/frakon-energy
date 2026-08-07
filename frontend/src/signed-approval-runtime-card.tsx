import React, { useEffect, useMemo, useState } from "react";
import { callHomeAssistantWs, type HomeAssistant } from "./home-assistant";
import "./signed-approval-runtime-card.css";

type LoadProfile = { profile_id: string; name: string };
type ProfilesResponse = { profiles: LoadProfile[] };
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
type VerifyResponse = {
  record: RuntimeApprovalRecord;
  verification: {
    valid: boolean;
    reason: string;
    approval_id: string;
    snapshot_digest: string;
    consumed: boolean;
    execution_performed: boolean;
  };
  execution_performed: boolean;
  executor_available: boolean;
  can_execute: boolean;
};

const VERIFY_LABELS: Record<string, string> = {
  ok: "HMAC podpis i aktuální snapshot jsou platné.",
  unknown_approval: "Approval už tento runtime nezná, například po restartu.",
  replayed: "Jednorázový approval už byl spotřebován.",
  revoked: "Approval byl revokován.",
  expired: "Approval vypršel.",
  not_yet_valid: "Approval ještě není časově platný.",
  invalid_signature: "Podpis je neplatný nebo byl artifact změněn.",
  snapshot_mismatch: "Profil, plán, policy nebo vazba se od schválení změnily.",
  policy_not_eligible: "Aktuální execution policy už kandidáta nepovoluje.",
};

function formatUnix(value: number): string {
  return new Date(value * 1000).toLocaleString("cs-CZ", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function formatIso(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("cs-CZ", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function shortHash(value: string): string { return `${value.slice(0, 12)}…${value.slice(-8)}`; }

export function SignedApprovalRuntimeCard({ hass, entryId }: { hass?: HomeAssistant; entryId: string | null }) {
  const [profiles, setProfiles] = useState<LoadProfile[]>([]);
  const [approvals, setApprovals] = useState<RuntimeApprovalRecord[]>([]);
  const [profileFilter, setProfileFilter] = useState("all");
  const [status, setStatus] = useState("Načítám admin runtime…");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [verification, setVerification] = useState<VerifyResponse | null>(null);

  const visible = useMemo(() => approvals.filter((record) => profileFilter === "all" || record.profile_id === profileFilter).slice().reverse(), [approvals, profileFilter]);
  const profileName = (profileId: string) => profiles.find((profile) => profile.profile_id === profileId)?.name ?? profileId;

  const refresh = async () => {
    if (!hass || !entryId) return;
    try {
      const [profileResponse, approvalResponse] = await Promise.all([
        callHomeAssistantWs<ProfilesResponse>(hass, { type: "frakon_energy/load_profiles/list", entry_id: entryId }),
        callHomeAssistantWs<ApprovalListResponse>(hass, { type: "frakon_energy/load_execution/approval_list", entry_id: entryId }),
      ]);
      setProfiles(profileResponse.profiles);
      setApprovals(approvalResponse.approvals);
      setStatus(`Načteno ${approvalResponse.approvals.length} runtime approvalů · admin-only`);
    } catch (error) {
      setApprovals([]);
      setStatus(`Správa signed approvalů vyžaduje administrátora: ${String(error)}`);
    }
  };

  useEffect(() => { void refresh(); }, [hass, entryId]);

  const verify = async (record: RuntimeApprovalRecord) => {
    if (!hass || !entryId) return;
    setBusyId(record.approval.approval_id);
    try {
      const response = await callHomeAssistantWs<VerifyResponse>(hass, { type: "frakon_energy/load_execution/approval_verify", entry_id: entryId, approval_id: record.approval.approval_id });
      setVerification(response);
      await refresh();
      setStatus(response.verification.valid ? "Verify prošel. Artifact je stále inertní a nic nebylo spuštěno." : `Verify zamítnut: ${VERIFY_LABELS[response.verification.reason] ?? response.verification.reason}`);
    } catch (error) {
      setStatus(`Chyba Verify: ${String(error)}`);
    } finally {
      setBusyId(null);
    }
  };

  const revoke = async (record: RuntimeApprovalRecord) => {
    if (!hass || !entryId) return;
    setBusyId(record.approval.approval_id);
    try {
      await callHomeAssistantWs(hass, { type: "frakon_energy/load_execution/approval_revoke", entry_id: entryId, approval_id: record.approval.approval_id });
      setVerification(null);
      await refresh();
      setStatus("Approval revokován. Další Verify bude fail-closed.");
    } catch (error) {
      setStatus(`Chyba Revoke: ${String(error)}`);
    } finally {
      setBusyId(null);
    }
  };

  return <article className="chart-card signed-runtime-card">
    <div className="signed-runtime-header"><div><span className="eyebrow">Signed Approval Runtime</span><h2>Evidence, Verify a Revoke</h2></div><div className="signed-runtime-safety"><span>Execute endpoint</span><b>neexistuje</b><small>can_execute = false</small></div></div>
    <p className="settings-copy">Tato karta pouze spravuje signed approvaly, které administrátor už explicitně vydal v Execution Guardu. Evidence je jen v paměti, po restartu zmizí a podpis se při Verify porovnává s čerstvě přepočítaným profilem, plánem, policy a stavem entity.</p>
    <div className="signed-runtime-toolbar"><label>Filtr profilu<select value={profileFilter} onChange={(e) => setProfileFilter(e.target.value)}><option value="all">Všechny profily</option>{profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.name}</option>)}</select></label><button className="secondary-action" onClick={() => void refresh()} disabled={!hass || !entryId}>Obnovit runtime</button><span>{status}</span></div>

    <div className="signed-runtime-list">
      {visible.length === 0 ? <div className="signed-runtime-empty">Žádný runtime signed approval. To je bezpečný výchozí stav a po restartu je seznam vždy prázdný.</div> : visible.map((record) => <section className={`signed-runtime-item signed-runtime-item--${record.status}`} key={record.approval.approval_id}>
        <div className="signed-runtime-item__top"><div><span>{record.status === "approved" ? "Aktivní signed approval" : record.status === "revoked" ? "Revokovaný approval" : "Vypršelý approval"}</span><strong>{profileName(record.profile_id)}</strong><small>{record.approval.approval_id}</small></div><div><span>Platí do</span><b>{formatUnix(record.approval.expires_at)}</b></div></div>
        <div className="signed-runtime-meta"><div><span>Digest</span><code>{shortHash(record.approval.snapshot_digest)}</code></div><div><span>Schválil</span><b>{record.approved_by}</b></div><div><span>Intent</span><b>{record.approval.intent}</b></div><div><span>Plán</span><b>{formatIso(record.plan_starts_at)} → {formatIso(record.plan_ends_at)}</b></div></div>
        {record.status === "approved" ? <div className="signed-runtime-actions"><button onClick={() => verify(record)} disabled={busyId === record.approval.approval_id}>{busyId === record.approval.approval_id ? "Pracuji…" : "Verify"}</button><button className="danger-action" onClick={() => revoke(record)} disabled={busyId === record.approval.approval_id}>Revoke</button></div> : null}
        <small>runtime_only={String(record.runtime_only)} · survives_restart={String(record.survives_restart)} · execution_performed={String(record.execution_performed)} · executor_available={String(record.executor_available)} · can_execute={String(record.can_execute)}</small>
      </section>)}
    </div>

    {verification ? <div className={`signed-runtime-verification ${verification.verification.valid ? "is-valid" : "is-invalid"}`}><b>{verification.verification.valid ? "Verify: platný" : "Verify: neplatný"}</b><span>{VERIFY_LABELS[verification.verification.reason] ?? verification.verification.reason}</span><small>consumed={String(verification.verification.consumed)} · execution_performed={String(verification.execution_performed)} · executor_available={String(verification.executor_available)} · can_execute={String(verification.can_execute)}</small></div> : null}
  </article>;
}
