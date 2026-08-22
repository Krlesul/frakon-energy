import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  applyDashboardDisplayClasses,
  DashboardDisplaySettingsCard,
  DEFAULT_DASHBOARD_DISPLAY_SETTINGS,
  loadDashboardDisplaySettings,
  saveDashboardDisplaySetting,
  type DashboardDisplayKey,
  type DashboardDisplaySettings,
} from "./dashboard-display-settings";
import { findFrakonEnergyEntryId, formatCountdown, useFrakonEnergyState, useHomeAssistant } from "./home-assistant";
import { SpotPriceCard } from "./spot-price-card";
import { SpotPriceSettingsCard } from "./spot-price-settings";
import { TariffSetupWizard } from "./tariff-wizard";
import "./styles.css";
import "./enhancements.css";
import "./spot-price-card.css";

type View = "overview" | "billing" | "tariffs" | "history" | "settings";
type ClockInterval = { start: string; end: string };

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

function DataBadge({ live, fallbackLabel = "HDO data nejsou dostupná" }: { live: boolean; fallbackLabel?: string }) {
  return <span className={`source-badge ${live ? "live" : "fallback"}`}>{live ? "Živá data HDO" : fallbackLabel}</span>;
}

function TariffHero() {
  const state = useFrakonEnergyState();
  const low = state.tariff === "NT";
  const unknown = state.tariff === "?";
  const hasTrustedTransition = !unknown && state.countdownSeconds !== null && state.nextChange !== null;
  const statusLabel = unknown
    ? "Čekám na živá data HDO"
    : hasTrustedTransition
      ? low ? "Vypnutí NT za" : "Zapnutí NT za"
      : low ? "NT je právě aktivní" : "VT je právě aktivní";

  return <article className={`tariff-card ${low ? "low" : unknown ? "unknown" : "high"}`}>
    <div className="tariff-card__top"><div><span className="eyebrow">Aktuální tarif</span><h2>{state.tariff}</h2></div><span className="status-dot" /></div>
    <div className="countdown-label">{statusLabel}</div>
    {hasTrustedTransition ? <div className="countdown" aria-live="polite">{formatCountdown(state.countdownSeconds)}</div> : null}
    <div className="next-change">{hasTrustedTransition ? `Další změna ve ${state.nextChange}` : "Čas další změny není dostupný"}</div>
    <div className="hero-meta"><DataBadge live={state.hdoQuality === "live"} /><span>{state.lastUpdated ? `Aktualizováno ${new Date(state.lastUpdated).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" })}` : "Čas aktualizace není dostupný"}</span></div>
    <div className="current-price">Aktuální cena <b>{state.currentPrice !== null ? `${formatNumber(state.currentPrice, 3)} Kč/kWh` : "Chybí cenové nastavení"}</b></div>
  </article>;
}

function BillingSummary() {
  const state = useFrakonEnergyState();
  return <article className="balance-card">
    <span className="eyebrow">Odhad vyúčtování</span>
    <strong className="balance">{formatMoney(state.projectedBalanceCzk)}</strong>
    <p>{state.projectedBalanceCzk === null ? "Predikce zatím není dostupná" : state.projectedBalanceCzk >= 0 ? "Předpokládaný přeplatek" : "Předpokládaný nedoplatek"}</p>
    <div className="balance-row"><span>Zaplacené zálohy</span><b>{formatMoney(state.paidAdvancesCzk)}</b></div>
    {missingReason(state.paidAdvancesCzk, "Chybí měsíční záloha nebo začátek období")}
    <div className="balance-row"><span>Dosavadní náklady</span><b>{formatMoney(state.accruedCostCzk)}</b></div>
    {missingReason(state.accruedCostCzk, "Chybí potvrzená cena pro zúčtovací období")}
    <div className="balance-row"><span>Doporučená záloha</span><b>{state.recommendedAdvanceCzk === null ? "—" : `${formatNumber(state.recommendedAdvanceCzk)} Kč`}</b></div>
  </article>;
}

function HdoTimeline() {
  const state = useFrakonEnergyState();
  const [now, setNow] = useState(() => new Date());
  const [inspectMinutes, setInspectMinutes] = useState<number | null>(null);

  useEffect(() => {
    const update = () => setNow(new Date());
    const timer = window.setInterval(update, 60_000);
    document.addEventListener("visibilitychange", update);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", update); };
  }, []);

  const today = state.todaySchedule.filter((item) => item.tariff === "NT");
  const intervals = useMemo(() => today.map((item) => ({
    start: new Date(item.start).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" }),
    end: new Date(item.end).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" }),
  })), [today]);
  const hasLiveSchedule = intervals.length > 0;
  const toMinutes = (value: string) => { const [h, m] = value.split(":").map(Number); return h * 60 + m; };
  const nowMinutes = now.getHours() * 60 + now.getMinutes();
  const markerPosition = (nowMinutes / 1440) * 100;
  const inspectionPosition = inspectMinutes === null ? null : (inspectMinutes / 1440) * 100;
  const inspectTariff = inspectMinutes === null ? null : intervals.some((item) => inspectMinutes >= toMinutes(item.start) && inspectMinutes < toMinutes(item.end)) ? "NT" : "VT";
  const inspectLabel = inspectMinutes === null ? "" : `${String(Math.floor(inspectMinutes / 60)).padStart(2, "0")}:${String(inspectMinutes % 60).padStart(2, "0")}`;
  const handleInspect = (event: React.PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    setInspectMinutes(Math.round(ratio * 1440));
  };

  return <article className="hdo-plan-card">
    <div className="hdo-plan-card__header"><div><span className="eyebrow">HDO plán</span><h2>Nízký tarif dnes</h2></div><DataBadge live={hasLiveSchedule} fallbackLabel={state.hdoQuality === "live" ? "Živý stav · rozvrh chybí" : "Rozvrh není dostupný"} /></div>
    <div className="day-scale day-scale--desktop">{Array.from({ length: 25 }, (_, hour) => <span key={hour}>{String(hour).padStart(2, "0")}</span>)}</div>
    <div className="day-scale day-scale--compact">{Array.from({ length: 13 }, (_, index) => <span key={index}>{String(index * 2).padStart(2, "0")}</span>)}</div>
    <div className="visual-timeline visual-timeline--live" onPointerMove={handleInspect} onPointerLeave={() => setInspectMinutes(null)} onPointerDown={handleInspect}>
      <div className="hour-grid" aria-hidden="true">{Array.from({ length: 25 }, (_, hour) => <i key={hour} style={{ left: `${(hour / 24) * 100}%` }} />)}</div>
      {intervals.map((item, index) => <span className="nt-segment" key={index} style={{ left: `${(toMinutes(item.start) / 1440) * 100}%`, width: `${((toMinutes(item.end) - toMinutes(item.start)) / 1440) * 100}%` }} title={`${item.start}–${item.end}`} />)}
      <div className="current-time-marker" style={{ left: `${markerPosition}%` }}><b>{now.toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" })}</b></div>
      {inspectionPosition !== null ? <div className="inspection-marker" style={{ left: `${inspectionPosition}%` }}><b>{inspectLabel}</b><small>{inspectTariff}</small></div> : null}
    </div>
    <div className="interval-list">{intervals.map((item, index) => <div key={index}><b>NT {index + 1}</b><span>{item.start} → {item.end}</span></div>)}</div>
    {!hasLiveSchedule ? <p className="fallback-warning">{state.hdoQuality === "live" ? "Živý tarif HDO je dostupný, ale dnešní strukturovaný rozvrh zatím ne. FRAKON proto nezobrazuje náhradní časy." : "HDO rozvrh není dostupný. FRAKON nezobrazuje neověřené náhradní časy."}</p> : null}
  </article>;
}

function Overview({ display }: { display: DashboardDisplaySettings }) {
  const state = useFrakonEnergyState();
  const meterTotal = state.highRateKwh !== null && state.lowRateKwh !== null ? state.highRateKwh + state.lowRateKwh : null;
  const showHero = display.show_hdo || display.show_billing_estimate;
  const showMetrics = display.show_daily_consumption || display.show_monthly_consumption || display.show_battery_status;

  return <>
    {showHero ? <section className="hero-grid">
      {display.show_hdo ? <TariffHero /> : null}
      {display.show_billing_estimate ? <BillingSummary /> : null}
    </section> : null}
    {showMetrics ? <section className="metrics-grid">
      {display.show_daily_consumption ? <Metric label="Spotřeba dnes" value={formatNumber(state.todayConsumptionKwh, 1)} suffix="kWh" reason="Chybí denní historie" /> : null}
      {display.show_daily_consumption ? <Metric label="Náklady dnes" value={state.todayCostCzk === null ? "—" : `${formatNumber(state.todayCostCzk, 2)} Kč`} reason={state.todayConsumptionKwh === null ? "Chybí denní historie" : "Chybí potvrzená all-in cena pro dnešní den"} /> : null}
      {display.show_monthly_consumption ? <Metric label="Tento měsíc" value={formatNumber(state.monthConsumptionKwh, 1)} suffix="kWh" reason="Chybí měsíční historie" /> : null}
      {display.show_battery_status ? <Metric label="Baterie VisionQ" value={formatNumber(state.batteryPercent, 1)} suffix="%" reason="Neplatná hodnota baterie" /> : null}
    </section> : null}
    {display.show_spot_prices ? <SpotPriceCard /> : null}
    {display.show_hdo_plan ? <HdoTimeline /> : null}
    {display.show_technical_measurements ? <section className="diagnostics-card">
      <span className="eyebrow">Technické měření</span>
      <div className="metrics-grid secondary">
        <Metric label="Elektroměr celkem" value={formatNumber(meterTotal, 3)} suffix="kWh" />
        <Metric label="VT registr" value={formatNumber(state.highRateKwh, 3)} suffix="kWh" />
        <Metric label="NT registr" value={formatNumber(state.lowRateKwh, 3)} suffix="kWh" />
        <Metric label="Kvalita dat" value={state.hdoQuality === "live" ? "Živá" : "Fallback"} />
      </div>
    </section> : null}
  </>;
}

function BillingView({ display }: { display: DashboardDisplaySettings }) {
  const state = useFrakonEnergyState();
  const dateLabel = state.settlementDate ? new Date(state.settlementDate).toLocaleDateString("cs-CZ") : null;
  return <section className="page-grid">
    {display.show_billing_estimate ? <BillingSummary /> : null}
    <article className="chart-card"><span className="eyebrow">Zúčtovací období</span><h2>{state.billingExpired && dateLabel ? `Období skončilo ${dateLabel}` : dateLabel ? `Do ${dateLabel}` : "Není kompletně nastaveno"}</h2><div className="setup-checklist">{state.billingExpired ? <div><span>!</span><b>Vyúčtovací období je ukončené</b><small>Založte nové období podle posledního vyúčtování a aktuálního elektroměru.</small></div> : state.missingSetup.length === 0 ? <p>Vyúčtování je připravené.</p> : state.missingSetup.map((item) => <div key={item}><span>!</span><b>{item}</b><small>Vyplňte v nastavení FRAKON Energy.</small></div>)}</div></article>
  </section>;
}

function TariffsView({ display }: { display: DashboardDisplaySettings }) {
  const hass = useHomeAssistant();
  return <>
    {display.show_hdo ? <TariffHero /> : null}
    <TariffSetupWizard hass={hass} />
    {display.show_hdo_plan ? <HdoTimeline /> : null}
  </>;
}

function HistoryView() {
  return <article className="chart-card"><span className="eyebrow">Historie</span><h2>Spotřeba a náklady</h2><div className="chart-placeholder"><span className="chart-caption">Graf se zobrazí po nasbírání historických denních hodnot.</span></div></article>;
}

function SettingsView({
  entryId,
  entryError,
  display,
  displayStatus,
  displaySaving,
  onDisplayChange,
}: {
  entryId: string | null;
  entryError: string | null;
  display: DashboardDisplaySettings;
  displayStatus: string;
  displaySaving: boolean;
  onDisplayChange: (key: DashboardDisplayKey, enabled: boolean) => void;
}) {
  const state = useFrakonEnergyState();
  const hass = useHomeAssistant();
  const openIntegration = () => { window.top!.location.href = "/config/integrations/integration/frakon_energy"; };

  return <section className="settings-grid">
    <DashboardDisplaySettingsCard settings={display} disabled={displaySaving || !entryId || !hass} status={displayStatus} onChange={onDisplayChange} />
    <article className="chart-card"><span className="eyebrow">Nastavení FRAKON Energy</span><h2>Dokončení vyúčtování</h2><p className="settings-copy">{state.billingExpired ? "Poslední zúčtovací období už skončilo. Založte nové období podle posledního vyúčtování." : "Pro výpočet nákladů a predikce doplňte počáteční stavy elektroměru a měsíční zálohu."}</p><div className="setup-checklist">{state.missingSetup.map((item) => <div key={item}><span>!</span><b>{item}</b><small>Hodnota zatím není dostupná.</small></div>)}</div><button className="primary-action" onClick={openIntegration}>Otevřít nastavení integrace</button></article>
    <SpotPriceSettingsCard hass={hass} entryId={entryId} />
    {entryError ? <article className="chart-card"><span className="eyebrow">Nastavení</span><h2>Nastavení není dostupné</h2><p className="settings-copy">{entryError}</p></article> : null}
    <article className="chart-card"><span className="eyebrow">Stav dat</span><h2>{state.connected ? "Home Assistant připojen" : "Čekám na Home Assistant"}</h2><div className="quality-list"><div><span>VisionQ</span><b>{state.highRateKwh !== null ? "Online" : "Bez dat"}</b></div><div><span>HDO</span><b>{state.hdoQuality === "live" ? "Živá data" : "Náhradní plán"}</b></div><div><span>Billing</span><b>{state.billingExpired ? "Nové období" : state.missingSetup.length === 0 ? "Připraven" : "Nedokončen"}</b></div></div></article>
  </section>;
}

function App() {
  const state = useFrakonEnergyState();
  const hass = useHomeAssistant();
  const [view, setView] = useState<View>("overview");
  const [entryId, setEntryId] = useState<string | null>(null);
  const [entryError, setEntryError] = useState<string | null>(null);
  const [display, setDisplay] = useState<DashboardDisplaySettings>(DEFAULT_DASHBOARD_DISPLAY_SETTINGS);
  const [displayStatus, setDisplayStatus] = useState("Načítám…");
  const [displaySaving, setDisplaySaving] = useState(false);
  const labels: Record<View, string> = { overview: "Přehled", billing: "Vyúčtování", tariffs: "Tarify", history: "Historie", settings: "Nastavení" };

  useEffect(() => {
    let active = true;
    if (!hass) {
      setEntryId(null);
      setDisplayStatus("Čekám na Home Assistant");
      return () => { active = false; };
    }
    findFrakonEnergyEntryId(hass)
      .then(async (id) => {
        if (!active) return;
        setEntryId(id);
        if (!id) {
          setEntryError("Integrace FRAKON Energy nebyla nalezena.");
          setDisplayStatus("Nastavení není dostupné");
          return;
        }
        setEntryError(null);
        try {
          const settings = await loadDashboardDisplaySettings(hass, id);
          if (!active) return;
          setDisplay(settings);
          setDisplayStatus("Uloženo");
        } catch (error) {
          if (!active) return;
          setDisplayStatus(`Chyba: ${String(error)}`);
        }
      })
      .catch((error) => {
        if (!active) return;
        setEntryError(String(error));
        setDisplayStatus("Nastavení není dostupné");
      });
    return () => { active = false; };
  }, [hass?.connection]);

  useEffect(() => {
    applyDashboardDisplayClasses(display);
  }, [display]);

  const onDisplayChange = async (key: DashboardDisplayKey, enabled: boolean) => {
    if (!hass || !entryId || displaySaving) return;
    const previous = display;
    const optimistic = { ...display, [key]: enabled };
    setDisplay(optimistic);
    setDisplaySaving(true);
    setDisplayStatus("Ukládám…");
    try {
      const saved = await saveDashboardDisplaySetting(hass, entryId, key, enabled);
      setDisplay(saved);
      setDisplayStatus("Uloženo");
      window.dispatchEvent(new Event("frakon-energy-hass-updated"));
    } catch (error) {
      setDisplay(previous);
      setDisplayStatus(`Chyba: ${String(error)}`);
    } finally {
      setDisplaySaving(false);
    }
  };

  return <main className="app-shell">
    <header className="topbar"><div><span className="brand-mark">F</span><div><h1>FRAKON Energy</h1><p>Energetický přehled domu</p></div></div><span className={state.connected ? "online" : "online demo"}>{state.connected ? "Online" : "Čekám na Home Assistant"}</span></header>
    <section className="view-header"><span>{labels[view]}</span>{view !== "overview" ? <button onClick={() => setView("overview")}>Zpět na přehled</button> : null}</section>
    {view === "overview" && <Overview display={display} />}
    {view === "billing" && <BillingView display={display} />}
    {view === "tariffs" && <TariffsView display={display} />}
    {view === "history" && <HistoryView />}
    {view === "settings" && <SettingsView entryId={entryId} entryError={entryError} display={display} displayStatus={displayStatus} displaySaving={displaySaving} onDisplayChange={onDisplayChange} />}
    <nav className="bottom-nav">{(Object.keys(labels) as View[]).map((key) => <button key={key} className={view === key ? "active" : ""} onClick={() => { setView(key); window.scrollTo({ top: 0, behavior: "smooth" }); }}>{labels[key]}</button>)}</nav>
  </main>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
