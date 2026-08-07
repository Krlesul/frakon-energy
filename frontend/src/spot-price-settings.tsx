import React, { useEffect, useState } from "react";
import { callHomeAssistantWs, type HomeAssistant } from "./home-assistant";
import { LoadExecutionPolicyCard } from "./load-execution-policy-card";
import { LoadProfilesCard } from "./load-profiles-card";
import "./load-execution-policy-card.css";
import "./load-profiles-card.css";

type FxMode = "auto" | "manual";
type Settings = { eur_czk: number; fx_mode: FxMode; supplier_fee_czk_kwh: number; variable_additions_czk_kwh: number; vat_percent: number };
const defaults: Settings = { eur_czk: 25, fx_mode: "auto", supplier_fee_czk_kwh: 0, variable_additions_czk_kwh: 0, vat_percent: 21 };

export function SpotPriceSettingsCard({ hass, entryId }: { hass?: HomeAssistant; entryId: string | null }) {
  const [values, setValues] = useState<Settings>(defaults);
  const [status, setStatus] = useState("Načítám…");

  useEffect(() => {
    if (!entryId || !hass) { setStatus("Čekám na Home Assistant"); return; }
    callHomeAssistantWs<Settings>(hass, { type: "frakon_energy/spot_price_settings/get", entry_id: entryId })
      .then((value) => { setValues(value); setStatus("Uloženo"); })
      .catch((error) => setStatus(`Chyba: ${String(error)}`));
  }, [entryId, hass]);

  const setNumber = (key: Exclude<keyof Settings, "fx_mode">, value: string) => setValues((current) => ({ ...current, [key]: Number(value) }));
  const save = async () => {
    if (!entryId || !hass) return;
    setStatus("Ukládám…");
    try {
      const saved = await callHomeAssistantWs<Settings>(hass, { type: "frakon_energy/spot_price_settings/set", entry_id: entryId, ...values });
      setValues(saved);
      setStatus("Uloženo");
      window.dispatchEvent(new Event("frakon-energy-hass-updated"));
    } catch (error) {
      setStatus(`Chyba: ${String(error)}`);
    }
  };

  return <>
    <article className="chart-card"><span className="eyebrow">Spotová cena</span><h2>Výpočet výsledné Kč/kWh</h2><p className="settings-copy">Tyto hodnoty se použijí pro každý 15minutový interval OTE. V automatickém režimu používá FRAKON oficiální kurz EUR/CZK z ČNB; uložený ruční kurz zůstává bezpečným fallbackem.</p><div className="spot-settings-grid"><label>Kurz EUR/CZK<select value={values.fx_mode} onChange={(e) => setValues((current) => ({ ...current, fx_mode: e.target.value as FxMode }))}><option value="auto">Automaticky · ČNB</option><option value="manual">Ručně</option></select></label><label>Ruční EUR/CZK<input type="number" step="0.01" value={values.eur_czk} disabled={values.fx_mode === "auto"} onChange={(e) => setNumber("eur_czk", e.target.value)} /><small>{values.fx_mode === "auto" ? "Použije se jen při nedostupnosti ČNB." : "Tento kurz bude použit pro výpočet spotu."}</small></label><label>Přirážka dodavatele · Kč/kWh<input type="number" step="0.001" value={values.supplier_fee_czk_kwh} onChange={(e) => setNumber("supplier_fee_czk_kwh", e.target.value)} /></label><label>Další variabilní složky · Kč/kWh<input type="number" step="0.001" value={values.variable_additions_czk_kwh} onChange={(e) => setNumber("variable_additions_czk_kwh", e.target.value)} /></label><label>DPH · %<input type="number" step="0.1" value={values.vat_percent} onChange={(e) => setNumber("vat_percent", e.target.value)} /></label></div><div className="spot-settings-actions"><button className="primary-action" onClick={save} disabled={!entryId || !hass}>Uložit výpočet spotu</button><span>{status}</span></div></article>
    <LoadProfilesCard hass={hass} entryId={entryId} />
    <LoadExecutionPolicyCard hass={hass} entryId={entryId} />
  </>;
}
