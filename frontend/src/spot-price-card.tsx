import React, { useEffect, useMemo, useState } from "react";

type Interval = { starts_at: string; ends_at: string; price_eur_mwh: number };
type DayBucket = { date: string; available: boolean; interval_count: number; intervals: Interval[]; minimum_eur_mwh: number | null; maximum_eur_mwh: number | null; average_eur_mwh: number | null; has_negative_price: boolean };
type Payload = { today: DayBucket; tomorrow: DayBucket; provider: string; stale: boolean; fallback_used: boolean };

type Hass = { callWS<T>(message: Record<string, unknown>): Promise<T> };

function useSpotPrices() {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    const load = async () => {
      const hass = (window as any).__FRAKON_ENERGY_HASS__ as Hass | undefined;
      if (!hass) return;
      try {
        const result = await hass.callWS<Payload>({ type: "frakon_energy/spot_prices/get" });
        if (active) { setData(result); setError(null); }
      } catch (err) { if (active) setError(err instanceof Error ? err.message : String(err)); }
    };
    load();
    const refresh = window.setInterval(load, 15 * 60 * 1000);
    window.addEventListener("frakon-energy-hass-updated", load);
    return () => { active = false; window.clearInterval(refresh); window.removeEventListener("frakon-energy-hass-updated", load); };
  }, []);
  return { data, error };
}

function DayChart({ day, active }: { day: DayBucket; active: boolean }) {
  const [inspect, setInspect] = useState<number | null>(null);
  const values = day.intervals.map((item) => item.price_eur_mwh);
  const min = Math.min(0, ...(values.length ? values : [0]));
  const max = Math.max(1, ...(values.length ? values : [1]));
  const range = max - min || 1;
  const points = useMemo(() => day.intervals.map((item, index) => {
    const x = day.intervals.length <= 1 ? 0 : (index / (day.intervals.length - 1)) * 100;
    const y = 92 - ((item.price_eur_mwh - min) / range) * 82;
    return `${x},${y}`;
  }).join(" "), [day.intervals, min, range]);
  if (!day.available) return <div className="spot-empty"><b>Ceny zatím nejsou zveřejněné</b><span>FRAKON je načte automaticky, jakmile budou dostupné.</span></div>;
  const inspected = inspect === null ? null : day.intervals[Math.max(0, Math.min(day.intervals.length - 1, inspect))];
  const currentIndex = active ? day.intervals.findIndex((item) => Date.now() >= new Date(item.starts_at).getTime() && Date.now() < new Date(item.ends_at).getTime()) : -1;
  return <div className="spot-chart" onPointerMove={(event) => { const rect = event.currentTarget.getBoundingClientRect(); setInspect(Math.round(((event.clientX - rect.left) / rect.width) * (day.intervals.length - 1))); }} onPointerLeave={() => setInspect(null)}>
    <div className="spot-axis"><span>{Math.round(max)} €</span><span>{Math.round((max + min) / 2)} €</span><span>{Math.round(min)} €</span></div>
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label={`Spotové ceny ${day.date}`}><line x1="0" y1={`${92 - ((0 - min) / range) * 82}`} x2="100" y2={`${92 - ((0 - min) / range) * 82}`} className="spot-zero"/><polyline points={points} className="spot-line"/></svg>
    {currentIndex >= 0 ? <i className="spot-now" style={{ left: `${(currentIndex / Math.max(1, day.intervals.length - 1)) * 100}%` }} /> : null}
    {inspected ? <div className="spot-tooltip" style={{ left: `${(inspect! / Math.max(1, day.intervals.length - 1)) * 100}%` }}><b>{new Date(inspected.starts_at).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" })}</b><span>{inspected.price_eur_mwh.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} €/MWh</span></div> : null}
    <div className="spot-hours">{[0, 6, 12, 18, 24].map(hour => <span key={hour}>{String(hour).padStart(2, "0")}</span>)}</div>
  </div>;
}

export function SpotPriceCard() {
  const { data, error } = useSpotPrices();
  const [dayKey, setDayKey] = useState<"today" | "tomorrow">("today");
  const day = data?.[dayKey];
  return <article className="spot-price-card">
    <div className="spot-header"><div><span className="eyebrow">Spotový trh · OTE</span><h2>Cena elektřiny</h2></div><div className="spot-tabs"><button className={dayKey === "today" ? "active" : ""} onClick={() => setDayKey("today")}>Dnes</button><button className={dayKey === "tomorrow" ? "active" : ""} onClick={() => setDayKey("tomorrow")}>Zítra</button></div></div>
    {!data && !error ? <div className="spot-empty">Načítám spotové ceny…</div> : null}
    {error ? <div className="spot-empty error"><b>Spotové ceny se nepodařilo načíst</b><span>{error}</span></div> : null}
    {day ? <><div className="spot-stats"><div><span>Minimum</span><b>{day.minimum_eur_mwh === null ? "—" : `${day.minimum_eur_mwh.toFixed(2)} €`}</b></div><div><span>Průměr</span><b>{day.average_eur_mwh === null ? "—" : `${day.average_eur_mwh.toFixed(2)} €`}</b></div><div><span>Maximum</span><b>{day.maximum_eur_mwh === null ? "—" : `${day.maximum_eur_mwh.toFixed(2)} €`}</b></div><div><span>Intervaly</span><b>{day.interval_count}</b></div></div><DayChart day={day} active={dayKey === "today"}/></> : null}
    {data ? <div className="spot-footer"><span>Zdroj {data.provider}</span>{data.stale ? <b>Poslední známá data</b> : <b>Aktuální data</b>}</div> : null}
  </article>;
}
