import React, { useEffect, useMemo, useState } from "react";
import { callHomeAssistantWs, type HomeAssistant } from "./home-assistant";

type ProfileKind = "ev" | "boiler" | "battery" | "generic";
type LoadProfile = {
  profile_id: string;
  name: string;
  kind: ProfileKind;
  duration_minutes: number;
  power_kw: number;
  enabled: boolean;
};
type ProfilesResponse = {
  entry_id: string;
  profiles: LoadProfile[];
  kinds: ProfileKind[];
  read_only_execution: boolean;
};
type Plan = {
  starts_at: string;
  ends_at: string;
  duration_minutes: number;
  interval_count: number;
  power_kw: number;
  average_czk_kwh: number;
  minimum_czk_kwh: number;
  maximum_czk_kwh: number;
  estimated_energy_kwh: number;
  estimated_cost_czk: number;
  read_only: boolean;
};
type PreviewResponse = {
  available: boolean;
  profile: LoadProfile;
  plan: Plan | null;
  read_only: boolean;
};

type FormState = LoadProfile;

const EMPTY_FORM: FormState = {
  profile_id: "",
  name: "",
  kind: "ev",
  duration_minutes: 60,
  power_kw: 2,
  enabled: true,
};

const KIND_LABELS: Record<ProfileKind, string> = {
  ev: "Elektromobil",
  boiler: "Bojler",
  battery: "Baterie",
  generic: "Obecná zátěž",
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

function formatPrice(value: number): string {
  return `${value.toLocaleString("cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Kč/kWh`;
}

export function LoadProfilesCard({ hass, entryId }: { hass?: HomeAssistant; entryId: string | null }) {
  const [profiles, setProfiles] = useState<LoadProfile[]>([]);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [status, setStatus] = useState("Načítám…");
  const [earliestStart, setEarliestStart] = useState("");
  const [deadline, setDeadline] = useState("");
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [previewingId, setPreviewingId] = useState<string | null>(null);

  const editing = useMemo(() => profiles.some((item) => item.profile_id === form.profile_id), [profiles, form.profile_id]);

  useEffect(() => {
    if (!entryId || !hass) {
      setStatus("Čekám na Home Assistant");
      return;
    }
    callHomeAssistantWs<ProfilesResponse>(hass, { type: "frakon_energy/load_profiles/list", entry_id: entryId })
      .then((value) => {
        setProfiles(value.profiles);
        setStatus(value.profiles.length > 0 ? "Profily načteny" : "Zatím není uložen žádný profil");
      })
      .catch((error) => setStatus(`Chyba: ${String(error)}`));
  }, [entryId, hass]);

  const resetForm = () => setForm(EMPTY_FORM);
  const edit = (profile: LoadProfile) => {
    setForm(profile);
    setPreview(null);
  };

  const save = async () => {
    if (!entryId || !hass) return;
    if (!form.profile_id.trim() || !form.name.trim()) {
      setStatus("Vyplň ID a název profilu.");
      return;
    }
    if (form.duration_minutes <= 0 || form.duration_minutes % 15 !== 0) {
      setStatus("Délka musí být násobek 15 minut.");
      return;
    }
    setStatus("Ukládám profil…");
    try {
      const response = await callHomeAssistantWs<ProfilesResponse>(hass, {
        type: "frakon_energy/load_profiles/upsert",
        entry_id: entryId,
        ...form,
      });
      setProfiles(response.profiles);
      setStatus("Profil uložen");
      resetForm();
    } catch (error) {
      setStatus(`Chyba: ${String(error)}`);
    }
  };

  const remove = async (profileId: string) => {
    if (!entryId || !hass) return;
    setStatus("Mažu profil…");
    try {
      const response = await callHomeAssistantWs<ProfilesResponse>(hass, {
        type: "frakon_energy/load_profiles/delete",
        entry_id: entryId,
        profile_id: profileId,
      });
      setProfiles(response.profiles);
      setPreview((current) => current?.profile.profile_id === profileId ? null : current);
      if (form.profile_id === profileId) resetForm();
      setStatus("Profil smazán");
    } catch (error) {
      setStatus(`Chyba: ${String(error)}`);
    }
  };

  const runPreview = async (profile: LoadProfile) => {
    if (!entryId || !hass || !profile.enabled) return;
    setPreviewingId(profile.profile_id);
    setStatus("Počítám nejlevnější plán…");
    try {
      const message: Record<string, unknown> = {
        type: "frakon_energy/load_plan/preview_profile",
        entry_id: entryId,
        profile_id: profile.profile_id,
      };
      const earliest = toIso(earliestStart);
      const latest = toIso(deadline);
      if (earliest) message.earliest_start = earliest;
      if (latest) message.deadline = latest;
      const response = await callHomeAssistantWs<PreviewResponse>(hass, message);
      setPreview(response);
      setStatus(response.available ? "Preview spočítán" : "V zadaném okně není dostupný souvislý interval");
    } catch (error) {
      setStatus(`Chyba: ${String(error)}`);
      setPreview(null);
    } finally {
      setPreviewingId(null);
    }
  };

  return <article className="chart-card load-profiles-card">
    <span className="eyebrow">Řízené spotřebiče</span>
    <h2>Profily pro levné spotové intervaly</h2>
    <p className="settings-copy">Ulož výkon a typickou délku běhu. FRAKON z profilu pouze vypočítá nejlevnější čas; zařízení se touto kartou nikdy samo nespustí.</p>

    <div className="load-profile-form">
      <label>ID profilu<input value={form.profile_id} disabled={editing} placeholder="např. enyaq-home" onChange={(e) => setForm((current) => ({ ...current, profile_id: e.target.value }))} /></label>
      <label>Název<input value={form.name} placeholder="např. Enyaq domácí nabíjení" onChange={(e) => setForm((current) => ({ ...current, name: e.target.value }))} /></label>
      <label>Typ<select value={form.kind} onChange={(e) => setForm((current) => ({ ...current, kind: e.target.value as ProfileKind }))}>{(Object.keys(KIND_LABELS) as ProfileKind[]).map((kind) => <option key={kind} value={kind}>{KIND_LABELS[kind]}</option>)}</select></label>
      <label>Výkon · kW<input type="number" min="0.001" step="0.1" value={form.power_kw} onChange={(e) => setForm((current) => ({ ...current, power_kw: Number(e.target.value) }))} /></label>
      <label>Délka · min<input type="number" min="15" step="15" value={form.duration_minutes} onChange={(e) => setForm((current) => ({ ...current, duration_minutes: Number(e.target.value) }))} /></label>
      <label className="load-profile-toggle"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm((current) => ({ ...current, enabled: e.target.checked }))} />Profil aktivní</label>
    </div>
    <div className="load-profile-actions"><button className="primary-action" disabled={!entryId || !hass} onClick={save}>{editing ? "Uložit změny" : "Přidat profil"}</button>{editing ? <button className="secondary-action" onClick={resetForm}>Zrušit úpravu</button> : null}<span>{status}</span></div>

    <div className="load-profile-runtime">
      <label>Nejdříve od<input type="datetime-local" value={earliestStart} onChange={(e) => setEarliestStart(e.target.value)} /></label>
      <label>Hotovo nejpozději<input type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)} /></label>
      <small>Časy jsou volitelné a používají se jen pro konkrétní preview.</small>
    </div>

    <div className="load-profile-list">
      {profiles.length === 0 ? <div className="load-profile-empty">Přidej první profil, například EV 11 kW na 120 minut nebo bojler 2 kW na 90 minut.</div> : profiles.map((profile) => <section className={`load-profile-item ${profile.enabled ? "" : "is-disabled"}`} key={profile.profile_id}>
        <div className="load-profile-item__main"><div><span>{KIND_LABELS[profile.kind]}</span><strong>{profile.name}</strong><small>{profile.profile_id}</small></div><div className="load-profile-spec"><b>{profile.power_kw.toLocaleString("cs-CZ")} kW</b><span>{profile.duration_minutes} min</span></div></div>
        <div className="load-profile-item__actions"><button onClick={() => runPreview(profile)} disabled={!profile.enabled || previewingId === profile.profile_id}>{previewingId === profile.profile_id ? "Počítám…" : "Spočítat preview"}</button><button onClick={() => edit(profile)}>Upravit</button><button className="danger-action" onClick={() => remove(profile.profile_id)}>Smazat</button></div>
      </section>)}
    </div>

    {preview ? <div className="load-profile-preview"><div><span className="eyebrow">Preview · {preview.profile.name}</span><h3>{preview.available && preview.plan ? `${formatTime(preview.plan.starts_at)} → ${formatTime(preview.plan.ends_at)}` : "Není dostupný vhodný interval"}</h3></div>{preview.plan ? <div className="load-profile-preview__metrics"><div><span>Průměrná cena</span><b>{formatPrice(preview.plan.average_czk_kwh)}</b></div><div><span>Rozsah ceny</span><b>{formatPrice(preview.plan.minimum_czk_kwh)} – {formatPrice(preview.plan.maximum_czk_kwh)}</b></div><div><span>Energie</span><b>{preview.plan.estimated_energy_kwh.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} kWh</b></div><div><span>Odhad ceny</span><b>{preview.plan.estimated_cost_czk.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} Kč</b></div></div> : null}<p>Read-only plán. FRAKON tímto krokem nic nezapíná ani nevypíná.</p></div> : null}
  </article>;
}
