const BRIDGE_ID = "frakon-tariff-confirmation-bridge";
const STYLE_ID = `${BRIDGE_ID}-style`;
const SUPPLIER_LABELS = { cez: "ČEZ", eon: "E.ON", pre: "PRE", mnd: "MND" };

let revision = 0;
let busy = false;
let currentProposal = null;
let currentConfirmation = null;

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
  if (url.protocol !== "https:") throw new Error("Vybraný ceník nemá bezpečnou HTTPS adresu.");
  url.hash = "";
  return url.href;
}

function wizard() {
  return document.querySelector(".tariff-wizard");
}

function pricePreview() {
  return document.querySelector(".tariff-wizard .tariff-price-preview");
}

function selectedCandidateCard() {
  return document.querySelector(".tariff-wizard .tariff-candidate.selected");
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

async function exactWizardContext() {
  const supplier = requiredValue("Dodavatel");
  const productName = requiredValue("Produkt ze smlouvy");
  const distributor = requiredValue("Distribuční území");
  const distributionTariff = requiredValue("Distribuční sazba");
  const phases = integerValue("Počet fází");
  const amperes = integerValue("Hlavní jistič");
  const validFrom = requiredValue("Smlouva platí od");
  const day = requiredValue("Ceník ověřit k datu");

  const primary = await callWs({ type: "frakon_energy/entry/primary" });
  if (!primary || primary.provider !== "visionq" || primary.loaded !== true || typeof primary.entry_id !== "string") {
    throw new Error("Backend neurčil aktivní VisionQ konfiguraci FRAKON Energy.");
  }
  const entryId = primary.entry_id;

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
  if (productMatches.length !== 1) {
    throw new Error("Vybraný produkt není v backend katalogu jednoznačný.");
  }
  const product = productMatches[0];
  if (product.price_scope !== "supplier_commercial") {
    throw new Error("Vybraný produkt nemá supplier-commercial autoritu.");
  }
  if (product.requires_document_resolver) {
    throw new Error("Vybraný produkt vyžaduje resolver, který není pro potvrzení připravený.");
  }
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

  return { entryId, contract, day };
}

function selectedSourceUrl() {
  const card = selectedCandidateCard();
  if (!card) throw new Error("Není vybraný přesný ověřený ceník.");
  const anchor = card.querySelector(".tariff-candidate__actions a[href]");
  if (!anchor) throw new Error("Vybraný ceník nemá dostupný oficiální zdroj.");
  return normalizeUrl(anchor.href);
}

async function rediscoverExactCandidate(context) {
  const discovery = await callWs({
    type: "frakon_energy/tariff/discover",
    entry_id: context.entryId,
    contract: context.contract,
    day: context.day,
  });
  if (
    !discovery ||
    typeof discovery.contract_fingerprint !== "string" ||
    !Array.isArray(discovery.candidates) ||
    discovery.download_performed ||
    discovery.parsing_performed ||
    discovery.persistence_performed ||
    discovery.activation_performed
  ) {
    throw new Error("Opakované discovery neprošlo read-only kontrolou.");
  }

  const sourceUrl = selectedSourceUrl();
  const previewSource = pricePreview()?.querySelector(".tariff-price-preview__source[href]");
  if (!previewSource || normalizeUrl(previewSource.href) !== sourceUrl) {
    throw new Error("Zobrazený cenový náhled už neodpovídá vybranému zdroji. Načtěte ceny znovu.");
  }
  const matches = discovery.candidates.filter((candidate) => {
    try {
      return (
        candidate?.supplier === context.contract.supplier &&
        candidate?.product_name === context.contract.product_name &&
        candidate?.price_scope === "supplier_commercial" &&
        typeof candidate?.fingerprint === "string" &&
        normalizeUrl(candidate.source_url) === sourceUrl
      );
    } catch {
      return false;
    }
  });
  if (matches.length !== 1) {
    throw new Error("Vybraný zdroj už není v backend discovery jednoznačný. Spusťte ověření ceníku znovu.");
  }
  return { discovery, candidate: matches[0] };
}

function bridgeRoot() {
  return document.getElementById(BRIDGE_ID);
}

function ensureRoot() {
  let root = bridgeRoot();
  if (root) return root;
  root = document.createElement("section");
  root.id = BRIDGE_ID;
  root.className = "frakon-tariff-confirmation-bridge";
  const appRoot = document.getElementById("root");
  if (!appRoot?.parentNode) return null;
  appRoot.parentNode.insertBefore(root, appRoot.nextSibling);
  return root;
}

function removeRoot() {
  bridgeRoot()?.remove();
  currentProposal = null;
  currentConfirmation = null;
  busy = false;
}

function bumpRevisionAndReset() {
  revision += 1;
  removeRoot();
  syncBridge();
}

function renderNotice(root, title, message, kind = "info") {
  root.replaceChildren();
  const box = text("div", `frakon-confirm__notice ${kind}`, "");
  box.append(text("b", "", title), text("span", "", message));
  root.append(box);
}

function addLabelValue(parent, label, value, className = "") {
  const item = text("div", className, "");
  item.append(text("span", "", label), text("b", "", value));
  parent.append(item);
}

function renderIdle() {
  const root = ensureRoot();
  if (!root || busy || currentProposal) return;
  root.replaceChildren();

  const panel = text("div", "frakon-confirm__panel", "");
  const header = text("div", "frakon-confirm__header", "");
  const heading = text("div", "", "");
  heading.append(
    text("span", "frakon-confirm__eyebrow", "Krok 4 · serverově ověřený all-in"),
    text("h3", "", "Sestavit kompletní cenu a připravit potvrzení"),
  );
  header.append(heading, text("span", "frakon-confirm__badge pending", "Aktivace zamčená"));

  const explanation = text(
    "p",
    "frakon-confirm__lead",
    "FRAKON Energy znovu ověří aktuální údaje smlouvy, přesný ceník dodavatele a potvrzenou regulovanou část. Z frontendového formuláře nepřevezme cenu ani URL jako autoritu.",
  );
  const button = text("button", "frakon-confirm__primary", "Sestavit kompletní all-in návrh");
  button.type = "button";
  button.addEventListener("click", prepareProposal);
  const safety = text(
    "span",
    "frakon-confirm__safety",
    "Tímto krokem vznikne pouze immutable nepotvrzený návrh. Aktivace vyžaduje ještě samostatné fingerprint-only potvrzení.",
  );
  const actions = text("div", "frakon-confirm__actions", "");
  actions.append(button, safety);
  panel.append(header, explanation, actions);
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
  const panel = text("div", "frakon-confirm__panel", "");
  const notice = text("div", "frakon-confirm__notice error", "");
  notice.append(text("b", "", "Potvrzení zůstává zamčené"), text("span", "", readableError(error)));
  const retry = text("button", "frakon-confirm__secondary", "Zkusit znovu od ověřeného náhledu");
  retry.type = "button";
  retry.addEventListener("click", () => {
    currentProposal = null;
    currentConfirmation = null;
    busy = false;
    renderIdle();
  });
  panel.append(notice, retry);
  root.append(panel);
}

function componentList(title, components, fixed = false) {
  const block = text("div", "frakon-confirm__component-block", "");
  block.append(text("h4", "", title));
  const list = text("div", "frakon-confirm__component-list", "");
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

function sourceBox(title, url, checksum) {
  const block = text("div", "frakon-confirm__source", "");
  block.append(text("span", "", title));
  const link = safeLink("Otevřít ověřený zdroj", url);
  if (link) block.append(link);
  block.append(text("code", "", checksum ? `SHA-256 ${checksum}` : "Zdroj bez checksumu"));
  return block;
}

function renderProposal() {
  const root = ensureRoot();
  if (!root || !currentProposal) return;
  root.replaceChildren();
  const response = currentProposal.response;
  const preview = response.preview;
  const confirmed = currentConfirmation?.confirmed === true;

  const panel = text("div", `frakon-confirm__panel${confirmed ? " confirmed" : ""}`, "");
  const header = text("div", "frakon-confirm__header", "");
  const heading = text("div", "", "");
  heading.append(
    text("span", "frakon-confirm__eyebrow", "Krok 4 · serverově ověřený návrh"),
    text("h3", "", "Kompletní all-in cena elektřiny"),
  );
  header.append(
    heading,
    text("span", `frakon-confirm__badge ${confirmed ? "confirmed" : "pending"}`, confirmed ? "Potvrzeno a aktivováno" : "Čeká na vaše potvrzení"),
  );
  panel.append(header);

  const totals = text("div", "frakon-confirm__totals", "");
  addLabelValue(totals, "All-in VT", `${preview.all_in_vt_czk_kwh} Kč/kWh`);
  addLabelValue(totals, "All-in NT", `${preview.all_in_nt_czk_kwh} Kč/kWh`);
  addLabelValue(totals, "Fixní platby", `${preview.fixed_monthly_total_czk} Kč/měsíc`);
  panel.append(totals);

  const context = text("div", "frakon-confirm__context", "");
  addLabelValue(context, "Dodavatel", SUPPLIER_LABELS[preview.supplier] ?? preview.supplier);
  addLabelValue(context, "Produkt", preview.product_name);
  addLabelValue(context, "Sazba", preview.distribution_tariff);
  addLabelValue(context, "Jistič", preview.breaker_code);
  addLabelValue(context, "Platnost", preview.valid_to ? `${preview.valid_from} → ${preview.valid_to}` : `od ${preview.valid_from}`);
  addLabelValue(context, "Návrh pro den", response.proposed_for_day);
  panel.append(context);

  const components = text("div", "frakon-confirm__components", "");
  components.append(
    componentList("Variabilní složky", Array.isArray(preview.variable_components) ? preview.variable_components : [], false),
    componentList("Fixní složky", Array.isArray(preview.fixed_components) ? preview.fixed_components : [], true),
  );
  panel.append(components);

  const sources = text("div", "frakon-confirm__sources", "");
  sources.append(
    sourceBox("Obchodní část", preview.supplier_source_url, preview.supplier_document_sha256),
    sourceBox("Regulovaná část", preview.regulated_source_url, preview.regulated_checksum),
  );
  panel.append(sources);

  const reasons = text("div", "frakon-confirm__checks", "");
  reasons.append(text("b", "", "Backend před vytvořením návrhu ověřil"));
  const list = document.createElement("ul");
  for (const reason of Array.isArray(preview.validation_reasons) ? preview.validation_reasons : []) {
    list.append(text("li", "", reason));
  }
  reasons.append(list);
  panel.append(reasons);

  const fingerprints = text("div", "frakon-confirm__fingerprints", "");
  for (const [label, value] of [
    ["Proposal", response.proposal_fingerprint],
    ["Smlouva", response.contract_fingerprint],
    ["All-in", response.all_in_tariff_fingerprint],
    ["Regulace", response.regulated_version_fingerprint],
  ]) {
    const row = text("span", "", "");
    row.append(text("b", "", `${label}: `), text("code", "", value));
    fingerprints.append(row);
  }
  panel.append(fingerprints);

  if (confirmed) {
    const success = text("div", "frakon-confirm__notice success", "");
    success.append(
      text("b", "", "Tarif je potvrzený a aktivní."),
      text("span", "", "Backend potvrdil přesně svázanou smlouvu a all-in verzi. Historické verze zůstaly zachované."),
    );
    panel.append(success);
  } else {
    const confirmBox = text("div", "frakon-confirm__confirm", "");
    const copy = text("div", "", "");
    copy.append(
      text("b", "", "Krok 5 · explicitní potvrzení"),
      text("span", "", "Do potvrzovacího requestu se neposílá cena, URL, PDF ani regulované hodnoty. Odesílá se pouze fingerprint tohoto serverově uloženého proposal envelope."),
    );
    const button = text("button", "frakon-confirm__confirm-button", busy ? "Potvrzuji přesný návrh…" : "Potvrdit a aktivovat tento tarif");
    button.type = "button";
    button.disabled = busy;
    button.addEventListener("click", confirmProposal);
    confirmBox.append(copy, button);
    panel.append(confirmBox);
  }

  root.append(panel);
}

function validateProposalResponse(response, context, discovery, candidate) {
  if (!response || typeof response !== "object" || !response.preview) {
    throw new Error("Backend nevrátil zákaznický all-in proposal.");
  }
  if (response.entry_id !== context.entryId) throw new Error("Proposal patří jiné konfiguraci.");
  if (response.candidate_fingerprint !== candidate.fingerprint) throw new Error("Proposal patří jinému ceníku.");
  if (response.contract_fingerprint !== discovery.contract_fingerprint) throw new Error("Proposal patří jiné verzi smlouvy.");
  if (response.source_url !== response.preview.supplier_source_url) throw new Error("Proposal má rozdílnou identitu zdrojové URL.");
  if (response.document_sha256 !== response.preview.supplier_document_sha256) throw new Error("Proposal má rozdílný SHA-256 dodavatelského dokumentu.");
  if (
    response.download_performed !== true ||
    response.parsing_performed !== true ||
    response.all_in_preview_performed !== true ||
    response.preview.all_in_ready !== true ||
    response.confirmation_performed !== false ||
    response.activation_performed !== false ||
    response.preview.persistence_performed !== false ||
    response.preview.activation_performed !== false
  ) {
    throw new Error("Backend nevrátil bezpečný nepotvrzený all-in proposal.");
  }
  for (const field of [
    "proposal_fingerprint",
    "contract_fingerprint",
    "all_in_tariff_fingerprint",
    "candidate_fingerprint",
    "regulated_version_fingerprint",
  ]) {
    const value = response[field];
    if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) {
      throw new Error(`Proposal má neplatné pole ${field}.`);
    }
  }
  if (response.preview.distribution_tariff !== context.contract.distribution_tariff) throw new Error("All-in sazba neodpovídá smlouvě.");
  if (response.preview.breaker_code !== `${context.contract.breaker.phases}x${context.contract.breaker.amperes}A`) throw new Error("All-in jistič neodpovídá smlouvě.");
  if (response.preview.product_name !== context.contract.product_name) throw new Error("All-in produkt neodpovídá smlouvě.");
}

async function prepareProposal() {
  if (busy || !pricePreview()) return;
  const startRevision = revision;
  busy = true;
  currentProposal = null;
  currentConfirmation = null;
  renderLoading("Ověřuji kompletní all-in návrh…", "Znovu načítám backend katalog, exact discovery, potvrzenou regulaci a oficiální dodavatelský dokument.");
  try {
    const context = await exactWizardContext();
    if (revision !== startRevision || !pricePreview()) return;
    const { discovery, candidate } = await rediscoverExactCandidate(context);
    if (revision !== startRevision || !pricePreview()) return;
    const response = await callWs({
      type: "frakon_energy/tariff/customer/propose",
      entry_id: context.entryId,
      contract: context.contract,
      day: context.day,
      candidate_fingerprint: candidate.fingerprint,
    });
    if (revision !== startRevision || !pricePreview()) return;
    validateProposalResponse(response, context, discovery, candidate);
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
  if (!response || typeof response !== "object" || response.confirmed !== true) {
    throw new Error("Backend nepotvrdil zákaznický tarif.");
  }
  for (const field of ["proposal_fingerprint", "contract_fingerprint", "all_in_tariff_fingerprint", "regulated_version_fingerprint"]) {
    if (response[field] !== proposal[field]) {
      throw new Error(`Potvrzení změnilo immutable referenci ${field}.`);
    }
  }
  if (
    response.activation_performed !== response.confirmation_performed ||
    response.persistence_performed !== response.confirmation_performed
  ) {
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
    if (revision !== startRevision || !pricePreview()) return;
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

function installStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
.frakon-tariff-confirmation-bridge{box-sizing:border-box;max-width:1600px;margin:0 auto 32px;padding:0 24px;color:#e2e8f0;font-family:inherit}.frakon-confirm__panel{display:grid;gap:16px;padding:22px;border:1px solid rgba(245,158,11,.3);border-radius:22px;background:linear-gradient(145deg,rgba(69,26,3,.16),rgba(2,6,23,.92));box-shadow:0 18px 54px rgba(2,6,23,.2)}.frakon-confirm__panel.confirmed{border-color:rgba(34,197,94,.34);background:linear-gradient(145deg,rgba(20,83,45,.16),rgba(2,6,23,.92))}.frakon-confirm__header,.frakon-confirm__actions,.frakon-confirm__confirm{display:flex;align-items:center;justify-content:space-between;gap:18px}.frakon-confirm__header h3{margin:4px 0 0;font-size:20px}.frakon-confirm__eyebrow{color:#7dd3fc;font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}.frakon-confirm__badge{display:inline-flex;align-items:center;min-height:30px;padding:0 11px;border-radius:999px;font-size:12px;font-weight:800;white-space:nowrap}.frakon-confirm__badge.pending{color:#fde68a;background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.24)}.frakon-confirm__badge.confirmed{color:#86efac;background:rgba(34,197,94,.12);border:1px solid rgba(34,197,94,.24)}.frakon-confirm__lead,.frakon-confirm__safety{margin:0;color:#94a3b8;font-size:13px;line-height:1.55}.frakon-confirm__primary,.frakon-confirm__secondary,.frakon-confirm__confirm-button{min-height:42px;padding:0 16px;border:1px solid rgba(56,189,248,.42);border-radius:11px;color:#f0f9ff;background:rgba(2,132,199,.28);font:inherit;font-size:13px;font-weight:850;cursor:pointer}.frakon-confirm__secondary{width:max-content;margin-top:12px}.frakon-confirm__confirm-button{border-color:rgba(74,222,128,.46);background:rgba(22,163,74,.26)}.frakon-confirm__primary:hover,.frakon-confirm__secondary:hover,.frakon-confirm__confirm-button:hover:not(:disabled){background:rgba(2,132,199,.4)}.frakon-confirm__confirm-button:hover:not(:disabled){background:rgba(22,163,74,.38)}.frakon-confirm__confirm-button:disabled{cursor:wait;opacity:.58}.frakon-confirm__notice{display:flex;flex-direction:column;gap:5px;padding:15px;border-radius:14px;color:#dbeafe;background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.2);line-height:1.45}.frakon-confirm__notice.loading{color:#bae6fd;background:rgba(14,165,233,.08);border-color:rgba(56,189,248,.2)}.frakon-confirm__notice.error{color:#fecaca;background:rgba(239,68,68,.08);border-color:rgba(239,68,68,.22)}.frakon-confirm__notice.success{color:#bbf7d0;background:rgba(34,197,94,.08);border-color:rgba(34,197,94,.22)}.frakon-confirm__totals{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.frakon-confirm__totals>div,.frakon-confirm__context>div{display:flex;flex-direction:column;gap:6px;padding:15px;border-radius:14px;background:rgba(2,6,23,.5);border:1px solid rgba(251,191,36,.13)}.frakon-confirm__totals span,.frakon-confirm__context span,.frakon-confirm__source span,.frakon-confirm__fingerprints span{color:#94a3b8;font-size:12px}.frakon-confirm__totals b{font-size:20px;color:#f8fafc}.frakon-confirm__context{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.frakon-confirm__components,.frakon-confirm__sources{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.frakon-confirm__component-block,.frakon-confirm__source{min-width:0;padding:15px;border-radius:14px;background:rgba(2,6,23,.38);border:1px solid rgba(148,163,184,.12)}.frakon-confirm__component-block h4{margin:0 0 10px}.frakon-confirm__component-list{display:grid}.frakon-confirm__component-list>div{display:flex;justify-content:space-between;gap:14px;padding:9px 0;border-top:1px solid rgba(148,163,184,.08)}.frakon-confirm__component-list>div:first-child{border-top:0}.frakon-confirm__component-list span{color:#94a3b8;font-size:12px}.frakon-confirm__component-list b{font-size:12px;text-align:right}.frakon-confirm__source{display:flex;flex-direction:column;gap:7px}.frakon-confirm__source a{width:max-content;color:#7dd3fc;font-size:13px;font-weight:750;text-decoration:none}.frakon-confirm__source a:hover{text-decoration:underline}.frakon-confirm__source code,.frakon-confirm__fingerprints code{color:#bae6fd;overflow-wrap:anywhere}.frakon-confirm__checks,.frakon-confirm__fingerprints{padding:14px;border-radius:13px;background:rgba(15,23,42,.48);border:1px solid rgba(148,163,184,.12)}.frakon-confirm__checks ul{margin:8px 0 0;padding-left:20px;line-height:1.55}.frakon-confirm__fingerprints{display:grid;gap:6px}.frakon-confirm__confirm{padding:16px;border-radius:15px;color:#fde68a;background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.22)}.frakon-confirm__confirm>div{display:flex;flex-direction:column;gap:5px;max-width:760px;line-height:1.45}.frakon-confirm__confirm span{color:#d6d3d1;font-size:12px}@media(max-width:900px){.frakon-confirm__totals,.frakon-confirm__context{grid-template-columns:repeat(2,minmax(0,1fr))}.frakon-confirm__components,.frakon-confirm__sources{grid-template-columns:1fr}}@media(max-width:640px){.frakon-tariff-confirmation-bridge{padding:0 16px}.frakon-confirm__header,.frakon-confirm__actions,.frakon-confirm__confirm{align-items:flex-start;flex-direction:column}.frakon-confirm__totals,.frakon-confirm__context{grid-template-columns:1fr}.frakon-confirm__primary,.frakon-confirm__secondary,.frakon-confirm__confirm-button{width:100%}.frakon-confirm__component-list>div{flex-direction:column;gap:4px}.frakon-confirm__component-list b{text-align:left}}
`;
  document.head.append(style);
}

function syncBridge() {
  installStyles();
  if (!pricePreview()) {
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
  if (event.target.closest(`#${BRIDGE_ID}`)) return;
  bumpRevisionAndReset();
}

document.addEventListener("change", onWizardChange, true);
document.addEventListener("click", (event) => {
  if (!(event.target instanceof Element)) return;
  if (!event.target.closest(".tariff-wizard button")) return;
  bumpRevisionAndReset();
}, true);
const observer = new MutationObserver(() => queueMicrotask(syncBridge));
observer.observe(document.documentElement, { childList: true, subtree: true });
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", syncBridge, { once: true });
else syncBridge();
