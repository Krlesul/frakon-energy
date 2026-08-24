import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../public/current-all-in-price-bridge.js", import.meta.url), "utf8");
const packaged = readFileSync(new URL("../../custom_components/frakon_energy/frontend_app/current-all-in-price-bridge.js", import.meta.url), "utf8");
const sourceIndex = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const packagedIndex = readFileSync(new URL("../../custom_components/frakon_energy/frontend_app/index.html", import.meta.url), "utf8");

assert.equal(source, packaged, "packaged confirmed current-price bridge must match source");
assert.match(sourceIndex, /current-all-in-price-bridge\.js/, "source dashboard must load confirmed current-price bridge");
assert.match(packagedIndex, /current-all-in-price-bridge\.js/, "packaged dashboard must load confirmed current-price bridge");

assert.match(source, /frakon_energy\/entry\/primary/, "bridge must resolve the primary FRAKON entry");
assert.match(source, /frakon_energy\/tariff\/diagnostics/, "bridge must use read-only confirmed tariff diagnostics");
assert.match(source, /all_in_vt_czk_kwh/, "VT price must come from confirmed all-in diagnostics");
assert.match(source, /all_in_nt_czk_kwh/, "NT price must come from confirmed all-in diagnostics");
assert.match(source, /priceAuthority = price === null \? "unavailable" : "confirmed_all_in"/, "rendered price must carry confirmed authority state");
assert.match(source, /REQUEST_TIMEOUT_MS = 12000/, "WebSocket lookup must be bounded");
assert.match(source, /requestAnimationFrame/, "DOM reconciliation must be coalesced");
assert.doesNotMatch(source, /hdo aktualni cena|cez_hdo_currentprice/i, "bridge must not use the legacy HDO price as billing authority");
assert.match(source, /read_only !== true/, "diagnostics response must be explicitly read-only");

console.log("confirmed current all-in price bridge contract OK");
