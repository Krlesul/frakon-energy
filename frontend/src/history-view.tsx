import { useEffect, useMemo, useState } from "react";

import { callHomeAssistantWs, useHomeAssistant } from "./home-assistant";
import "./history-view.css";

type HistoryRange = 30 | 90 | 365;

type DailyCostRecord = {
  day: string;
  high_rate_kwh: string;
  low_rate_kwh: string;
  total_kwh: string;
  variable_cost_czk: string;
  supplier: string;
  product_name: string;
};

type DailyCostResponse = {
  entry_id: string;
  start_day: string;
  end_day: string;
  price_source: "confirmed_all_in";
  fixed_monthly_excluded: boolean;
  records: DailyCostRecord[];
  read_only: true;
};

type ParsedRecord = {
  day: string;
  high: number;
  low: number;
  total: number;
  cost: number;
  supplier: string;
  productName: string;
};

const RANGE_OPTIONS: Array<{ value: HistoryRange; label: string }> = [
  { value: 30, label: "30 dní" },
  { value: 90, label: "90 dní" },
  { value: 365, label: "1 rok" },
];

function localIsoDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function numberValue(value: string): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatNumber(value: number, digits = 1): string {
  return value.toLocaleString("cs-CZ", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function dayLabel(day: string, short = false): string {
  const date = new Date(`${day}T12:00:00`);
  if (Number.isNaN(date.getTime())) return day;
  return date.toLocaleDateString("cs-CZ", short
    ? { day: "2-digit", month: "2-digit" }
    : { day: "2-digit", month: "2-digit", year: "numeric" });
}

function normalizeError(reason: unknown): string {
  if (reason instanceof Error) return reason.message;
  if (typeof reason === "object" && reason !== null && "message" in reason) {
    return String((reason as { message?: unknown }).message ?? "Historii se nepodařilo načíst.");
  }
  return String(reason || "Historii se nepodařilo načíst.");
}

function HistoryMetric({ label, value, note }: { label: string; value: string; note?: string }) {
  return <article className="history-metric">
    <span>{label}</span>
    <strong>{value}</strong>
    {note ? <small>{note}</small> : null}
  </article>;
}

export function HistoryView({ entryId }: { entryId: string | null }) {
  const hass = useHomeAssistant();
  const [range, setRange] = useState<HistoryRange>(30);
  const [response, setResponse] = useState<DailyCostResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    if (!hass || !entryId) {
      setResponse(null);
      setError(null);
      return () => { active = false; };
    }

    const end = new Date();
    const start = new Date(end);
    start.setDate(start.getDate() - (range - 1));
    setLoading(true);
    setError(null);

    callHomeAssistantWs<DailyCostResponse>(hass, {
      type: "frakon_energy/tariff/daily_costs",
      entry_id: entryId,
      start_day: localIsoDate(start),
      end_day: localIsoDate(end),
    })
      .then((result) => {
        if (!active) return;
        setResponse(result);
      })
      .catch((reason) => {
        if (!active) return;
        setResponse(null);
        setError(normalizeError(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => { active = false; };
  }, [hass?.connection, entryId, range]);

  const records = useMemo<ParsedRecord[]>(() => (response?.records ?? []).map((item) => ({
    day: item.day,
    high: numberValue(item.high_rate_kwh),
    low: numberValue(item.low_rate_kwh),
    total: numberValue(item.total_kwh),
    cost: numberValue(item.variable_cost_czk),
    supplier: item.supplier,
    productName: item.product_name,
  })), [response]);

  const summary = useMemo(() => {
    const total = records.reduce((sum, item) => sum + item.total, 0);
    const high = records.reduce((sum, item) => sum + item.high, 0);
    const low = records.reduce((sum, item) => sum + item.low, 0);
    const cost = records.reduce((sum, item) => sum + item.cost, 0);
    const average = records.length > 0 ? total / records.length : 0;
    const averageVariablePrice = total > 0 ? cost / total : 0;
    const lowShare = total > 0 ? (low / total) * 100 : 0;
    return { total, high, low, cost, average, averageVariablePrice, lowShare };
  }, [records]);

  const maxDaily = useMemo(() => Math.max(0, ...records.map((item) => item.total)), [records]);
  const chartLabelEvery = records.length > 180 ? 30 : records.length > 60 ? 14 : 7;
  const latest = [...records].reverse().slice(0, 14);
  const pricingIdentity = records.length > 0
    ? `${records[records.length - 1].supplier}${records[records.length - 1].productName ? ` · ${records[records.length - 1].productName}` : ""}`
    : null;

  return <section className="history-view">
    <article className="chart-card history-card">
      <div className="history-header">
        <div><span className="eyebrow">Historie</span><h2>Spotřeba a náklady</h2></div>
        <div className="segmented history-range" aria-label="Rozsah historie">
          {RANGE_OPTIONS.map((option) => <button
            key={option.value}
            className={range === option.value ? "active" : ""}
            onClick={() => setRange(option.value)}
            type="button"
          >{option.label}</button>)}
        </div>
      </div>

      {!entryId ? <div className="history-state">Čekám na aktivní FRAKON Energy konfiguraci.</div> : null}
      {loading ? <div className="history-state">Načítám denní historii…</div> : null}
      {!loading && error ? <div className="history-state history-state--error"><b>Historii nelze nacenit.</b><span>{error}</span><small>Spotřeba se do tohoto přehledu načítá přes potvrzenou all-in historii tarifu; FRAKON žádné ceny nedopočítává odhadem.</small></div> : null}
      {!loading && !error && entryId && records.length === 0 ? <div className="history-state"><b>Zatím nejsou denní záznamy.</b><span>Pro první denní rozdíl jsou potřeba alespoň dva uložené stavy elektroměru z různých dnů.</span></div> : null}

      {!loading && !error && records.length > 0 ? <>
        <div className="history-metrics">
          <HistoryMetric label="Spotřeba" value={`${formatNumber(summary.total, 1)} kWh`} note={`${records.length} dnů s měřením`} />
          <HistoryMetric label="Variabilní náklady" value={`${formatNumber(summary.cost, 0)} Kč`} note="bez stálých měsíčních plateb" />
          <HistoryMetric label="Denní průměr" value={`${formatNumber(summary.average, 1)} kWh`} note={`NT podíl ${formatNumber(summary.lowShare, 0)} %`} />
          <HistoryMetric label="Průměrná variabilní cena" value={`${formatNumber(summary.averageVariablePrice, 2)} Kč/kWh`} note="z potvrzených all-in cen" />
        </div>

        <div className="history-chart-wrap">
          <div className="history-chart-legend"><span><i className="history-legend-vt" />VT</span><span><i className="history-legend-nt" />NT</span><b>Maximum dne: {formatNumber(maxDaily, 1)} kWh</b></div>
          <div className="history-chart-scroll">
            <div className="history-bars" style={{ minWidth: `${Math.max(720, records.length * 7)}px` }}>
              {records.map((item, index) => {
                const height = maxDaily > 0 ? Math.max(2, (item.total / maxDaily) * 100) : 2;
                const highShare = item.total > 0 ? (item.high / item.total) * 100 : 0;
                const lowShare = item.total > 0 ? (item.low / item.total) * 100 : 0;
                const showLabel = index === 0 || index === records.length - 1 || index % chartLabelEvery === 0;
                return <div className="history-bar-cell" key={item.day} title={`${dayLabel(item.day)} · ${formatNumber(item.total, 2)} kWh · ${formatNumber(item.cost, 2)} Kč`}>
                  <div className="history-bar-track">
                    <div className="history-bar-stack" style={{ height: `${height}%` }}>
                      <span className="history-bar-vt" style={{ height: `${highShare}%` }} />
                      <span className="history-bar-nt" style={{ height: `${lowShare}%` }} />
                    </div>
                  </div>
                  <small>{showLabel ? dayLabel(item.day, true) : ""}</small>
                </div>;
              })}
            </div>
          </div>
        </div>

        <div className="history-breakdown">
          <div><span>VT za období</span><b>{formatNumber(summary.high, 1)} kWh</b></div>
          <div><span>NT za období</span><b>{formatNumber(summary.low, 1)} kWh</b></div>
          <div><span>Zdroj ceny</span><b>Potvrzená all-in historie</b><small>{pricingIdentity ?? ""}</small></div>
        </div>
      </> : null}
    </article>

    {!loading && !error && latest.length > 0 ? <article className="chart-card history-table-card">
      <div className="history-header"><div><span className="eyebrow">Denní detail</span><h2>Posledních {latest.length} záznamů</h2></div><span className="history-readonly">Read-only</span></div>
      <div className="history-table" role="table" aria-label="Denní spotřeba a náklady">
        <div className="history-row history-row--head" role="row"><span>Den</span><span>VT</span><span>NT</span><span>Celkem</span><span>Náklady</span></div>
        {latest.map((item) => <div className="history-row" role="row" key={item.day}>
          <b>{dayLabel(item.day)}</b>
          <span>{formatNumber(item.high, 2)} kWh</span>
          <span>{formatNumber(item.low, 2)} kWh</span>
          <strong>{formatNumber(item.total, 2)} kWh</strong>
          <strong>{formatNumber(item.cost, 2)} Kč</strong>
        </div>)}
      </div>
      <p className="history-footnote">Denní náklady obsahují pouze variabilní VT/NT složku podle potvrzeného tarifu. Stálé měsíční platby zůstávají ve vyúčtování a nejsou uměle rozpouštěny do jednotlivých dnů.</p>
    </article> : null}
  </section>;
}
