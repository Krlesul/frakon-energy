import React, { useEffect, useMemo, useState } from "react";

type CostBreakdown = { wholesale_czk_kwh: number; supplier_fee_czk_kwh: number; variable_additions_czk_kwh: number; vat_czk_kwh: number; total_czk_kwh: number; eur_czk: number };
type Interval = { starts_at: string; ends_at: string; price_eur_mwh: number; price_czk_kwh: number; wholesale_czk_kwh: number; cost_breakdown: CostBreakdown };
type DayBucket = { date: string; available: boolean; interval_count: number; intervals: Interval[]; minimum_czk_kwh: number | null; maximum_czk_kwh: number | null; average_czk_kwh: number | null; has_negative_price: boolean };
type ExchangeRate = { pair: string; rate: number; mode: "auto" | "manual"; source: string; fetched_at: string | null; fallback_used: boolean; error: string | null };
type Payload = { today: DayBucket; tomorrow: DayBucket; provider: string; stale: boolean; fallback_used: boolean; exchange_rate: ExchangeRate };
type Hass = { callWS<T>(message: Record<string, unknown>): Promise<T> };
const czk = (value: number) => `${value.toLocaleString("cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 3 })} Kč/kWh`;

function useSpotPrices() {
  const [data, setData] = useState<Payload | null>(null); const [error, setError] = useState<string | null>(null);
  useEffect(() => { let active = true; const load = async () => { const hass = (window as any).__FRAKON_ENERGY_HASS__ as Hass | undefined; if (!hass) return; try { const result = await hass.callWS<Payload>({ type: "frakon_energy/spot_prices/get" }); if (active) { setData(result); setError(null); } } catch (err) { if (active) setError(err instanceof Error ? err.message : String(err)); } }; load(); const refresh = window.setInterval(load, 15 * 60 * 1000); window.addEventListener("frakon-energy-hass-updated", load); return () => { active = false; window.clearInterval(refresh); window.removeEventListener("frakon-energy-hass-updated", load); }; }, []);
  return { data, error };
}

function DayChart({ day, active }: { day: DayBucket; active: boolean }) {
  const [inspect, setInspect] = useState<number | null>(null); const values = day.intervals.map(item => item.price_czk_kwh); const min = Math.min(0, ...(values.length ? values : [0])); const max = Math.max(1, ...(values.length ? values : [1])); const range = max - min || 1;
  const points = useMemo(() => day.intervals.map((item, index) => `${day.intervals.length <= 1 ? 0 : (index / (day.intervals.length - 1)) * 100},${92 - ((item.price_czk_kwh - min) / range) * 82}`).join(" "), [day.intervals, min, range]);
  if (!day.available) return <div className="spot-empty"><b>Ceny zatím nejsou zveřejněné</b><span>FRAKON je načte automaticky, jakmile budou dostupné.</span></div>;
  const inspected = inspect === null ? null : day.intervals[Math.max(0, Math.min(day.intervals.length - 1, inspect))]; const currentIndex = active ? day.intervals.findIndex(item => Date.now() >= new Date(item.starts_at).getTime() && Date.now() < new Date(item.ends_at).getTime()) : -1;
  return <div className="spot-chart" onPointerMove={event => { const rect = event.currentTarget.getBoundingClientRect(); setInspect(Math.round(((event.clientX - rect.left) / rect.width) * (day.intervals.length - 1))); }} onPointerLeave={() => setInspect(null)}>
    <div className="spot-axis"><span>{max.toFixed(2)} Kč</span><span>{((max + min) / 2).toFixed(2)} Kč</span><span>{min.toFixed(2)} Kč</span></div>
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label={`Spotové ceny ${day.date}`}><line x1="0" y1={`${92 - ((0 - min) / range) * 82}`} x2="100" y2={`${92 - ((0 - min) / range) * 82}`} className="spot-zero"/><polyline points={points} className="spot-line"/></svg>
    {currentIndex >= 0 ? <i className="spot-now" style={{ left: `${(currentIndex / Math.max(1, day.intervals.length - 1)) * 100}%` }} /> : null}
    {inspected ? <div className="spot-tooltip" style={{ left: `${(inspect! / Math.max(1, day.intervals.length - 1)) * 100}%` }}><b>{new Date(inspected.starts_at).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" })} · {czk(inspected.price_czk_kwh)}</b><span>OTE {inspected.price_eur_mwh.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} €/MWh · silová {czk(inspected.wholesale_czk_kwh)}</span><span>Dodavatel {czk(inspected.cost_breakdown.supplier_fee_czk_kwh)} · další {czk(inspected.cost_breakdown.variable_additions_czk_kwh)} · DPH {czk(inspected.cost_breakdown.vat_czk_kwh)}</span></div> : null}
    <div className="spot-hours">{[0, 6, 12, 18, 24].map(hour => <span key={hour}>{String(hour).padStart(2, "0")}</span>)}</div>
  </div>;
}

export function SpotPriceCard() {
  const { data, error } = useSpotPrices(); const [dayKey, setDayKey] = useState<"today" | "tomorrow">("today"); const day = data?.[dayKey];
  const fx = data?.exchange_rate; const fxLabel = fx ? `${fx.rate.toLocaleString("cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 3 })} Kč/EUR · ${fx.source === "CNB" ? "ČNB" : fx.source === "manual" ? "ruční" : "ruční fallback"}` : null;
  return <article className="spot-price-card"><div className="spot-header"><div><span className="eyebrow">Spotový trh · OTE</span><h2>Výsledná cena elektřiny</h2></div><div className="spot-tabs"><button className={dayKey === "today" ? "active" : ""} onClick={() => setDayKey("today")}>Dnes</button><button className={dayKey === "tomorrow" ? "active" : ""} onClick={() => setDayKey("tomorrow")}>Zítra</button></div></div>
    {!data && !error ? <div className="spot-empty">Načítám spotové ceny…</div> : null}{error ? <div className="spot-empty error"><b>Spotové ceny se nepodařilo načíst</b><span>{error}</span></div> : null}
    {day ? <><div className="spot-stats"><div><span>Minimum</span><b>{day.minimum_czk_kwh === null ? "—" : czk(day.minimum_czk_kwh)}</b></div><div><span>Průměr</span><b>{day.average_czk_kwh === null ? "—" : czk(day.average_czk_kwh)}</b></div><div><span>Maximum</span><b>{day.maximum_czk_kwh === null ? "—" : czk(day.maximum_czk_kwh)}</b></div><div><span>Intervaly</span><b>{day.interval_count}</b></div></div><DayChart day={day} active={dayKey === "today"}/></> : null}
    {fx ? <div className={`spot-fx-status ${fx.fallback_used ? "fallback" : ""}`}><span>Kurz EUR/CZK</span><b>{fxLabel}</b>{fx.fallback_used ? <small>ČNB není dostupná, výpočet bezpečně používá uložený ruční kurz.</small> : fx.mode === "auto" ? <small>Automatický kurz pro přepočet OTE.</small> : <small>Ruční kurz uzamčený v nastavení.</small>}</div> : null}
    {data ? <div className="spot-footer"><span>Zdroj {data.provider} · výsledná cena dle nastavení FRAKON</span>{data.stale ? <b>Poslední známá data</b> : <b>Aktuální data</b>}</div> : null}</article>;
}
