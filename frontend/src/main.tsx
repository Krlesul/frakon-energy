import React from "react";
import { createRoot } from "react-dom/client";
import { formatCountdown, useFrakonEnergyState } from "./home-assistant";
import "./styles.css";

type ScheduleItem = { start: string; end: string; tariff: string };
type ClockInterval = { start: string; end: string };

const DEFAULT_NT_SCHEDULE = {
  weekday: [["02:00", "05:30"], ["13:10", "15:25"], ["21:35", "23:50"]],
  weekend: [["03:45", "06:55"], ["14:45", "17:30"], ["21:30", "23:35"]],
} as const;

function formatNumber(value: number | null, digits = 0): string {
  return value === null ? "—" : value.toLocaleString("cs-CZ", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function formatMoney(value: number | null): string {
  return value === null ? "—" : `${value >= 0 ? "+" : ""}${value.toLocaleString("cs-CZ", { maximumFractionDigits: 0 })} Kč`;
}

function Metric({ label, value, suffix }: { label: string; value: string; suffix?: string }) {
  return <article className="metric"><span>{label}</span><strong>{value}{suffix && value !== "—" ? <small> {suffix}</small> : null}</strong></article>;
}

function scheduleDuration(start: string, end: string): number {
  const startTime = new Date(start).getTime();
  const endTime = new Date(end).getTime();
  if (!Number.isFinite(startTime) || !Number.isFinite(endTime) || endTime <= startTime) return 1;
  return Math.max(1, (endTime - startTime) / 60000);
}

function asTime(value: string): string {
  return new Date(value).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" });
}

function isCurrentInterval(item: ScheduleItem, now = Date.now()): boolean {
  const start = new Date(item.start).getTime();
  const end = new Date(item.end).getTime();
  return Number.isFinite(start) && Number.isFinite(end) && start <= now && now < end;
}

function fallbackNtIntervals(date = new Date()): ClockInterval[] {
  const isWeekend = date.getDay() === 0 || date.getDay() === 6;
  const source = isWeekend ? DEFAULT_NT_SCHEDULE.weekend : DEFAULT_NT_SCHEDULE.weekday;
  return source.map(([start, end]) => ({ start, end }));
}

function clockMinutes(value: string): number {
  const [hours, minutes] = value.split(":").map(Number);
  return hours * 60 + minutes;
}

function buildFullDay(ntIntervals: ClockInterval[]): { start: string; end: string; tariff: "NT" | "VT" }[] {
  const sorted = [...ntIntervals].sort((a, b) => clockMinutes(a.start) - clockMinutes(b.start));
  const result: { start: string; end: string; tariff: "NT" | "VT" }[] = [];
  let cursor = "00:00";
  for (const item of sorted) {
    if (clockMinutes(cursor) < clockMinutes(item.start)) result.push({ start: cursor, end: item.start, tariff: "VT" });
    result.push({ ...item, tariff: "NT" });
    cursor = item.end;
  }
  if (clockMinutes(cursor) < 24 * 60) result.push({ start: cursor, end: "24:00", tariff: "VT" });
  return result;
}

function formatDuration(start: string, end: string): string {
  const minutes = Math.max(0, clockMinutes(end) - clockMinutes(start));
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return `${hours > 0 ? `${hours} h ` : ""}${rest.toString().padStart(2, "0")} min`;
}

function TariffCard() {
  const state = useFrakonEnergyState();
  const low = state.tariff === "NT";
  const unknown = state.tariff === "?";
  return <article className={`tariff-card ${low ? "low" : unknown ? "unknown" : "high"}`}>
    <div className="tariff-card__top"><div><span className="eyebrow">Aktuální tarif</span><h2>{state.tariff}</h2></div><span className="status-dot" /></div>
    <div className="countdown-label">{unknown ? "Čekám na data HDO" : low ? "Vypnutí NT za" : "Zapnutí NT za"}</div>
    <div className="countdown" aria-live="polite">{formatCountdown(state.countdownSeconds)}</div>
    <div className="next-change">{state.nextChange ? `${low ? "NT skončí" : "NT začne"} ve ${state.nextChange}` : "Čas další změny není dostupný"}</div>
    <div className="timeline" aria-label="Dnešní rozvrh HDO">
      {state.todaySchedule.length > 0 ? state.todaySchedule.map((item, index) => <span key={`${item.start}-${index}`} className={item.tariff.toLowerCase()} style={{ flex: scheduleDuration(item.start, item.end) }} title={`${item.tariff} ${asTime(item.start)}–${asTime(item.end)}`} />) : <span className="timeline-empty">Rozvrh není dostupný</span>}
    </div>
    {state.currentPrice !== null ? <div className="current-price">Aktuální cena <b>{formatNumber(state.currentPrice, 3)} Kč/kWh</b></div> : null}
  </article>;
}

function IntervalRows({ intervals, live = false }: { intervals: (ClockInterval | ScheduleItem)[]; live?: boolean }) {
  return <div className="hdo-table" role="table">{intervals.map((item, index) => {
    const start = live ? asTime((item as ScheduleItem).start) : item.start;
    const end = live ? asTime((item as ScheduleItem).end) : item.end;
    const active = live && isCurrentInterval(item as ScheduleItem);
    return <div className={`hdo-row ${active ? "active" : ""}`} role="row" key={`${start}-${end}-${index}`}><span className="hdo-row__dot" /><span className="hdo-row__label">NT {index + 1}</span><strong>{start}</strong><span className="hdo-row__arrow">→</span><strong>{end}</strong><span className="hdo-duration">{formatDuration(start, end)}</span>{active ? <span className="active-chip">Právě teď</span> : null}</div>;
  })}</div>;
}

function HdoScheduleCard() {
  const energy = useFrakonEnergyState();
  const todayLiveNt = energy.todaySchedule.filter((item) => item.tariff === "NT");
  const tomorrowLiveNt = energy.tomorrowSchedule.filter((item) => item.tariff === "NT");
  const todayFallback = fallbackNtIntervals();
  const tomorrowDate = new Date();
  tomorrowDate.setDate(tomorrowDate.getDate() + 1);
  const tomorrowFallback = fallbackNtIntervals(tomorrowDate);
  const todayIsLive = todayLiveNt.length > 0;
  const tomorrowIsLive = tomorrowLiveNt.length > 0;
  const fullToday = buildFullDay(todayIsLive ? todayLiveNt.map((item) => ({ start: asTime(item.start), end: asTime(item.end) })) : todayFallback);
  const sourceLabel = todayIsLive && tomorrowIsLive ? "Živý rozvrh dnes i zítra" : todayIsLive ? "Dnes živě, zítra záložně" : "Výchozí plán";
  return <article className="hdo-plan-card">
    <div className="hdo-plan-card__header"><div><span className="eyebrow">HDO plán</span><h2>Nízký tarif dnes a zítra</h2></div><span className={`source-badge ${todayIsLive ? "live" : "fallback"}`}>{sourceLabel}</span></div>
    <div className="hdo-days-grid"><section><h3>Dnes</h3><IntervalRows intervals={todayIsLive ? todayLiveNt : todayFallback} live={todayIsLive} /></section><section><h3>Zítra</h3><IntervalRows intervals={tomorrowIsLive ? tomorrowLiveNt : tomorrowFallback} live={tomorrowIsLive} /></section></div>
    <details className="hdo-details"><summary>Tarifní sekvence na celý den</summary><section className="sequence-card"><div className="sequence-list">{fullToday.map((item, index) => <div className={`sequence-row ${item.tariff.toLowerCase()}`} key={`${item.start}-${index}`}><span>{item.start}</span><b>{item.tariff}</b><span>{item.end}</span></div>)}</div></section></details>
    <div className="hdo-plan-note">Pracovní dny: 02:00–05:30, 13:10–15:25, 21:35–23:50. Víkendy a státní svátky: 03:45–06:55, 14:45–17:30, 21:30–23:35.</div>
  </article>;
}

function BillingCard() {
  const energy = useFrakonEnergyState();
  const projected = energy.projectedBalanceCzk;
  const label = projected === null ? "Predikce zatím není dostupná" : projected >= 0 ? "Předpokládaný přeplatek" : "Předpokládaný nedoplatek";
  return <article className="balance-card"><span className="eyebrow">Odhad vyúčtování</span><strong className="balance">{formatMoney(projected)}</strong><p>{label}{energy.settlementDate ? ` k ${new Date(energy.settlementDate).toLocaleDateString("cs-CZ")}` : ""}</p><div className="balance-row"><span>Zaplacené zálohy</span><b>{formatMoney(energy.paidAdvancesCzk)}</b></div><div className="balance-row"><span>Dosavadní náklady</span><b>{formatMoney(energy.accruedCostCzk)}</b></div><div className="balance-row"><span>Doporučená záloha</span><b>{energy.recommendedAdvanceCzk === null ? "—" : `${formatNumber(energy.recommendedAdvanceCzk)} Kč`}</b></div></article>;
}

function App() {
  const energy = useFrakonEnergyState();
  const meterTotal = energy.highRateKwh !== null && energy.lowRateKwh !== null ? energy.highRateKwh + energy.lowRateKwh : null;
  return <main className="app-shell">
    <header className="topbar"><div><span className="brand-mark">F</span><div><h1>FRAKON Energy</h1><p>Energetický přehled domu</p></div></div><span className={energy.connected ? "online" : "online demo"}>{energy.connected ? "Online" : "Čekám na Home Assistant"}</span></header>
    <section className="hero-grid"><TariffCard /><BillingCard /></section>
    <HdoScheduleCard />
    <section className="metrics-grid"><Metric label="Spotřeba dnes" value={formatNumber(energy.todayConsumptionKwh, 1)} suffix="kWh" /><Metric label="Tento měsíc" value={formatNumber(energy.monthConsumptionKwh, 1)} suffix="kWh" /><Metric label="Měsíční záloha" value={formatNumber(energy.monthlyAdvanceCzk)} suffix="Kč" /><Metric label="Baterie VisionQ" value={formatNumber(energy.batteryPercent, 1)} suffix="%" /></section>
    <section className="metrics-grid secondary"><Metric label="Elektroměr celkem" value={formatNumber(meterTotal, 3)} suffix="kWh" /><Metric label="VT celkem" value={formatNumber(energy.highRateKwh, 3)} suffix="kWh" /><Metric label="NT celkem" value={formatNumber(energy.lowRateKwh, 3)} suffix="kWh" /><Metric label="Průběžný rozdíl" value={formatMoney(energy.currentBalanceCzk)} /></section>
    <section className="chart-card"><div className="section-title"><div><span className="eyebrow">Spotřeba a náklady</span><h2>Průběh zúčtovacího období</h2></div></div><div className="chart-placeholder"><span className="chart-caption">Graf se zobrazí po načtení historických denních hodnot z FRAKON Energy.</span></div></section>
    <nav className="bottom-nav"><button className="active">Přehled</button><button>Vyúčtování</button><button>Tarify</button><button>Historie</button><button>Nastavení</button></nav>
  </main>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
