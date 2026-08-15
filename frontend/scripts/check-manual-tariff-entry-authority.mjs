import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../public/manual-tariff-entry-bridge.js", import.meta.url), "utf8");

assert.equal(source.includes("innerHTML"), false, "backend text must never be rendered through innerHTML");
assert.equal(source.includes("manual/confirm"), false, "manual UI must not create a second confirmation boundary");
assert.equal(source.includes("confirm_customer_tariff_proposal"), false, "frontend must not expose backend confirmation internals");
assert.match(source, /if \(url\.protocol !== "https:"\) return null;/, "rendered source links must require HTTPS");
assert.match(source, /if \(url\.protocol !== "https:"\) throw new Error/, "candidate matching must require HTTPS");
assert.match(source, /candidate\?\.match_score === 100/, "manual source must be an exact 100-score candidate");
assert.match(source, /exact\.candidate\.fingerprint !== currentCandidate\.candidate\.fingerprint/, "candidate must be rediscovered before proposal staging");

const proposalCall = source.match(/const response = await callWs\(message\);\s*if \(revision !== startRevision[\s\S]*?validateManualProposal/);
assert.ok(proposalCall, "manual proposal call must exist and be validated");
const proposalMessage = source.match(/const message = \{\s*type: "frakon_energy\/tariff\/customer\/manual\/propose",[\s\S]*?manual_commercial: manualValues,\s*\};/);
assert.ok(proposalMessage, "manual customer proposal request must exist");
for (const required of [
  "entry_id: context.entryId",
  "contract: context.contract",
  "day: context.day",
  "candidate_fingerprint: exact.candidate.fingerprint",
  "manual_commercial: manualValues",
]) {
  assert.equal(proposalMessage[0].includes(required), true, `manual proposal is missing ${required}`);
}
for (const forbidden of [
  "authority_method",
  "source_url",
  "document_sha256",
  "regulated",
  "regulated_evidence",
  "all_in_vt_czk_kwh",
  "all_in_nt_czk_kwh",
  "fixed_monthly_total_czk",
  "parser_name",
  "includes_vat",
]) {
  assert.equal(proposalMessage[0].includes(forbidden), false, `manual proposal must not contain ${forbidden}`);
}
assert.match(source, /if \(sourceContext\) message\.source_context = sourceContext;/, "operational source context may only be attached explicitly when required");

const confirmCall = source.match(/const response = await callWs\(\{\s*type: "frakon_energy\/tariff\/customer\/confirm",[\s\S]*?\n\s*\}\);/);
assert.ok(confirmCall, "shared customer confirmation call must exist");
for (const required of ["entry_id: entryId", "proposal_fingerprint: proposal.proposal_fingerprint"]) {
  assert.equal(confirmCall[0].includes(required), true, `confirmation request is missing ${required}`);
}
for (const forbidden of [
  "contract:",
  "manual_commercial",
  "source_context",
  "postcode",
  "candidate_fingerprint",
  "source_url",
  "document_sha256",
  "regulated",
  "all_in_tariff_fingerprint",
]) {
  assert.equal(confirmCall[0].includes(forbidden), false, `confirmation request must not contain ${forbidden}`);
}

for (const guard of [
  'response.authority_method !== "manual_user_entry"',
  "response.manual_entry !== true",
  "response.download_performed !== true",
  "response.parsing_performed !== false",
  "response.activation_performed !== false",
  'response.preview.authority_method !== "manual_user_entry"',
  "response.preview.all_in_ready !== true",
  "response.preview.parsing_performed !== false",
  "response.preview.persistence_performed !== false",
  "response.preview.activation_performed !== false",
]) {
  assert.equal(source.includes(guard), true, `manual response validation is missing ${guard}`);
}
assert.match(source, /Object\.prototype\.hasOwnProperty\.call\(response, "postcode"\)/, "raw postcode must be rejected from proposal response");
assert.match(source, /manual\.high_rate_czk_per_kwh !== manualValues\.high_rate_czk_per_kwh/, "manual VT must round-trip exactly");
assert.match(source, /manual\.low_rate_czk_per_kwh !== manualValues\.low_rate_czk_per_kwh/, "manual NT must round-trip exactly");
assert.match(source, /manual\.supplier_standing_czk_month !== manualValues\.supplier_standing_czk_month/, "manual standing charge must round-trip exactly");
assert.match(source, /response\[field\] !== proposal\[field\]/, "confirmation immutable references must be checked");

console.log("Manual tariff entry frontend authority boundary OK");
