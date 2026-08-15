import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../public/supplier-parser-preview-bridge.js", import.meta.url), "utf8");

assert.equal(source.includes("innerHTML"), false, "backend text must not be rendered through innerHTML");
assert.equal(source.includes("tariff/customer/propose"), false, "automatic parser companion must not stage customer tariffs");
assert.equal(source.includes("tariff/customer/confirm"), false, "automatic parser companion must not confirm customer tariffs");
assert.equal(source.includes("mnd_commercial"), false, "MND parser authority must remain unavailable");
assert.match(source, /eon: "eon_commercial_v1"/, "E.ON exact parser must be explicitly authorized");
assert.match(source, /pre: "pre_commercial_v1"/, "PRE exact parser must be explicitly authorized");
assert.match(source, /candidate\?\.match_score === 100/, "automatic parsing must require a 100-score candidate");
assert.match(source, /normalizeUrl\(candidate\?\.source_url\) === sourceUrl/, "candidate source URL must match the displayed official source");
assert.match(source, /if \(url\.protocol !== "https:"\) throw new Error/, "candidate and preview sources must require HTTPS");

const parseCall = source.match(/const response = await callWs\(\{\s*type: "frakon_energy\/tariff\/parse_preview",[\s\S]*?\n\s*\}\);/);
assert.ok(parseCall, "parse_preview WebSocket call must exist");
for (const required of [
  "entry_id: context.entryId",
  "contract: context.contract",
  "day: context.day",
  "candidate_fingerprint: candidate.fingerprint",
]) {
  assert.equal(parseCall[0].includes(required), true, `parse preview request is missing ${required}`);
}
for (const forbidden of [
  "price",
  "source_url",
  "document_sha256",
  "authority_method",
  "regulated",
  "evidence",
  "all_in",
]) {
  assert.equal(parseCall[0].includes(forbidden), false, `parse preview request must not contain ${forbidden}`);
}

for (const guard of [
  "response.download_performed !== true",
  "response.parsing_performed !== true",
  "response.persistence_performed !== false",
  "response.activation_performed !== false",
  "preview.parsing_performed !== true",
  "preview.persistence_performed !== false",
  "preview.activation_performed !== false",
  'preview.price_scope !== "supplier_commercial"',
  "preview.includes_vat !== true",
  "preview.extraction_confidence !== 100",
]) {
  assert.equal(source.includes(guard), true, `parser response validation is missing ${guard}`);
}
assert.match(source, /response\.candidate_fingerprint !== candidate\.fingerprint/, "candidate fingerprint identity must be checked");
assert.match(source, /response\.contract_fingerprint !== discovery\.contract_fingerprint/, "contract fingerprint identity must be checked");
assert.match(source, /response\.document_sha256 !== preview\.document_sha256/, "document SHA identity must be checked");
assert.match(source, /preview\.parser_name !== SUPPORTED_PARSERS\[context\.contract\.supplier\]/, "supplier parser identity must be checked");
assert.match(source, /"tariff-price-preview frakon-auto-parser-preview"/, "automatic result must render the standard price-preview class");
assert.match(source, /"tariff-price-preview__source"/, "automatic result must render the standard source anchor");
assert.match(source, /card\.classList\.add\("selected"\)/, "exact candidate card must be marked selected for the confirmation bridge");
assert.match(source, /frakon-auto-parser-available #frakon-manual-tariff-entry-bridge/, "manual fallback must remain hidden while automatic parsing is available");
assert.match(source, /automaticFailed = true/, "manual fallback must become available after automatic parser failure");

console.log("E.ON/PRE automatic parser frontend authority boundary OK");
