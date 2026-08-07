import React, { useEffect, useState } from "react";

type Settings = { eur_czk: number; supplier_fee_czk_kwh: number; variable_additions_czk_kwh: number; vat_percent: number };
type Hass = { callWS<T>(message: Record<string, unknown>): Promise<T> };
const defaults: Settings = { eur_czk: 25, supplier_fee_czk_kwh: 0, variable_additions_czk_kwh: 0, vat_percent: 21 };

export function SpotPriceSettingsCard({ entryId }: { entryId: string | null }) {
  const [values, setValues] = useState<Settings>(defaults);
  const [status, setStatus] = useState("Načítám…");
  const hass = () => (window as any).__FRAKON_ENERGY_HASS__ as Hass | undefined;
  useEffect(() => { if (!entryId || !hass()) { setStatus("Čekám na Home Assistant"); return; } hass()!.callWS<Settings>({ type: "frakon_energy/spot_price_settings/get", entry_id: entryId }).then(v => { setValues(v); setStatus("Uloženo"); }).catch(e => setStatus(String(e))); }, [entryId]);
  const set = (key: keyof Settings, value: string) => setValues(current => ({ ...current, [key]: Number(value) }));
  const save = async () => { if (!entryId || !hass()) return; setStatus("Ukládám…"); try { const saved = await hass()!.callWS<Settings>({ type: "frakon_energy/spot_price_settings/set", entry_id: entryId, ...values }); setValues(saved); setStatus("Uloženo"); window.dispatchEvent(new Event("frakon-energy-hass-updated")); } catch (e) { setStatus(`Chyba: ${String(e)}`); } };
  return <article className="chart-card"><span className="eyebrow">Spotová cena</span><h2>Výpočet výsledné Kč/kWh</h2><p className="settings-copy">Tyto hodnoty se použijí pro každý 15minutový interval OTE. Regulované složky zadávejte pouze tehdy, když znáte jejich správnou variabilní hodnotu.</p><div className="spot-settings-grid"><label>EUR/CZK<input type="number" step="0.01" value={values.eur_czk} onChange={e => set("eur_czk", e.target.value)} /></label><label>Přirážka dodavatele · Kč/kWh<input type="number" step="0.001" value={values.supplier_fee_czk_kwh} onChange={e => set("supplier_fee_czk_kwh", e.target.value)} /></label><label>Další variabilní složky · Kč/kWh<input type="number" step="0.001" value={values.variable_additions_czk_kwh} onChange={e => set("variable_additions_czk_kwh", e.target.value)} /></label><label>DPH · %<input type="number" step="0.1" value={values.vat_percent} onChange={e => set("vat_percent", e.target.value)} /></label></div><div className="spot-settings-actions"><button className="primary-action" onClick={save}>Uložit výpočet spotu</button><span>{status}</span></div></article>;
}
