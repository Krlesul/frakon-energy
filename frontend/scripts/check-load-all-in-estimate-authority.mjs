import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../public/load-all-in-estimate-bridge.js", import.meta.url), "utf8");

assert.equal(source.includes("innerHTML"), false, "backend text must never be rendered with innerHTML");
for (const forbidden of [
  "tariff/customer/propose",
  "tariff/customer/confirm",
  "tariff/customer/manual/propose",
  "authority_method:",
  "all_in_tariff_fingerprint:",
  "source_url",
  "manual_commercial",
  "regulated_evidence",
]) {
  assert.equal(source.includes(forbidden), false, `companion request/UI must not supply ${forbidden}`);
}
assert.match(source, /type: "frakon_energy\/entry\/primary"/, "entry resolution must use the authoritative loaded VisionQ runtime");
assert.match(source, /primary\.provider !== "visionq"/, "primary runtime must be explicitly verified as VisionQ");
assert.match(source, /primary\.loaded !== true/, "primary runtime must be verified as loaded");
assert.match(source, /type: "frakon_energy\/load_profiles\/list"/, "entry resolution must use read-only persisted profile lookup");
assert.match(source, /type: "frakon_energy\/load_plan\/preview_profile"/, "all-in view must use profile preview only");
assert.match(source, /if \(matches\.length !== 1\)/, "profile must resolve exactly once inside the authoritative VisionQ entry");
assert.equal(source.includes('type: "config_entries/get"'), false, "multi-entry VisionQ + HDO installations must not use client-side config entry guessing");
assert.match(source, /estimate\.source !== "confirmed_all_in"/, "all-in source must be confirmed catalog authority");
assert.match(source, /estimate\.fixed_monthly_excluded !== true/, "single-run estimate must exclude fixed monthly fees");
assert.match(source, /SHA256_RE\.test\(record\.all_in_tariff_fingerprint/, "tariff fingerprint must be validated");
assert.match(source, /ALLOWED_AUTHORITY\.has\(record\.authority_method\)/, "authority method must be allowlisted");
assert.match(source, /Math\.abs\(energy - planEnergy\) > 0\.002/, "all-in and spot-plan energy must agree");
assert.match(source, /\["Průměrná cena", "Spot optimalizační průměr"\]/, "spot average label must be explicit");
assert.match(source, /\["Rozsah ceny", "Spot rozsah ceny"\]/, "spot range label must be explicit");
assert.match(source, /\["Odhad ceny", "Spot optimalizační náklad"\]/, "spot cost label must be explicit");
assert.match(source, /All-in VT náklad/, "VT scenario must be rendered");
assert.match(source, /All-in NT náklad/, "NT scenario must be rendered");
assert.match(source, /Fixní měsíční platby nejsou přiřazené jednomu běhu/, "fixed-fee semantics must be explicit");
assert.match(source, /Spot optimalizační náklad není konečná zákaznická cena/, "unavailable all-in must not masquerade as final price");

const previewMessage = source.match(/const message = \{\s*type: "frakon_energy\/load_plan\/preview_profile",[\s\S]*?profile_id: profileId,\s*\};/);
assert.ok(previewMessage, "profile preview request must exist");
for (const required of ["entry_id: resolved.entryId", "profile_id: profileId"]) {
  assert.equal(previewMessage[0].includes(required), true, `profile preview request missing ${required}`);
}
for (const forbidden of ["price", "tariff", "authority", "fingerprint", "source", "manual", "regulated"]) {
  assert.equal(previewMessage[0].toLowerCase().includes(forbidden), false, `profile preview request must not contain ${forbidden}`);
}

console.log("Load all-in estimate frontend authority boundary OK");
