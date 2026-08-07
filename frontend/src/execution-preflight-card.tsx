import React, { useEffect, useMemo, useState } from "react";
import { callHomeAssistantWs, type HomeAssistant } from "./home-assistant";
import "./execution-preflight-card.css";

type LoadProfile = { profile_id: string; name: string };
type ProfilesResponse = { profiles: LoadProfile[] };
type ApprovalRecord = {
  profile_id: string;
  approved_by: string;
  status: "approved" | "expired" | "revoked";
  approval: {
    approval_id: string;
    intent: string;
    snapshot_digest: string;
    expires_at: number;
  };
  plan_starts_at: string;
  plan_ends_at: string;
};
type ApprovalListResponse = { approvals: ApprovalRecord[] };
type Attempt = {
  attempt_id: string;
  idempotency_key: string;
  approval_id: string;
  snapshot_digest: string;
  profile_id: string;
  entity_id: string;
  action: string;
  planned_starts_at: string;
  planned_ends_at: string;
  state: string;
  execution_performed: boolean;
};
type AttemptsResponse = {
  attempts: Attempt[];
  runtime_only: boolean;
  survives_restart: boolean;
  dry_run: boolean;
  approval_consumed: boolean;
  execution_performed: boolean;
  executor_available: boolean;
  can_execute: boolean;
};
type PreflightResponse = {
  approval: ApprovalRecord;
  verification: { valid: boolean; reason: string; consumed: boolean };
  preflight: {
    status: "ready" | "blocked";
    reasons: string[];
    attempt: Attempt | null;
    proposal: { domain: string; service: string; entity_id: string; service_data: Record<string, unknown> } | null;
    dry_run: boolean;
    approval_consumed: boolean;
    execution_performed: boolean;
    executor_available: boolean;
    can_execute: boolean;
  };
  dry_run: boolean;
  approval_consumed: boolean;
  execution_performed: boolean;
  executor_available: boolean;
  can_execute: boolean;
};

type CancelResponse = {
  attempt: Attempt;
  dry_run: boolean;
  approval_consumed: boolean;
  execution_performed: boolean;
  executor_available: boolean;
  can_execute: boolean;
};

const REASON_LABELS: Record<string, string> = {
  approval_invalid: "Signed approval už není platný pro aktuální snapshot.",
  entity_required: "Approval nemá použitelnou vazbu na entitu.",
  unsupported_entity_domain: "Doména entity zatím nemá bezpečně definovanou start akci.",
};

function formatIso(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("cs-CZ", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function shortHash(value: string): string { return value.length > 22 ? `${value.slice(0, 11)}…${value.slice(-7)}` : value; }

export function ExecutionPreflightCard({ hass, entryId }: { hass?: HomeAssistant; entryId: string | null }) {
  const [profiles, setProfiles] = useState<LoadProfile[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [approvalId, setApprovalId] = useState("");
  const [status, setStatus] = useState("Načítám dry-run preflight…");
  const [result, setResult] = useState<PreflightResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [busyAttempt, setBusyAttempt] = useState<string | null>(null);

  const activeApprovals = useMemo(() => approvals.filter((record) => record.status === "approved"), [approvals]);
  const profileName = (profileId: string) => profiles.find((profile) => profile.profile_id === profileId)?.name ?? profileId;

  const refresh = async () => {
    if (!hass || !entryId) return;
    try {
      const [profileResponse, approvalResponse, attemptResponse] = await Promise.all([
        callHomeAssistantWs<ProfilesResponse>(hass, { type: "frakon_energy/load_profiles/list", entry_id: entryId }),
        callHomeAssistantWs<ApprovalListResponse>(hass, { type: "frakon_energy/load_execution/approval_list", entry_id: entryId }),
        callHomeAssistantWs<AttemptsResponse>(hass, { type: "frakon_energy/load_execution/attempts/list", entry_id: entryId }),
      ]);
      setProfiles(profileResponse.profiles);
      setApprovals(approvalResponse.approvals);
      setAttempts(attemptResponse.attempts);
      const active = approvalResponse.approvals.filter((record) => record.status === "approved");
      if (!active.some((record) => record.approval.approval_id === approvalId)) setApprovalId(active[0]?.approval.approval_id ?? "");
      setStatus(`Runtime: ${active.length} aktivních approvalů · ${attemptResponse.attempts.length} attemptů`);
    } catch (error) {
      setStatus(`Preflight je admin-only nebo není dostupný: ${String(error)}`);
    }
  };

  useEffect(() => { void refresh(); }, [hass, entryId]);

  const runPreflight = async () => {
    if (!hass || !entryId || !approvalId) return;
    setBusy(true);
    setResult(null);
    setStatus("Ověřuji HMAC approval, snapshot, policy a připravuji idempotentní dry-run attempt…");
    try {
      const response = await callHomeAssistantWs<PreflightResponse>(hass, {
        type: "frakon_energy/load_execution/preflight",
        entry_id: entryId,
        approval_id: approvalId,
      });
      setResult(response);
      await refresh();
      setStatus(response.preflight.status === "ready" ? "Dry-run preflight připraven. Navrhovaná HA služba nebyla zavolána." : "Preflight byl fail-closed zablokován.");
    } catch (error) {
      setStatus(`Preflight chyba: ${String(error)}`);
    } finally {
      setBusy(false);
    }
  };

  const cancelAttempt = async (attempt: Attempt) => {
    if (!hass || !entryId) return;
    setBusyAttempt(attempt.attempt_id);
    try {
      await callHomeAssistantWs<CancelResponse>(hass, {
        type: "frakon_energy/load_execution/attempts/cancel",
        entry_id: entryId,
        attempt_id: attempt.attempt_id,
      });
      await refresh();
      setStatus("Prepared attempt byl zrušen. Nic nebylo spuštěno.");
    } catch (error) {
      setStatus(`Attempt nelze zrušit: ${String(error)}`);
    } finally {
      setBusyAttempt(null);
    }
  };

  return <article className="chart-card execution-preflight-card">
    <div className="execution-preflight-header"><div><span className="eyebrow">Execution Preflight</span><h2>Idempotentní dry-run před budoucím executorem</h2></div><div className="execution-preflight-lock"><span>Home Assistant service call</span><b>zakázán v této vrstvě</b><small>dry_run = true</small></div></div>
    <p className="settings-copy">Preflight znovu ověří serverový signed approval a teprve potom připraví jeden idempotentní attempt. Z entity odvodí pouze návrh služby. Zatím jsou záměrně podporované jen reverzibilní start akce <b>switch.turn_on</b> a <b>input_boolean.turn_on</b>; climate, water_heater, number, select a button se fail-closed blokují.</p>

    <div className="execution-preflight-controls"><label>Aktivní signed approval<select value={approvalId} onChange={(e) => { setApprovalId(e.target.value); setResult(null); }} disabled={!activeApprovals.length}><option value="">{activeApprovals.length ? "Vyber approval" : "Žádný aktivní approval"}</option>{activeApprovals.map((record) => <option key={record.approval.approval_id} value={record.approval.approval_id}>{profileName(record.profile_id)} · {record.approval.approval_id.slice(0, 10)}…</option>)}</select></label><button className="primary-action" onClick={runPreflight} disabled={!approvalId || busy || !hass || !entryId}>{busy ? "Kontroluji…" : "Spustit Dry-run preflight"}</button><button className="secondary-action" onClick={() => void refresh()} disabled={!hass || !entryId}>Obnovit</button><span>{status}</span></div>

    {result ? <section className={`execution-preflight-result execution-preflight-result--${result.preflight.status}`}><div className="execution-preflight-result__top"><div><span className="eyebrow">Verdikt</span><strong>{result.preflight.status === "ready" ? "Prepared · dry-run only" : "Blocked"}</strong></div><b>approval verify: {result.verification.reason}</b></div>{result.preflight.proposal ? <div className="execution-preflight-proposal"><div><span>Navrhovaná služba</span><b>{result.preflight.proposal.domain}.{result.preflight.proposal.service}</b></div><div><span>Entita</span><b>{result.preflight.proposal.entity_id}</b></div><div><span>Service data</span><code>{JSON.stringify(result.preflight.proposal.service_data)}</code></div></div> : null}{result.preflight.attempt ? <div className="execution-preflight-attempt"><span>Attempt</span><b>{result.preflight.attempt.attempt_id}</b><small>idempotency {shortHash(result.preflight.attempt.idempotency_key)} · stav {result.preflight.attempt.state}</small></div> : null}{result.preflight.reasons.length ? <div className="execution-preflight-reasons">{result.preflight.reasons.map((reason) => <span key={reason}>{REASON_LABELS[reason] ?? reason}</span>)}</div> : null}<small>approval_consumed={String(result.approval_consumed)} · execution_performed={String(result.execution_performed)} · executor_available={String(result.executor_available)} · can_execute={String(result.can_execute)}</small></section> : null}

    <div className="execution-attempt-runtime"><div className="execution-attempt-runtime__header"><div><span className="eyebrow">Attempt Runtime</span><h3>Připravené a zrušené dry-run attempty</h3></div><span>{attempts.length} záznamů</span></div>{attempts.length === 0 ? <div className="execution-attempt-empty">Zatím nebyl připraven žádný attempt. Po restartu runtime ledger začíná prázdný.</div> : <div className="execution-attempt-list">{attempts.slice().reverse().map((attempt) => <section className={`execution-attempt-item execution-attempt-item--${attempt.state}`} key={attempt.attempt_id}><div className="execution-attempt-item__top"><div><span>{profileName(attempt.profile_id)}</span><strong>{attempt.attempt_id}</strong><small>{attempt.action} · {attempt.entity_id}</small></div><b>{attempt.state}</b></div><div className="execution-attempt-meta"><div><span>Approval</span><b>{attempt.approval_id}</b></div><div><span>Idempotency</span><code>{shortHash(attempt.idempotency_key)}</code></div><div><span>Plán</span><b>{formatIso(attempt.planned_starts_at)} → {formatIso(attempt.planned_ends_at)}</b></div><div><span>Snapshot</span><code>{shortHash(attempt.snapshot_digest)}</code></div></div>{attempt.state === "prepared" ? <button className="danger-action" onClick={() => cancelAttempt(attempt)} disabled={busyAttempt === attempt.attempt_id}>{busyAttempt === attempt.attempt_id ? "Ruším…" : "Zrušit prepared attempt"}</button> : null}<small>execution_performed={String(attempt.execution_performed)} · service call nebyl proveden</small></section>)}</div>}</div>
  </article>;
}
