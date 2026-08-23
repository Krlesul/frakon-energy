const BRIDGE_ID = "frakon-tariff-confirmation-bridge-v2";
const STYLE_ID = `${BRIDGE_ID}-style`;
const SUPPLIER_LABELS = { cez: "ČEZ", eon: "E.ON", pre: "PRE", mnd: "MND" };
const LOCAL_WS_TIMEOUT_MS = 12000;
const DOWNLOAD_WS_TIMEOUT_MS = 35000;

let revision = 0;
let busy = false;
let currentRegulatedProposal = null;
let currentCustomerProposal = null;
let currentConfirmation = null;
let syncQueued = false;

function hassObject() {
  return window.__FRAKON_ENERGY_HASS__ ?? window.hass;
}

function timeoutError(stage, timeoutMs) {
  return new Error(`${stage} neodpověděl do ${Math.round(timeoutMs / 1000)} s. Požadavek byl v UI bezpečně ukončen; nic nebylo aktivováno.`);
}

async function callWs(message, { timeoutMs = LOCAL_WS_TIMEOUT_MS, stage = "Home Assistant" } = {}) {
  const hass = hassObject();
  if (!hass) throw new Error("Home Assistant není dostupný.");
  let request;
  if (typeof hass.callWS === "function") request = hass.callWS(message);
  else if (typeof hass.connection?.sendMessagePromise === "function") request = hass.connection.sendMessagePromise(message);
  else throw new Error("WebSocket Home Assistantu není dostupný.");

  let timer;
  try {
    return await Promise.race([
      request,
      new Promise((_, reject) => {
        timer = window.setTimeout(() => reject(timeoutError(stage, timeoutMs)), timeoutMs);
      }),
    ]);
  } finally {
    if (timer) window.clearTimeout(timer);
  }
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

function errorCode(error) {
  if (error && typeof error === "object" && typeof error.code === "string") return error.code;
  return null;
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
  if (!Number.isInteger(value) || value <= 0) throw new Error(`Pole „${labelText}“ nemá platnou celočíselnou hodnotu.`);
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

  const primary = await callWs(
    { type: "frakon_energy/entry/primary" },
    { stage: "Určení aktivní konfigurace" },
  );
  if (!primary || primary.provider !== "visionq" || primary.loaded !== true || typeof primary.entry_id !== "string") {
    throw new Error("Backend neurčil aktivní VisionQ konfiguraci FRAKON Energy.");
  }

  const entryId = primary.entry_id;
  const catalog = await callWs(
    { type: "frakon_energy/tariff/catalog", entry_id: entryId },
    { stage: "Načtení katalogu dodavatelů" },
  );
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
  if (product.price_scope !== "supplier_commercial") throw new Error("Vybraný produkt nemá supplier-commercial autoritu.");
  if (product.requires_document_resolver) throw new Error("Vybraný produkt vyžaduje resolver, který není pro potvrzení připravený.");
  if (product.contract_kind !== "fixed" && product.contract_kind !== "indefinite") throw new Error("Backend vrátil nepodporovaný typ smlouvy.");

  let fixationEnd = null;
  if (product.contract_kind === "fixed") fixationEnd = requiredValue("Fixace do");

  return {
    entryId,
    day,
    contract: {
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
    },
  };
}

function selectedSourceUrl() {
  const card = selectedCandidateCard();
  if (!card) throw new Error("Není vybraný přesný ověřený ceník.");
  const anchor = card.querySelector(".tariff-candidate__actions a[href]");
  if (!anchor) throw new Error("Vybraný ceník nemá dostupný oficiální zdroj.");
  return normalizeUrl(anchor.href);
}

async function rediscoverExactCandidate(context) {
  const discovery = await callWs(
    {
      type: "frakon_energy/tariff/discover",
      entry_id: context.entryId,
      contract: context.contract,
      day: context.day,
    },
    { stage: "Opakované ověření ceníku" },
  );
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
  if (matches.length !== 1) throw new Error("Vybraný zdroj už není v backend discovery jednoznačný. Spusťte ověření ceníku znovu.");
  return { discovery, candidate: matches[0] };
}

function bridgeRoot() {
  return document.getElementById(BRIDGE_ID);
}

function ensureRoot() {
  let root = bridgeRoot();
  if (root) return root;
  const appRoot = document.getElementById("root");
  if (!appRoot?.parentNode) return null;
  root = document.createElement("section");
  root.id = BRIDGE_ID;
  root.className = "frakon-tariff-confirmation-bridge";
  appRoot.parentNode.insertBefore(root, appRoot.nextSibling);
  return root;
}

function resetState({ remove = true } = {}) {
  revision += 1;
  busy = false;
  currentRegulatedProposal = null;
  currentCustomerProposal = null;
  currentConfirmation = null;
  if (remove) bridgeRoot()?.remove();
}

function renderNotice(title, message, kind = "info") {
  const root = ensureRoot();
  if (!root) return;
  root.replaceChildren();
  const box = text("div", `frakon-confirm__notice ${kind}`, "");
  box.append(text("b", "", title), text("span", "", message));
  root.append(box);
}

function addLabelValue(parent, label, value) {
  const item = text("div", "", "");
  item.append(text("span", "", label), text("b", "", value));
  parent.append(item);
}

function sourceBox(title, url, checksum) {
  const block = text("div", "frakon-confirm__source", "");
  block.append(text("span", "", title));
  const link = safeLink("Otevřít ověřený zdroj", url);
  if (link) block.append(link);
  if (checksum) block.append(text("code", "", `SHA-256 ${checksum}`));
  return block;
}

function renderIdle() {
  if (!pricePreview() || busy) return;
  const root = ensureRoot();
  if (!root) return;
  root.replaceChildren();

  const panel = text("div", "frakon-confirm__panel", "");
  const header = text("div", "frakon-confirm__header", "");
  const heading = text("div", "", "");
  heading.append(
    text("span", "frakon-confirm__eyebrow", "Krok 4 · serverově ověřený all-in"),
    text("h3", "", "Sestavit kompletní cenu a připravit potvrzení"),
  );
  header.append(heading, text("span", "frakon-confirm__badge pending", "Aktivace zamčená"));
  panel.append(header);
  panel.append(text("p", "frakon-confirm__lead", "FRAKON znovu ověří smlouvu, přesný ceník a regulovanou část. Žádnou cenu ani URL nepřebírá z formuláře jako autoritu."));

  const button = text("button", "frakon-confirm__primary", "Sestavit kompletní all-in návrh");
  button.type = "button";
  button.addEventListener("click", prepareProposal);
  const actions = text("div", "frakon-confirm__actions", "");
  actions.append(button, text("span", "frakon-confirm__safety", "Krok vytváří jen nepotvrzený návrh. Aktivace vyžaduje další explicitní potvrzení."));
  panel.append(actions);
  root.append(panel);
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
    busy = false;
    currentRegulatedProposal = null;
    currentCustomerProposal = null;
    currentConfirmation = null;
    renderIdle();
  });
  panel.append(notice, retry);
  root.append(panel);
}

function renderRegulatedProposal() {
  const root = ensureRoot();
  if (!root || !currentRegulatedProposal) return;
  root.replaceChildren();
  const { response, context } = currentRegulatedProposal;
  const proposal = response.proposal;
  const bundle = proposal?.bundle;
  if (!bundle) return renderError(new Error("Backend nevrátil regulovaný návrh."));

  const panel = text("div", "frakon-confirm__panel", "");
  const header = text("div", "frakon-confirm__header", "");
  const heading = text("div", "", "");
  heading.append(text("span", "frakon-confirm__eyebrow", "Krok 4A · oficiální regulovaná část"), text("h3", "", "Ověřená regulovaná cena čeká na potvrzení"));
  header.append(heading, text("span", "frakon-confirm__badge pending", "Aktivace stále zamčená"));
  panel.append(header);
  panel.append(text("p", "frakon-confirm__lead", "Regulované částky sestavil server z oficiálního katalogu pro přesnou sazbu, jistič a datum. Tímto krokem se zákaznický tarif ještě neaktivuje."));

  const info = text("div", "frakon-confirm__context", "");
  addLabelValue(info, "Distributor", bundle.distributor);
  addLabelValue(info, "Sazba", bundle.distribution_tariff);
  addLabelValue(info, "Jistič", bundle.breaker_code);
  addLabelValue(info, "Platnost", `${bundle.valid_from} → ${bundle.valid_to ?? "bez konce"}`);
  addLabelValue(info, "Ověřeno k datu", context.day);
  addLabelValue(info, "Fingerprint", `${response.proposal_fingerprint?.slice(0, 16) ?? "—"}…`);
  panel.append(info);

  const components = text("div", "frakon-confirm__components", "");
  for (const component of bundle.variable_components ?? []) {
    const row = text("div", "frakon-confirm__component", "");
    row.append(text("span", "", component.name ?? component.kind ?? "Proměnná složka"), text("b", "", `VT ${component.high_rate_czk_per_kwh ?? "—"} · NT ${component.low_rate_czk_per_kwh ?? "—"} Kč/kWh bez DPH`));
    components.append(row);
  }
  for (const component of bundle.fixed_components ?? []) {
    const row = text("div", "frakon-confirm__component", "");
    row.append(text("span", "", component.name ?? component.kind ?? "Fixní složka"), text("b", "", `${component.monthly_czk ?? "—"} Kč/měsíc bez DPH`));
    components.append(row);
  }
  panel.append(components);

  const sources = text("div", "frakon-confirm__sources", "");
  for (const item of proposal.evidence ?? []) sources.append(sourceBox(item.document_name ?? item.source_name ?? "Oficiální regulovaný zdroj", item.source_url, item.checksum));
  panel.append(sources);

  const button = text("button", "frakon-confirm__confirm-button", "Potvrdit ověřenou regulovanou část");
  button.type = "button";
  button.addEventListener("click", confirmRegulatedProposal);
  panel.append(button);
  root.append(panel);
}

function renderCustomerProposal() {
  const root = ensureRoot();
  if (!root || !currentCustomerProposal) return;
  root.replaceChildren();
  const response = currentCustomerProposal.response;
  const preview = response.preview;
  if (!preview) return renderError(new Error("Backend nevrátil kompletní all-in náhled."));

  const panel = text("div", `frakon-confirm__panel${currentConfirmation?.confirmed ? " confirmed" : ""}`, "");
  const header = text("div", "frakon-confirm__header", "");
  const heading = text("div", "", "");
  heading.append(text("span", "frakon-confirm__eyebrow", "Krok 4B · kompletní all-in"), text("h3", "", "Kompletní cena elektřiny"));
  header.append(heading, text("span", `frakon-confirm__badge ${currentConfirmation?.confirmed ? "confirmed" : "pending"}`, currentConfirmation?.confirmed ? "Potvrzeno a aktivováno" : "Čeká na vaše potvrzení"));
  panel.append(header);

  const totals = text("div", "frakon-confirm__totals", "");
  addLabelValue(totals, "All-in VT", `${preview.all_in_vt_czk_kwh} Kč/kWh`);
  addLabelValue(totals, "All-in NT", `${preview.all_in_nt_czk_kwh} Kč/kWh`);
  addLabelValue(totals, "Fixní platby", `${preview.fixed_monthly_total_czk} Kč/měsíc`);
  panel.append(totals);

  const info = text("div", "frakon-confirm__context", "");
  addLabelValue(info, "Dodavatel", SUPPLIER_LABELS[preview.supplier] ?? preview.supplier);
  addLabelValue(info, "Produkt", preview.product_name);
  addLabelValue(info, "Sazba", preview.distribution_tariff);
  addLabelValue(info, "Jistič", preview.breaker_code);
  addLabelValue(info, "Platnost", preview.valid_to ? `${preview.valid_from} → ${preview.valid_to}` : `od ${preview.valid_from}`);
  addLabelValue(info, "Návrh pro den", response.proposed_for_day);
  panel.append(info);

  const sources = text("div", "frakon-confirm__sources", "");
  sources.append(sourceBox("Obchodní část", preview.supplier_source_url, preview.supplier_document_sha256));
  sources.append(sourceBox("Regulovaná část", preview.regulated_source_url, preview.regulated_checksum));
  panel.append(sources);

  if (currentConfirmation?.confirmed) {
    const success = text("div", "frakon-confirm__notice success", "");
    success.append(text("b", "", "Tarif je potvrzený a aktivní."), text("span", "", "Backend potvrdil přesně svázanou smlouvu a all-in verzi. Historické verze zůstaly zachované."));
    panel.append(success);
  } else {
    const button = text("button", "frakon-confirm__confirm-button", "Potvrdit a aktivovat tento all-in tarif");
    button.type = "button";
    button.addEventListener("click", confirmCustomerProposal);
    panel.append(button);
  }
  root.append(panel);
}

async function requestCustomerProposal(context, candidate) {
  const response = await callWs(
    {
      type: "frakon_energy/tariff/customer/propose",
      entry_id: context.entryId,
      contract: context.contract,
      day: context.day,
      candidate_fingerprint: candidate.fingerprint,
    },
    { timeoutMs: DOWNLOAD_WS_TIMEOUT_MS, stage: "Stažení, ověření a sestavení all-in ceníku" },
  );
  if (!response || typeof response.proposal_fingerprint !== "string" || !response.preview) throw new Error("Backend nevrátil platný zákaznický all-in návrh.");
  if (response.entry_id !== context.entryId) throw new Error("All-in návrh patří jiné konfiguraci.");
  if (response.proposed_for_day !== context.day) throw new Error("All-in návrh byl vytvořen pro jiné datum.");
  if (response.confirmation_performed !== false || response.activation_performed !== false) throw new Error("Nepotvrzený all-in návrh nesmí nic aktivovat.");
  if (response.preview.product_name !== context.contract.product_name) throw new Error("All-in produkt neodpovídá smlouvě.");
  if (response.preview.distribution_tariff !== context.contract.distribution_tariff) throw new Error("All-in sazba neodpovídá smlouvě.");
  if (response.preview.breaker_code !== `${context.contract.breaker.phases}x${context.contract.breaker.amperes}A`) throw new Error("All-in jistič neodpovídá smlouvě.");
  return response;
}

async function requestOfficialRegulatedProposal(context) {
  const response = await callWs(
    {
      type: "frakon_energy/tariff/regulated/official_propose",
      entry_id: context.entryId,
      distributor: context.contract.distributor,
      distribution_tariff: context.contract.distribution_tariff,
      breaker_code: `${context.contract.breaker.phases}x${context.contract.breaker.amperes}A`,
      day: context.day,
    },
    { stage: "Sestavení oficiální regulované části" },
  );
  if (!response || response.server_authored !== true || response.source_authority !== "official_2026_frozen" || !response.proposal) throw new Error("Backend nevrátil platný oficiální regulovaný návrh.");
  if (response.entry_id !== context.entryId || response.requested_day !== context.day) throw new Error("Regulovaný návrh neodpovídá konfiguraci nebo datu.");
  if (response.confirmation_performed !== false || response.activation_performed !== false) throw new Error("Nepotvrzená regulovaná část nesmí nic aktivovat.");
  return response;
}

async function prepareProposal() {
  if (busy || !pricePreview()) return;
  const startRevision = revision;
  busy = true;
  currentRegulatedProposal = null;
  currentCustomerProposal = null;
  currentConfirmation = null;
  renderNotice("Ověřuji kompletní all-in návrh…", "Kontroluji konfiguraci, katalog a přesný vybraný ceník. Každý krok má časový limit.", "loading");

  try {
    const context = await exactWizardContext();
    if (revision !== startRevision || !pricePreview()) return;
    const { candidate } = await rediscoverExactCandidate(context);
    if (revision !== startRevision || !pricePreview()) return;

    try {
      const response = await requestCustomerProposal(context, candidate);
      if (revision !== startRevision || !pricePreview()) return;
      currentCustomerProposal = { response, entryId: context.entryId };
      busy = false;
      renderCustomerProposal();
      bridgeRoot()?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    } catch (error) {
      if (errorCode(error) !== "regulated_tariff_not_available") throw error;
    }

    renderNotice("Načítám oficiální regulovanou část…", "Pro tuto sazbu zatím není potvrzená regulovaná verze. Server vytváří přesný návrh z ověřeného 2026 katalogu.", "loading");
    const regulatedResponse = await requestOfficialRegulatedProposal(context);
    if (revision !== startRevision || !pricePreview()) return;
    currentRegulatedProposal = { response: regulatedResponse, entryId: context.entryId, context, candidate, revision: startRevision };
    busy = false;
    renderRegulatedProposal();
    bridgeRoot()?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    if (revision === startRevision) {
      busy = false;
      renderError(error);
    }
  }
}

async function confirmRegulatedProposal() {
  if (busy || !currentRegulatedProposal) return;
  const staged = currentRegulatedProposal;
  const startRevision = staged.revision;
  busy = true;
  renderNotice("Potvrzuji regulovanou část…", "Odesílám pouze fingerprint serverově uloženého návrhu. Zákaznický tarif se tím ještě neaktivuje.", "loading");
  try {
    const response = await callWs(
      {
        type: "frakon_energy/tariff/regulated/confirm",
        entry_id: staged.entryId,
        proposal_fingerprint: staged.response.proposal_fingerprint,
      },
      { stage: "Potvrzení regulované části" },
    );
    if (revision !== startRevision || !pricePreview()) return;
    if (!response || response.confirmed !== true || response.entry_id !== staged.entryId || response.activation_performed !== false) throw new Error("Backend nepotvrdil regulovanou část bezpečným způsobem.");

    renderNotice("Regulovaná část potvrzena…", "Sestavuji finální all-in návrh z potvrzené regulace a stejného přesného ceníku dodavatele.", "loading");
    const customerResponse = await requestCustomerProposal(staged.context, staged.candidate);
    if (revision !== startRevision || !pricePreview()) return;
    currentRegulatedProposal = null;
    currentCustomerProposal = { response: customerResponse, entryId: staged.entryId };
    busy = false;
    renderCustomerProposal();
    bridgeRoot()?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    if (revision === startRevision) {
      busy = false;
      renderError(error);
    }
  }
}

async function confirmCustomerProposal() {
  if (busy || !currentCustomerProposal || currentConfirmation?.confirmed) return;
  const proposal = currentCustomerProposal.response;
  const entryId = currentCustomerProposal.entryId;
  const startRevision = revision;
  busy = true;
  renderNotice("Potvrzuji a aktivuji all-in tarif…", "Odesílám pouze fingerprint přesně sestaveného serverového návrhu.", "loading");
  try {
    const response = await callWs(
      {
        type: "frakon_energy/tariff/customer/confirm",
        entry_id: entryId,
        proposal_fingerprint: proposal.proposal_fingerprint,
      },
      { stage: "Potvrzení zákaznického all-in tarifu" },
    );
    if (revision !== startRevision || !pricePreview()) return;
    if (!response || response.confirmed !== true || response.entry_id !== entryId) throw new Error("Backend nepotvrdil zákaznický tarif.");
    for (const field of ["proposal_fingerprint", "contract_fingerprint", "all_in_tariff_fingerprint", "regulated_version_fingerprint"]) {
      if (response[field] !== proposal[field]) throw new Error(`Potvrzení změnilo immutable referenci ${field}.`);
    }
    currentConfirmation = response;
    busy = false;
    renderCustomerProposal();
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
.frakon-tariff-confirmation-bridge{box-sizing:border-box;max-width:1600px;margin:0 auto 32px;padding:0 24px;color:#e2e8f0;font-family:inherit}.frakon-confirm__panel{display:grid;gap:16px;padding:22px;border:1px solid rgba(245,158,11,.3);border-radius:22px;background:linear-gradient(145deg,rgba(69,26,3,.16),rgba(2,6,23,.92));box-shadow:0 18px 54px rgba(2,6,23,.2)}.frakon-confirm__panel.confirmed{border-color:rgba(34,197,94,.34);background:linear-gradient(145deg,rgba(20,83,45,.16),rgba(2,6,23,.92))}.frakon-confirm__header,.frakon-confirm__actions{display:flex;align-items:center;justify-content:space-between;gap:18px}.frakon-confirm__header h3{margin:4px 0 0;font-size:20px}.frakon-confirm__eyebrow{color:#7dd3fc;font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}.frakon-confirm__badge{display:inline-flex;align-items:center;min-height:30px;padding:0 11px;border-radius:999px;font-size:12px;font-weight:800;white-space:nowrap}.frakon-confirm__badge.pending{color:#fde68a;background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.24)}.frakon-confirm__badge.confirmed{color:#86efac;background:rgba(34,197,94,.12);border:1px solid rgba(34,197,94,.24)}.frakon-confirm__lead,.frakon-confirm__safety{margin:0;color:#94a3b8;font-size:13px;line-height:1.55}.frakon-confirm__primary,.frakon-confirm__secondary,.frakon-confirm__confirm-button{min-height:44px;padding:0 16px;border:1px solid rgba(56,189,248,.42);border-radius:11px;color:#f0f9ff;background:rgba(2,132,199,.28);font:inherit;font-size:13px;font-weight:850;cursor:pointer}.frakon-confirm__confirm-button{border-color:rgba(74,222,128,.46);background:rgba(22,163,74,.26)}.frakon-confirm__notice{display:flex;flex-direction:column;gap:6px;padding:16px;border-radius:14px;color:#dbeafe;background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.2);line-height:1.45}.frakon-confirm__notice.loading{color:#bae6fd;background:rgba(14,165,233,.08);border-color:rgba(56,189,248,.2)}.frakon-confirm__notice.error{color:#fecaca;background:rgba(239,68,68,.08);border-color:rgba(239,68,68,.22)}.frakon-confirm__notice.success{color:#bbf7d0;background:rgba(34,197,94,.08);border-color:rgba(34,197,94,.22)}.frakon-confirm__totals,.frakon-confirm__context{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.frakon-confirm__totals>div,.frakon-confirm__context>div,.frakon-confirm__component,.frakon-confirm__source{display:flex;flex-direction:column;gap:6px;padding:14px;border-radius:14px;background:rgba(2,6,23,.48);border:1px solid rgba(148,163,184,.12)}.frakon-confirm__totals span,.frakon-confirm__context span,.frakon-confirm__source span,.frakon-confirm__component span{color:#94a3b8;font-size:12px}.frakon-confirm__totals b{font-size:19px}.frakon-confirm__components,.frakon-confirm__sources{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.frakon-confirm__component b{font-size:12px}.frakon-confirm__source a{color:#7dd3fc;font-size:13px;font-weight:750;text-decoration:none}.frakon-confirm__source code{color:#bae6fd;overflow-wrap:anywhere;font-size:11px}@media(max-width:900px){.frakon-confirm__totals,.frakon-confirm__context,.frakon-confirm__components,.frakon-confirm__sources{grid-template-columns:1fr}}@media(max-width:640px){.frakon-tariff-confirmation-bridge{padding:0 16px}.frakon-confirm__header,.frakon-confirm__actions{align-items:flex-start;flex-direction:column}.frakon-confirm__primary,.frakon-confirm__secondary,.frakon-confirm__confirm-button{width:100%}}
`;
  document.head.append(style);
}

function renderCurrentStateIfNeeded() {
  const root = bridgeRoot();
  if (!root || root.hasChildNodes() || busy) return;
  if (currentCustomerProposal) renderCustomerProposal();
  else if (currentRegulatedProposal) renderRegulatedProposal();
  else renderIdle();
}

function syncBridge() {
  installStyles();
  if (!pricePreview()) {
    if (bridgeRoot()) resetState({ remove: true });
    return;
  }
  ensureRoot();
  renderCurrentStateIfNeeded();
}

function scheduleSync() {
  if (syncQueued) return;
  syncQueued = true;
  window.requestAnimationFrame(() => {
    syncQueued = false;
    syncBridge();
  });
}

function mutationTouchesOnlyBridge(mutation, root) {
  if (!root) return false;
  const target = mutation.target;
  return target === root || (target instanceof Node && root.contains(target));
}

document.addEventListener("change", (event) => {
  if (!(event.target instanceof Element) || !event.target.closest(".tariff-wizard")) return;
  resetState({ remove: true });
  scheduleSync();
}, true);

document.addEventListener("click", (event) => {
  if (!(event.target instanceof Element)) return;
  if (!event.target.closest(".tariff-wizard button")) return;
  resetState({ remove: true });
  scheduleSync();
}, true);

const observer = new MutationObserver((mutations) => {
  const root = bridgeRoot();
  if (root && mutations.length > 0 && mutations.every((mutation) => mutationTouchesOnlyBridge(mutation, root))) return;
  scheduleSync();
});
observer.observe(document.documentElement, { childList: true, subtree: true });

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scheduleSync, { once: true });
else scheduleSync();
