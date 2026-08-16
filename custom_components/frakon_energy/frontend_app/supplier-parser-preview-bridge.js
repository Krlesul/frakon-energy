const AUTO_PARSER_STYLE_ID = "frakon-supplier-parser-preview-style";
const AUTO_PARSER_PREVIEW_ID = "frakon-supplier-parser-preview";
const SHA256_RE = /^[0-9a-f]{64}$/;
const SUPPORTED_PARSERS = {
  eon: "eon_commercial_v1",
  pre: "pre_commercial_v1",
};

let revision = 0;
let busyFingerprint = null;
let automaticFailed = false;

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
  return "Automatický parser se nepodařilo bezpečně dokončit.";
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
  const value = fieldControl(labelText)?.value?.trim();
  if (!value) throw new Error(`Chybí hodnota pole „${labelText}“.`);
  return value;
}

function integerValue(labelText) {
  const value = Number(requiredValue(labelText));
  if (!Number.isInteger(value) || value <= 0) throw new Error(`Pole „${labelText}“ není platné celé číslo.`);
  return value;
}

function normalizeUrl(rawUrl) {
  const url = new URL(String(rawUrl), document.baseURI);
  if (url.protocol !== "https:") throw new Error("Ověřený ceník nemá bezpečnou HTTPS adresu.");
  url.hash = "";
  return url.href;
}

async function resolveEntryId() {
  const primary = await callWs({ type: "frakon_energy/entry/primary" });
  if (!primary || primary.provider !== "visionq" || primary.loaded !== true || typeof primary.entry_id !== "string") {
    throw new Error("Backend neurčil aktivní VisionQ konfiguraci FRAKON Energy.");
  }
  return primary.entry_id;
}

async function exactWizardContext() {
  const supplier = requiredValue("Dodavatel");
  if (!SUPPORTED_PARSERS[supplier]) throw new Error("Tento automatic parser companion podporuje pouze E.ON a PRE.");
  const productName = requiredValue("Produkt ze smlouvy");
  const distributor = requiredValue("Distribuční území");
  const distributionTariff = requiredValue("Distribuční sazba");
  const phases = integerValue("Počet fází");
  const amperes = integerValue("Hlavní jistič");
  const validFrom = requiredValue("Smlouva platí od");
  const day = requiredValue("Ceník ověřit k datu");
  const entryId = await resolveEntryId();

  const catalog = await callWs({ type: "frakon_energy/tariff/catalog", entry_id: entryId });
  if (!catalog || !Array.isArray(catalog.suppliers) || catalog.activation_performed || catalog.persistence_performed) {
    throw new Error("Backend katalog neprošel read-only kontrolou.");
  }
  const group = catalog.suppliers.find((item) => item?.supplier === supplier);
  const products = Array.isArray(group?.products) ? group.products : [];
  const matches = products.filter((item) => item?.product_name === productName);
  if (matches.length !== 1) throw new Error("Vybraný produkt není v backend katalogu jednoznačný.");
  const product = matches[0];
  if (product.price_scope !== "supplier_commercial" || product.requires_document_resolver) {
    throw new Error("Vybraný produkt nemá statickou supplier-commercial parser boundary.");
  }
  if (product.contract_kind !== "fixed" && product.contract_kind !== "indefinite") {
    throw new Error("Backend vrátil nepodporovaný typ smlouvy.");
  }
  const fixationEnd = product.contract_kind === "fixed" ? requiredValue("Fixace do") : null;
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

function candidateSource(card) {
  const anchor = card.querySelector(".tariff-candidate__actions a[href]");
  if (!anchor) throw new Error("Candidate nemá oficiální zdrojovou URL.");
  return normalizeUrl(anchor.href);
}

async function rediscoverCardCandidate(context, card) {
  const discovery = await callWs({
    type: "frakon_energy/tariff/discover",
    entry_id: context.entryId,
    contract: context.contract,
    day: context.day,
  });
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
    throw new Error("Opakované discovery neprošlo read-only kontrolou.");
  }
  const sourceUrl = candidateSource(card);
  const matches = discovery.candidates.filter((candidate) => {
    try {
      return (
        candidate?.supplier === context.contract.supplier &&
        candidate?.product_name === context.contract.product_name &&
        candidate?.price_scope === "supplier_commercial" &&
        candidate?.match_score === 100 &&
        SHA256_RE.test(candidate?.fingerprint ?? "") &&
        normalizeUrl(candidate?.source_url) === sourceUrl &&
        candidate?.download_performed === false &&
        candidate?.parsing_performed === false &&
        candidate?.persistence_performed === false &&
        candidate?.activation_performed === false
      );
    } catch {
      return false;
    }
  });
  if (matches.length !== 1) throw new Error("Candidate už není v backend discovery právě jednou jako 100/100 shoda.");
  return { discovery, candidate: matches[0] };
}

function validatePreviewResponse(response, context, discovery, candidate) {
  if (!response || typeof response !== "object" || !response.preview) throw new Error("Backend nevrátil parser preview.");
  const preview = response.preview;
  if (response.entry_id !== context.entryId) throw new Error("Parser preview patří jiné konfiguraci.");
  if (response.contract_fingerprint !== discovery.contract_fingerprint) throw new Error("Parser preview patří jiné smlouvě.");
  if (response.candidate_fingerprint !== candidate.fingerprint) throw new Error("Parser preview patří jinému candidate fingerprintu.");
  if (normalizeUrl(response.source_url) !== normalizeUrl(candidate.source_url)) throw new Error("Parser preview změnil supplier source URL.");
  if (!SHA256_RE.test(response.document_sha256 ?? "") || response.document_sha256 !== preview.document_sha256) {
    throw new Error("Parser preview nemá konzistentní SHA-256.");
  }
  if (
    response.download_performed !== true ||
    response.parsing_performed !== true ||
    response.persistence_performed !== false ||
    response.activation_performed !== false ||
    preview.parsing_performed !== true ||
    preview.persistence_performed !== false ||
    preview.activation_performed !== false
  ) {
    throw new Error("Backend parser preview porušil read-only boundary.");
  }
  if (preview.supplier !== context.contract.supplier) throw new Error("Parser preview změnil dodavatele.");
  if (preview.product_name !== context.contract.product_name) throw new Error("Parser preview změnil produkt.");
  if (preview.distribution_tariff !== context.contract.distribution_tariff) throw new Error("Parser preview změnil distribuční sazbu.");
  if (preview.valid_from !== candidate.valid_from) throw new Error("Parser preview změnil začátek platnosti candidate.");
  if (preview.price_scope !== "supplier_commercial" || preview.includes_vat !== true || preview.extraction_confidence !== 100) {
    throw new Error("Parser preview nemá přesnou supplier-commercial VAT autoritu.");
  }
  if (preview.parser_name !== SUPPORTED_PARSERS[context.contract.supplier]) {
    throw new Error("Backend použil neočekávaný supplier parser.");
  }
}

function removePreview() {
  document.getElementById(AUTO_PARSER_PREVIEW_ID)?.remove();
  for (const card of wizard()?.querySelectorAll(".tariff-candidate[data-frakon-auto-selected='true']") ?? []) {
    card.classList.remove("selected");
    delete card.dataset.frakonAutoSelected;
  }
}

function renderPreview(response, card) {
  removePreview();
  const preview = response.preview;
  const section = text("section", "tariff-price-preview frakon-auto-parser-preview", "");
  section.id = AUTO_PARSER_PREVIEW_ID;

  const head = text("div", "tariff-wizard__results-head", "");
  const title = text("div", "", "");
  title.append(text("span", "eyebrow", "Krok 3 · automatic exact parser"), text("h3", "", "Ověřený návrh obchodní ceny"));
  head.append(title, text("span", "", `${preview.extraction_confidence}/100 exact guards`));
  section.append(head);

  const grid = text("div", "tariff-price-preview__grid", "");
  for (const [label, value] of [
    ["Vysoký tarif", `${preview.high_rate_czk_per_kwh} Kč/kWh`],
    ["Nízký tarif", preview.low_rate_czk_per_kwh ? `${preview.low_rate_czk_per_kwh} Kč/kWh` : "—"],
    ["Stálý plat dodavatele", `${preview.supplier_standing_czk_month} Kč/měsíc`],
    ["DPH", preview.includes_vat ? "Zahrnuta" : "Nezahrnuta"],
  ]) {
    const item = text("div", "", "");
    item.append(text("span", "", label), text("b", "", value));
    grid.append(item);
  }
  section.append(grid);

  const meta = text("div", "tariff-price-preview__meta", "");
  for (const [label, value] of [
    ["Produkt", preview.product_name],
    ["Sazba", preview.distribution_tariff],
    ["Platnost od", preview.valid_from],
    ["PDF", `${preview.page_count} str.`],
    ["Parser", preview.parser_name],
  ]) {
    const item = text("span", "", "");
    item.append(text("b", "", `${label}: `), document.createTextNode(String(value)));
    meta.append(item);
  }
  const sha = text("span", "", "");
  sha.append(text("b", "", "SHA-256: "), text("code", "", response.document_sha256));
  meta.append(sha);
  section.append(meta);

  const source = text("a", "tariff-price-preview__source", "Otevřít přesný ověřený zdrojový dokument");
  source.href = normalizeUrl(response.source_url);
  source.target = "_blank";
  source.rel = "noreferrer";
  section.append(source);

  const checks = text("div", "tariff-price-preview__checks", "");
  checks.append(text("b", "", "Prošlé validační kontroly"));
  const list = document.createElement("ul");
  for (const reason of Array.isArray(preview.validation_reasons) ? preview.validation_reasons : []) list.append(text("li", "", reason));
  checks.append(list);
  section.append(checks);

  const notice = text("div", "tariff-wizard__notice pending", "");
  notice.append(text("b", "", "Toto ještě není all-in cena."), text("span", "", "Jde pouze o automaticky parsovanou obchodní část. Regulovaná část zůstává nezávislou autoritou."));
  section.append(notice);

  card.classList.add("selected");
  card.dataset.frakonAutoSelected = "true";
  const results = wizard()?.querySelector(".tariff-wizard__results");
  (results ?? wizard())?.insertAdjacentElement("afterend", section);
}

function renderCardError(card, error) {
  let errorBox = card.querySelector(".frakon-auto-parser__error");
  if (!errorBox) {
    errorBox = text("div", "frakon-auto-parser__error", "");
    card.append(errorBox);
  }
  errorBox.replaceChildren(text("b", "", "Automatický parser selhal"), text("span", "", readableError(error)));
}

async function previewCard(card, button) {
  if (busyFingerprint) return;
  const startRevision = revision;
  automaticFailed = false;
  document.body.classList.add("frakon-auto-parser-available");
  card.querySelector(".frakon-auto-parser__error")?.remove();
  button.disabled = true;
  button.textContent = "Stahuji a ověřuji PDF…";
  try {
    const context = await exactWizardContext();
    const { discovery, candidate } = await rediscoverCardCandidate(context, card);
    busyFingerprint = candidate.fingerprint;
    const response = await callWs({
      type: "frakon_energy/tariff/parse_preview",
      entry_id: context.entryId,
      contract: context.contract,
      day: context.day,
      candidate_fingerprint: candidate.fingerprint,
    });
    if (revision !== startRevision) return;
    validatePreviewResponse(response, context, discovery, candidate);
    renderPreview(response, card);
    button.textContent = "Ceny ověřeny automaticky";
  } catch (error) {
    if (revision === startRevision) {
      automaticFailed = true;
      document.body.classList.remove("frakon-auto-parser-available");
      removePreview();
      renderCardError(card, error);
      button.textContent = "Zkusit automatický parser znovu";
    }
  } finally {
    busyFingerprint = null;
    button.disabled = false;
  }
}

function decorateCandidates() {
  installStyles();
  const target = wizard();
  const supplier = fieldControl("Dodavatel")?.value?.trim();
  if (!target || !SUPPORTED_PARSERS[supplier]) {
    document.body.classList.remove("frakon-auto-parser-available");
    return;
  }
  const cards = [...target.querySelectorAll(".tariff-candidate")];
  let decorated = 0;
  for (const card of cards) {
    const pending = card.querySelector(".tariff-candidate__parser-pending");
    const actions = card.querySelector(".tariff-candidate__actions");
    if (!pending || !actions) continue;
    decorated += 1;
    if (card.querySelector(".frakon-auto-parser__button")) continue;
    const button = text("button", "tariff-candidate__preview-button frakon-auto-parser__button", "Načíst ověřené ceny automaticky");
    button.type = "button";
    button.addEventListener("click", () => previewCard(card, button));
    pending.insertAdjacentElement("beforebegin", button);
    pending.hidden = true;
  }
  if (decorated && !automaticFailed) document.body.classList.add("frakon-auto-parser-available");
}

function resetAutomatic() {
  revision += 1;
  busyFingerprint = null;
  automaticFailed = false;
  document.body.classList.remove("frakon-auto-parser-available");
  removePreview();
  for (const error of wizard()?.querySelectorAll(".frakon-auto-parser__error") ?? []) error.remove();
}

function installStyles() {
  if (document.getElementById(AUTO_PARSER_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = AUTO_PARSER_STYLE_ID;
  style.textContent = `
body.frakon-auto-parser-available #frakon-manual-tariff-entry-bridge{display:none!important}.frakon-auto-parser__error{display:flex;flex-direction:column;gap:4px;margin-top:10px;padding:11px 12px;border:1px solid rgba(239,68,68,.24);border-radius:10px;color:#fecaca;background:rgba(239,68,68,.08);font-size:12px}.frakon-auto-parser-preview{scroll-margin-top:18px}
`;
  document.head.append(style);
}

document.addEventListener("change", (event) => {
  if (!(event.target instanceof Element) || !event.target.closest(".tariff-wizard")) return;
  resetAutomatic();
  queueMicrotask(decorateCandidates);
}, true);
document.addEventListener("click", (event) => {
  if (!(event.target instanceof Element)) return;
  if (event.target.closest(".frakon-auto-parser__button")) return;
  if (!event.target.closest(".tariff-wizard button")) return;
  resetAutomatic();
  queueMicrotask(decorateCandidates);
}, true);
const observer = new MutationObserver(() => queueMicrotask(decorateCandidates));
observer.observe(document.documentElement, { childList: true, subtree: true });
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", decorateCandidates, { once: true });
else decorateCandidates();
