import React from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Tariff = "NT" | "VT";

const demo = {
  tariff: "NT" as Tariff,
  countdown: "02:17:43",
  nextChange: "23:50",
  todayKwh: 18.4,
  monthKwh: 542,
  paidAdvances: 35000,
  currentCosts: 32780,
  projectedBalance: 1590,
};

function Metric({ label, value, suffix }: { label: string; value: string | number; suffix?: string }) {
  return (
    <article className="metric">
      <span>{label}</span>
      <strong>{value}{suffix ? <small> {suffix}</small> : null}</strong>
    </article>
  );
}

function TariffCard() {
  const low = demo.tariff === "NT";
  return (
    <article className={`tariff-card ${low ? "low" : "high"}`}>
      <div className="tariff-card__top">
        <div>
          <span className="eyebrow">Aktuální tarif</span>
          <h2>{demo.tariff}</h2>
        </div>
        <span className="status-dot" aria-label={low ? "Nízký tarif je aktivní" : "Vysoký tarif je aktivní"} />
      </div>
      <div className="countdown-label">{low ? "Vypnutí NT za" : "Zapnutí NT za"}</div>
      <div className="countdown" aria-live="polite">{demo.countdown}</div>
      <div className="next-change">{low ? "NT skončí" : "NT začne"} ve {demo.nextChange}</div>
      <div className="timeline" aria-label="Dnešní rozvrh HDO">
        <span className="nt" style={{ flex: 3.5 }} />
        <span className="vt" style={{ flex: 7.67 }} />
        <span className="nt" style={{ flex: 2.25 }} />
        <span className="vt" style={{ flex: 6.17 }} />
        <span className="nt" style={{ flex: 2.25 }} />
      </div>
    </article>
  );
}

function App() {
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
        <span className="online">Online</span>
      </header>

      <section className="hero-grid">
        <TariffCard />
        <article className="balance-card">
          <span className="eyebrow">Odhad vyúčtování</span>
          <strong className="balance">+{demo.projectedBalance.toLocaleString("cs-CZ")} Kč</strong>
          <p>Předpokládaný přeplatek k 31. 1.</p>
          <div className="progress"><span style={{ width: "78%" }} /></div>
          <div className="balance-row"><span>Zaplacené zálohy</span><b>{demo.paidAdvances.toLocaleString("cs-CZ")} Kč</b></div>
          <div className="balance-row"><span>Dosavadní náklady</span><b>{demo.currentCosts.toLocaleString("cs-CZ")} Kč</b></div>
        </article>
      </section>

      <section className="metrics-grid">
        <Metric label="Spotřeba dnes" value={demo.todayKwh} suffix="kWh" />
        <Metric label="Tento měsíc" value={demo.monthKwh} suffix="kWh" />
        <Metric label="Měsíční záloha" value="5 000" suffix="Kč" />
        <Metric label="Baterie VisionQ" value="53" suffix="%" />
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
