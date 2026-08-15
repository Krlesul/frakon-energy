const MANUAL_BRIDGE_ID = "frakon-manual-tariff-entry-bridge";
const MANUAL_STYLE_ID = `${MANUAL_BRIDGE_ID}-style`;
const SHA256_RE = /^[0-9a-f]{64}$/;
const POSTCODE_RE = /^[1-7]\d{4}$/;
const DECIMAL_RE = /^(?:0|[1-9]\d{0,11})(?:\.\d{1,6})?$/;
const SUPPLIER_LABELS = { cez: "ČEZ", eon: "E.ON", pre: "PRE", mnd: "MND" };

let revision = 0;
let busy = false;
let currentCandidate = null;
let currentProposal = null;
let currentConfirmation = null;
let draft = { postcode: "", highRate: "", lowRate: "", standing: "" };

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

function safeLink(label, rawUrl) {
  let url;
  try {
    url = new URL(String(rawUrl));
  } catch {
    return null;
  }
  if (url.protocol !== "https:") return null;
  const link = text("a", "", label);
  link.href = url.href;
  link.target = "_blank";
  link.rel = "noreferrer";
  return link;
}

function normalizeUrl(rawUrl) {
  const url = new URL(String(rawUrl), document.baseURI);
  if (url.protocol !== "https:") throw new Error("Ověřený ceník nemá bezpečnou HTTPS adresu.");
  url.hash = "";
  return url.href;
}

function wizard() {
  return document.querySelector(".tariff-wizard");
}

function pricePreview() {
  return document.querySelector(".tariff-wizard .tariff-price-preview");
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
  if (!POSTCODE_RE.test(normalized)) throw new Error("PSČ musí být platné české pětimístné PSČ.");
  return normalized;
}

function normalizeDecimal(rawValue, label) {
  const normalized = String(rawValue ?? "").trim().replace(",", ".");
  if (!DECIMAL_RE.test(normalized)) {
    throw new Error(`${label} musí být nezáporné desetinné číslo s nejvýše 6 desetinnými místy.`);
  }
  return normalized;
}

async function resolveEntryId() {
  const entries = await callWs({ type: "config_entries/get" });
  if (!Array.isArray(entries)) throw new Error("Backend nevrátil seznam konfigurací.");
  const matches = entries.filter((entry) => entry?.domain === "frakon_energy");
  if (matches.length !== 1 || typeof matches[0]?.entry_id !== "string") {
    throw new Error("Nelze jednoznačně určit konfiguraci FRAKON Energy.");
  }
  return matches[0].entry_id;
}

async function exactWizardContext() {
  const supplier = requiredValue("Dodavatel");
  const productName = requiredValue("Produkt ze smlouvy");
  const distributor = requiredValue("Distribuční území");
  const distributionTariff = requiredValue("Distribuční sazba");
  const phases = integerValue("Počet fází");
  const amperes = integerValue("Hlavní jistič");
  const validFrom = requiredValue("Smlouva platí od");
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
    throw new Error("Katalog dodavatelů neprošel read-only kontrolou.");
  }
  const group = catalog.suppliers.find((item) => item?.supplier === supplier);
  const products = Array.isArray(group?.products) ? group.products : [];
  const productMatches = products.filter((item) => item?.product_name === productName);
  if (productMatches.length !== 1) throw new Error("Vybraný produkt není v backend katalogu jednoznačný.");
  const product = productMatches[0];
  if (product.price_scope !== "supplier_commercial") throw new Error("Produkt nemá supplier-commercial autoritu.");
  if (product.contract_kind !== "fixed" && product.contract_kind !== "indefinite") {
    throw new Error("Backend vrátil nepodporovaný typ smlouvy.");
  }

  let fixationEnd = null;
  if (product.contract_kind === "fixed") fixationEnd = requiredValue("Fixace do");
  const contract = {
    schema_version: 1,
    supplier,
    distributor,
    product_name: productName,
    contract_kind: product.contract_kind,
    distribution_tariff: distributionTariff,
    breaker: { phases, amperes },
    valid_from: validFrom,
    valid_to: null,
    fixation_end: fixationEnd,
    customer_confirmed: false,
  };
  return { entryId, contract, day, product };
}

function sourceContextFor(context) {
  if (!context.product.requires_document_resolver) return null;
  return { postcode: normalizePostcode(draft.postcode) };
}

async function discoverExactCandidate(context, sourceContext) {
  const message = {
    type: "frakon_energy/tariff/discover",
    entry_id: context.entryId,
    contract: context.contract,
    day: context.day,
  };
  if (sourceContext) message.source_context = sourceContext;
  const discovery = await callWs(message);
  if (
    !discovery ||
    discovery.entry_id !== context.entryId ||
    !SHA256_RE.test(discovery.contract_fingerprint ?? "") ||
    !Array.isArray(discovery.candidates) ||
    discovery.download_performed ||
    discovery.parsing_performed ||
    discovery.persistence_performed ||
    discovery.activation_performed
  ) {
    throw new Error("Read-only discovery neprošlo bezpečnostní kontrolou.");
  }
  if (sourceContext && !SHA256_RE.test(discovery.source_context_fingerprint ?? "")) {
    throw new Error("Discovery nevrátilo platný fingerprint dočasného source contextu.");
  }
  const matches = discovery.candidates.filter((candidate) => {
    try {
      return (
        candidate?.supplier === context.contract.supplier &&
        candidate?.product_name === context.contract.product_name &&
        candidate?.price_scope === "supplier_commercial" &&
        candidate?.match_score === 100 &&
        SHA256_RE.test(candidate?.fingerprint ?? "") &&
        candidate?.download_performed === false &&
        candidate?.parsing_performed === false &&
        candidate?.persistence_performed === false &&
        candidate?.activation_performed === false &&
        normalizeUrl(candidate?.source_url).startsWith("https://")
      );
    } catch {
      return false;
    }
  });
  if (matches.length !== 1) {
    if (context.contract.supplier === "mnd") {
      throw new Error("Pro toto PSČ není právě jeden potvrzený MND ceník. Nejdřív dokončete MND source-confirmation krok pro stejné PSČ a produkt.");
    }
    throw new Error("Backend nenašel právě jeden přesný 100/100 ceník pro ruční zadání.");
  }
  return { discovery, candidate: matches[0] };
}

function bridgeRoot() {
  return document.getElementById(MANUAL_BRIDGE_ID);
}

function ensureRoot() {
  let root = bridgeRoot();
  if (root) return root;
  const appRoot = document.getElementById("root");
  if (!appRoot?.parentNode) return null;
  root = document.createElement("section");
  root.id = MANUAL_BRIDGE_ID;
  root.className = "frakon-manual-tariff-bridge";
  const automatic = document.getElementById("frakon-tariff-confirmation-bridge");
  const anchor = automatic ?? appRoot;
  anchor.parentNode.insertBefore(root, anchor.nextSibling);
  return root;
}

function removeRoot() {
  bridgeRoot()?.remove();
}

function resetState({ clearDraft = true } = {}) {
  revision += 1;
  busy = false;
  currentCandidate = null;
  currentProposal = null;
  currentConfirmation = null;
  if (clearDraft) draft = { postcode: "", highRate: "", lowRate: "", standing: "" };
  removeRoot();
  queueMicrotask(syncBridge);
}

function renderNotice(root, title, message, kind = "info") {
  root.replaceChildren();
  const notice = text("div", `frakon-manual__notice ${kind}`, "");
  notice.append(text("b", "", title), text("span", "", message));
  root.append(notice);
}

function labeledInput(labelText, value, placeholder, onInput, inputMode = "decimal") {
  const label = text("label", "frakon-manual__field", "");
  label.append(text("span", "", labelText));
  const input = document.createElement("input");
  input.type = "text";
  input.inputMode = inputMode;
  input.value = value;
  input.placeholder = placeholder;
  input.autocomplete = "off";
  input.spellcheck = false;
  input.addEventListener("input", () => onInput(input.value));
  label.append(input);
  return label;
}

function addValue(parent, label, value) {
  const item = text("div", "", "");
  item.append(text("span", "", label), text("b", "", value));
  parent.append(item);
}

function renderIdle() {
  const root = ensureRoot();
  if (!root || busy || currentCandidate || currentProposal) return;
  root.replaceChildren();
  const supplier = fieldControl("Dodavatel")?.value?.trim();
  const panel = text("div", "frakon-manual__panel", "");
  const header = text("div", "frakon-manual__header", "");
  const heading = text("div", "", "");
  heading.append(
    text("span", "frakon-manual__eyebrow", "Ruční fallback · supplier-commercial pouze"),
    text("h3", "", "Zadat obchodní cenu z přesného ověřeného ceníku"),
  );
  header.append(heading, text("span", "frakon-manual__badge", "Aktivace zamčená"));
  panel.append(header);
  panel.append(text("p", "frakon-manual__lead", "Ruční režim nikdy nepřebírá regulované složky, URL ani all-in součet z formuláře. Nejdřív backend musí znovu najít právě jeden 100/100 oficiální ceník; až potom se odemknou tři obchodní hodnoty."));

  if (supplier === "mnd") {
    const form = text("div", "frakon-manual__form one", "");
    form.append(labeledInput("PSČ pro MND discovery", draft.postcode, "412 01", (value) => { draft.postcode = value; }, "numeric"));
    panel.append(form);
    const hint = text("div", "frakon-manual__notice info", "");
    hint.append(text("b", "", "MND vyžaduje potvrzený source resolver"), text("span", "", "Použijte stejné PSČ jako v MND source-confirmation kroku. PSČ je pouze dočasný discovery context a není součástí cenové provenance."));
    panel.append(hint);
  }

  const button = text("button", "frakon-manual__primary", "Načíst přesný ceník pro ruční zadání");
  button.type = "button";
  button.addEventListener("click", prepareCandidate);
  const actions = text("div", "frakon-manual__actions", "");
  actions.append(button, text("span", "frakon-manual__safety", "Tento krok je read-only: nic nestahuje, neukládá ani neaktivuje."));
  panel.append(actions);
  root.append(panel);
}

function renderCandidate() {
  const root = ensureRoot();
  if (!root || !currentCandidate || currentProposal) return;
  root.replaceChildren();
  const { context, candidate } = currentCandidate;
  const panel = text("div", "frakon-manual__panel", "");
  const header = text("div", "frakon-manual__header", "");
  const heading = text("div", "", "");
  heading.append(text("span", "frakon-manual__eyebrow", "Přesný 100/100 candidate ověřen"), text("h3", "", "Opište pouze obchodní ceny z tohoto dokumentu"));
  header.append(heading, text("span", "frakon-manual__badge ready", "Zdroj svázán fingerprintem"));
  panel.append(header);

  const identity = text("div", "frakon-manual__identity", "");
  addValue(identity, "Dodavatel", SUPPLIER_LABELS[context.contract.supplier] ?? context.contract.supplier);
  addValue(identity, "Produkt", context.contract.product_name);
  addValue(identity, "Sazba", context.contract.distribution_tariff);
  addValue(identity, "Platnost", candidate.valid_to ? `${candidate.valid_from} → ${candidate.valid_to}` : `od ${candidate.valid_from}`);
  panel.append(identity);

  const source = text("div", "frakon-manual__source", "");
  source.append(text("span", "", "Oficiální supplier-commercial dokument"));
  const link = safeLink("Otevřít ceník, ze kterého opíšete ceny", candidate.source_url);
  if (link) source.append(link);
  source.append(text("code", "", `Candidate ${candidate.fingerprint}`));
  if (candidate.document_sha256) source.append(text("code", "", `Pinned SHA-256 ${candidate.document_sha256}`));
  panel.append(source);

  const form = text("div", "frakon-manual__form", "");
  form.append(
    labeledInput("VT s DPH [Kč/kWh]", draft.highRate, "2,899", (value) => { draft.highRate = value; }),
    labeledInput("NT s DPH [Kč/kWh]", draft.lowRate, "2,899", (value) => { draft.lowRate = value; }),
    labeledInput("Stálý plat s DPH [Kč/měsíc]", draft.standing, "168", (value) => { draft.standing = value; }),
  );
  panel.append(form);

  const warning = text("div", "frakon-manual__notice warning", "");
  warning.append(text("b", "", "Nezadávejte distribuci ani celkovou all-in cenu"), text("span", "", "Backend vezme regulovanou část pouze z nezávisle potvrzeného regulator catalogu a kompletní cenu dopočítá sám."));
  panel.append(warning);

  const submit = text("button", "frakon-manual__primary", "Serverově sestavit manual all-in proposal");
  submit.type = "button";
  submit.addEventListener("click", prepareProposal);
  const reset = text("button", "frakon-manual__secondary", "Znovu ověřit candidate");
  reset.type = "button";
  reset.addEventListener("click", () => { currentCandidate = null; renderIdle(); });
  const actions = text("div", "frakon-manual__actions", "");
  actions.append(submit, reset);
  panel.append(actions);
  root.append(panel);
}

function renderLoading(title, message) {
  const root = ensureRoot();
  if (!root) return;
  renderNotice(root, title, message, "loading");
}

function renderError(error) {
  const root = ensureRoot();
  if (!root) return;
  root.replaceChildren();
  const panel = text("div", "frakon-manual__panel", "");
  const notice = text("div", "frakon-manual__notice error", "");
  notice.append(text("b", "", "Ruční proposal zůstává zamčený"), text("span", "", readableError(error)));
  const retry = text("button", "frakon-manual__secondary", "Zkusit znovu");
  retry.type = "button";
  retry.addEventListener("click", () => {
    busy = false;
    currentCandidate = null;
    currentProposal = null;
    currentConfirmation = null;
    renderIdle();
  });
  panel.append(notice, retry);
  root.append(panel);
}

function componentList(title, components, fixed = false) {
  const block = text("div", "frakon-manual__component-block", "");
  block.append(text("h4", "", title));
  const list = text("div", "frakon-manual__component-list", "");
  for (const component of components) {
    const row = text("div", "", "");
    row.append(text("span", "", component?.name ?? component?.kind ?? "Složka"));
    const value = fixed
      ? `${component?.gross_monthly_czk ?? "—"} Kč/měsíc`
      : `VT ${component?.gross_vt_czk_per_kwh ?? "—"} · NT ${component?.gross_nt_czk_per_kwh ?? "—"} Kč/kWh`;
    row.append(text("b", "", value));
    list.append(row);
  }
  block.append(list);
  return block;
}

function renderProposal() {
  const root = ensureRoot();
  if (!root || !currentProposal) return;
  root.replaceChildren();
  const response = currentProposal.response;
  const preview = response.preview;
  const confirmed = currentConfirmation?.confirmed === true;
  const panel = text("div", `frakon-manual__panel${confirmed ? " confirmed" : ""}`, "");
  const header = text("div", "frakon-manual__header", "");
  const heading = text("div", "", "");
  heading.append(text("span", "frakon-manual__eyebrow", "Manual user entry · regulace server-authoritative"), text("h3", "", "Kompletní all-in cena z ruční obchodní části"));
  header.append(heading, text("span", `frakon-manual__badge ${confirmed ? "confirmed" : "ready"}`, confirmed ? "Potvrzeno a aktivováno" : "Čeká na explicitní potvrzení"));
  panel.append(header);

  const totals = text("div", "frakon-manual__totals", "");
  addValue(totals, "All-in VT", `${preview.all_in_vt_czk_kwh} Kč/kWh`);
  addValue(totals, "All-in NT", `${preview.all_in_nt_czk_kwh} Kč/kWh`);
  addValue(totals, "Fixní platby", `${preview.fixed_monthly_total_czk} Kč/měsíc`);
  panel.append(totals);

  const manual = preview.manual_supplier_commercial;
  const manualBox = text("div", "frakon-manual__manual-values", "");
  addValue(manualBox, "Ruční VT", `${manual.high_rate_czk_per_kwh} Kč/kWh`);
  addValue(manualBox, "Ruční NT", `${manual.low_rate_czk_per_kwh} Kč/kWh`);
  addValue(manualBox, "Ruční stálý plat", `${manual.supplier_standing_czk_month} Kč/měsíc`);
  panel.append(manualBox);

  const components = text("div", "frakon-manual__components", "");
  components.append(
    componentList("Variabilní složky", Array.isArray(preview.variable_components) ? preview.variable_components : [], false),
    componentList("Fixní složky", Array.isArray(preview.fixed_components) ? preview.fixed_components : [], true),
  );
  panel.append(components);

  const source = text("div", "frakon-manual__source", "");
  source.append(text("span", "", "Supplier document použitý jako provenance"));
  const link = safeLink("Otevřít ověřený zdroj", preview.supplier_source_url);
  if (link) source.append(link);
  source.append(text("code", "", `SHA-256 ${preview.supplier_document_sha256}`));
  panel.append(source);

  if (confirmed) {
    const success = text("div", "frakon-manual__notice success", "");
    success.append(text("b", "", "Tarif je potvrzený a aktivní."), text("span", "", "Potvrzení proběhlo přes společnou fingerprint-only customer/confirm boundary."));
    panel.append(success);
  } else {
    const confirm = text("div", "frakon-manual__confirm", "");
    const copy = text("div", "", "");
    copy.append(text("b", "", "Explicitní potvrzení"), text("span", "", "Do finálního requestu se neposílají ceny, URL, PSČ ani PDF. Pouze fingerprint uloženého proposal envelope."));
    const button = text("button", "frakon-manual__confirm-button", busy ? "Potvrzuji…" : "Potvrdit a aktivovat tento tarif");
    button.type = "button";
    button.disabled = busy;
    button.addEventListener("click", confirmProposal);
    confirm.append(copy, button);
    panel.append(confirm);
  }
  root.append(panel);
}

async function prepareCandidate() {
  if (busy) return;
  const startRevision = revision;
  busy = true;
  currentCandidate = null;
  currentProposal = null;
  currentConfirmation = null;
  renderLoading("Ověřuji přesný candidate…", "Backend provádí read-only discovery bez stahování nebo ukládání cen.");
  try {
    const context = await exactWizardContext();
    const sourceContext = sourceContextFor(context);
    const { discovery, candidate } = await discoverExactCandidate(context, sourceContext);
    if (revision !== startRevision || !manualFallbackEligible()) return;
    currentCandidate = { context, sourceContext, discovery, candidate };
    busy = false;
    renderCandidate();
    bridgeRoot()?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    if (revision === startRevision) {
      busy = false;
      renderError(error);
    }
  }
}

function validateManualProposal(response, exact, manualValues) {
  const { context, discovery, candidate } = exact;
  if (!response || typeof response !== "object" || !response.preview) throw new Error("Backend nevrátil manual customer proposal.");
  if (response.entry_id !== context.entryId) throw new Error("Proposal patří jiné konfiguraci.");
  if (response.candidate_fingerprint !== candidate.fingerprint) throw new Error("Proposal patří jinému ceníku.");
  if (response.contract_fingerprint !== discovery.contract_fingerprint) throw new Error("Proposal patří jiné verzi smlouvy.");
  if (normalizeUrl(response.source_url) !== normalizeUrl(candidate.source_url)) throw new Error("Proposal změnil supplier source URL.");
  if (!SHA256_RE.test(response.document_sha256 ?? "") || response.document_sha256 !== response.preview.supplier_document_sha256) {
    throw new Error("Proposal nemá konzistentní SHA-256 supplier dokumentu.");
  }
  if (
    response.authority_method !== "manual_user_entry" ||
    response.manual_entry !== true ||
    response.download_performed !== true ||
    response.parsing_performed !== false ||
    response.all_in_preview_performed !== true ||
    response.confirmation_performed !== false ||
    response.activation_performed !== false ||
    response.preview.authority_method !== "manual_user_entry" ||
    response.preview.manual_entry !== true ||
    response.preview.all_in_ready !== true ||
    response.preview.parsing_performed !== false ||
    response.preview.persistence_performed !== false ||
    response.preview.activation_performed !== false
  ) {
    throw new Error("Backend nevrátil bezpečný manual_user_entry proposal.");
  }
  if (Object.prototype.hasOwnProperty.call(response, "postcode") || Object.prototype.hasOwnProperty.call(response, "source_context")) {
    throw new Error("Backend vrátil raw operational source context v proposal response.");
  }
  const manual = response.preview.manual_supplier_commercial;
  if (!manual || manual.includes_vat !== true) throw new Error("Manual supplier-commercial preview nemá explicitní DPH autoritu.");
  if (
    manual.high_rate_czk_per_kwh !== manualValues.high_rate_czk_per_kwh ||
    manual.low_rate_czk_per_kwh !== manualValues.low_rate_czk_per_kwh ||
    manual.supplier_standing_czk_month !== manualValues.supplier_standing_czk_month
  ) {
    throw new Error("Backend změnil ručně zadané supplier-commercial hodnoty.");
  }
  for (const field of ["proposal_fingerprint", "contract_fingerprint", "all_in_tariff_fingerprint", "candidate_fingerprint", "regulated_version_fingerprint"]) {
    if (!SHA256_RE.test(response[field] ?? "")) throw new Error(`Proposal má neplatné pole ${field}.`);
  }
}

async function prepareProposal() {
  if (busy || !currentCandidate) return;
  const startRevision = revision;
  busy = true;
  currentProposal = null;
  currentConfirmation = null;
  renderLoading("Sestavuji kompletní manual all-in…", "Znovu ověřuji candidate fingerprint, stahuji přesný PDF dokument a kombinuji pouze tři ruční obchodní hodnoty s potvrzenou regulací.");
  try {
    const manualValues = {
      high_rate_czk_per_kwh: normalizeDecimal(draft.highRate, "VT"),
      low_rate_czk_per_kwh: normalizeDecimal(draft.lowRate, "NT"),
      supplier_standing_czk_month: normalizeDecimal(draft.standing, "Stálý plat"),
    };
    const context = await exactWizardContext();
    const sourceContext = sourceContextFor(context);
    const exact = await discoverExactCandidate(context, sourceContext);
    if (exact.candidate.fingerprint !== currentCandidate.candidate.fingerprint || normalizeUrl(exact.candidate.source_url) !== normalizeUrl(currentCandidate.candidate.source_url)) {
      throw new Error("Přesný candidate se od předchozího ověření změnil. Načtěte jej znovu.");
    }
    const message = {
      type: "frakon_energy/tariff/customer/manual/propose",
      entry_id: context.entryId,
      contract: context.contract,
      day: context.day,
      candidate_fingerprint: exact.candidate.fingerprint,
      manual_commercial: manualValues,
    };
    if (sourceContext) message.source_context = sourceContext;
    const response = await callWs(message);
    if (revision !== startRevision || !manualFallbackEligible()) return;
    validateManualProposal(response, { context, ...exact }, manualValues);
    currentProposal = { response, entryId: context.entryId };
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

function validateConfirmation(response, proposal) {
  if (!response || typeof response !== "object" || response.confirmed !== true) throw new Error("Backend nepotvrdil zákaznický tarif.");
  for (const field of ["proposal_fingerprint", "contract_fingerprint", "all_in_tariff_fingerprint", "regulated_version_fingerprint"]) {
    if (response[field] !== proposal[field]) throw new Error(`Potvrzení změnilo immutable referenci ${field}.`);
  }
  if (response.activation_performed !== response.confirmation_performed || response.persistence_performed !== response.confirmation_performed) {
    throw new Error("Backend vrátil nekonzistentní stav aktivace.");
  }
}

async function confirmProposal() {
  if (busy || !currentProposal || currentConfirmation?.confirmed) return;
  const startRevision = revision;
  const proposal = currentProposal.response;
  const entryId = currentProposal.entryId;
  busy = true;
  renderProposal();
  try {
    const response = await callWs({
      type: "frakon_energy/tariff/customer/confirm",
      entry_id: entryId,
      proposal_fingerprint: proposal.proposal_fingerprint,
    });
    if (revision !== startRevision || !manualFallbackEligible()) return;
    validateConfirmation(response, proposal);
    currentConfirmation = response;
    busy = false;
    renderProposal();
  } catch (error) {
    if (revision === startRevision) {
      busy = false;
      renderError(error);
    }
  }
}

function manualFallbackEligible() {
  if (!wizard() || pricePreview()) return false;
  const supplier = fieldControl("Dodavatel")?.value?.trim();
  if (!supplier) return false;
  if (supplier === "mnd") return true;
  return Boolean(wizard()?.querySelector(".tariff-candidate__parser-pending"));
}

function installStyles() {
  if (document.getElementById(MANUAL_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = MANUAL_STYLE_ID;
  style.textContent = `
.frakon-manual-tariff-bridge{box-sizing:border-box;max-width:1600px;margin:0 auto 32px;padding:0 24px;color:#e2e8f0;font-family:inherit}.frakon-manual__panel{display:grid;gap:16px;padding:22px;border:1px solid rgba(168,85,247,.32);border-radius:22px;background:linear-gradient(145deg,rgba(88,28,135,.14),rgba(2,6,23,.93));box-shadow:0 18px 54px rgba(2,6,23,.2)}.frakon-manual__panel.confirmed{border-color:rgba(34,197,94,.34);background:linear-gradient(145deg,rgba(20,83,45,.16),rgba(2,6,23,.93))}.frakon-manual__header,.frakon-manual__actions,.frakon-manual__confirm{display:flex;align-items:center;justify-content:space-between;gap:18px}.frakon-manual__header h3{margin:4px 0 0;font-size:20px}.frakon-manual__eyebrow{color:#c4b5fd;font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}.frakon-manual__badge{display:inline-flex;align-items:center;min-height:30px;padding:0 11px;border-radius:999px;color:#fde68a;background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.24);font-size:12px;font-weight:800;white-space:nowrap}.frakon-manual__badge.ready{color:#ddd6fe;background:rgba(139,92,246,.12);border-color:rgba(167,139,250,.25)}.frakon-manual__badge.confirmed{color:#86efac;background:rgba(34,197,94,.12);border-color:rgba(34,197,94,.24)}.frakon-manual__lead,.frakon-manual__safety{margin:0;color:#94a3b8;font-size:13px;line-height:1.55}.frakon-manual__form{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.frakon-manual__form.one{grid-template-columns:minmax(180px,360px)}.frakon-manual__field{display:grid;gap:7px}.frakon-manual__field>span{color:#c4b5fd;font-size:12px;font-weight:750}.frakon-manual__field input{min-width:0;height:42px;padding:0 12px;border:1px solid rgba(148,163,184,.2);border-radius:10px;color:#f8fafc;background:rgba(2,6,23,.58);font:inherit}.frakon-manual__field input:focus{outline:2px solid rgba(167,139,250,.35);border-color:rgba(167,139,250,.5)}.frakon-manual__primary,.frakon-manual__secondary,.frakon-manual__confirm-button{min-height:42px;padding:0 16px;border:1px solid rgba(167,139,250,.44);border-radius:11px;color:#faf5ff;background:rgba(126,34,206,.25);font:inherit;font-size:13px;font-weight:850;cursor:pointer}.frakon-manual__secondary{background:rgba(15,23,42,.62)}.frakon-manual__confirm-button{border-color:rgba(74,222,128,.46);background:rgba(22,163,74,.26)}.frakon-manual__primary:hover,.frakon-manual__secondary:hover{background:rgba(126,34,206,.38)}.frakon-manual__confirm-button:hover:not(:disabled){background:rgba(22,163,74,.38)}.frakon-manual__confirm-button:disabled{cursor:wait;opacity:.58}.frakon-manual__notice{display:flex;flex-direction:column;gap:5px;padding:15px;border-radius:14px;color:#dbeafe;background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.2);line-height:1.45}.frakon-manual__notice span{font-size:12px;color:inherit}.frakon-manual__notice.loading{color:#ddd6fe;background:rgba(139,92,246,.08);border-color:rgba(167,139,250,.24)}.frakon-manual__notice.error{color:#fecaca;background:rgba(239,68,68,.08);border-color:rgba(239,68,68,.22)}.frakon-manual__notice.warning{color:#fde68a;background:rgba(245,158,11,.08);border-color:rgba(245,158,11,.22)}.frakon-manual__notice.success{color:#bbf7d0;background:rgba(34,197,94,.08);border-color:rgba(34,197,94,.22)}.frakon-manual__identity,.frakon-manual__totals,.frakon-manual__manual-values{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.frakon-manual__totals,.frakon-manual__manual-values{grid-template-columns:repeat(3,minmax(0,1fr))}.frakon-manual__identity>div,.frakon-manual__totals>div,.frakon-manual__manual-values>div,.frakon-manual__source,.frakon-manual__component-block{padding:14px;border-radius:13px;background:rgba(2,6,23,.45);border:1px solid rgba(148,163,184,.12)}.frakon-manual__identity>div,.frakon-manual__totals>div,.frakon-manual__manual-values>div{display:flex;flex-direction:column;gap:6px}.frakon-manual__identity span,.frakon-manual__totals span,.frakon-manual__manual-values span,.frakon-manual__source span{color:#94a3b8;font-size:12px}.frakon-manual__totals b{font-size:19px}.frakon-manual__source{display:flex;flex-direction:column;gap:7px}.frakon-manual__source a{width:max-content;color:#c4b5fd;font-size:13px;font-weight:750;text-decoration:none}.frakon-manual__source a:hover{text-decoration:underline}.frakon-manual__source code{color:#ddd6fe;overflow-wrap:anywhere}.frakon-manual__components{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.frakon-manual__component-block h4{margin:0 0 10px}.frakon-manual__component-list{display:grid}.frakon-manual__component-list>div{display:flex;justify-content:space-between;gap:14px;padding:9px 0;border-top:1px solid rgba(148,163,184,.08)}.frakon-manual__component-list>div:first-child{border-top:0}.frakon-manual__component-list span{color:#94a3b8;font-size:12px}.frakon-manual__component-list b{font-size:12px;text-align:right}.frakon-manual__confirm{padding:16px;border-radius:15px;color:#fde68a;background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.22)}.frakon-manual__confirm>div{display:flex;flex-direction:column;gap:5px;max-width:800px;line-height:1.45}.frakon-manual__confirm span{color:#d6d3d1;font-size:12px}@media(max-width:900px){.frakon-manual__form,.frakon-manual__identity,.frakon-manual__totals,.frakon-manual__manual-values{grid-template-columns:repeat(2,minmax(0,1fr))}.frakon-manual__components{grid-template-columns:1fr}}@media(max-width:640px){.frakon-manual-tariff-bridge{padding:0 16px}.frakon-manual__header,.frakon-manual__actions,.frakon-manual__confirm{align-items:flex-start;flex-direction:column}.frakon-manual__form,.frakon-manual__identity,.frakon-manual__totals,.frakon-manual__manual-values{grid-template-columns:1fr}.frakon-manual__primary,.frakon-manual__secondary,.frakon-manual__confirm-button{width:100%}.frakon-manual__component-list>div{flex-direction:column;gap:4px}.frakon-manual__component-list b{text-align:left}}
`;
  document.head.append(style);
}

function syncBridge() {
  installStyles();
  if (!manualFallbackEligible()) {
    removeRoot();
    return;
  }
  ensureRoot();
  if (currentProposal) renderProposal();
  else if (currentCandidate) renderCandidate();
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
