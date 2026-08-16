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
if (!helper.includes("frakonEntries.find((entry) => !isHdoConfigEntry(entry))")) {
  throw new Error("Primary FRAKON Energy config entry selection must prefer the non-HDO entry.");
}

console.log("Runtime live-data/discovery contract OK");
