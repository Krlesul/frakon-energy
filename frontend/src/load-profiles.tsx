import React, { useEffect, useMemo, useState } from "react";
import { callHomeAssistantWs, type HomeAssistant } from "./home-assistant";

type LoadKind = "ev" | "boiler" | "battery" | "generic";
type LoadProfile = { profile_id: string; name: string; kind: LoadKind; duration_minutes: number; power_kw: number; enabled: boolean };
type ProfilesPayload = { entry_id: string; profiles: LoadProfile[]; kinds: LoadKind[]; read_only_execution: boolean };
type LoadPlan = { load_id: string; name: string; starts_at: string; ends_at: string; duration_minutes: number; interval_count: number; power_kw: number; average_czk_kwh: number; minimum_czk_kwh: number; maximum_czk_kwh: number; estimated_energy_kwh: number; estimated_cost_czk: number };
type PreviewPayload = { available: boolean; profile: LoadProfile; plan: LoadPlan | null; read_only: boolean };

const EMPTY_PROFILE: LoadProfile = { profile_id: "", name: "", kind: "generic", duration_minutes: 60, power_kw: 2, enabled: true };
const KIND_LABELS: Record<LoadKind, string> = { ev: "Elektromobil", boiler: "Bojler / TUV", battery: "Baterie", generic: "Obecný spotřebič" };
const money = (value: number) => `${value.toLocaleString("cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Kč`;
const price = (value: number) => `${value.toLocaleString("cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 3 })} Kč/kWh`;
const dateTime = (value: string) => new Date(value).toLocaleString("cs-CZ", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const slug = (value: string) => value.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");

export function LoadProfilesCard({ hass, entryId }: { hass?: HomeAssistant; entryId: string | null }) {
  const [profiles, setProfiles] = useState<LoadProfile[]>([]);
  const [kinds, setKinds] = useState<LoadKind[]>(["ev", "boiler", "battery", "generic"]);
  const [form, setForm] = useState<LoadProfile>(EMPTY_PROFILE);
  const [status, setStatus] = useState("Načítám…");
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewPayload | null>(null);
  const [deadline, setDeadline] = useState("");

  const load = async () => {
    if (!hass || !entryId) { setStatus("Čekám na Home Assistant"); return; }
    try {
      const result = await callHomeAssistantWs<ProfilesPayload>(hass, { type: "frakon_energy/load_profiles/list", entry_id: entryId });
      setProfiles(result.profiles);
      setKinds(result.kinds);
      setStatus("Připraveno");
    } catch (error) { setStatus(`Chyba: ${String(error)}`); }
  };

  useEffect(() => { void load(); }, [hass, entryId]);

  const update = <K extends keyof LoadProfile>(key: K, value: LoadProfile[K]) => setForm(current => ({ ...current, [key]: value }));
  const editing = useMemo(() => profiles.some(item => item.profile_id === form.profile_id), [profiles, form.profile_id]);

  const save = async () => {
    if (!hass || !entryId) return;
    const profileId = form.profile_id.trim() || slug(form.name);
    if (!profileId || !form.name.trim()) { setStatus("Vyplňte název profilu."); return; }
    setStatus("Ukládám profil…");
    try {
      const result = await callHomeAssistantWs<ProfilesPayload>(hass, {
        type: "frakon_energy/load_profiles/upsert", entry_id: entryId, profile_id: profileId, name: form.name.trim(), kind: form.kind,
        duration_minutes: form.duration_minutes, power_kw: form.power_kw, enabled: form.enabled,
      });
      setProfiles(result.profiles); setKinds(result.kinds); setForm(EMPTY_PROFILE); setStatus("Profil uložen");
    } catch (error) { setStatus(`Chyba: ${String(error)}`); }
  };

  const remove = async (profileId: string) => {
    if (!hass || !entryId) return;
    setStatus("Mažu profil…");
    try {
      const result = await callHomeAssistantWs<ProfilesPayload>(hass, { type: "frakon_energy/load_profiles/delete", entry_id: entryId, profile_id: profileId });
      setProfiles(result.profiles); if (form.profile_id === profileId) setForm(EMPTY_PROFILE); if (previewId === profileId) { setPreviewId(null); setPreview(null); } setStatus("Profil odstraněn");
    } catch (error) { setStatus(`Chyba: ${String(error)}`); }
  };

  const requestPreview = async (profile: LoadProfile) => {
    if (!hass || !entryId) return;
    setPreviewId(profile.profile_id); setPreview(null); setStatus("Počítám nejlevnější plán…");
    try {
      const message: Record<string, unknown> = { type: "frakon_energy/load_plan/preview_profile", entry_id: entryId, profile_id: profile.profile_id };
      if (deadline) message.deadline = new Date(deadline).toISOString();
      const result = await callHomeAssistantWs<PreviewPayload>(hass, message);
      setPreview(result); setStatus(result.available ? "Plán vypočten" : "V dostupných cenách není vhodné okno");
    } catch (error) { setStatus(`Chyba: ${String(error)}`); }
  };

  return <article className="chart-card load-profiles-card">
    <div className="load-profiles-header"><div><span className="eyebrow">Řízení spotřeby</span><h2>Profily flexibilních spotřebičů</h2></div><span className="read-only-badge">Pouze plánování</span></div>
    <p className="settings-copy">Uložte parametry elektromobilu, bojleru, baterie nebo jiného spotřebiče. FRAKON zatím pouze vypočítá nejlevnější čas; zařízení sám nespustí.</p>

    <div className="load-profile-form">
      <label>Název<input value={form.name} placeholder="Např. Enyaq – noční nabíjení" onChange={e => update("name", e.target.value)} /></label>
      <label>Typ<select value={form.kind} onChange={e => update("kind", e.target.value as LoadKind)}>{kinds.map(kind => <option key={kind} value={kind}>{KIND_LABELS[kind] ?? kind}</option>)}</select></label>
      <label>Doba běhu · min<input type="number" min="15" step="15" value={form.duration_minutes} onChange={e => update("duration_minutes", Number(e.target.value))} /></label>
      <label>Výkon · kW<input type="number" min="0.001" step="0.1" value={form.power_kw} onChange={e => update("power_kw", Number(e.target.value))} /></label>
      <label className="load-profile-id">ID profilu<input value={form.profile_id} placeholder="Automaticky z názvu" onChange={e => update("profile_id", e.target.value)} disabled={editing} /></label>
      <label className="load-enabled"><input type="checkbox" checked={form.enabled} onChange={e => update("enabled", e.target.checked)} /> Aktivní profil</label>
    </div>
    <div className="load-profile-actions"><button className="primary-action" onClick={save} disabled={!hass || !entryId}>{editing ? "Uložit změny" : "Přidat profil"}</button>{editing ? <button onClick={() => setForm(EMPTY_PROFILE)}>Zrušit úpravy</button> : null}<span>{status}</span></div>

    {profiles.length === 0 ? <div className="load-profile-empty">Zatím není uložen žádný flexibilní spotřebič.</div> : <div className="load-profile-list">{profiles.map(profile => <section className={`load-profile-item ${profile.enabled ? "" : "disabled"}`} key={profile.profile_id}><div className="load-profile-main"><div><span>{KIND_LABELS[profile.kind] ?? profile.kind}</span><h3>{profile.name}</h3><small>{profile.duration_minutes} min · {profile.power_kw.toLocaleString("cs-CZ")} kW · {profile.enabled ? "aktivní" : "vypnutý"}</small></div><div className="load-profile-buttons"><button onClick={() => { setForm(profile); setPreviewId(null); setPreview(null); }}>Upravit</button><button onClick={() => void requestPreview(profile)} disabled={!profile.enabled}>Najít nejlevnější čas</button><button className="danger" onClick={() => void remove(profile.profile_id)}>Smazat</button></div></div>{previewId === profile.profile_id ? <div className="load-preview"><label>Nejpozději dokončit · volitelné<input type="datetime-local" value={deadline} onChange={e => setDeadline(e.target.value)} /></label>{preview?.available && preview.plan ? <div className="load-preview-result"><div><span>Doporučený čas</span><b>{dateTime(preview.plan.starts_at)} → {dateTime(preview.plan.ends_at)}</b></div><div><span>Průměrná cena</span><b>{price(preview.plan.average_czk_kwh)}</b></div><div><span>Odhad energie</span><b>{preview.plan.estimated_energy_kwh.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} kWh</b></div><div><span>Odhad nákladů</span><b>{money(preview.plan.estimated_cost_czk)}</b></div></div> : preview && !preview.available ? <p>V aktuálně dostupných spotových intervalech nebylo nalezeno vhodné souvislé okno.</p> : <p>Výpočet probíhá nebo ještě nebyl spuštěn.</p>}</div> : null}</section>)}</div>}
  </article>;
}
