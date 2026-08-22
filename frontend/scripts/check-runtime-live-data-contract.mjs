import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

const mountFiles = [
  "src/technology-settings-mount.tsx",
  "src/technology-overview-mount.tsx",
  "src/energy-flow-summary.tsx",
];

for (const path of mountFiles) {
  const source = read(path);
  if (/new\s+MutationObserver\s*\(\s*mount\s*\)/.test(source)) {
    throw new Error(`${path}: MutationObserver must not call mount directly; that recreates the React/DOM feedback loop.`);
  }
  if (!/new\s+MutationObserver\s*\(\s*reconcileStructure\s*\)/.test(source)) {
    throw new Error(`${path}: structural MutationObserver guard is missing.`);
  }
  if (!source.includes("root?.unmount()")) {
    throw new Error(`${path}: React root must be unmounted before removing a host.`);
  }
}

const settings = read("src/technology-settings.tsx");
if (!settings.includes("findFrakonEnergyEntryId")) {
  throw new Error("technology-settings.tsx must route discovery through the primary FRAKON Energy entry selector.");
}
if (/useCallback\([\s\S]*?\},\s*\[hass\]\s*\)/.test(settings)) {
  throw new Error("technology-settings.tsx must not recreate entity discovery loading on every hass object update.");
}

const helper = read("src/home-assistant.ts");
for (const alias of [
  "hdo tarif",
  "hdo odpocet",
  "hdo dalsi prepnuti",
  "hdo dnesni rozvrh",
  "hdo aktualni cena",
]) {
  if (!helper.includes(`\"${alias}\"`)) {
    throw new Error(`home-assistant.ts is missing the live HDO alias: ${alias}`);
  }
}
if (!helper.includes('type: "frakon_energy/entry/primary"')) {
  throw new Error("Primary FRAKON Energy entry must be resolved by the authoritative server endpoint.");
}
if (helper.includes('type: "config_entries/get"')) {
  throw new Error("home-assistant.ts must not guess the primary runtime from the client-side config-entry list.");
}

const capacity = read("src/site-capacity-settings.tsx");
if (!capacity.includes("findFrakonEnergyEntryId")) {
  throw new Error("site-capacity-settings.tsx must use the authoritative VisionQ entry resolver.");
}
if (capacity.includes('type: "config_entries/get"')) {
  throw new Error("site-capacity-settings.tsx still contains the old first-config-entry routing.");
}

const settlement = read("src/phase-settlement-status.tsx");
if (!settlement.includes("findFrakonEnergyEntryId")) {
  throw new Error("phase-settlement-status.tsx must use the authoritative VisionQ entry resolver.");
}

for (const path of [
  "public/tariff-confirmation-bridge.js",
  "public/manual-tariff-entry-bridge.js",
  "public/supplier-parser-preview-bridge.js",
  "public/mnd-source-confirmation-bridge.js",
  "public/legacy-tariff-migration-bridge.js",
  "public/load-all-in-estimate-bridge.js",
]) {
  const source = read(path);
  if (!source.includes('frakon_energy/entry/primary')) {
    throw new Error(`${path}: tariff/profile bridge must bind to the active VisionQ runtime.`);
  }
  if (source.includes('type: "config_entries/get"')) {
    throw new Error(`${path}: multi-entry installations must not be rejected by config_entries/get heuristics.`);
  }
}

const technologyOverviewCss = read("src/technology-overview.css");
for (const forbidden of [
  ".frakon-no-tariff .tariff-card",
  ".frakon-no-tariff .hdo-plan-card",
  ".frakon-no-tariff .bottom-nav button:nth-child(3)",
]) {
  if (technologyOverviewCss.includes(forbidden)) {
    throw new Error(`Core tariff/HDO UI must never be hidden by technology discovery: ${forbidden}`);
  }
}

const tariffWizard = read("src/tariff-wizard.tsx");
if (tariffWizard.includes("}, [hass]);")) {
  throw new Error("Tariff wizard must not reset catalog/product on every Home Assistant state object update.");
}
if (!tariffWizard.includes("[hass?.connection]")) {
  throw new Error("Tariff wizard must initialize against the stable Home Assistant connection.");
}
if (!tariffWizard.includes("TARIFF_PREVIEW_REQUEST_TIMEOUT_MS = 45_000") || !tariffWizard.includes("withRequestTimeout(")) {
  throw new Error("Tariff preview must have a finite frontend request watchdog.");
}

const mainUi = read("src/main.tsx");
for (const forbidden of ["DEFAULT_NT_SCHEDULE", "fallbackNtIntervals", "Používá se náhradní plán"]) {
  if (mainUi.includes(forbidden)) {
    throw new Error(`Production HDO UI must not fabricate fallback intervals: ${forbidden}`);
  }
}
if (!mainUi.includes("state.countdownSeconds !== null && state.nextChange !== null")) {
  throw new Error("HDO countdown must only render when the next transition is also available.");
}
if (!mainUi.includes("hasTrustedTransition ? <div className=\"countdown\"")) {
  throw new Error("HDO countdown must be conditionally rendered behind the trusted-transition guard.");
}
if (!helper.includes("findStructuredScheduleEntity")) {
  throw new Error("home-assistant.ts must discover structured HDO schedules even when entity names are customized.");
}

const displaySettings = read("src/dashboard-display-settings.tsx");
for (const required of [
  'frakon_energy/dashboard_display_settings/get',
  'frakon_energy/dashboard_display_settings/set',
  'show_hdo',
  'show_spot_prices',
  'show_daily_consumption',
  'show_photovoltaics',
  'show_energy_flow',
]) {
  if (!displaySettings.includes(required)) {
    throw new Error(`Dashboard visibility settings are missing contract token: ${required}`);
  }
}
const displayCss = read("src/dashboard-display-settings.css");
for (const required of [
  ".frakon-hide-technology-overview #frakon-technology-overview-host",
  ".frakon-hide-energy-flow #frakon-energy-flow-host",
  '[data-technology="photovoltaics"]',
]) {
  if (!displayCss.includes(required)) {
    throw new Error(`Dashboard visibility CSS is missing ${required}.`);
  }
}

const spotSettings = read("src/spot-price-settings.tsx");
if (!spotSettings.includes("settings-stack--full") || !spotSettings.includes("commissioning-hardening.css")) {
  throw new Error("Spot/load settings must use the full-width hardened settings stack.");
}

const css = read("src/commissioning-hardening.css");
for (const required of [".spot-settings-grid", ".settings-stack--full", ".bottom-nav"]) {
  if (!css.includes(required)) {
    throw new Error(`Commissioning CSS is missing ${required}.`);
  }
}

console.log("Runtime live-data/discovery commissioning contract OK");
