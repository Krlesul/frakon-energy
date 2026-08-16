import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(
  new URL("../public/legacy-tariff-migration-bridge.js", import.meta.url),
  "utf8",
);
const sourceIndex = await readFile(new URL("../index.html", import.meta.url), "utf8");
const packagedIndex = await readFile(
  new URL("../../custom_components/frakon_energy/frontend_app/index.html", import.meta.url),
  "utf8",
);

assert.equal(source.includes("innerHTML"), false, "backend text must never be rendered through innerHTML");
assert.match(source, /const SHA256_RE = \/\^\[0-9a-f\]\{64\}\$\//, "migration fingerprint must be validated as SHA-256");
assert.equal(source.includes('result.source !== "legacy_options"'), true, "proposal/confirmation source must stay legacy_options");
assert.equal(source.includes('result.authority_method !== "legacy_manual_import"'), true, "migration authority must stay legacy_manual_import");
for (const guard of [
  "result.component_breakdown_available !== false",
  "result.official_provenance_available !== false",
  "result.historical_only !== true",
  "result.live_pricing_changed !== false",
  "result.activation_performed !== false",
]) {
  assert.equal(source.includes(guard), true, `migration response validation is missing ${guard}`);
}
assert.equal(source.includes("let errorState = null;"), true, "errors must have persistent render state");
assert.match(source, /else if \(errorState\) renderError\(root\);/, "sync must preserve visible backend errors");

const proposalCall = source.match(
  /const result = await callWs\(\{\s*type: "frakon_energy\/tariff\/legacy\/propose",[\s\S]*?\n\s*\}\);/,
);
assert.ok(proposalCall, "legacy migration proposal call must exist");
for (const required of [
  'type: "frakon_energy/tariff/legacy/propose"',
  "entry_id: entryId",
  "valid_from: validFrom",
  "valid_to: validTo",
]) {
  assert.equal(proposalCall[0].includes(required), true, `migration proposal is missing ${required}`);
}
for (const forbidden of [
  "price_vt_czk_kwh",
  "price_nt_czk_kwh",
  "fixed_monthly_czk",
  "high_rate_czk_per_kwh",
  "low_rate_czk_per_kwh",
  "authority_method",
  "source_url",
  "checksum",
  "supplier",
  "product_name",
  "component_breakdown",
  "official_provenance",
  "fingerprint",
]) {
  assert.equal(proposalCall[0].includes(forbidden), false, `migration proposal must not contain ${forbidden}`);
}

const confirmCall = source.match(
  /const result = await callWs\(\{\s*type: "frakon_energy\/tariff\/legacy\/confirm",[\s\S]*?\n\s*\}\);/,
);
assert.ok(confirmCall, "legacy migration confirmation call must exist");
for (const required of [
  'type: "frakon_energy/tariff/legacy/confirm"',
  "entry_id: expected.entryId",
  "snapshot_fingerprint: expected.fingerprint",
]) {
  assert.equal(confirmCall[0].includes(required), true, `migration confirmation is missing ${required}`);
}
for (const forbidden of [
  "valid_from",
  "valid_to",
  "price_vt_czk_kwh",
  "price_nt_czk_kwh",
  "fixed_monthly_czk",
  "high_rate_czk_per_kwh",
  "low_rate_czk_per_kwh",
  "authority_method",
  "source_url",
  "checksum",
  "supplier",
  "product_name",
]) {
  assert.equal(confirmCall[0].includes(forbidden), false, `migration confirmation must not contain ${forbidden}`);
}

assert.match(source, /result\.entry_id !== context\.entryId/, "proposal must bind to the exact config entry");
assert.match(source, /result\.valid_from !== context\.validFrom \|\| result\.valid_to !== context\.validTo/, "proposal must round-trip the exact migration window");
assert.match(source, /result\.entry_id !== expected\.entryId \|\| result\.fingerprint !== expected\.fingerprint/, "confirmation must bind to the staged fingerprint");
assert.match(source, /result\.valid_from !== expected\.validFrom \|\| result\.valid_to !== expected\.validTo/, "confirmation must preserve the staged window");
assert.match(source, /normalizeDecimal\(result\.high_rate_czk_per_kwh,[\s\S]*?!== expected\.highRate/, "server VT must remain unchanged between proposal and confirmation");
assert.match(source, /normalizeDecimal\(result\.low_rate_czk_per_kwh,[\s\S]*?!== expected\.lowRate/, "server NT must remain unchanged between proposal and confirmation");
assert.match(source, /normalizeDecimal\(result\.fixed_monthly_czk,[\s\S]*?!== expected\.fixedMonthly/, "server fixed monthly price must remain unchanged between proposal and confirmation");

const editableInputs = [...source.matchAll(/input\.type = "([^"]+)";/g)].map((match) => match[1]);
assert.deepEqual(editableInputs, ["date"], "legacy migration UI must expose only date inputs");
assert.equal(source.includes("inputMode"), false, "legacy prices must never become editable numeric inputs");

assert.equal(
  (sourceIndex.match(/src="\/legacy-tariff-migration-bridge\.js"/g) ?? []).length,
  1,
  "frontend index must load the migration bridge exactly once",
);
assert.equal(
  (packagedIndex.match(/src="\.\/legacy-tariff-migration-bridge\.js"/g) ?? []).length,
  1,
  "packaged index must load the migration bridge exactly once",
);

console.log("Legacy tariff migration frontend authority boundary OK");
