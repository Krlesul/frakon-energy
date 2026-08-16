const MND_BRIDGE_ID = "frakon-mnd-source-confirmation-bridge";
const MND_STYLE_ID = `${MND_BRIDGE_ID}-style`;
const SHA256_RE = /^[0-9a-f]{64}$/;
const POSTCODE_RE = /^[1-7]\d{4}$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

let revision = 0;
let busy = false;
let currentProposal = null;
let currentConfirmation = null;
let currentDiscovery = null;
let draft = { postcode: "", sourceUrl: "", sourceValidFrom: "" };

function hassObject() {
  return window.__FRAKON_ENERGY_HASS__ ?? window.hass;
}

async function callWs(message) {
  const hass = hassObject();
  if (!hass) throw new Error("Home Assistant není dostupný.");
  if (typeof hass.callWS === "function") return hass.callWS(message);
  if (typeof hass.connection?.sendMessagePromise === "function") {
    return hass.connection.sendMessagePromise(message);
  }
  throw new Error("WebSocket Home Assistantu není dostupný.");
}

function readableError(error) {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string") return error;
  if (error && typeof error === "object") {
    if (typeof error.message === "string") return error.message;
    if (typeof error.error === "string") return error.error;
  }
  return "Požadavek se nepodařilo bezpečně dokončit.";
}

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? "" : String(value);
  return node;
}

function wizard() {
  return document.querySelector(".tariff-wizard");
}

function fieldControl(labelText) {
  const labels = wizard()?.querySelectorAll(".tariff-wizard__form-grid label") ?? [];
  for (const label of labels) {
    const caption = label.querySelector(":scope > span:first-child")?.textContent?.trim();
    if (caption !== labelText) continue;
    const control = label.querySelector("select, input");
    if (control) return control;
  }
  return null;
}

function requiredValue(labelText) {
  const control = fieldControl(labelText);
  const value = control?.value?.trim();
  if (!value) throw new Error(`Chybí hodnota pole „${labelText}“.`);
  return value;
}

function integerValue(labelText) {
  const value = Number(requiredValue(labelText));
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error(`Pole „${labelText}“ nemá platnou celočíselnou hodnotu.`);
  }
  return value;
}

function normalizePostcode(rawValue) {
  const normalized = String(rawValue ?? "").replace(/\s+/g, "");
  if (!POSTCODE_RE.test(normalized)) {
    throw new Error("PSČ musí být platné české pětimístné PSČ.");
  }
  return normalized;
}

function normalizeIsoDate(rawValue, label) {
  const value = String(rawValue ?? "").trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new Error(`${label} musí být datum ve formátu RRRR-MM-DD.`);
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) {
    throw new Error(`${label} není platné kalendářní datum.`);
  }
  return value;
}

function normalizeMndDocumentUrl(rawValue) {
  let url;
  try {
    url = new URL(String(rawValue ?? "").trim());
  } catch {
    throw new Error("Zadejte úplnou oficiální HTTPS adresu MND ceníku.");
  }
  if (url.protocol !== "https:") throw new Error("MND ceník musí používat HTTPS.");
  if (url.username || url.password) throw new Error("MND URL nesmí obsahovat přihlašovací údaje.");
  if (url.port && url.port !== "443") throw new Error("MND URL nesmí používat nestandardní HTTPS port.");
  const host = url.hostname.toLowerCase().replace(/\.$/, "");
  if (host !== "mnd.cz" && !host.endsWith(".mnd.cz")) {
    throw new Error("Zdroj musí být na oficiální doméně mnd.cz.");
  }
  if (url.search || url.hash) throw new Error("MND URL nesmí obsahovat query ani fragment.");
  const prefix = "/documents/view/";
  if (!url.pathname.startsWith(prefix)) {
    throw new Error("MND zdroj musí mít tvar /documents/view/<uuid>.");
  }
  const identifier = url.pathname.slice(prefix.length);
  if (!UUID_RE.test(identifier) || url.pathname !== `${prefix}${identifier}`) {
    throw new Error("MND zdroj musí obsahovat právě jeden canonical UUID dokumentu.");
  }
  return url.href;
}

function safeMndLink(label, rawUrl) {
  let href;
  try {
    href = normalizeMndDocumentUrl(rawUrl);
  } catch {
    return null;
  }
  const link = text("a", "", label);
  link.href = href;
  link.target = "_blank";
  link.rel = "noreferrer";
  return link;
}

async function resolveEntryId() {
  const primary = await callWs({ type: "frakon_energy/entry/primary" });
  if (!primary || primary.provider !== "visionq" || primary.loaded !== true || typeof primary.entry_id !== "string") {
    throw new Error("Backend neurčil aktivní VisionQ konfiguraci FRAKON Energy.");
  }
  return primary.entry_id;
}

async function exactMndContext() {
  const supplier = requiredValue("Dodavatel");
  if (supplier !== "mnd") throw new Error("MND source bridge je dostupný pouze pro dodavatele MND.");
  const productName = requiredValue("Produkt ze smlouvy");
  const distributor = requiredValue("Distribuční území");
  const distributionTariff = requiredValue("Distribuční sazba");
  const phases = integerValue("Počet fází");
  const amperes = integerValue("Hlavní jistič");
  const contractValidFrom = requiredValue("Smlouva platí od");
  const day = requiredValue("Ceník ověřit k datu");
  const entryId = await resolveEntryId();

  const catalog = await callWs({ type: "frakon_energy/tariff/catalog", entry_id: entryId });
  if (
    !catalog ||
    !Array.isArray(catalog.suppliers) ||
    catalog.download_performed ||
    catalog.parsing_performed ||
    catalog.persistence_performed ||
    catalog.activation_performed
  ) {
    throw new Error("Backend katalog MND neprošel read-only kontrolou.");
  }
  const group = catalog.suppliers.find((item) => item?.supplier === "mnd");
  const products = Array.isArray(group?.products) ? group.products : [];
  const matches = products.filter((item) => item?.product_name === productName);
  if (matches.length !== 1) throw new Error("Vybraný MND produkt není v backend katalogu jednoznačný.");
  const product = matches[0];
  if (product.price_scope !== "supplier_commercial" || product.requires_document_resolver !== true) {
    throw new Error("Vybraný MND produkt nemá očekávanou resolver boundary.");
  }
  if (product.contract_kind !== "fixed" && product.contract_kind !== "indefinite") {
    throw new Error("Backend vrátil nepodporovaný typ MND smlouvy.");
  }

  let fixationEnd = null;
  if (product.contract_kind === "fixed") fixationEnd = normalizeIsoDate(requiredValue("Fixace do"), "Konec fixace");
  const contract = {
    schema_version: 1,
    supplier: "mnd",
    distributor,
    product_name: productName,
    contract_kind: product.contract_kind,
    distribution_tariff: distributionTariff,
    breaker: { phases, amperes },
    valid_from: normalizeIsoDate(contractValidFrom, "Začátek smlouvy"),
    valid_to: null,
    fixation_end: fixationEnd,
    customer_confirmed: false,
  };

  return { entryId, product, contract, day: normalizeIsoDate(day, "Datum ověření"), fixationEnd };
}

function bridgeRoot() {
  return document.getElementById(MND_BRIDGE_ID);
}

function ensureRoot() {
  let root = bridgeRoot();
  if (root) return root;
  const target = wizard();
  if (!target?.parentNode) return null;
  root = document.createElement("section");
  root.id = MND_BRIDGE_ID;
  root.className = "frakon-mnd-source-bridge";
  target.parentNode.insertBefore(root, target.nextSibling);
  return root;
}

function removeRoot() {
  bridgeRoot()?.remove();
}

function resetState({ clearDraft = true } = {}) {
  revision += 1;
  busy = false;
  currentProposal = null;
  currentConfirmation = null;
  currentDiscovery = null;
  if (clearDraft) draft = { postcode: "", sourceUrl: "", sourceValidFrom: "" };
  removeRoot();
  queueMicrotask(syncBridge);
}

function renderNotice(root, title, message, kind = "info") {
  root.replaceChildren();
  const notice = text("div", `frakon-mnd-source__notice ${kind}`, "");
  notice.append(text("b", "", title), text("span", "", message));
  root.append(notice);
}

function labeledInput(labelText, value, placeholder, onInput, type = "text") {
  const label = text("label", "frakon-mnd-source__field", "");
  label.append(text("span", "", labelText));
  const input = document.createElement("input");
  input.type = type;
  input.value = value;
  input.placeholder = placeholder;
  input.autocomplete = "off";
  input.spellcheck = false;
  input.addEventListener("input", () => onInput(input.value));
  label.append(input);
  return label;
}

function appendIdentity(parent, label, value) {
  const item = text("div", "", "");
  item.append(text("span", "", label), text("b", "", value));
  parent.append(item);
}

function renderIdle() {
  const root = ensureRoot();
  if (!root || busy || currentProposal) return;
  root.replaceChildren();
  const panel = text("div", "frakon-mnd-source__panel", "");
  const header = text("div", "frakon-mnd-source__header", "");
  const heading = text("div", "", "");
  heading.append(
    text("span", "frakon-mnd-source__eyebrow", "MND · krok před cenovým náhledem"),
    text("h3", "", "Potvrdit přesný oficiální MND ceník"),
  );
  header.append(heading, text("span", "frakon-mnd-source__badge pending", "Cenová autorita zamčená"));
  panel.append(header);
  panel.append(
    text(
      "p",
      "frakon-mnd-source__lead",
      "MND vybírá ceník dynamicky podle PSČ. FRAKON Energy proto nejdřív serverově stáhne přesnou oficiální PDF adresu, ověří ji a spočítá SHA-256. PSČ se neukládá do cenové provenance ani do potvrzeného resolveru.",
    ),
  );

  const form = text("div", "frakon-mnd-source__form", "");
  form.append(
    labeledInput("PSČ pro výběr ceníku", draft.postcode, "412 01", (value) => { draft.postcode = value; }),
    labeledInput("Oficiální MND URL ceníku", draft.sourceUrl, "https://prod.mnd.cz/documents/view/…", (value) => { draft.sourceUrl = value; }),
    labeledInput("Ceník platí od", draft.sourceValidFrom, "RRRR-MM-DD", (value) => { draft.sourceValidFrom = value; }, "date"),
  );
  panel.append(form);

  const instructions = text("div", "frakon-mnd-source__notice info", "");
  instructions.append(
    text("b", "", "Jak získat správnou URL"),
    text("span", "", "Na webu MND zadejte stejné PSČ, otevřete aktuální ceník vybraného produktu a zkopírujte konečnou adresu /documents/view/<uuid>. Datum „ceník platí od“ opište z PDF. Backend URL i PDF znovu ověří."),
  );
  panel.append(instructions);

  const button = text("button", "frakon-mnd-source__primary", "Serverově ověřit a připravit zdroj");
  button.type = "button";
  button.addEventListener("click", prepareMndSourceProposal);
  const actions = text("div", "frakon-mnd-source__actions", "");
  actions.append(
    button,
    text("span", "frakon-mnd-source__safety", "Tento krok nepotvrzuje cenu a nic neaktivuje. Vznikne pouze immutable proposal dokumentu."),
  );
  panel.append(actions);
  root.append(panel);
}

function renderLoading(title, message) {
  const root = ensureRoot();
  if (!root) return;
  renderNotice(root, title, message, "loading");
}

function renderError(error, { confirmed = false } = {}) {
  const root = ensureRoot();
  if (!root) return;
  root.replaceChildren();
  const panel = text("div", "frakon-mnd-source__panel", "");
  const notice = text("div", "frakon-mnd-source__notice error", "");
  notice.append(
    text("b", "", confirmed ? "Zdroj byl potvrzen, ale následná kontrola discovery selhala" : "MND zdroj zůstává nepotvrzený"),
    text("span", "", readableError(error)),
  );
  const retry = text("button", "frakon-mnd-source__secondary", confirmed ? "Znovu ověřit discovery" : "Upravit údaje a zkusit znovu");
  retry.type = "button";
  retry.addEventListener("click", async () => {
    if (confirmed && currentProposal && currentConfirmation) {
      await retryDiscovery();
      return;
    }
    currentProposal = null;
    currentConfirmation = null;
    currentDiscovery = null;
    busy = false;
    renderIdle();
  });
  panel.append(notice, retry);
  root.append(panel);
}

function renderProposal() {
  const root = ensureRoot();
  if (!root || !currentProposal) return;
  root.replaceChildren();
  const proposal = currentProposal.response;
  const confirmed = currentConfirmation?.confirmed === true;
  const verified = confirmed && currentDiscovery?.candidate;
  const panel = text("div", `frakon-mnd-source__panel${verified ? " confirmed" : ""}`, "");
  const header = text("div", "frakon-mnd-source__header", "");
  const heading = text("div", "", "");
  heading.append(
    text("span", "frakon-mnd-source__eyebrow", confirmed ? "MND · potvrzený dokument" : "MND · serverově ověřený návrh"),
    text("h3", "", confirmed ? "Přesný MND zdroj je potvrzen" : "Backend ověřil přesný MND PDF dokument"),
  );
  header.append(
    heading,
    text("span", `frakon-mnd-source__badge ${verified ? "confirmed" : "pending"}`, verified ? "Resolver aktivní" : confirmed ? "Ověřuji resolver" : "Čeká na potvrzení"),
  );
  panel.append(header);

  const identity = text("div", "frakon-mnd-source__identity", "");
  appendIdentity(identity, "Produkt", proposal.product_name);
  appendIdentity(identity, "Distributor", proposal.distributor);
  appendIdentity(identity, "Typ smlouvy", proposal.contract_kind);
  appendIdentity(identity, "Platnost zdroje", proposal.valid_to ? `${proposal.valid_from} → ${proposal.valid_to}` : `od ${proposal.valid_from}`);
  panel.append(identity);

  const source = text("div", "frakon-mnd-source__source", "");
  source.append(text("span", "", "Serverově stažený oficiální zdroj"));
  const link = safeMndLink("Otevřít MND dokument", proposal.source_url);
  if (link) source.append(link);
  source.append(text("code", "", `SHA-256 ${proposal.document_sha256}`));
  panel.append(source);

  const fingerprints = text("div", "frakon-mnd-source__fingerprints", "");
  for (const [label, value] of [
    ["Proposal", proposal.proposal_fingerprint],
    ["Context", proposal.source_context_fingerprint],
    ["Document", proposal.document_sha256],
  ]) {
    const row = text("span", "", "");
    row.append(text("b", "", `${label}: `), text("code", "", value));
    fingerprints.append(row);
  }
  panel.append(fingerprints);

  if (!confirmed) {
    const confirm = text("div", "frakon-mnd-source__confirm", "");
    const copy = text("div", "", "");
    copy.append(
      text("b", "", "Explicitní potvrzení zdroje"),
      text("span", "", "Do potvrzení se neposílá PSČ, URL, SHA, produkt ani cena. Odesílá se pouze fingerprint serverově uloženého proposal."),
    );
    const button = text("button", "frakon-mnd-source__confirm-button", busy ? "Potvrzuji…" : "Potvrdit tento MND dokument");
    button.type = "button";
    button.disabled = busy;
    button.addEventListener("click", confirmMndSourceProposal);
    confirm.append(copy, button);
    panel.append(confirm);
  } else if (verified) {
    const success = text("div", "frakon-mnd-source__notice success", "");
    success.append(
      text("b", "", "Resolver je aktivní pro tuto konfiguraci a tento PSČ-context."),
      text("span", "", "Read-only discovery vrátil přesně stejný produkt, URL a SHA-256. MND parser cen je zatím záměrně zamčený, takže tento krok ještě nepovoluje all-in výpočet ani aktivaci tarifu."),
    );
    panel.append(success);
  }

  root.append(panel);
}

function validateProposalResponse(response, context, postcode, sourceUrl, sourceValidFrom) {
  if (!response || typeof response !== "object" || !response.proposal) {
    throw new Error("Backend nevrátil MND source proposal.");
  }
  if (response.entry_id !== context.entryId) throw new Error("MND proposal patří jiné konfiguraci.");
  if (response.product_name !== context.contract.product_name) throw new Error("MND proposal patří jinému produktu.");
  if (response.distributor !== context.contract.distributor) throw new Error("MND proposal patří jinému distributorovi.");
  if (response.contract_kind !== context.product.contract_kind) throw new Error("MND proposal má jiný typ smlouvy.");
  if (normalizeMndDocumentUrl(response.source_url) !== sourceUrl) throw new Error("Backend potvrdil jinou MND URL.");
  if (response.valid_from !== sourceValidFrom) throw new Error("Backend změnil začátek platnosti MND zdroje.");
  const expectedValidTo = context.product.contract_kind === "fixed" ? context.fixationEnd : null;
  if ((response.valid_to ?? null) !== expectedValidTo) throw new Error("Backend změnil konec platnosti MND zdroje.");
  for (const field of ["proposal_fingerprint", "source_context_fingerprint", "document_sha256"]) {
    if (!SHA256_RE.test(response[field] ?? "")) throw new Error(`MND proposal má neplatné pole ${field}.`);
  }
  if (
    response.download_performed !== true ||
    response.parsing_performed !== false ||
    response.confirmation_performed !== false ||
    response.activation_performed !== false
  ) {
    throw new Error("Backend nevrátil bezpečný nepotvrzený MND source proposal.");
  }
  if (Object.prototype.hasOwnProperty.call(response, "postcode") || Object.prototype.hasOwnProperty.call(response.proposal, "postcode")) {
    throw new Error("Backend vrátil raw PSČ v persistentním MND proposal payloadu.");
  }
  if (response.proposal.fingerprint !== response.proposal_fingerprint) throw new Error("MND proposal fingerprint není konzistentní.");
  if (response.proposal.document_sha256 !== response.document_sha256) throw new Error("MND proposal SHA-256 není konzistentní.");
  if (response.proposal.source_context_fingerprint !== response.source_context_fingerprint) throw new Error("MND source-context fingerprint není konzistentní.");
  if (response.proposal.source_url !== response.source_url) throw new Error("MND proposal URL není konzistentní.");
  if (!POSTCODE_RE.test(postcode)) throw new Error("Lokální PSČ context není platný.");
}

async function prepareMndSourceProposal() {
  if (busy) return;
  const startRevision = revision;
  busy = true;
  currentProposal = null;
  currentConfirmation = null;
  currentDiscovery = null;
  renderLoading("Ověřuji MND dokument…", "Backend zkontroluje identitu produktu, oficiální URL, stáhne PDF bez redirectu a vypočítá jeho SHA-256.");
  try {
    const postcode = normalizePostcode(draft.postcode);
    const sourceUrl = normalizeMndDocumentUrl(draft.sourceUrl);
    const sourceValidFrom = normalizeIsoDate(draft.sourceValidFrom, "Ceník platí od");
    const context = await exactMndContext();
    if (revision !== startRevision || requiredValue("Dodavatel") !== "mnd") return;
    const message = {
      type: "frakon_energy/tariff/mnd/source/propose",
      entry_id: context.entryId,
      source_context: { postcode },
      product_name: context.contract.product_name,
      distributor: context.contract.distributor,
      contract_kind: context.product.contract_kind,
      source_url: sourceUrl,
      valid_from: sourceValidFrom,
    };
    if (context.product.contract_kind === "fixed") message.valid_to = context.fixationEnd;
    const response = await callWs(message);
    if (revision !== startRevision || requiredValue("Dodavatel") !== "mnd") return;
    validateProposalResponse(response, context, postcode, sourceUrl, sourceValidFrom);
    currentProposal = { response, context, postcode, sourceUrl, sourceValidFrom };
    busy = false;
    renderProposal();
    bridgeRoot()?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    if (revision === startRevision) {
      busy = false;
      renderError(error);
    }
  }
}

function validateConfirmationResponse(response, proposal) {
  if (!response || typeof response !== "object" || response.confirmed !== true) {
    throw new Error("Backend nepotvrdil MND source proposal.");
  }
  if (response.entry_id !== proposal.context.entryId) throw new Error("MND potvrzení patří jiné konfiguraci.");
  if (response.proposal_fingerprint !== proposal.response.proposal_fingerprint) throw new Error("MND potvrzení změnilo proposal fingerprint.");
  if (response.document_sha256 !== proposal.response.document_sha256) throw new Error("MND potvrzení změnilo SHA-256 dokumentu.");
  if (!SHA256_RE.test(response.confirmed_resolution_fingerprint ?? "")) throw new Error("MND potvrzení nemá platný resolver fingerprint.");
  if (
    response.download_performed !== false ||
    response.parsing_performed !== false ||
    response.activation_performed !== false ||
    response.persistence_performed !== response.confirmation_performed
  ) {
    throw new Error("Backend vrátil nekonzistentní stav MND confirmation.");
  }
}

async function verifyConfirmedDiscovery(proposal) {
  const { context, postcode, sourceUrl, sourceValidFrom } = proposal;
  const discovery = await callWs({
    type: "frakon_energy/tariff/discover",
    entry_id: context.entryId,
    contract: context.contract,
    day: context.day,
    source_context: { postcode },
  });
  if (
    !discovery ||
    !SHA256_RE.test(discovery.contract_fingerprint ?? "") ||
    discovery.source_context_fingerprint !== proposal.response.source_context_fingerprint ||
    !Array.isArray(discovery.candidates) ||
    discovery.download_performed ||
    discovery.parsing_performed ||
    discovery.persistence_performed ||
    discovery.activation_performed
  ) {
    throw new Error("Read-only MND discovery po potvrzení neprošlo bezpečnostní kontrolou.");
  }
  const expectedValidTo = context.product.contract_kind === "fixed" ? context.fixationEnd : null;
  const matches = discovery.candidates.filter((candidate) => {
    try {
      return (
        candidate?.supplier === "mnd" &&
        candidate?.product_name === context.contract.product_name &&
        candidate?.price_scope === "supplier_commercial" &&
        candidate?.valid_from === sourceValidFrom &&
        (candidate?.valid_to ?? null) === expectedValidTo &&
        candidate?.document_sha256 === proposal.response.document_sha256 &&
        SHA256_RE.test(candidate?.fingerprint ?? "") &&
        normalizeMndDocumentUrl(candidate?.source_url) === sourceUrl &&
        candidate?.download_performed === false &&
        candidate?.parsing_performed === false &&
        candidate?.persistence_performed === false &&
        candidate?.activation_performed === false
      );
    } catch {
      return false;
    }
  });
  if (matches.length !== 1) {
    throw new Error("Potvrzený MND zdroj se v entry-isolated discovery neobjevil přesně jednou.");
  }
  return { response: discovery, candidate: matches[0] };
}

async function confirmMndSourceProposal() {
  if (busy || !currentProposal || currentConfirmation?.confirmed) return;
  const startRevision = revision;
  busy = true;
  renderProposal();
  try {
    const response = await callWs({
      type: "frakon_energy/tariff/mnd/source/confirm",
      entry_id: currentProposal.context.entryId,
      proposal_fingerprint: currentProposal.response.proposal_fingerprint,
    });
    if (revision !== startRevision || requiredValue("Dodavatel") !== "mnd") return;
    validateConfirmationResponse(response, currentProposal);
    currentConfirmation = response;
    currentDiscovery = await verifyConfirmedDiscovery(currentProposal);
    if (revision !== startRevision || requiredValue("Dodavatel") !== "mnd") return;
    busy = false;
    renderProposal();
  } catch (error) {
    if (revision === startRevision) {
      busy = false;
      renderError(error, { confirmed: currentConfirmation?.confirmed === true });
    }
  }
}

async function retryDiscovery() {
  if (busy || !currentProposal || !currentConfirmation?.confirmed) return;
  busy = true;
  renderLoading("Ověřuji entry-isolated discovery…", "Kontroluji, že potvrzený MND dokument je dostupný pouze této konfiguraci a se stejným SHA-256.");
  try {
    currentDiscovery = await verifyConfirmedDiscovery(currentProposal);
    busy = false;
    renderProposal();
  } catch (error) {
    busy = false;
    renderError(error, { confirmed: true });
  }
}

function installStyles() {
  if (document.getElementById(MND_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = MND_STYLE_ID;
  style.textContent = `
.frakon-mnd-source-bridge{box-sizing:border-box;max-width:1600px;margin:16px auto 28px;padding:0 24px;color:#e2e8f0;font-family:inherit}.frakon-mnd-source__panel{display:grid;gap:16px;padding:22px;border:1px solid rgba(168,85,247,.32);border-radius:22px;background:linear-gradient(145deg,rgba(88,28,135,.14),rgba(2,6,23,.92));box-shadow:0 18px 54px rgba(2,6,23,.18)}.frakon-mnd-source__panel.confirmed{border-color:rgba(34,197,94,.34);background:linear-gradient(145deg,rgba(20,83,45,.16),rgba(2,6,23,.92))}.frakon-mnd-source__header,.frakon-mnd-source__actions,.frakon-mnd-source__confirm{display:flex;align-items:center;justify-content:space-between;gap:18px}.frakon-mnd-source__header h3{margin:4px 0 0;font-size:20px}.frakon-mnd-source__eyebrow{color:#c4b5fd;font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}.frakon-mnd-source__badge{display:inline-flex;align-items:center;min-height:30px;padding:0 11px;border-radius:999px;font-size:12px;font-weight:800;white-space:nowrap}.frakon-mnd-source__badge.pending{color:#fde68a;background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.24)}.frakon-mnd-source__badge.confirmed{color:#86efac;background:rgba(34,197,94,.12);border:1px solid rgba(34,197,94,.24)}.frakon-mnd-source__lead,.frakon-mnd-source__safety{margin:0;color:#94a3b8;font-size:13px;line-height:1.55}.frakon-mnd-source__form{display:grid;grid-template-columns:minmax(150px,.55fr) minmax(320px,1.7fr) minmax(180px,.65fr);gap:12px}.frakon-mnd-source__field{display:grid;gap:7px}.frakon-mnd-source__field>span{color:#a78bfa;font-size:12px;font-weight:750}.frakon-mnd-source__field input{min-width:0;height:42px;padding:0 12px;border:1px solid rgba(148,163,184,.2);border-radius:10px;color:#f8fafc;background:rgba(2,6,23,.58);font:inherit}.frakon-mnd-source__field input:focus{outline:2px solid rgba(167,139,250,.35);border-color:rgba(167,139,250,.5)}.frakon-mnd-source__primary,.frakon-mnd-source__secondary,.frakon-mnd-source__confirm-button{min-height:42px;padding:0 16px;border:1px solid rgba(167,139,250,.44);border-radius:11px;color:#faf5ff;background:rgba(126,34,206,.25);font:inherit;font-size:13px;font-weight:850;cursor:pointer}.frakon-mnd-source__secondary{width:max-content;margin-top:12px}.frakon-mnd-source__confirm-button{border-color:rgba(74,222,128,.46);background:rgba(22,163,74,.26)}.frakon-mnd-source__primary:hover,.frakon-mnd-source__secondary:hover{background:rgba(126,34,206,.38)}.frakon-mnd-source__confirm-button:hover:not(:disabled){background:rgba(22,163,74,.38)}.frakon-mnd-source__confirm-button:disabled{cursor:wait;opacity:.58}.frakon-mnd-source__notice{display:flex;flex-direction:column;gap:5px;padding:15px;border-radius:14px;color:#dbeafe;background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.2);line-height:1.45}.frakon-mnd-source__notice.loading{color:#ddd6fe;background:rgba(139,92,246,.08);border-color:rgba(167,139,250,.24)}.frakon-mnd-source__notice.error{color:#fecaca;background:rgba(239,68,68,.08);border-color:rgba(239,68,68,.22)}.frakon-mnd-source__notice.success{color:#bbf7d0;background:rgba(34,197,94,.08);border-color:rgba(34,197,94,.22)}.frakon-mnd-source__notice.info{color:#dbeafe}.frakon-mnd-source__notice span{font-size:12px;color:inherit}.frakon-mnd-source__identity{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.frakon-mnd-source__identity>div,.frakon-mnd-source__source,.frakon-mnd-source__fingerprints{padding:14px;border-radius:13px;background:rgba(2,6,23,.45);border:1px solid rgba(148,163,184,.12)}.frakon-mnd-source__identity>div{display:flex;flex-direction:column;gap:6px}.frakon-mnd-source__identity span,.frakon-mnd-source__source span,.frakon-mnd-source__fingerprints span{color:#94a3b8;font-size:12px}.frakon-mnd-source__source{display:flex;flex-direction:column;gap:7px}.frakon-mnd-source__source a{width:max-content;color:#c4b5fd;font-size:13px;font-weight:750;text-decoration:none}.frakon-mnd-source__source a:hover{text-decoration:underline}.frakon-mnd-source__source code,.frakon-mnd-source__fingerprints code{color:#ddd6fe;overflow-wrap:anywhere}.frakon-mnd-source__fingerprints{display:grid;gap:6px}.frakon-mnd-source__confirm{padding:16px;border-radius:15px;color:#fde68a;background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.22)}.frakon-mnd-source__confirm>div{display:flex;flex-direction:column;gap:5px;max-width:780px;line-height:1.45}.frakon-mnd-source__confirm span{color:#d6d3d1;font-size:12px}@media(max-width:900px){.frakon-mnd-source__form{grid-template-columns:1fr}.frakon-mnd-source__identity{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:640px){.frakon-mnd-source-bridge{padding:0 16px}.frakon-mnd-source__header,.frakon-mnd-source__actions,.frakon-mnd-source__confirm{align-items:flex-start;flex-direction:column}.frakon-mnd-source__identity{grid-template-columns:1fr}.frakon-mnd-source__primary,.frakon-mnd-source__secondary,.frakon-mnd-source__confirm-button{width:100%}}
`;
  document.head.append(style);
}

function syncBridge() {
  installStyles();
  const supplier = fieldControl("Dodavatel")?.value?.trim();
  if (!wizard() || supplier !== "mnd") {
    removeRoot();
    return;
  }
  ensureRoot();
  if (currentProposal) renderProposal();
  else if (!busy && !bridgeRoot()?.hasChildNodes()) renderIdle();
}

function onWizardChange(event) {
  if (!(event.target instanceof Element)) return;
  if (!event.target.closest(".tariff-wizard")) return;
  resetState({ clearDraft: true });
}

document.addEventListener("change", onWizardChange, true);
document.addEventListener("click", (event) => {
  if (!(event.target instanceof Element)) return;
  if (!event.target.closest(".tariff-wizard button")) return;
  resetState({ clearDraft: true });
}, true);
const observer = new MutationObserver(() => queueMicrotask(syncBridge));
observer.observe(document.documentElement, { childList: true, subtree: true });
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", syncBridge, { once: true });
else syncBridge();
