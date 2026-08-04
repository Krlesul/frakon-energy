import React from "react";
import { createRoot } from "react-dom/client";
import { formatCountdown, useFrakonEnergyState } from "./home-assistant";
import "./styles.css";

const demoBilling = {
  todayKwh: 18.4,
  monthKwh: 542,
  paidAdvances: 35000,
  currentCosts: 32780,
  projectedBalance: 1590,
  monthlyAdvance: 5000,
};

type ScheduleItem = { start: string; end: string; tariff: string };
type ClockInterval = { start: string; end: string };
type PlannedAction = { time: string; icon: string; label: string; detail?: string };

const DEFAULT_NT_SCHEDULE = {
  weekday: [
    ["02:00", "05:30"],
    ["13:10", "15:25"],
    ["21:35", "23:50"],
  ],
  weekend: [
    ["03:45", "06:55"],
    ["14:45", "17:30"],
    ["21:30", "23:35"],
  ],
} as const;

const DEMO_ACTIONS: PlannedAction[] = [
  { time: "21:35", icon: "⚡", label: "Nízký tarif začíná", detail: "HDO" },
  { time: "21:36", icon: "🔥", label: "Ohřev bojleru", detail: "Připraveno pro automatizaci" },
  { time: "21:37", icon: "🚗", label: "Nabíjení Enyaqu", detail: "Připraveno pro wallbox" },
  { time: "23:45", icon: "🔥", label: "Vypnutí bojleru", detail: "Bezpečná rezerva 5 minut" },
  { time: "23:50", icon: "○", label: "Nízký tarif končí", detail: "Přechod na VT" },
];

function Metric({ label, value, suffix }: { label: string; value: string | number; suffix?: string }) {
  return (
    <article className="metric">
      <span>{label}</span>
      <strong>{value}{suffix ? <small> {suffix}</small> : null}</strong>
    </article>
  );
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

  return (
    <article className={`tariff-card ${low ? "low" : unknown ? "unknown" : "high"}`}>
      <div className="tariff-card__top">
        <div>
          <span className="eyebrow">Aktuální tarif</span>
          <h2>{state.tariff}</h2>
        </div>
        <span className="status-dot" aria-label={unknown ? "Tarif není dostupný" : low ? "Nízký tarif je aktivní" : "Vysoký tarif je aktivní"} />
      </div>
      <div className="countdown-label">{unknown ? "Čekám na data HDO" : low ? "Vypnutí NT za" : "Zapnutí NT za"}</div>
      <div className="countdown" aria-live="polite">{formatCountdown(state.countdownSeconds)}</div>
      <div className="next-change">
        {state.nextChange ? `${low ? "NT skončí" : "NT začne"} ve ${state.nextChange}` : "Čas další změny není dostupný"}
      </div>
      <div className="timeline" aria-label="Dnešní rozvrh HDO">
        {state.todaySchedule.length > 0 ? state.todaySchedule.map((item, index) => (
          <span key={`${item.start}-${index}`} className={item.tariff.toLowerCase()} style={{ flex: scheduleDuration(item.start, item.end) }} title={`${item.tariff} ${asTime(item.start)}–${asTime(item.end)}`} />
        )) : <span className="timeline-empty">Rozvrh není dostupný</span>}
      </div>
      {state.currentPrice !== null ? (
        <div className="current-price">Aktuální cena <b>{state.currentPrice.toLocaleString("cs-CZ", { minimumFractionDigits: 3, maximumFractionDigits: 3 })} Kč/kWh</b></div>
      ) : null}
    </article>
  );
}

function IntervalRows({ intervals, live = false }: { intervals: (ClockInterval | ScheduleItem)[]; live?: boolean }) {
  return (
    <div className="hdo-table" role="table">
      {intervals.map((item, index) => {
        const start = live ? asTime((item as ScheduleItem).start) : item.start;
        const end = live ? asTime((item as ScheduleItem).end) : item.end;
        const active = live && isCurrentInterval(item as ScheduleItem);
        return (
          <div className={`hdo-row ${active ? "active" : ""}`} role="row" key={`${start}-${end}-${index}`}>
            <span className="hdo-row__dot" aria-hidden="true" />
            <span className="hdo-row__label">NT {index + 1}</span>
            <strong>{start}</strong><span className="hdo-row__arrow">→</span><strong>{end}</strong>
            <span className="hdo-duration">{formatDuration(start, end)}</span>
            {active ? <span className="active-chip">Právě teď</span> : null}
          </div>
        );
      })}
    </div>
  );
}

function HdoScheduleCard() {
  const energy = useFrakonEnergyState();
  const ntFromLiveData = energy.todaySchedule.filter((item) => item.tariff === "NT");
  const todayFallback = fallbackNtIntervals();
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const tomorrowFallback = fallbackNtIntervals(tomorrow);
  const usesLiveData = ntFromLiveData.length > 0;
  const fullToday = buildFullDay(usesLiveData ? ntFromLiveData.map((item) => ({ start: asTime(item.start), end: asTime(item.end) })) : todayFallback);

  return (
    <article className="hdo-plan-card">
      <div className="hdo-plan-card__header">
        <div><span className="eyebrow">HDO plán</span><h2>Nízký tarif dnes a zítra</h2></div>
        <span className={`source-badge ${usesLiveData ? "live" : "fallback"}`}>{usesLiveData ? "Živý rozvrh" : "Výchozí plán"}</span>
      </div>

      <div className="hdo-days-grid">
        <section>
          <h3>Dnes</h3>
          <IntervalRows intervals={usesLiveData ? ntFromLiveData : todayFallback} live={usesLiveData} />
        </section>
        <section>
          <h3>Zítra</h3>
          <IntervalRows intervals={tomorrowFallback} />
        </section>
      </div>

      <details className="hdo-details">
        <summary>Dalších 24 hodin a plánované akce</summary>
        <div className="hdo-detail-grid">
          <section className="sequence-card">
            <span className="eyebrow">Tarifní sekvence</span>
            <div className="sequence-list">
              {fullToday.map((item, index) => (
                <div className={`sequence-row ${item.tariff.toLowerCase()}`} key={`${item.start}-${index}`}>
                  <span>{item.start}</span><b>{item.tariff}</b><span>{item.end}</span>
                </div>
              ))}
            </div>
          </section>
          <section className="actions-card">
            <span className="eyebrow">Plán domu</span>
            <div className="action-list">
              {DEMO_ACTIONS.map((action) => (
                <div className="action-row" key={`${action.time}-${action.label}`}>
                  <time>{action.time}</time><span className="action-icon">{action.icon}</span>
                  <div><b>{action.label}</b>{action.detail ? <small>{action.detail}</small> : null}</div>
                </div>
              ))}
            </div>
            <p className="demo-note">Akce jsou zatím návrh rozhraní. Později se načtou z automatizací Home Assistantu a FRAKONu.</p>
          </section>
        </div>
      </details>

      <div className="hdo-plan-note">Pracovní dny: 02:00–05:30, 13:10–15:25, 21:35–23:50. Víkendy a státní svátky: 03:45–06:55, 14:45–17:30, 21:30–23:35.</div>
    </article>
  );
}

function App() {
  const energy = useFrakonEnergyState();
  const meterTotal = energy.highRateKwh !== null && energy.lowRateKwh !== null ? energy.highRateKwh + energy.lowRateKwh : null;

  return (
    <main className="app-shell">
      <header className="topbar"><div><span className="brand-mark">F</span><div><h1>FRAKON Energy</h1><p>Energetický přehled domu</p></div></div><span className={energy.connected ? "online" : "online demo"}>{energy.connected ? "Online" : "Demo režim"}</span></header>
      <section className="hero-grid"><TariffCard /><article className="balance-card"><span className="eyebrow">Odhad vyúčtování</span><strong className="balance">+{demoBilling.projectedBalance.toLocaleString("cs-CZ")} Kč</strong><p>Předpokládaný přeplatek k 31. 1.</p><div className="progress"><span style={{ width: "78%" }} /></div><div className="balance-row"><span>Zaplacené zálohy</span><b>{demoBilling.paidAdvances.toLocaleString("cs-CZ")} Kč</b></div><div className="balance-row"><span>Dosavadní náklady</span><b>{demoBilling.currentCosts.toLocaleString("cs-CZ")} Kč</b></div></article></section>
      <HdoScheduleCard />
      <section className="metrics-grid"><Metric label="Spotřeba dnes" value={demoBilling.todayKwh} suffix="kWh" /><Metric label="Tento měsíc" value={demoBilling.monthKwh} suffix="kWh" /><Metric label="Měsíční záloha" value={demoBilling.monthlyAdvance.toLocaleString("cs-CZ")} suffix="Kč" /><Metric label="Baterie VisionQ" value={energy.batteryPercent?.toLocaleString("cs-CZ", { maximumFractionDigits: 1 }) ?? "—"} suffix="%" /></section>
      <section className="metrics-grid secondary"><Metric label="Elektroměr celkem" value={meterTotal?.toLocaleString("cs-CZ", { maximumFractionDigits: 3 }) ?? "—"} suffix="kWh" /><Metric label="VT celkem" value={energy.highRateKwh?.toLocaleString("cs-CZ", { maximumFractionDigits: 3 }) ?? "—"} suffix="kWh" /><Metric label="NT celkem" value={energy.lowRateKwh?.toLocaleString("cs-CZ", { maximumFractionDigits: 3 }) ?? "—"} suffix="kWh" /><Metric label="Datový zdroj" value={energy.connected ? "Home Assistant" : "Ukázková data"} /></section>
      <section className="chart-card"><div className="section-title"><div><span className="eyebrow">Spotřeba a náklady</span><h2>Průběh zúčtovacího období</h2></div><div className="segmented"><button className="active">Měsíc</button><button>Rok</button></div></div><div className="chart-placeholder"><div className="chart-line actual" /><div className="chart-line advances" /><span className="chart-caption">Skutečná data a budoucí predikce budou zřetelně oddělené.</span></div></section>
      <nav className="bottom-nav"><button className="active">Přehled</button><button>Vyúčtování</button><button>Tarify</button><button>Historie</button><button>Nastavení</button></nav>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
