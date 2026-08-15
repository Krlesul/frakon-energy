import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(
  new URL("../public/mnd-source-confirmation-bridge.js", import.meta.url),
  "utf8",
);

assert.equal(source.includes("innerHTML"), false, "backend text must never be rendered with innerHTML");
assert.match(source, /if \(url\.protocol !== "https:"\) throw new Error/, "MND source URL must require HTTPS");
assert.match(source, /host !== "mnd\.cz" && !host\.endsWith\("\.mnd\.cz"\)/, "MND source URL must require the official mnd.cz domain boundary");
assert.match(source, /const prefix = "\/documents\/view\/";/, "MND source URL must require the official document path");
assert.match(source, /UUID_RE\.test\(identifier\)/, "MND source URL must require a canonical document UUID");

const proposalCall = source.match(/const response = await callWs\(message\);/);
assert.ok(proposalCall, "MND source proposal WebSocket call must exist");
const proposalMessage = source.match(/const message = \{\s*type: "frakon_energy\/tariff\/mnd\/source\/propose",[\s\S]*?\n\s*\};/);
assert.ok(proposalMessage, "MND source proposal request object must exist");
for (const required of [
  "entry_id: context.entryId",
  "source_context: { postcode }",
  "product_name: context.contract.product_name",
  "distributor: context.contract.distributor",
  "contract_kind: context.product.contract_kind",
  "source_url: sourceUrl",
  "valid_from: sourceValidFrom",
]) {
  assert.equal(proposalMessage[0].includes(required), true, `MND proposal request is missing ${required}`);
}
for (const forbidden of [
  "document_sha256",
  "price",
  "czk",
  "all_in",
  "regulated",
  "evidence",
]) {
  assert.equal(proposalMessage[0].toLowerCase().includes(forbidden), false, `MND proposal request must not contain ${forbidden}`);
}

const confirmCall = source.match(/const response = await callWs\(\{\s*type: "frakon_energy\/tariff\/mnd\/source\/confirm",[\s\S]*?\n\s*\}\);/);
assert.ok(confirmCall, "MND source confirmation WebSocket call must exist");
for (const required of [
  "entry_id: currentProposal.context.entryId",
  "proposal_fingerprint: currentProposal.response.proposal_fingerprint",
]) {
  assert.equal(confirmCall[0].includes(required), true, `MND confirmation request is missing ${required}`);
}
for (const forbidden of [
  "source_context",
  "postcode",
  "source_url",
  "document_sha256",
  "product_name",
  "distributor",
  "contract_kind",
  "valid_from",
  "valid_to",
  "price",
  "czk",
]) {
  assert.equal(confirmCall[0].toLowerCase().includes(forbidden), false, `MND confirmation request must not contain ${forbidden}`);
}

const discoveryCall = source.match(/const discovery = await callWs\(\{\s*type: "frakon_energy\/tariff\/discover",[\s\S]*?\n\s*\}\);/);
assert.ok(discoveryCall, "post-confirm MND discovery call must exist");
for (const required of [
  "entry_id: context.entryId",
  "contract: context.contract",
  "day: context.day",
  "source_context: { postcode }",
]) {
  assert.equal(discoveryCall[0].includes(required), true, `post-confirm discovery is missing ${required}`);
}
for (const forbidden of ["price", "czk", "source_url", "document_sha256", "activation"] ) {
  assert.equal(discoveryCall[0].toLowerCase().includes(forbidden), false, `post-confirm discovery request must not contain ${forbidden}`);
}

assert.match(source, /response\.download_performed !== true/, "MND proposal must require a server-side document download");
assert.match(source, /response\.parsing_performed !== false/, "MND proposal must reject parsing");
assert.match(source, /response\.activation_performed !== false/, "MND proposal must reject activation");
assert.match(source, /response\.document_sha256 !== proposal\.response\.document_sha256/, "MND confirmation must keep the exact document SHA-256");
assert.match(source, /candidate\?\.document_sha256 === proposal\.response\.document_sha256/, "post-confirm discovery must require the exact pinned SHA-256");
assert.match(source, /discovery\.persistence_performed/, "post-confirm discovery must reject persistence side effects");
assert.match(source, /Object\.prototype\.hasOwnProperty\.call\(response, "postcode"\)/, "proposal response must reject a raw postcode field");
assert.match(source, /MND parser cen je zatím záměrně zamčený/, "UI must state that MND price parsing remains locked");

console.log("MND source confirmation frontend authority boundary OK");
