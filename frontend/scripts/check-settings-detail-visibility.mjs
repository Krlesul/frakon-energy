import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../public/settings-detail-visibility.js", import.meta.url), "utf8");
const sourceIndex = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const packaged = readFileSync(new URL("../../custom_components/frakon_energy/frontend_app/settings-detail-visibility.js", import.meta.url), "utf8");
const packagedIndex = readFileSync(new URL("../../custom_components/frakon_energy/frontend_app/index.html", import.meta.url), "utf8");

assert.equal(source, packaged, "packaged settings detail visibility bridge must match source");
assert.match(sourceIndex, /settings-detail-visibility\.js/, "source dashboard must load settings detail visibility");
assert.match(packagedIndex, /settings-detail-visibility\.js/, "packaged dashboard must load settings detail visibility");

assert.match(source, /frakon-hide-spot-prices/, "spot detail visibility must follow the Spot prices switch");
assert.match(source, /frakon-hide-billing-estimate/, "billing detail visibility must follow billing-related switches");
assert.match(source, /frakon-hide-daily-consumption/, "daily consumption must participate in billing detail visibility");
assert.match(source, /frakon-hide-monthly-consumption/, "monthly consumption must participate in billing detail visibility");
assert.match(source, /frakon-hide-technology-overview/, "technology detail visibility must follow the technology switch");
assert.match(source, /frakon-hide-photovoltaics/, "FVE detail visibility must follow the photovoltaics switch");
assert.match(source, /frakon-hide-energy-flow/, "energy-flow detail visibility must follow the energy-flow switch");
assert.match(source, /site-capacity-settings/, "site and phase capacity details must be gated with energy flow");
assert.match(source, /frakon-energy-dashboard-display-changed/, "visibility changes must apply immediately after a master switch changes");
assert.match(source, /requestAnimationFrame/, "DOM reconciliation must be coalesced instead of running synchronously for every mutation");
assert.doesNotMatch(source, /observer\.observe\([^)]*attributes\s*:\s*true/s, "the bridge must not observe its own hidden-attribute writes");

console.log("settings detail visibility contract OK");
