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

export type DashboardState = {
  connected: boolean;
  tariff: "NT" | "VT" | "?";
  countdownSeconds: number | null;
  nextChange: string | null;
  todaySchedule: Array<{ start: string; end: string; tariff: "NT" | "VT" }>;
  currentPrice: number | null;
  batteryPercent: number | null;
  highRateKwh: number | null;
  lowRateKwh: number | null;
};

const DEMO_STATE: DashboardState = {
  connected: false,
  tariff: "NT",
  countdownSeconds: 2 * 3600 + 17 * 60 + 43,
  nextChange: "23:50",
  todaySchedule: [
    { start: "2026-08-04T02:00:00+02:00", end: "2026-08-04T05:30:00+02:00", tariff: "NT" },
    { start: "2026-08-04T05:30:00+02:00", end: "2026-08-04T13:10:00+02:00", tariff: "VT" },
    { start: "2026-08-04T13:10:00+02:00", end: "2026-08-04T15:25:00+02:00", tariff: "NT" },
    { start: "2026-08-04T15:25:00+02:00", end: "2026-08-04T21:35:00+02:00", tariff: "VT" },
    { start: "2026-08-04T21:35:00+02:00", end: "2026-08-04T23:50:00+02:00", tariff: "NT" },
  ],
  currentPrice: 4.673,
  batteryPercent: 53.1,
  highRateKwh: 327.124,
  lowRateKwh: 315.358,
};

function findState(hass: HomeAssistant, suffix: string): HassEntity | undefined {
  return Object.values(hass.states).find(
    (entity) =>
      entity.entity_id.startsWith("sensor.frakon_energy_") &&
      entity.entity_id.endsWith(suffix),
  );
}

function numberState(entity: HassEntity | undefined): number | null {
  if (!entity || ["unknown", "unavailable"].includes(entity.state)) return null;
  const value = Number(entity.state);
  return Number.isFinite(value) ? value : null;
}

function parseCountdown(value: string | undefined): number | null {
  if (!value) return null;
  const match = /^(\d+):(\d{2}):(\d{2})$/.exec(value);
  if (!match) return null;
  return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
}

function parseSchedule(entity: HassEntity | undefined): DashboardState["todaySchedule"] {
  const raw = entity?.attributes.schedule;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const candidate = item as Record<string, unknown>;
    const tariff = String(candidate.tariff ?? "").toUpperCase();
    if (tariff !== "NT" && tariff !== "VT") return [];
    return [{ start: String(candidate.start), end: String(candidate.end), tariff }];
  });
}

function readDashboardState(hass?: HomeAssistant): DashboardState {
  if (!hass) return DEMO_STATE;

  const tariffEntity = findState(hass, "_tariff");
  const countdownEntity = findState(hass, "_countdown");
  const nextSwitchEntity = findState(hass, "_next_switch");
  const scheduleEntity = findState(hass, "_today_schedule");

  const tariff = tariffEntity?.state === "NT" || tariffEntity?.state === "VT"
    ? tariffEntity.state
    : "?";

  const nextTimestamp = nextSwitchEntity?.state;
  const nextDate = nextTimestamp && !["unknown", "unavailable"].includes(nextTimestamp)
    ? new Date(nextTimestamp)
    : null;

  return {
    connected: true,
    tariff,
    countdownSeconds: parseCountdown(countdownEntity?.state),
    nextChange: nextDate && !Number.isNaN(nextDate.getTime())
      ? nextDate.toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" })
      : null,
    todaySchedule: parseSchedule(scheduleEntity),
    currentPrice: numberState(findState(hass, "_current_price")),
    batteryPercent: numberState(findState(hass, "_battery_state")),
    highRateKwh: numberState(findState(hass, "_high_rate")),
    lowRateKwh: numberState(findState(hass, "_low_rate")),
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
    const timer = window.setTimeout(() => setLocalCountdown((value) => value === null ? null : Math.max(0, value - 1)), 1000);
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
