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
        <span
          className="status-dot"
          aria-label={unknown ? "Tarif není dostupný" : low ? "Nízký tarif je aktivní" : "Vysoký tarif je aktivní"}
        />
      </div>
      <div className="countdown-label">
        {unknown ? "Čekám na data HDO" : low ? "Vypnutí NT za" : "Zapnutí NT za"}
      </div>
      <div className="countdown" aria-live="polite">{formatCountdown(state.countdownSeconds)}</div>
      <div className="next-change">
        {state.nextChange
          ? `${low ? "NT skončí" : "NT začne"} ve ${state.nextChange}`
          : "Čas další změny není dostupný"}
      </div>
      <div className="timeline" aria-label="Dnešní rozvrh HDO">
        {state.todaySchedule.length > 0 ? state.todaySchedule.map((item, index) => (
          <span
            key={`${item.start}-${index}`}
            className={item.tariff.toLowerCase()}
            style={{ flex: scheduleDuration(item.start, item.end) }}
            title={`${item.tariff} ${new Date(item.start).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" })}–${new Date(item.end).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" })}`}
          />
        )) : <span className="timeline-empty">Rozvrh není dostupný</span>}
      </div>
      {state.currentPrice !== null ? (
        <div className="current-price">Aktuální cena <b>{state.currentPrice.toLocaleString("cs-CZ", { minimumFractionDigits: 3, maximumFractionDigits: 3 })} Kč/kWh</b></div>
      ) : null}
    </article>
  );
}

function App() {
  const energy = useFrakonEnergyState();
  const meterTotal = energy.highRateKwh !== null && energy.lowRateKwh !== null
    ? energy.highRateKwh + energy.lowRateKwh
    : null;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <span className="brand-mark">F</span>
          <div>
            <h1>FRAKON Energy</h1>
            <p>Energetický přehled domu</p>
          </div>
        </div>
        <span className={energy.connected ? "online" : "online demo"}>{energy.connected ? "Online" : "Demo režim"}</span>
      </header>

      <section className="hero-grid">
        <TariffCard />
        <article className="balance-card">
          <span className="eyebrow">Odhad vyúčtování</span>
          <strong className="balance">+{demoBilling.projectedBalance.toLocaleString("cs-CZ")} Kč</strong>
          <p>Předpokládaný přeplatek k 31. 1.</p>
          <div className="progress"><span style={{ width: "78%" }} /></div>
          <div className="balance-row"><span>Zaplacené zálohy</span><b>{demoBilling.paidAdvances.toLocaleString("cs-CZ")} Kč</b></div>
          <div className="balance-row"><span>Dosavadní náklady</span><b>{demoBilling.currentCosts.toLocaleString("cs-CZ")} Kč</b></div>
        </article>
      </section>

      <section className="metrics-grid">
        <Metric label="Spotřeba dnes" value={demoBilling.todayKwh} suffix="kWh" />
        <Metric label="Tento měsíc" value={demoBilling.monthKwh} suffix="kWh" />
        <Metric label="Měsíční záloha" value={demoBilling.monthlyAdvance.toLocaleString("cs-CZ")} suffix="Kč" />
        <Metric label="Baterie VisionQ" value={energy.batteryPercent?.toLocaleString("cs-CZ", { maximumFractionDigits: 1 }) ?? "—"} suffix="%" />
      </section>

      <section className="metrics-grid secondary">
        <Metric label="Elektroměr celkem" value={meterTotal?.toLocaleString("cs-CZ", { maximumFractionDigits: 3 }) ?? "—"} suffix="kWh" />
        <Metric label="VT celkem" value={energy.highRateKwh?.toLocaleString("cs-CZ", { maximumFractionDigits: 3 }) ?? "—"} suffix="kWh" />
        <Metric label="NT celkem" value={energy.lowRateKwh?.toLocaleString("cs-CZ", { maximumFractionDigits: 3 }) ?? "—"} suffix="kWh" />
        <Metric label="Datový zdroj" value={energy.connected ? "Home Assistant" : "Ukázková data"} />
      </section>

      <section className="chart-card">
        <div className="section-title">
          <div><span className="eyebrow">Spotřeba a náklady</span><h2>Průběh zúčtovacího období</h2></div>
          <div className="segmented"><button className="active">Měsíc</button><button>Rok</button></div>
        </div>
        <div className="chart-placeholder">
          <div className="chart-line actual" />
          <div className="chart-line advances" />
          <span className="chart-caption">Skutečná data a budoucí predikce budou zřetelně oddělené.</span>
        </div>
      </section>

      <nav className="bottom-nav">
        <button className="active">Přehled</button>
        <button>Vyúčtování</button>
        <button>Tarify</button>
        <button>Historie</button>
        <button>Nastavení</button>
      </nav>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
