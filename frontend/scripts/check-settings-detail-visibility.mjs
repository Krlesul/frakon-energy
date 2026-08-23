import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../public/settings-detail-visibility.js", import.meta.url), "utf8");
const sourceIndex = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const packaged = readFileSync(new URL("../../custom_components/frakon_energy/frontend_app/settings-detail-visibility.js", import.meta.url), "utf8");
const packagedIndex = readFileSync(new URL("../../custom_components/frakon_energy/frontend_app/index.html", import.meta.url), "utf8");

assert.equal(source, packaged, "packaged settings detail visibility bridge must match source");
assert.match(sourceIndex, /settings-detail-visibility\.js/, "source dashboard must load settings detail visibility");
assert.match(packagedIndex, /settings-detail-visibility\.js/, "packaged dashboard must load settings detail visibility");

for (const guard of [
  "spot-prices",
  "billing-estimate",
  "daily-consumption",
  "monthly-consumption",
  "technology-overview",
  "photovoltaics",
  "energy-flow",
]) {
  assert.ok(source.includes(`isDisabled("${guard}")`), `missing settings visibility guard: ${guard}`);
}
assert.match(source, /site-capacity-settings/, "site and phase capacity details must be gated with energy flow");
assert.match(source, /frakon-energy-dashboard-display-changed/, "visibility changes must apply immediately after a master switch changes");
assert.match(source, /requestAnimationFrame/, "DOM reconciliation must be coalesced instead of running synchronously for every mutation");
assert.doesNotMatch(source, /observer\.observe\([^)]*attributes\s*:\s*true/s, "the bridge must not observe its own hidden-attribute writes");

console.log("settings detail visibility contract OK");
