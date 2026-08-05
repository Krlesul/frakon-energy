import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { formatCountdown, useFrakonEnergyState } from "./home-assistant";
import "./styles.css";
import "./enhancements.css";

type View = "overview" | "billing" | "tariffs" | "history" | "settings";
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

function missingReason(value: number | string | null, reason: string): React.ReactNode {
  return value === null ? <small className="missing-reason">{reason}</small> : null;
}

function Metric({ label, value, suffix, reason }: { label: string; value: string; suffix?: string; reason?: string }) {
  return <article className="metric"><span>{label}</span><strong>{value}{suffix && value !== "—" ? <small> {suffix}</small> : null}</strong>{value === "—" && reason ? <small className="missing-reason">{reason}</small> : null}</article>;
}

function fallbackNtIntervals(date = new Date()): ClockInterval[] {
  const isWeekend = date.getDay() === 0 || date.getDay() === 6;
  const source = isWeekend ? DEFAULT_NT_SCHEDULE.weekend : DEFAULT_NT_SCHEDULE.weekday;
  return source.map(([start, end]) => ({ start, end }));
}

function DataBadge({ live }: { live: boolean }) {
  return <span className={`source-badge ${live ? "live" : "fallback"}`}>{live ? "Živá data HDO" : "Náhradní plán · nepotvrzeno"}</span>;
}

function TariffHero() {
  const state = useFrakonEnergyState();
  const low = state.tariff === "NT";
  const unknown = state.tariff === "?";
  return <article className={`tariff-card ${low ? "low" : unknown ? "unknown" : "high"}`}>
    <div className="tariff-card__top"><div><span className="eyebrow">Aktuální tarif</span><h2>{state.tariff}</h2></div><span className="status-dot" /></div>
    <div className="countdown-label">{unknown ? "Čekám na živá data HDO" : low ? "Vypnutí NT za" : "Zapnutí NT za"}</div>
    <div className="countdown" aria-live="polite">{formatCountdown(state.countdownSeconds)}</div>
    <div className="next-change">{state.nextChange ? `Další změna ve ${state.nextChange}` : "Čas další změny není dostupný"}</div>
    <div className="hero-meta"><DataBadge live={state.hdoQuality === "live"} /><span>{state.lastUpdated ? `Aktualizováno ${new Date(state.lastUpdated).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" })}` : "Čas aktualizace není dostupný"}</span></div>
    {state.currentPrice !== null ? <div className="current-price">Aktuální cena <b>{formatNumber(state.currentPrice, 3)} Kč/kWh</b></div> : <div className="current-price">Aktuální cena <b>Chybí cenové nastavení</b></div>}
  </article>;
}

function BillingSummary() {
  const state = useFrakonEnergyState();
  return <article className="balance-card"><span className="eyebrow">Odhad vyúčtování</span><strong className="balance">{formatMoney(state.projectedBalanceCzk)}</strong><p>{state.projectedBalanceCzk === null ? "Predikce zatím není dostupná" : state.projectedBalanceCzk >= 0 ? "Předpokládaný přeplatek" : "Předpokládaný nedoplatek"}</p><div className="balance-row"><span>Zaplacené zálohy</span><b>{formatMoney(state.paidAdvancesCzk)}</b></div>{missingReason(state.paidAdvancesCzk, "Chybí měsíční záloha nebo začátek období")}<div className="balance-row"><span>Dosavadní náklady</span><b>{formatMoney(state.accruedCostCzk)}</b></div>{missingReason(state.accruedCostCzk, "Chybí ceny VT/NT nebo počáteční stav")}<div className="balance-row"><span>Doporučená záloha</span><b>{state.recommendedAdvanceCzk === null ? "—" : `${formatNumber(state.recommendedAdvanceCzk)} Kč`}</b></div></article>;
}

function HdoTimeline() {
  const state = useFrakonEnergyState();
  const today = state.todaySchedule.filter((item) => item.tariff === "NT");
  const intervals = today.length > 0 ? today.map((item) => ({ start: new Date(item.start).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" }), end: new Date(item.end).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" }) })) : fallbackNtIntervals();
  const toMinutes = (value: string) => { const [h, m] = value.split(":").map(Number); return h * 60 + m; };
  return <article className="hdo-plan-card"><div className="hdo-plan-card__header"><div><span className="eyebrow">HDO plán</span><h2>Nízký tarif dnes</h2></div><DataBadge live={today.length > 0} /></div><div className="day-scale"><span>00</span><span>04</span><span>08</span><span>12</span><span>16</span><span>20</span><span>24</span></div><div className="visual-timeline">{intervals.map((item, index) => <span key={index} style={{ left: `${(toMinutes(item.start) / 1440) * 100}%`, width: `${((toMinutes(item.end) - toMinutes(item.start)) / 1440) * 100}%` }} title={`${item.start}–${item.end}`} />)}</div><div className="interval-list">{intervals.map((item, index) => <div key={index}><b>NT {index + 1}</b><span>{item.start} → {item.end}</span></div>)}</div>{today.length === 0 ? <p className="fallback-warning">Používá se náhradní plán. Časy nejsou potvrzené živými daty distributora.</p> : null}</article>;
}

function Overview() {
  const state = useFrakonEnergyState();
  const meterTotal = state.highRateKwh !== null && state.lowRateKwh !== null ? state.highRateKwh + state.lowRateKwh : null;
  return <><section className="hero-grid"><TariffHero /><BillingSummary /></section><section className="metrics-grid"><Metric label="Spotřeba dnes" value={formatNumber(state.todayConsumptionKwh, 1)} suffix="kWh" reason="Chybí denní historie" /><Metric label="Náklady dnes" value="—" reason="Chybí ceny a denní historie" /><Metric label="Tento měsíc" value={formatNumber(state.monthConsumptionKwh, 1)} suffix="kWh" reason="Chybí měsíční historie" /><Metric label="Baterie VisionQ" value={formatNumber(state.batteryPercent, 1)} suffix="%" reason="Neplatná hodnota baterie" /></section><HdoTimeline /><section className="diagnostics-card"><span className="eyebrow">Technické měření</span><div className="metrics-grid secondary"><Metric label="Elektroměr celkem" value={formatNumber(meterTotal, 3)} suffix="kWh" /><Metric label="VT registr" value={formatNumber(state.highRateKwh, 3)} suffix="kWh" /><Metric label="NT registr" value={formatNumber(state.lowRateKwh, 3)} suffix="kWh" /><Metric label="Kvalita dat" value={state.hdoQuality === "live" ? "Živá" : "Fallback"} /></div></section></>;
}

function BillingView() {
  const state = useFrakonEnergyState();
  return <section className="page-grid"><BillingSummary /><article className="chart-card"><span className="eyebrow">Zúčtovací období</span><h2>{state.settlementDate ? `Do ${new Date(state.settlementDate).toLocaleDateString("cs-CZ")}` : "Není kompletně nastaveno"}</h2><div className="setup-checklist">{state.missingSetup.length === 0 ? <p>Vyúčtování je připravené.</p> : state.missingSetup.map((item) => <div key={item}><span>!</span><b>{item}</b><small>Vyplňte v možnostech integrace FRAKON Energy.</small></div>)}</div></article></section>;
}

function TariffsView() { return <><TariffHero /><HdoTimeline /></>; }

function HistoryView() { return <article className="chart-card"><span className="eyebrow">Historie</span><h2>Spotřeba a náklady</h2><div className="chart-placeholder"><span className="chart-caption">Graf se zobrazí po nasbírání historických denních hodnot.</span></div></article>; }

function SettingsView() {
  const state = useFrakonEnergyState();
  const openIntegration = () => { window.top!.location.href = "/config/integrations/integration/frakon_energy"; };
  return <section className="settings-grid"><article className="chart-card"><span className="eyebrow">Nastavení FRAKON Energy</span><h2>Dokončení vyúčtování</h2><p className="settings-copy">Pro výpočet nákladů a predikce doplňte počáteční stavy elektroměru a měsíční zálohu.</p><div className="setup-checklist">{state.missingSetup.map((item) => <div key={item}><span>!</span><b>{item}</b><small>Hodnota zatím není dostupná.</small></div>)}</div><button className="primary-action" onClick={openIntegration}>Otevřít nastavení integrace</button></article><article className="chart-card"><span className="eyebrow">Stav dat</span><h2>{state.connected ? "Home Assistant připojen" : "Čekám na Home Assistant"}</h2><div className="quality-list"><div><span>VisionQ</span><b>{state.highRateKwh !== null ? "Online" : "Bez dat"}</b></div><div><span>HDO</span><b>{state.hdoQuality === "live" ? "Živá data" : "Náhradní plán"}</b></div><div><span>Billing</span><b>{state.missingSetup.length === 0 ? "Připraven" : "Nedokončen"}</b></div></div></article></section>;
}

function App() {
  const state = useFrakonEnergyState();
  const [view, setView] = useState<View>("overview");
  const labels: Record<View, string> = { overview: "Přehled", billing: "Vyúčtování", tariffs: "Tarify", history: "Historie", settings: "Nastavení" };
  return <main className="app-shell"><header className="topbar"><div><span className="brand-mark">F</span><div><h1>FRAKON Energy</h1><p>Energetický přehled domu</p></div></div><span className={state.connected ? "online" : "online demo"}>{state.connected ? "Online" : "Čekám na Home Assistant"}</span></header><section className="view-header"><span>{labels[view]}</span>{view !== "overview" ? <button onClick={() => setView("overview")}>Zpět na přehled</button> : null}</section>{view === "overview" && <Overview />}{view === "billing" && <BillingView />}{view === "tariffs" && <TariffsView />}{view === "history" && <HistoryView />}{view === "settings" && <SettingsView />}<nav className="bottom-nav">{(Object.keys(labels) as View[]).map((key) => <button key={key} className={view === key ? "active" : ""} onClick={() => { setView(key); window.scrollTo({ top: 0, behavior: "smooth" }); }}>{labels[key]}</button>)}</nav></main>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
