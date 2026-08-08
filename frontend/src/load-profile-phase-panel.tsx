import React, { useEffect, useState } from "react";
import { callHomeAssistantWs, type HomeAssistant } from "./home-assistant";

type PhaseTopology = "unknown" | "single_phase" | "three_phase";
type Profile = {
  profile_id: string;
  name: string;
  phase_topology: PhaseTopology;
  phase_current_l1_a?: number | null;
  phase_current_l2_a?: number | null;
  phase_current_l3_a?: number | null;
  phase_model_ready?: boolean;
};
type ProfilesResponse = { profiles: Profile[] };
type PhaseValue = {
  current_a: number;
  planned_current_a: number;
  projected_current_a: number;
  max_current_a: number;
  projected_headroom_a: number;
  projected_over_limit_a: number;
  over_limit: boolean;
};
type Projection = {
  profile_id: string;
  status: string;
  can_evaluate: boolean;
  phase_topology: PhaseTopology;
  phases: Record<string, PhaseValue>;
  over_limit_phases: string[];
  worst_phase: string | null;
  reason: string;
  read_only: boolean;
  execution_guard_active: boolean;
};

type Draft = {
  topology: PhaseTopology;
  l1: string;
  l2: string;
  l3: string;
};

const EMPTY: Draft = { topology: "unknown", l1: "", l2: "", l3: "" };
const LABELS: Record<PhaseTopology, string> = {
  unknown: "Neurčeno",
  single_phase: "1 fáze",
  three_phase: "3 fáze",
};

function current(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function draftFor(profile: Profile): Draft {
  return {
    topology: profile.phase_topology ?? "unknown",
    l1: profile.phase_current_l1_a?.toString() ?? "",
    l2: profile.phase_current_l2_a?.toString() ?? "",
    l3: profile.phase_current_l3_a?.toString() ?? "",
  };
}

export function LoadProfilePhasePanel({ hass, entryId }: { hass?: HomeAssistant; entryId: string | null }) {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [projection, setProjection] = useState<Projection | null>(null);
  const [status, setStatus] = useState("Načítám fázové profily…");

  const load = async () => {
    if (!hass || !entryId) return;
    try {
      const response = await callHomeAssistantWs<ProfilesResponse>(hass, { type: "frakon_energy/load_profiles/list", entry_id: entryId });
      setProfiles(response.profiles);
      setSelectedId((old) => old || response.profiles[0]?.profile_id || "");
      setStatus(response.profiles.length ? "Fázové profily načteny" : "Nejdřív vytvoř profil spotřebiče výše.");
    } catch (error) {
      setStatus(`Chyba: ${String(error)}`);
    }
  };

  useEffect(() => { void load(); }, [hass, entryId]);
  useEffect(() => {
    const profile = profiles.find((item) => item.profile_id === selectedId);
    setDraft(profile ? draftFor(profile) : EMPTY);
    setProjection(null);
  }, [profiles, selectedId]);

  const save = async () => {
    if (!hass || !entryId) return;
    const profile = profiles.find((item) => item.profile_id === selectedId);
    if (!profile) return;
    const l1 = current(draft.l1); const l2 = current(draft.l2); const l3 = current(draft.l3);
    const count = [l1, l2, l3].filter((value) => value !== undefined).length;
    if (draft.topology === "unknown" && count !== 0) { setStatus("U neurčené topologie nesmí být zadán žádný proud."); return; }
    if (draft.topology === "single_phase" && count !== 1) { setStatus("U 1f profilu zadej právě jednu fázi L1/L2/L3."); return; }
    if (draft.topology === "three_phase" && count !== 3) { setStatus("U 3f profilu zadej proud L1, L2 i L3."); return; }
    setStatus("Ukládám fázový model…");
    try {
      const full = await callHomeAssistantWs<any>(hass, { type: "frakon_energy/load_profiles/list", entry_id: entryId });
      const source = full.profiles.find((item: any) => item.profile_id === selectedId);
      if (!source) throw new Error("Profil už neexistuje");
      const message: Record<string, unknown> = {
        type: "frakon_energy/load_profiles/upsert", entry_id: entryId,
        profile_id: source.profile_id, name: source.name, kind: source.kind,
        duration_minutes: source.duration_minutes, power_kw: source.power_kw,
        enabled: source.enabled, phase_topology: draft.topology,
      };
      if (source.entity_id) message.entity_id = source.entity_id;
      if (l1 !== undefined) message.phase_current_l1_a = l1;
      if (l2 !== undefined) message.phase_current_l2_a = l2;
      if (l3 !== undefined) message.phase_current_l3_a = l3;
      await callHomeAssistantWs(hass, message);
      await load();
      setStatus("Fázový model uložen. Výkon kW nebyl použit k odhadu proudů.");
    } catch (error) { setStatus(`Chyba: ${String(error)}`); }
  };

  const preview = async () => {
    if (!hass || !entryId || !selectedId) return;
    setStatus("Počítám read-only fázovou projekci…");
    try {
      const value = await callHomeAssistantWs<Projection>(hass, { type: "frakon_energy/load_profiles/phase_preview", entry_id: entryId, profile_id: selectedId });
      setProjection(value);
      setStatus(value.reason);
    } catch (error) { setProjection(null); setStatus(`Chyba: ${String(error)}`); }
  };

  const selected = profiles.find((item) => item.profile_id === selectedId);
  return <section className="load-profile-phase-panel">
    <span className="eyebrow">Fázový model spotřebiče</span>
    <h3>Explicitní L1 / L2 / L3</h3>
    <p className="settings-copy">Proudy se nikdy neodvozují z celkového výkonu. Zadej skutečné elektrické zapojení spotřebiče. Projekce je pouze diagnostická a sama nic nespustí ani nezablokuje.</p>
    <div className="load-profile-form">
      <label>Profil<select value={selectedId} onChange={(e) => setSelectedId(e.target.value)}><option value="">Vyber profil</option>{profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.name}</option>)}</select></label>
      <label>Topologie<select value={draft.topology} disabled={!selected} onChange={(e) => setDraft({ topology: e.target.value as PhaseTopology, l1: "", l2: "", l3: "" })}>{(Object.keys(LABELS) as PhaseTopology[]).map((value) => <option key={value} value={value}>{LABELS[value]}</option>)}</select></label>
      <label>L1 · A<input type="number" min="0.01" step="0.1" value={draft.l1} disabled={!selected || draft.topology === "unknown"} onChange={(e) => setDraft((old) => ({ ...old, l1: e.target.value }))} /></label>
      <label>L2 · A<input type="number" min="0.01" step="0.1" value={draft.l2} disabled={!selected || draft.topology === "unknown"} onChange={(e) => setDraft((old) => ({ ...old, l2: e.target.value }))} /></label>
      <label>L3 · A<input type="number" min="0.01" step="0.1" value={draft.l3} disabled={!selected || draft.topology === "unknown"} onChange={(e) => setDraft((old) => ({ ...old, l3: e.target.value }))} /></label>
    </div>
    <div className="load-profile-actions"><button className="primary-action" disabled={!selected} onClick={save}>Uložit fázový model</button><button className="secondary-action" disabled={!selected?.phase_model_ready} onClick={preview}>Fázové preview</button><span>{status}</span></div>
    {projection ? <div className="load-profile-preview"><div><span className="eyebrow">Phase preview · {selected?.name}</span><h3>{projection.can_evaluate ? (projection.over_limit_phases.length ? `Překročení: ${projection.over_limit_phases.join(", ")}` : "Všechny fáze v diagnostickém limitu") : "Nelze vyhodnotit"}</h3></div>{projection.can_evaluate ? <div className="load-profile-preview__metrics">{["L1", "L2", "L3"].map((phase) => { const value = projection.phases[phase]; return <div key={phase}><span>{phase} · nyní + profil → po startu</span><b>{value.current_a.toLocaleString("cs-CZ")} + {value.planned_current_a.toLocaleString("cs-CZ")} → {value.projected_current_a.toLocaleString("cs-CZ")} A</b><small>{value.over_limit ? `Nad limitem o ${value.projected_over_limit_a.toLocaleString("cs-CZ")} A` : `Rezerva ${value.projected_headroom_a.toLocaleString("cs-CZ")} A`}</small></div>; })}</div> : null}<p>{projection.reason} Read-only; Execution Guard tímto není aktivován.</p></div> : null}
  </section>;
}
