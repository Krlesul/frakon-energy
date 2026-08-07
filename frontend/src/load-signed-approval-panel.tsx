import React, { useEffect, useMemo, useState } from "react";
import { callHomeAssistantWs, type HomeAssistant } from "./home-assistant";
import "./load-signed-approval-panel.css";

export type SignedApprovalProfile = {
  profile_id: string;
  name: string;
  power_kw: number;
  duration_minutes: number;
  enabled: boolean;
  entity_id?: string | null;
};

type ApprovalPreview = {
  eligible: boolean;
  status: "blocked" | "approval_required";
  reasons: string[];
  intent: "execute_load_plan";
  schema_version: number;
  snapshot_digest: string | null;
  profile: SignedApprovalProfile | null;
  policy: { mode: string; max_power_kw: number | null; max_duration_minutes: number | null } | null;
  plan: { starts_at: string; ends_at: string; power_kw: number; duration_minutes: number; estimated_cost_czk: number } | null;
  entity_id: string | null;
  entity_available: boolean | null;
  ttl_seconds: number;
  max_ttl_seconds: number;
  approval_issued: boolean;
  execution_performed: boolean;
  executor_available: boolean;
  preview_only: boolean;
  can_execute: boolean;
};

type ApprovalRecord = {
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
    signature?: string;
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
  approvals: ApprovalRecord[];
  runtime_only: boolean;
  survives_restart: boolean;
  execution_performed: boolean;
  executor_available: boolean;
  can_execute: boolean;
};

type IssueResponse = {
  issued: boolean;
  record: ApprovalRecord;
  preview: ApprovalPreview;
  execution_performed: boolean;
  executor_available: boolean;
  can_execute: boolean;
};

type VerifyResponse = {
  record: ApprovalRecord;
  verification: { valid: boolean; reason: string; consumed: boolean; execution_performed: boolean };
  execution_performed: boolean;
  executor_available: boolean;
  can_execute: boolean;
};

const REASON_LABELS: Record<string, string> = {
  policy_disabled: "Execution policy je vypnutá.",
  profile_disabled: "Profil spotřebiče je vypnutý.",
  entity_binding_required: "Chybí vazba na Home Assistant entitu.",
  entity_unavailable: "Navázaná entita není dostupná.",
  power_limit_exceeded: "Plán překračuje limit výkonu.",
  duration_limit_exceeded: "Plán překračuje limit délky.",
  plan_unavailable: "Pro zadané časové okno není vhodný plán.",
};
const VERIFY_LABELS: Record<string, string> = {
  ok: "Podpis i aktuální snapshot jsou platné.",
  unknown_approval: "Approval už tento runtime nezná (např. po restartu).",
  replayed: "Jednorázový approval už byl spotřebován.",
  revoked: "Approval byl zrušen.",
  expired: "Approval vypršel.",
  not_yet_valid: "Čas approvalu ještě nezačal platit.",
  invalid_signature: "Podpis approvalu je neplatný nebo byl změněn.",
  snapshot_mismatch: "Profil, plán, entita nebo policy se od schválení změnily.",
  policy_not_eligible: "Aktuální execution policy už kandidáta nepovoluje.",
};

function toIso(value: string): string | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}
function formatDate(value: string | number): string {
  const parsed = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("cs-CZ", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function shortHash(value: string): string { return value.length > 22 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value; }

export function LoadSignedApprovalPanel({ hass, entryId, profiles, earliestStart, deadline }: { hass?: HomeAssistant; entryId: string | null; profiles: SignedApprovalProfile[]; earliestStart: string; deadline: string }) {
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [ttlSeconds, setTtlSeconds] = useState(120);
  const [preview, setPreview] = useState<ApprovalPreview | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [status, setStatus] = useState("Načítám signed approvals…");
  const [previewing, setPreviewing] = useState(false);
  const [issuing, setIssuing] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [verification, setVerification] = useState<VerifyResponse | null>(null);

  const selectedProfile = useMemo(() => profiles.find((item) => item.profile_id === selectedProfileId) ?? null, [profiles, selectedProfileId]);

  const refresh = async () => {
    if (!entryId || !hass) return;
    const response = await callHomeAssistantWs<ApprovalListResponse>(hass, { type: "frakon_energy/load_execution/approval_list", entry_id: entryId });
    setApprovals(response.approvals);
  };

  useEffect(() => {
    if (!profiles.length) { setSelectedProfileId(""); return; }
    if (!profiles.some((item) => item.profile_id === selectedProfileId)) setSelectedProfileId(profiles[0].profile_id);
  }, [profiles, selectedProfileId]);

  useEffect(() => {
    setPreview(null);
    setVerification(null);
  }, [selectedProfileId, ttlSeconds, earliestStart, deadline]);

  useEffect(() => {
    if (!entryId || !hass) { setStatus("Čekám na Home Assistant"); return; }
    refresh().then(() => setStatus("Signed approval runtime načten")).catch((error) => setStatus(`Chyba: ${String(error)}`));
  }, [entryId, hass]);

  const previewScope = async () => {
    if (!entryId || !hass || !selectedProfile) return;
    setPreviewing(true);
    setVerification(null);
    setStatus("Přepočítávám přesný snapshot k případnému schválení…");
    try {
      const message: Record<string, unknown> = {
        type: "frakon_energy/load_execution/approval_preview",
        entry_id: entryId,
        profile_id: selectedProfile.profile_id,
        ttl_seconds: ttlSeconds,
      };
      const earliest = toIso(earliestStart);
      const latest = toIso(deadline);
      if (earliest) message.earliest_start = earliest;
      if (latest) message.deadline = latest;
      const result = await callHomeAssistantWs<ApprovalPreview>(hass, message);
      setPreview(result);
      setStatus(result.eligible ? "Snapshot je připraven k explicitnímu schválení. Zatím nebyl vydán žádný approval." : "Snapshot není způsobilý ke schválení.");
    } catch (error) {
      setPreview(null);
      setStatus(`Chyba: ${String(error)}`);
    } finally {
      setPreviewing(false);
    }
  };

  const issueApproval = async () => {
    if (!entryId || !hass || !selectedProfile || !preview?.eligible || !preview.snapshot_digest) return;
    setIssuing(true);
    setStatus("Znovu ověřuji digest a vydávám krátkodobý HMAC-signed approval…");
    try {
      const message: Record<string, unknown> = {
        type: "frakon_energy/load_execution/approval_issue",
        entry_id: entryId,
        profile_id: selectedProfile.profile_id,
        intent: "execute_load_plan",
        expected_snapshot_digest: preview.snapshot_digest,
        ttl_seconds: ttlSeconds,
      };
      const earliest = toIso(earliestStart);
      const latest = toIso(deadline);
      if (earliest) message.earliest_start = earliest;
      if (latest) message.deadline = latest;
      const result = await callHomeAssistantWs<IssueResponse>(hass, message);
      await refresh();
      setPreview(result.preview);
      setStatus("Snapshot byl explicitně podepsán. Approval je stále inertní: executor neexistuje a nic se nespustilo.");
    } catch (error) {
      setStatus(`Approval nevydán: ${String(error)}`);
    } finally {
      setIssuing(false);
    }
  };

  const verify = async (record: ApprovalRecord) => {
    if (!entryId || !hass) return;
    setBusyId(record.approval.approval_id);
    try {
      const result = await callHomeAssistantWs<VerifyResponse>(hass, { type: "frakon_energy/load_execution/approval_verify", entry_id: entryId, approval_id: record.approval.approval_id });
      setVerification(result);
      await refresh();
      setStatus(result.verification.valid ? "Approval je stále kryptograficky i obsahově platný. Nic nebylo spuštěno." : `Approval není platný: ${VERIFY_LABELS[result.verification.reason] ?? result.verification.reason}`);
    } catch (error) {
      setStatus(`Chyba ověření: ${String(error)}`);
    } finally {
      setBusyId(null);
    }
  };

  const revoke = async (record: ApprovalRecord) => {
    if (!entryId || !hass) return;
    setBusyId(record.approval.approval_id);
    try {
      await callHomeAssistantWs(hass, { type: "frakon_energy/load_execution/approval_revoke", entry_id: entryId, approval_id: record.approval.approval_id });
      await refresh();
      setVerification(null);
      setStatus("Signed approval byl revokován a už neprojde verify.");
    } catch (error) {
      setStatus(`Chyba revokace: ${String(error)}`);
    } finally {
      setBusyId(null);
    }
  };

  return <section className="signed-approval-panel">
    <div className="signed-approval-header"><div><span className="eyebrow">Signed Approval Guard</span><h3>Náhled → explicitní podpis → ověření</h3></div><div className="signed-approval-lock"><span>Executor</span><b>není implementován</b><small>can_execute = false</small></div></div>
    <p>Nejdřív si zobrazíš přesný snapshot a jeho SHA-256 digest. Tlačítko Schválit pak server znovu přepočítá stejný kandidát a HMAC podpis vydá jen pokud digest stále přesně souhlasí. Platnost je nejvýše 5 minut a nikdy nepřesáhne začátek plánovaného okna.</p>

    <div className="signed-approval-controls">
      <label>Profil<select value={selectedProfileId} onChange={(e) => setSelectedProfileId(e.target.value)} disabled={!profiles.length}>{profiles.length ? profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.name}</option>) : <option value="">Žádný profil</option>}</select></label>
      <label>TTL<select value={ttlSeconds} onChange={(e) => setTtlSeconds(Number(e.target.value))}><option value={60}>60 s</option><option value={120}>120 s</option><option value={300}>300 s · maximum</option></select></label>
      <button onClick={previewScope} disabled={!selectedProfile || previewing || !entryId || !hass}>{previewing ? "Počítám…" : "1. Zobrazit snapshot"}</button>
    </div>

    {preview ? <div className={`signed-approval-preview ${preview.eligible ? "is-eligible" : "is-blocked"}`}>
      <div className="signed-approval-preview__top"><div><span className="eyebrow">Approval preview</span><strong>{preview.eligible ? "Způsobilý snapshot" : "Zablokovaný snapshot"}</strong></div><b>{preview.intent}</b></div>
      {preview.plan ? <div className="signed-approval-scope"><div><span>Entita</span><b>{preview.entity_id || "—"}</b></div><div><span>Plán</span><b>{formatDate(preview.plan.starts_at)} → {formatDate(preview.plan.ends_at)}</b></div><div><span>Výkon / délka</span><b>{preview.plan.power_kw.toLocaleString("cs-CZ")} kW · {preview.plan.duration_minutes} min</b></div><div><span>Odhad</span><b>{preview.plan.estimated_cost_czk.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} Kč</b></div></div> : null}
      {preview.snapshot_digest ? <div className="signed-approval-digest"><span>SHA-256 snapshot digest</span><code>{preview.snapshot_digest}</code></div> : null}
      {preview.reasons.length ? <div className="signed-approval-reasons">{preview.reasons.map((reason) => <span key={reason}>{REASON_LABELS[reason] ?? reason}</span>)}</div> : null}
      <div className="signed-approval-preview__footer"><small>approval_issued = false · execution_performed = false · preview_only = true</small>{preview.eligible && preview.snapshot_digest ? <button className="primary-action" onClick={issueApproval} disabled={issuing}>{issuing ? "Podepisuji…" : "2. Schválit přesně tento snapshot"}</button> : null}</div>
    </div> : null}

    <div className="signed-approval-status">{status}</div>

    <div className="signed-approval-list">
      {approvals.length === 0 ? <div className="signed-approval-empty">Žádné aktivní runtime approvals. Po restartu jsou všechny předchozí approvaly automaticky neplatné.</div> : approvals.slice().reverse().map((record) => <article className={`signed-approval-item signed-approval-item--${record.status}`} key={record.approval.approval_id}>
        <div className="signed-approval-item__top"><div><span>{record.status === "approved" ? "Podepsaný approval" : record.status === "revoked" ? "Revokovaný approval" : "Vypršelý approval"}</span><strong>{profiles.find((profile) => profile.profile_id === record.profile_id)?.name ?? record.profile_id}</strong><small>ID {record.approval.approval_id}</small></div><div><span>Platí do</span><b>{formatDate(record.approval.expires_at)}</b></div></div>
        <div className="signed-approval-meta"><div><span>Digest</span><code>{shortHash(record.approval.snapshot_digest)}</code></div><div><span>Schválil</span><b>{record.approved_by}</b></div><div><span>Intent</span><b>{record.approval.intent}</b></div><div><span>Plán start</span><b>{formatDate(record.plan_starts_at)}</b></div></div>
        <div className="signed-approval-actions">{record.status === "approved" ? <><button onClick={() => verify(record)} disabled={busyId === record.approval.approval_id}>Ověřit</button><button className="danger-action" onClick={() => revoke(record)} disabled={busyId === record.approval.approval_id}>Revokovat</button></> : null}</div>
      </article>)}
    </div>

    {verification ? <div className={`signed-approval-verification ${verification.verification.valid ? "is-valid" : "is-invalid"}`}><b>{verification.verification.valid ? "Verify: platný" : "Verify: neplatný"}</b><span>{VERIFY_LABELS[verification.verification.reason] ?? verification.verification.reason}</span><small>consumed = {String(verification.verification.consumed)} · execution_performed = false · executor_available = false · can_execute = false</small></div> : null}
  </section>;
}
