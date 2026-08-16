import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../public/tariff-confirmation-bridge.js", import.meta.url), "utf8");

assert.equal(source.includes("innerHTML"), false, "backend text must not be rendered through innerHTML");
assert.match(source, /if \(url\.protocol !== "https:"\) return null;/, "rendered source links must require HTTPS");
assert.match(source, /if \(url\.protocol !== "https:"\) throw new Error/, "candidate source matching must require HTTPS");

const proposalCall = source.match(/const response = await callWs\(\{\s*type: "frakon_energy\/tariff\/customer\/propose",[\s\S]*?\n\s*\}\);/);
assert.ok(proposalCall, "customer proposal WebSocket call must exist");
for (const required of ["entry_id: context.entryId", "contract: context.contract", "day: context.day", "candidate_fingerprint: candidate.fingerprint"]) {
  assert.equal(proposalCall[0].includes(required), true, `proposal request is missing ${required}`);
}
for (const forbidden of ["source_url", "document_sha256", "price", "bundle", "evidence", "all_in_vt_czk_kwh", "regulated_version_fingerprint"]) {
  assert.equal(proposalCall[0].includes(forbidden), false, `proposal request must not contain ${forbidden}`);
}

const regulatedProposalCall = source.match(/const response = await callWs\(\{\s*type: "frakon_energy\/tariff\/regulated\/official_propose",[\s\S]*?\n\s*\}\);/);
assert.ok(regulatedProposalCall, "official regulated proposal WebSocket call must exist");
for (const required of [
  "entry_id: context.entryId",
  "distributor: context.contract.distributor",
  "distribution_tariff: context.contract.distribution_tariff",
  "breaker_code: `${context.contract.breaker.phases}x${context.contract.breaker.amperes}A`",
  "day: context.day",
]) {
  assert.equal(regulatedProposalCall[0].includes(required), true, `official regulated request is missing ${required}`);
}
for (const forbidden of ["price", "bundle", "evidence", "source_url", "checksum", "variable_components", "fixed_components"]) {
  assert.equal(regulatedProposalCall[0].includes(forbidden), false, `official regulated request must not contain ${forbidden}`);
}

const regulatedConfirmCall = source.match(/const response = await callWs\(\{\s*type: "frakon_energy\/tariff\/regulated\/confirm",[\s\S]*?\n\s*\}\);/);
assert.ok(regulatedConfirmCall, "regulated confirmation WebSocket call must exist");
for (const required of ["entry_id: entryId", "proposal_fingerprint: proposal.proposal_fingerprint"]) {
  assert.equal(regulatedConfirmCall[0].includes(required), true, `regulated confirmation is missing ${required}`);
}
for (const forbidden of ["contract:", "price", "bundle", "evidence", "source_url", "variable_components", "fixed_components"]) {
  assert.equal(regulatedConfirmCall[0].includes(forbidden), false, `regulated confirmation must not contain ${forbidden}`);
}

const confirmCall = source.match(/const response = await callWs\(\{\s*type: "frakon_energy\/tariff\/customer\/confirm",[\s\S]*?\n\s*\}\);/);
assert.ok(confirmCall, "customer confirmation WebSocket call must exist");
for (const required of ["entry_id: entryId", "proposal_fingerprint: proposal.proposal_fingerprint"]) {
  assert.equal(confirmCall[0].includes(required), true, `confirmation request is missing ${required}`);
}
for (const forbidden of ["contract:", "candidate_fingerprint", "source_url", "document_sha256", "price", "bundle", "evidence", "all_in_tariff_fingerprint", "regulated_version_fingerprint"]) {
  assert.equal(confirmCall[0].includes(forbidden), false, `confirmation request must not contain ${forbidden}`);
}

assert.match(
  source,
  /if \(errorCode\(error\) !== "regulated_tariff_not_available"\) throw error;/,
  "regulated bootstrap must fail closed for every backend error except regulated_tariff_not_available",
);
assert.match(source, /response\.server_authored !== true/, "official regulated response must be server-authored");
assert.match(source, /bundle\.confirmed !== false/, "regulated proposal must stay unconfirmed until the explicit fingerprint confirmation");
assert.match(source, /response\.candidate_fingerprint !== candidate\.fingerprint/, "proposal response candidate identity must be checked");
assert.match(source, /response\.contract_fingerprint !== discovery\.contract_fingerprint/, "proposal response contract identity must be checked");
assert.match(source, /response\.document_sha256 !== response\.preview\.supplier_document_sha256/, "supplier document checksum identity must be checked");
assert.match(source, /response\.activation_performed !== false/, "unconfirmed proposal must reject activation");
assert.match(source, /response\[field\] !== proposal\[field\]/, "confirmation immutable references must be checked");

console.log("Tariff confirmation frontend authority boundary OK");
