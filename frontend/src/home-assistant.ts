import { useEffect, useMemo, useState } from "react";

export type HassEntity = {
  entity_id: string;
  state: string;
  attributes: Record<string, unknown>;
  last_changed?: string;
  last_updated?: string;
};

export type HomeAssistant = {
  states: Record<string, HassEntity>;
  callWS?<T>(message: Record<string, unknown>): Promise<T>;
  connection?: {
    sendMessagePromise?: <T>(message: Record<string, unknown>) => Promise<T>;
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
type ConfigEntrySummary = { entry_id: string; domain?: string; title?: string };
export type DataQuality = "live" | "fallback" | "missing";

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
  lastUpdated: string | null;
  hdoQuality: DataQuality;
  missingSetup: string[];
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
  lastUpdated: null,
  hdoQuality: "missing",
  missingSetup: [],
};

const normalize = (value: string) => value.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, "_");

function allStates(hass: HomeAssistant): HassEntity[] { return Object.values(hass.states); }
function findByAliases(hass: HomeAssistant, aliases: string[]): HassEntity | undefined { const normalizedAliases = aliases.map(normalize); return allStates(hass).find((entity) => { const friendlyName = String(entity.attributes.friendly_name ?? ""); const haystack = normalize(`${entity.entity_id} ${friendlyName}`); return normalizedAliases.some((alias) => haystack.includes(alias)); }); }
function numberState(entity: HassEntity | undefined): number | null { if (!entity || ["unknown", "unavailable", "none", "null", ""].includes(entity.state.toLowerCase())) return null; const value = Number(String(entity.state).replace(",", ".")); return Number.isFinite(value) ? value : null; }
function textState(entity: HassEntity | undefined): string | null { if (!entity || ["unknown", "unavailable", "none", "null", ""].includes(entity.state.toLowerCase())) return null; return entity.state; }
function parseCountdown(value: string | undefined): number | null { if (!value) return null; const match = /^(\d+):(\d{2}):(\d{2})$/.exec(value); if (!match) return null; return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]); }
function parseRawSchedule(raw: unknown): ScheduleItem[] { if (!Array.isArray(raw)) return []; return raw.flatMap((item) => { if (!item || typeof item !== "object") return []; const candidate = item as Record<string, unknown>; const tariff = String(candidate.tariff ?? candidate.rate ?? "").toUpperCase(); const start = String(candidate.start ?? candidate.from ?? ""); const end = String(candidate.end ?? candidate.to ?? ""); if ((tariff !== "NT" && tariff !== "VT") || !start || !end) return []; return [{ start, end, tariff } as ScheduleItem]; }); }
function sameLocalDate(value: string, target: Date): boolean { const date = new Date(value); return !Number.isNaN(date.getTime()) && date.getFullYear() === target.getFullYear() && date.getMonth() === target.getMonth() && date.getDate() === target.getDate(); }
function splitSchedule(entity: HassEntity | undefined): { today: ScheduleItem[]; tomorrow: ScheduleItem[] } { const directToday = parseRawSchedule(entity?.attributes.today_schedule ?? entity?.attributes.dnesni_rozvrh); const directTomorrow = parseRawSchedule(entity?.attributes.tomorrow_schedule ?? entity?.attributes.zitrejsi_rozvrh); const all = parseRawSchedule(entity?.attributes.schedule ?? entity?.attributes.intervals ?? entity?.attributes.raw_data); const now = new Date(); const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1); return { today: directToday.length > 0 ? directToday : all.filter((item) => sameLocalDate(item.start, now)), tomorrow: directTomorrow.length > 0 ? directTomorrow : all.filter((item) => sameLocalDate(item.start, tomorrow)) }; }
function normalizeBattery(value: number | null): number | null { if (value === null) return null; if (value >= 0 && value <= 1) return Math.round(value * 1000) / 10; if (value >= 0 && value <= 100) return Math.round(value * 10) / 10; return null; }
function isHdoConfigEntry(entry: ConfigEntrySummary): boolean { return normalize(entry.title ?? "").includes("cez_hdo"); }

function readDashboardState(hass?: HomeAssistant): DashboardState {
  if (!hass) return EMPTY_STATE;
  const tariffEntity = findByAliases(hass, ["hdo tarif", "frakon energy tariff", "aktualni tarif", "cez hdo aktivni interval", "nizky tarif aktivni", "vysoky tarif aktivni"]); const ntActive = findByAliases(hass, ["cez_hdo_lowtariffactive", "nizky tarif aktivni"]); const countdownEntity = findByAliases(hass, ["hdo odpocet", "frakon energy countdown", "cez hdo odpocet", "cez hdo dalsi zmena odpocet", "nizky tarif zbyva", "vysoky tarif zbyva"]); const nextSwitchEntity = findByAliases(hass, ["hdo dalsi prepnuti", "frakon energy next switch", "cez hdo dalsi zmena", "nizky tarif konec", "nizky tarif zacatek"]); const scheduleEntity = findByAliases(hass, ["hdo dnesni rozvrh", "frakon energy schedule", "cez hdo dnesni rozvrh", "hdo rozvrh", "cez_hdo_schedule"]); const schedules = splitSchedule(scheduleEntity);
  let tariff: "NT" | "VT" | "?" = "?"; const tariffText = tariffEntity?.state.toUpperCase() ?? ""; if (tariffText === "NT" || tariffText.includes("NIZKY")) tariff = "NT"; else if (tariffText === "VT" || tariffText.includes("VYSOKY")) tariff = "VT"; else if (ntActive?.state === "on" || ntActive?.state.toLowerCase() === "true") tariff = "NT"; else if (ntActive?.state === "off" || ntActive?.state.toLowerCase() === "false") tariff = "VT";
  const nextTimestamp = nextSwitchEntity?.state; const nextDate = nextTimestamp && !["unknown", "unavailable"].includes(nextTimestamp) ? new Date(nextTimestamp) : null; const highRateKwh = numberState(findByAliases(hass, ["frakon energy high rate", "vt celkem"])); const lowRateKwh = numberState(findByAliases(hass, ["frakon energy low rate", "nt celkem"])); const monthlyAdvanceCzk = numberState(findByAliases(hass, ["billing monthly advance", "mesicni zaloha", "stala mesicni platba"])); const settlementDate = textState(findByAliases(hass, ["billing settlement date", "predpokladane vyuctovani", "datum vyuctovani"])); const missingSetup: string[] = []; if (monthlyAdvanceCzk === null) missingSetup.push("Měsíční záloha"); if (settlementDate === null) missingSetup.push("Datum vyúčtování"); if (findByAliases(hass, ["pocatecni stav vt", "baseline high rate"]) === undefined) missingSetup.push("Počáteční stav VT"); if (findByAliases(hass, ["pocatecni stav nt", "baseline low rate"]) === undefined) missingSetup.push("Počáteční stav NT"); const lastEntity = findByAliases(hass, ["posledni aktivita", "frakon energy last activity"]); const lastUpdated = lastEntity?.last_updated ?? lastEntity?.last_changed ?? null;
  return { connected: true, tariff, countdownSeconds: parseCountdown(countdownEntity?.state), nextChange: nextDate && !Number.isNaN(nextDate.getTime()) ? nextDate.toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" }) : null, todaySchedule: schedules.today, tomorrowSchedule: schedules.tomorrow, currentPrice: numberState(findByAliases(hass, ["hdo aktualni cena", "cez_hdo_currentprice", "aktualni cena", "skutecna cena", "current price"])), batteryPercent: normalizeBattery(numberState(findByAliases(hass, ["stav baterie", "battery state", "battery level"]))), highRateKwh, lowRateKwh, monthlyAdvanceCzk, paidAdvancesCzk: numberState(findByAliases(hass, ["zaplacene zalohy", "billing paid advances"])), projectedAdvancesCzk: numberState(findByAliases(hass, ["zalohy za cele obdobi", "billing projected advances"])), accruedCostCzk: numberState(findByAliases(hass, ["dosavadni naklady", "skutecne naklady", "billing accrued cost"])), currentBalanceCzk: numberState(findByAliases(hass, ["prubezny rozdil", "prubezny preplatek nedoplatek", "billing current balance"])), projectedBalanceCzk: numberState(findByAliases(hass, ["odhad preplatku nedoplatku", "billing projected balance"])), recommendedAdvanceCzk: numberState(findByAliases(hass, ["doporucena zaloha", "billing recommended advance"])), todayConsumptionKwh: numberState(findByAliases(hass, ["spotreba dnes celkem", "today consumption"])), monthConsumptionKwh: numberState(findByAliases(hass, ["spotreba tento mesic", "month consumption"])), settlementDate, lastUpdated, hdoQuality: schedules.today.length > 0 || tariff !== "?" ? "live" : "fallback", missingSetup };
}

export function useHomeAssistant(): HomeAssistant | undefined { return window.__FRAKON_ENERGY_HASS__ ?? window.hass; }
export async function callHomeAssistantWs<T>(hass: HomeAssistant, message: Record<string, unknown>): Promise<T> { if (hass.callWS) return hass.callWS<T>(message); if (hass.connection?.sendMessagePromise) return hass.connection.sendMessagePromise<T>(message); throw new Error("WebSocket Home Assistantu není dostupný."); }
export async function findFrakonEnergyEntryId(hass: HomeAssistant): Promise<string | null> {
  const entries = await callHomeAssistantWs<ConfigEntrySummary[]>(hass, { type: "config_entries/get" });
  const frakonEntries = entries.filter((entry) => entry.domain === "frakon_energy");
  return frakonEntries.find((entry) => !isHdoConfigEntry(entry))?.entry_id ?? frakonEntries[0]?.entry_id ?? null;
}

export function useFrakonEnergyState(): DashboardState { const [revision, setRevision] = useState(0); const hass = useHomeAssistant(); useEffect(() => { const timer = window.setInterval(() => setRevision((value) => value + 1), 1000); let unsubscribe: (() => void) | undefined; hass?.connection?.subscribeEvents?.(() => setRevision((value) => value + 1), "state_changed").then((remove) => { unsubscribe = remove; }).catch(() => undefined); return () => { window.clearInterval(timer); unsubscribe?.(); }; }, [hass]); const source = useMemo(() => readDashboardState(hass), [hass, revision]); const [localCountdown, setLocalCountdown] = useState(source.countdownSeconds); useEffect(() => { setLocalCountdown(source.countdownSeconds); }, [source.countdownSeconds, source.tariff]); useEffect(() => { if (localCountdown === null || localCountdown <= 0) return; const timer = window.setTimeout(() => setLocalCountdown((value) => value === null ? null : Math.max(0, value - 1)), 1000); return () => window.clearTimeout(timer); }, [localCountdown]); return { ...source, countdownSeconds: localCountdown }; }
export function formatCountdown(seconds: number | null): string { if (seconds === null) return "--:--:--"; const hours = Math.floor(seconds / 3600); const minutes = Math.floor((seconds % 3600) / 60); const remaining = seconds % 60; return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`; }
