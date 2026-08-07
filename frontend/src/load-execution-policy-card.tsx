import React, { useEffect, useMemo, useState } from "react";
import { callHomeAssistantWs, type HomeAssistant } from "./home-assistant";

type LoadProfile = {
  profile_id: string;
  name: string;
  duration_minutes: number;
  power_kw: number;
  enabled: boolean;
  entity_id?: string | null;
};
type ExecutionMode = "disabled" | "approval_required";
type ExecutionPolicy = {
  profile_id: string;
  mode: ExecutionMode;
  max_power_kw: number | null;
  max_duration_minutes: number | null;
  require_entity_binding: boolean;
  require_entity_available: boolean;
};
type ProfilesResponse = { profiles: LoadProfile[] };
type PoliciesResponse = {
  policies: ExecutionPolicy[];
  modes: ExecutionMode[];
  default_mode: ExecutionMode;
  executor_available: boolean;
};
type Evaluation = {
  status: "blocked" | "approval_required";
  profile_id: string;
  entity_id: string | null;
  reasons: string[];
  profile: LoadProfile;
  policy: ExecutionPolicy;
  plan: { starts_at: string; ends_at: string; average_czk_kwh: number; estimated_cost_czk: number } | null;
  entity_available: boolean | null;
  entity_state: string | null;
  execution_performed: boolean;
  executor_available: boolean;
};

const REASONS: Record<string, string> = {
  policy_disabled: "Řízení je pro profil vypnuté.",
  policy_profile_mismatch: "Policy nepatří k tomuto profilu.",
  plan_profile_mismatch: "Vypočtený plán nepatří k tomuto profilu.",
  profile_disabled: "Profil spotřebiče je vypnutý.",
  entity_binding_required: "Chybí vazba na Home Assistant entitu.",
  entity_unavailable: "Navázaná Home Assistant entita není dostupná.",
  power_limit_exceeded: "Plán překračuje maximální povolený výkon.",
  duration_limit_exceeded: "Plán překračuje maximální povolenou délku běhu.",
  plan_unavailable: "V zadaném časovém okně není dostupný vhodný plán.",
};

function toIso(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("cs-CZ", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function LoadExecutionPolicyCard({ hass, entryId }: { hass?: HomeAssistant; entryId: string | null }) {
  const [profiles, setProfiles] = useState<LoadProfile[]>([]);
  const [policies, setPolicies] = useState<ExecutionPolicy[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [mode, setMode] = useState<ExecutionMode>("disabled");
  const [maxPower, setMaxPower] = useState("");
  const [maxDuration, setMaxDuration] = useState("");
  const [requireBinding, setRequireBinding] = useState(true);
  const [requireAvailable, setRequireAvailable] = useState(true);
  const [earliestStart, setEarliestStart] = useState("");
  const [deadline, setDeadline] = useState("");
  const [status, setStatus] = useState("Načítám…");
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [evaluating, setEvaluating] = useState(false);

  const selectedProfile = useMemo(() => profiles.find((item) => item.profile_id === selectedId) ?? null, [profiles, selectedId]);

  useEffect(() => {
    if (!entryId || !hass) { setStatus("Čekám na Home Assistant"); return; }
    Promise.all([
      callHomeAssistantWs<ProfilesResponse>(hass, { type: "frakon_energy/load_profiles/list", entry_id: entryId }),
      callHomeAssistantWs<PoliciesResponse>(hass, { type: "frakon_energy/load_execution_policies/list", entry_id: entryId }),
    ]).then(([profileResponse, policyResponse]) => {
      setProfiles(profileResponse.profiles);
      setPolicies(policyResponse.policies);
      const first = profileResponse.profiles[0]?.profile_id ?? "";
      setSelectedId((current) => current || first);
      setStatus(profileResponse.profiles.length ? "Bezpečnostní policy načteny" : "Nejdřív vytvoř profil spotřebiče");
    }).catch((error) => setStatus(`Chyba: ${String(error)}`));
  }, [entryId, hass]);

  useEffect(() => {
    const profile = profiles.find((item) => item.profile_id === selectedId) ?? null;
    const policy = policies.find((item) => item.profile_id === selectedId) ?? null;
    setMode(policy?.mode ?? "disabled");
    setMaxPower(policy?.max_power_kw == null ? "" : String(policy.max_power_kw));
    setMaxDuration(policy?.max_duration_minutes == null ? "" : String(policy.max_duration_minutes));
    setRequireBinding(policy?.require_entity_binding ?? true);
    setRequireAvailable(policy?.require_entity_available ?? true);
    if (!policy && profile) {
      setMaxPower("");
      setMaxDuration("");
    }
    setEvaluation(null);
  }, [selectedId, policies, profiles]);

  const chooseMode = (value: ExecutionMode) => {
    setMode(value);
    setEvaluation(null);
    if (value === "approval_required" && selectedProfile) {
      if (!maxPower) setMaxPower(String(selectedProfile.power_kw));
      if (!maxDuration) setMaxDuration(String(selectedProfile.duration_minutes));
    }
  };

  const savePolicy = async () => {
    if (!entryId || !hass || !selectedProfile) return;
    const power = Number(maxPower);
    const duration = Number(maxDuration);
    if (mode === "approval_required" && (!Number.isFinite(power) || power <= 0 || !Number.isInteger(duration) || duration <= 0 || duration % 15 !== 0)) {
      setStatus("Pro režim se schválením nastav platný limit výkonu a délku po 15 minutách.");
      return;
    }
    setStatus("Ukládám bezpečnostní policy…");
    try {
      const message: Record<string, unknown> = {
        type: "frakon_energy/load_execution_policies/upsert",
        entry_id: entryId,
        profile_id: selectedProfile.profile_id,
        mode,
        require_entity_binding: requireBinding,
        require_entity_available: requireAvailable,
      };
      if (mode === "approval_required") {
        message.max_power_kw = power;
        message.max_duration_minutes = duration;
      }
      const response = await callHomeAssistantWs<PoliciesResponse>(hass, message);
      setPolicies(response.policies);
      setStatus(mode === "disabled" ? "Řízení zůstává vypnuté" : "Policy uložena · každé budoucí spuštění musí vyžadovat schválení");
      setEvaluation(null);
    } catch (error) {
      setStatus(`Chyba: ${String(error)}`);
    }
  };

  const resetPolicy = async () => {
    if (!entryId || !hass || !selectedProfile) return;
    setStatus("Resetuji policy…");
    try {
      const response = await callHomeAssistantWs<PoliciesResponse>(hass, {
        type: "frakon_energy/load_execution_policies/delete",
        entry_id: entryId,
        profile_id: selectedProfile.profile_id,
      });
      setPolicies(response.policies);
      setStatus("Policy resetována do bezpečného režimu disabled");
      setEvaluation(null);
    } catch (error) {
      setStatus(`Chyba: ${String(error)}`);
    }
  };

  const evaluate = async () => {
    if (!entryId || !hass || !selectedProfile) return;
    setEvaluating(true);
    setStatus("Kontroluji plán, policy a stav entity…");
    try {
      const message: Record<string, unknown> = {
        type: "frakon_energy/load_execution/evaluate_profile",
        entry_id: entryId,
        profile_id: selectedProfile.profile_id,
      };
      const earliest = toIso(earliestStart);
      const latest = toIso(deadline);
      if (earliest) message.earliest_start = earliest;
      if (latest) message.deadline = latest;
      const result = await callHomeAssistantWs<Evaluation>(hass, message);
      setEvaluation(result);
      setStatus(result.status === "approval_required" ? "Kontrola prošla · skutečné spuštění by stále vyžadovalo explicitní schválení" : "Kontrola zablokována bezpečnostní policy");
    } catch (error) {
      setEvaluation(null);
      setStatus(`Chyba: ${String(error)}`);
    } finally {
      setEvaluating(false);
    }
  };

  return <article className="chart-card execution-policy-card">
    <span className="eyebrow">Bezpečnost řízení</span>
    <h2>Policy před budoucím spuštěním</h2>
    <p className="settings-copy">Tato vrstva nic nespíná. Jen rozhodne, zda je plán zablokovaný, nebo zda může pokračovat k samostatnému explicitnímu schválení. Automatický režim zde záměrně neexistuje.</p>

    <div className="execution-policy-grid">
      <label>Profil<select value={selectedId} onChange={(e) => setSelectedId(e.target.value)} disabled={!profiles.length}>{profiles.length ? profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.name}</option>) : <option value="">Žádný profil</option>}</select></label>
      <label>Režim<select value={mode} onChange={(e) => chooseMode(e.target.value as ExecutionMode)} disabled={!selectedProfile}><option value="disabled">Vypnuto</option><option value="approval_required">Vyžadovat schválení</option></select></label>
      <label>Max. výkon · kW<input type="number" min="0.001" step="0.1" value={maxPower} disabled={mode === "disabled"} onChange={(e) => setMaxPower(e.target.value)} /></label>
      <label>Max. délka · min<input type="number" min="15" step="15" value={maxDuration} disabled={mode === "disabled"} onChange={(e) => setMaxDuration(e.target.value)} /></label>
    </div>

    <div className="execution-policy-flags">
      <label><input type="checkbox" checked={requireBinding} onChange={(e) => setRequireBinding(e.target.checked)} />Vyžadovat vazbu na HA entitu</label>
      <label><input type="checkbox" checked={requireAvailable} onChange={(e) => setRequireAvailable(e.target.checked)} />Vyžadovat dostupnou HA entitu</label>
    </div>

    {selectedProfile ? <div className="execution-policy-profile"><div><span>Profil</span><b>{selectedProfile.name}</b></div><div><span>Plánovaný výkon</span><b>{selectedProfile.power_kw.toLocaleString("cs-CZ")} kW</b></div><div><span>Plánovaná délka</span><b>{selectedProfile.duration_minutes} min</b></div><div><span>HA entita</span><b>{selectedProfile.entity_id || "není nastavena"}</b></div></div> : null}

    <div className="execution-policy-actions"><button className="primary-action" onClick={savePolicy} disabled={!selectedProfile || !hass || !entryId}>Uložit policy</button><button className="secondary-action" onClick={resetPolicy} disabled={!selectedProfile || !hass || !entryId}>Reset na disabled</button><span>{status}</span></div>

    <div className="execution-policy-check">
      <div className="execution-policy-runtime"><label>Nejdříve od<input type="datetime-local" value={earliestStart} onChange={(e) => setEarliestStart(e.target.value)} /></label><label>Hotovo nejpozději<input type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)} /></label></div>
      <button onClick={evaluate} disabled={!selectedProfile || evaluating || !hass || !entryId}>{evaluating ? "Kontroluji…" : "Zkontrolovat připravenost"}</button>
    </div>

    {evaluation ? <section className={`execution-policy-result ${evaluation.status === "approval_required" ? "is-ready" : "is-blocked"}`}>
      <div className="execution-policy-result__header"><div><span className="eyebrow">Read-only verdikt</span><h3>{evaluation.status === "approval_required" ? "Vyžaduje explicitní schválení" : "Zablokováno"}</h3></div><b>{evaluation.entity_state == null ? "Entita: —" : `Entita: ${evaluation.entity_state}`}</b></div>
      {evaluation.plan ? <div className="execution-policy-result__plan"><span>{formatTime(evaluation.plan.starts_at)} → {formatTime(evaluation.plan.ends_at)}</span><b>{evaluation.plan.average_czk_kwh.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} Kč/kWh · odhad {evaluation.plan.estimated_cost_czk.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} Kč</b></div> : null}
      {evaluation.reasons.length ? <ul>{evaluation.reasons.map((reason) => <li key={reason}>{REASONS[reason] ?? reason}</li>)}</ul> : <p>Všechny současné policy kontroly prošly. Žádná akce nebyla provedena a další vrstva musí vyžádat schválení.</p>}
      <small>execution_performed = {String(evaluation.execution_performed)} · executor_available = {String(evaluation.executor_available)}</small>
    </section> : null}
  </article>;
}
