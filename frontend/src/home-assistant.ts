import { useEffect, useMemo, useState } from "react";

export type HassEntity = {
  entity_id: string;
  state: string;
  attributes: Record<string, unknown>;
};

export type HomeAssistant = {
  states: Record<string, HassEntity>;
  connection?: {
    subscribeEvents?: (
      callback: (event: { event_type: string; data: Record<string, unknown> }) => void,
      eventType: string,
    ) => Promise<() => void>;
  };
};

declare global {
  interface Window {
    hass?: HomeAssistant;
    __FRAKON_ENERGY_HASS__?: HomeAssistant;
  }
}

type ScheduleItem = { start: string; end: string; tariff: "NT" | "VT" };

export type DashboardState = {
  connected: boolean;
  tariff: "NT" | "VT" | "?";
  countdownSeconds: number | null;
  nextChange: string | null;
  todaySchedule: ScheduleItem[];
  tomorrowSchedule: ScheduleItem[];
  currentPrice: number | null;
  batteryPercent: number | null;
  highRateKwh: number | null;
  lowRateKwh: number | null;
  monthlyAdvanceCzk: number | null;
  paidAdvancesCzk: number | null;
  projectedAdvancesCzk: number | null;
  accruedCostCzk: number | null;
  currentBalanceCzk: number | null;
  projectedBalanceCzk: number | null;
  recommendedAdvanceCzk: number | null;
  todayConsumptionKwh: number | null;
  monthConsumptionKwh: number | null;
  settlementDate: string | null;
};

const EMPTY_STATE: DashboardState = {
  connected: false,
  tariff: "?",
  countdownSeconds: null,
  nextChange: null,
  todaySchedule: [],
  tomorrowSchedule: [],
  currentPrice: null,
  batteryPercent: null,
  highRateKwh: null,
  lowRateKwh: null,
  monthlyAdvanceCzk: null,
  paidAdvancesCzk: null,
  projectedAdvancesCzk: null,
  accruedCostCzk: null,
  currentBalanceCzk: null,
  projectedBalanceCzk: null,
  recommendedAdvanceCzk: null,
  todayConsumptionKwh: null,
  monthConsumptionKwh: null,
  settlementDate: null,
};

function findState(hass: HomeAssistant, suffix: string): HassEntity | undefined {
  return Object.values(hass.states).find(
    (entity) => entity.entity_id.startsWith("sensor.frakon_energy_") && entity.entity_id.endsWith(suffix),
  );
}

function findAnyState(hass: HomeAssistant, suffixes: string[]): HassEntity | undefined {
  for (const suffix of suffixes) {
    const entity = findState(hass, suffix);
    if (entity) return entity;
  }
  return undefined;
}

function numberState(entity: HassEntity | undefined): number | null {
  if (!entity || ["unknown", "unavailable", "none", "null"].includes(entity.state.toLowerCase())) return null;
  const value = Number(entity.state);
  return Number.isFinite(value) ? value : null;
}

function percentageState(entity: HassEntity | undefined): number | null {
  const value = numberState(entity);
  if (value === null) return null;
  const unit = String(entity?.attributes.unit_of_measurement ?? "").trim();
  const normalized = unit === "%" ? value : value >= 0 && value <= 1 ? value * 100 : value;
  return normalized >= 0 && normalized <= 100 ? normalized : null;
}

function textState(entity: HassEntity | undefined): string | null {
  if (!entity || ["unknown", "unavailable", "none", "null"].includes(entity.state.toLowerCase())) return null;
  return entity.state;
}

function parseCountdown(value: string | undefined): number | null {
  if (!value) return null;
  const match = /^(\d+):(\d{2}):(\d{2})$/.exec(value);
  if (!match) return null;
  return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
}

function parseRawSchedule(raw: unknown): ScheduleItem[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const candidate = item as Record<string, unknown>;
    const tariff = String(candidate.tariff ?? "").toUpperCase();
    const start = String(candidate.start ?? "");
    const end = String(candidate.end ?? "");
    if ((tariff !== "NT" && tariff !== "VT") || !start || !end) return [];
    return [{ start, end, tariff } as ScheduleItem];
  });
}

function sameLocalDate(value: string, target: Date): boolean {
  const date = new Date(value);
  return !Number.isNaN(date.getTime()) &&
    date.getFullYear() === target.getFullYear() &&
    date.getMonth() === target.getMonth() &&
    date.getDate() === target.getDate();
}

function splitSchedule(entity: HassEntity | undefined): { today: ScheduleItem[]; tomorrow: ScheduleItem[] } {
  const directToday = parseRawSchedule(entity?.attributes.today_schedule);
  const directTomorrow = parseRawSchedule(entity?.attributes.tomorrow_schedule);
  const all = parseRawSchedule(entity?.attributes.schedule);

  const now = new Date();
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);

  return {
    today: directToday.length > 0 ? directToday : all.filter((item) => sameLocalDate(item.start, now)),
    tomorrow: directTomorrow.length > 0 ? directTomorrow : all.filter((item) => sameLocalDate(item.start, tomorrow)),
  };
}

function readDashboardState(hass?: HomeAssistant): DashboardState {
  if (!hass) return EMPTY_STATE;

  const tariffEntity = findAnyState(hass, ["_tariff", "_tarif"]);
  const countdownEntity = findAnyState(hass, ["_countdown", "_odpocet"]);
  const nextSwitchEntity = findAnyState(hass, ["_next_switch", "_dalsi_zmena"]);
  const scheduleEntity = findAnyState(hass, ["_today_schedule", "_schedule", "_dnesni_rozvrh"]);
  const schedules = splitSchedule(scheduleEntity);

  const tariff = tariffEntity?.state === "NT" || tariffEntity?.state === "VT" ? tariffEntity.state : "?";
  const nextTimestamp = nextSwitchEntity?.state;
  const nextDate = nextTimestamp && !["unknown", "unavailable"].includes(nextTimestamp) ? new Date(nextTimestamp) : null;

  return {
    connected: true,
    tariff,
    countdownSeconds: parseCountdown(countdownEntity?.state),
    nextChange: nextDate && !Number.isNaN(nextDate.getTime())
      ? nextDate.toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" })
      : null,
    todaySchedule: schedules.today,
    tomorrowSchedule: schedules.tomorrow,
    currentPrice: numberState(findAnyState(hass, ["_current_price", "_aktualni_cena", "_skutecna_cena"])),
    batteryPercent: percentageState(findAnyState(hass, ["_battery_state", "_stav_baterie"])),
    highRateKwh: numberState(findAnyState(hass, ["_high_rate", "_vt_celkem"])),
    lowRateKwh: numberState(findAnyState(hass, ["_low_rate", "_nt_celkem"])),
    monthlyAdvanceCzk: numberState(findAnyState(hass, ["_billing_monthly_advance", "_mesicni_zaloha"])),
    paidAdvancesCzk: numberState(findAnyState(hass, ["_billing_paid_advances", "_zaplacene_zalohy"])),
    projectedAdvancesCzk: numberState(findAnyState(hass, ["_billing_projected_advances", "_zalohy_za_cele_obdobi"])),
    accruedCostCzk: numberState(findAnyState(hass, ["_billing_accrued_cost", "_dosavadni_naklady", "_skutecne_naklady"])),
    currentBalanceCzk: numberState(findAnyState(hass, ["_billing_current_balance", "_prubezny_rozdil", "_prubezny_preplatek_nedoplatek"])),
    projectedBalanceCzk: numberState(findAnyState(hass, ["_billing_projected_balance", "_odhad_preplatku_nedoplatku"])),
    recommendedAdvanceCzk: numberState(findAnyState(hass, ["_billing_recommended_advance", "_doporucena_zaloha"])),
    todayConsumptionKwh: numberState(findAnyState(hass, ["_today_consumption", "_spotreba_dnes_celkem"])),
    monthConsumptionKwh: numberState(findAnyState(hass, ["_month_consumption", "_spotreba_tento_mesic"])),
    settlementDate: textState(findAnyState(hass, ["_billing_settlement_date", "_predpokladane_vyuctovani"])),
  };
}

export function useFrakonEnergyState(): DashboardState {
  const [revision, setRevision] = useState(0);
  const hass = window.__FRAKON_ENERGY_HASS__ ?? window.hass;

  useEffect(() => {
    const timer = window.setInterval(() => setRevision((value) => value + 1), 1000);
    let unsubscribe: (() => void) | undefined;

    hass?.connection?.subscribeEvents?.(
      () => setRevision((value) => value + 1),
      "frakon_energy_tariff_changed",
    ).then((remove) => { unsubscribe = remove; }).catch(() => undefined);

    return () => {
      window.clearInterval(timer);
      unsubscribe?.();
    };
  }, [hass]);

  const source = useMemo(() => readDashboardState(hass), [hass, revision]);
  const [localCountdown, setLocalCountdown] = useState(source.countdownSeconds);

  useEffect(() => {
    setLocalCountdown(source.countdownSeconds);
  }, [source.countdownSeconds, source.tariff]);

  useEffect(() => {
    if (localCountdown === null || localCountdown <= 0) return;
    const timer = window.setTimeout(
      () => setLocalCountdown((value) => value === null ? null : Math.max(0, value - 1)),
      1000,
    );
    return () => window.clearTimeout(timer);
  }, [localCountdown]);

  return { ...source, countdownSeconds: localCountdown };
}

export function formatCountdown(seconds: number | null): string {
  if (seconds === null) return "--:--:--";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remaining = seconds % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`;
}
