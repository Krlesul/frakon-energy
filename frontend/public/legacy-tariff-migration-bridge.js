const LEGACY_MIGRATION_BRIDGE_ID = "frakon-legacy-tariff-migration-bridge";
const LEGACY_MIGRATION_STYLE_ID = `${LEGACY_MIGRATION_BRIDGE_ID}-style`;
const SHA256_RE = /^[0-9a-f]{64}$/;
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DECIMAL_RE = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;

let revision = 0;
let busy = false;
let proposal = null;
let confirmation = null;
let errorState = null;
let draft = { validFrom: "", validTo: "" };

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
  return "Migraci se nepodařilo bezpečně dokončit.";
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

async function resolveEntryId() {
  const entries = await callWs({ type: "config_entries/get" });
  if (!Array.isArray(entries)) throw new Error("Backend nevrátil seznam konfigurací.");
  const matches = entries.filter((entry) => entry?.domain === "frakon_energy");
  if (matches.length !== 1 || typeof matches[0]?.entry_id !== "string") {
    throw new Error("Nelze jednoznačně určit konfiguraci FRAKON Energy.");
  }
  return matches[0].entry_id;
}

function normalizeDate(rawValue, label) {
  const value = String(rawValue ?? "").trim();
  if (!ISO_DATE_RE.test(value)) throw new Error(`${label} musí být platné datum.`);
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) {
    throw new Error(`${label} musí být platné datum.`);
  }
  return value;
}

function normalizeDecimal(rawValue, label) {
  const value = String(rawValue ?? "").trim();
  if (!DECIMAL_RE.test(value)) throw new Error(`${label} nemá platný desetinný formát.`);
  return value;
}

function bridgeRoot() {
  return document.getElementById(LEGACY_MIGRATION_BRIDGE_ID);
}

function ensureRoot() {
  let root = bridgeRoot();
  if (root) return root;
  const appRoot = document.getElementById("root");
  if (!appRoot?.parentNode) return null;
  root = document.createElement("section");
  root.id = LEGACY_MIGRATION_BRIDGE_ID;
  root.className = "frakon-legacy-migration";
  const anchor =
    document.getElementById("frakon-load-all-in-estimate-bridge") ??
    document.getElementById("frakon-manual-tariff-entry-bridge") ??
    document.getElementById("frakon-tariff-confirmation-bridge") ??
    appRoot;
  anchor.parentNode.insertBefore(root, anchor.nextSibling);
  return root;
}

function removeRoot() {
  bridgeRoot()?.remove();
}

function invalidateView() {
  const root = bridgeRoot();
  if (root) root.dataset.view = "";
}

function resetState() {
  revision += 1;
  busy = false;
  proposal = null;
  confirmation = null;
  errorState = null;
  invalidateView();
  queueMicrotask(syncBridge);
}

function clearError() {
  errorState = null;
  invalidateView();
  queueMicrotask(syncBridge);
}

function renderOnce(root, view, builder) {
  if (root.dataset.view === view) return;
  root.dataset.view = view;
  root.replaceChildren();
  builder(root);
}

function addValue(parent, label, value) {
  const item = text("div", "frakon-legacy-migration__value", "");
  item.append(text("span", "", label), text("b", "", value));
  parent.append(item);
}

function dateField(labelText, value, onInput) {
  const label = text("label", "frakon-legacy-migration__field", "");
  label.append(text("span", "", labelText));
  const input = document.createElement("input");
  input.type = "date";
  input.value = value;
  input.autocomplete = "off";
  input.addEventListener("input", () => onInput(input.value));
  label.append(input);
  return label;
}

function validateProposal(result, context) {
  if (!result || typeof result !== "object") throw new Error("Backend nevrátil migration preview.");
  if (result.entry_id !== context.entryId) throw new Error("Migration preview patří jiné konfiguraci.");
  if (!SHA256_RE.test(result.fingerprint ?? "")) throw new Error("Migration preview nemá platný fingerprint.");
  if (result.valid_from !== context.validFrom || result.valid_to !== context.validTo) {
    throw new Error("Backend změnil potvrzované období migrace.");
  }
  if (result.source !== "legacy_options" || result.authority_method !== "legacy_manual_import") {
    throw new Error("Backend nevrátil očekávanou legacy authority.");
  }
  if (
    result.component_breakdown_available !== false ||
    result.official_provenance_available !== false ||
    result.historical_only !== true ||
    result.confirmed !== false ||
    result.proposal_performed !== true ||
    result.confirmation_performed !== false ||
    result.live_pricing_changed !== false ||
    result.activation_performed !== false
  ) {
    throw new Error("Migration preview porušil read-only/historical bezpečnostní kontrakt.");
  }
  const highRate = normalizeDecimal(result.high_rate_czk_per_kwh, "Legacy VT");
  const lowRate = normalizeDecimal(result.low_rate_czk_per_kwh, "Legacy NT");
  const fixedMonthly = normalizeDecimal(result.fixed_monthly_czk, "Legacy měsíční platba");
  return {
    entryId: context.entryId,
    validFrom: context.validFrom,
    validTo: context.validTo,
    fingerprint: result.fingerprint,
    highRate,
    lowRate,
    fixedMonthly,
  };
}

function validateConfirmation(result, expected) {
  if (!result || typeof result !== "object") throw new Error("Backend nevrátil potvrzení migrace.");
  if (result.entry_id !== expected.entryId || result.fingerprint !== expected.fingerprint) {
    throw new Error("Potvrzení migrace neodpovídá připravenému fingerprintu.");
  }
  if (result.valid_from !== expected.validFrom || result.valid_to !== expected.validTo) {
    throw new Error("Potvrzené období se liší od migration preview.");
  }
  if (result.source !== "legacy_options" || result.authority_method !== "legacy_manual_import") {
    throw new Error("Potvrzení nemá legacy authority.");
  }
  if (
    result.component_breakdown_available !== false ||
    result.official_provenance_available !== false ||
    result.historical_only !== true ||
    result.confirmed !== true ||
    result.proposal_performed !== false ||
    result.live_pricing_changed !== false ||
    result.activation_performed !== false
  ) {
    throw new Error("Potvrzení migrace porušilo historical-only bezpečnostní kontrakt.");
  }
  if (
    normalizeDecimal(result.high_rate_czk_per_kwh, "Legacy VT") !== expected.highRate ||
    normalizeDecimal(result.low_rate_czk_per_kwh, "Legacy NT") !== expected.lowRate ||
    normalizeDecimal(result.fixed_monthly_czk, "Legacy měsíční platba") !== expected.fixedMonthly
  ) {
    throw new Error("Serverové legacy ceny se mezi preview a potvrzením změnily.");
  }
  return { ...expected, confirmed: true };
}

async function proposeMigration() {
  if (busy) return;
  const myRevision = ++revision;
  busy = true;
  proposal = null;
  confirmation = null;
  errorState = null;
  invalidateView();
  syncBridge();
  try {
    const validFrom = normalizeDate(draft.validFrom, "Platnost od");
    const validTo = normalizeDate(draft.validTo, "Platnost do");
    if (validTo < validFrom) throw new Error("Platnost do nesmí být před platností od.");
    const entryId = await resolveEntryId();
    const result = await callWs({
      type: "frakon_energy/tariff/legacy/propose",
      entry_id: entryId,
      valid_from: validFrom,
      valid_to: validTo,
    });
    if (revision !== myRevision) return;
    proposal = validateProposal(result, { entryId, validFrom, validTo });
  } catch (error) {
    if (revision !== myRevision) return;
    errorState = {
      kind: "propose",
      title: "Migraci nelze připravit",
      message: readableError(error),
    };
  } finally {
    if (revision === myRevision) {
      busy = false;
      invalidateView();
      queueMicrotask(syncBridge);
    }
  }
}

async function confirmMigration() {
  if (busy || !proposal) return;
  const expected = proposal;
  const myRevision = ++revision;
  busy = true;
  errorState = null;
  invalidateView();
  syncBridge();
  try {
    const result = await callWs({
      type: "frakon_energy/tariff/legacy/confirm",
      entry_id: expected.entryId,
      snapshot_fingerprint: expected.fingerprint,
    });
    if (revision !== myRevision) return;
    confirmation = validateConfirmation(result, expected);
  } catch (error) {
    if (revision !== myRevision) return;
    errorState = {
      kind: "confirm",
      title: "Historii nelze potvrdit",
      message: readableError(error),
    };
  } finally {
    if (revision === myRevision) {
      busy = false;
      invalidateView();
      queueMicrotask(syncBridge);
    }
  }
}

function renderIdle(root) {
  renderOnce(root, "idle", (target) => {
    const panel = text("div", "frakon-legacy-migration__panel", "");
    const header = text("div", "frakon-legacy-migration__header", "");
    const heading = text("div", "", "");
    heading.append(
      text("span", "frakon-legacy-migration__eyebrow", "Historie · explicitní migrace"),
      text("h3", "", "Převést staré VT / NT ceny do historie"),
    );
    header.append(heading, text("span", "frakon-legacy-migration__badge", "Live tarif nedotčen"));
    panel.append(header);
    panel.append(
      text(
        "p",
        "frakon-legacy-migration__lead",
        "FRAKON načte původní VT, NT a měsíční platbu přímo z uložené konfigurace. Zde potvrďte pouze období, pro které staré ceny skutečně platily. Historie nikdy nepředstírá dodavatele, regulované složky ani oficiální provenance.",
      ),
    );

    const form = text("div", "frakon-legacy-migration__form", "");
    form.append(
      dateField("Staré ceny platily od", draft.validFrom, (value) => {
        draft.validFrom = value;
      }),
      dateField("Staré ceny platily do", draft.validTo, (value) => {
        draft.validTo = value;
      }),
    );
    panel.append(form);

    const notice = text("div", "frakon-legacy-migration__notice", "");
    notice.append(
      text("b", "", "Pouze minulost"),
      text("span", "", "Backend odmítne období končící dnes nebo v budoucnu. Nový potvrzený all-in tarif má při historickém výpočtu vždy přednost."),
    );
    panel.append(notice);

    const actions = text("div", "frakon-legacy-migration__actions", "");
    const button = text("button", "frakon-legacy-migration__primary", "Načíst serverové staré ceny");
    button.type = "button";
    button.addEventListener("click", proposeMigration);
    actions.append(button, text("span", "frakon-legacy-migration__safety", "Tento krok vytvoří pouze nepotvrzený historický snapshot."));
    panel.append(actions);
    target.append(panel);
  });
}

function renderBusy(root) {
  renderOnce(root, `busy-${revision}`, (target) => {
    const panel = text("div", "frakon-legacy-migration__panel", "");
    panel.append(
      text("span", "frakon-legacy-migration__eyebrow", "Ověřuji serverovou historii"),
      text("h3", "", "Kontroluji immutable migration boundary…"),
      text("p", "frakon-legacy-migration__lead", "Live tarif ani aktivace se tímto krokem nemění."),
    );
    target.append(panel);
  });
}

function renderError(root) {
  const state = errorState;
  if (!state) return;
  renderOnce(root, `error-${state.kind}-${revision}`, (target) => {
    const panel = text("div", "frakon-legacy-migration__panel", "");
    panel.append(
      text("h3", "", state.title),
      text("p", "frakon-legacy-migration__error", state.message),
    );
    const retry = text(
      "button",
      "frakon-legacy-migration__secondary",
      state.kind === "confirm" ? "Zpět na preview" : "Zpět k období",
    );
    retry.type = "button";
    retry.addEventListener("click", clearError);
    panel.append(retry);
    target.append(panel);
  });
}

function renderProposal(root) {
  renderOnce(root, `proposal-${proposal.fingerprint}`, (target) => {
    const panel = text("div", "frakon-legacy-migration__panel", "");
    const header = text("div", "frakon-legacy-migration__header", "");
    const heading = text("div", "", "");
    heading.append(
      text("span", "frakon-legacy-migration__eyebrow", "Serverové legacy ceny načteny"),
      text("h3", "", "Potvrďte historický snapshot"),
    );
    header.append(heading, text("span", "frakon-legacy-migration__badge ready", "Fingerprint připraven"));
    panel.append(header);

    const values = text("div", "frakon-legacy-migration__values", "");
    addValue(values, "Období", `${proposal.validFrom} → ${proposal.validTo}`);
    addValue(values, "VT", `${proposal.highRate} Kč/kWh`);
    addValue(values, "NT", `${proposal.lowRate} Kč/kWh`);
    addValue(values, "Měsíční platba", `${proposal.fixedMonthly} Kč/měs.`);
    addValue(values, "Authority", "legacy_manual_import");
    addValue(values, "Fingerprint", proposal.fingerprint);
    panel.append(values);

    const warning = text("div", "frakon-legacy-migration__notice warning", "");
    warning.append(
      text("b", "", "Bez falešné provenance"),
      text("span", "", "Tyto staré hodnoty jsou již hotové all-in ceny. FRAKON z nich nevymýšlí commodity, distribuci, POZE ani zdrojový dokument."),
    );
    panel.append(warning);

    const actions = text("div", "frakon-legacy-migration__actions", "");
    const confirm = text("button", "frakon-legacy-migration__primary", "Potvrdit do historie");
    confirm.type = "button";
    confirm.addEventListener("click", confirmMigration);
    const change = text("button", "frakon-legacy-migration__secondary", "Změnit období");
    change.type = "button";
    change.addEventListener("click", resetState);
    actions.append(confirm, change);
    panel.append(actions);
    target.append(panel);
  });
}

function renderConfirmed(root) {
  renderOnce(root, `confirmed-${confirmation.fingerprint}`, (target) => {
    const panel = text("div", "frakon-legacy-migration__panel success", "");
    const header = text("div", "frakon-legacy-migration__header", "");
    const heading = text("div", "", "");
    heading.append(
      text("span", "frakon-legacy-migration__eyebrow", "Historie potvrzena"),
      text("h3", "", "Staré ceny jsou bezpečně uložené jako historický snapshot"),
    );
    header.append(heading, text("span", "frakon-legacy-migration__badge success", "Live tarif beze změny"));
    panel.append(header);
    panel.append(
      text(
        "p",
        "frakon-legacy-migration__lead",
        `Období ${confirmation.validFrom} až ${confirmation.validTo} může vyplnit pouze historické dny bez přesného confirmed all-in tarifu.`,
      ),
    );
    const values = text("div", "frakon-legacy-migration__values", "");
    addValue(values, "VT", `${confirmation.highRate} Kč/kWh`);
    addValue(values, "NT", `${confirmation.lowRate} Kč/kWh`);
    addValue(values, "Měsíční platba", `${confirmation.fixedMonthly} Kč/měs.`);
    addValue(values, "Fingerprint", confirmation.fingerprint);
    panel.append(values);
    const another = text("button", "frakon-legacy-migration__secondary", "Přidat jiné historické období");
    another.type = "button";
    another.addEventListener("click", () => {
      draft = { validFrom: "", validTo: "" };
      resetState();
    });
    panel.append(another);
    target.append(panel);
  });
}

function syncBridge() {
  if (!wizard()) {
    removeRoot();
    return;
  }
  const root = ensureRoot();
  if (!root) return;
  if (busy) renderBusy(root);
  else if (errorState) renderError(root);
  else if (confirmation) renderConfirmed(root);
  else if (proposal) renderProposal(root);
  else renderIdle(root);
}

function ensureStyle() {
  if (document.getElementById(LEGACY_MIGRATION_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = LEGACY_MIGRATION_STYLE_ID;
  style.textContent = `
    .frakon-legacy-migration{margin:16px 0;color:var(--frakon-text,#e8edf4);font-family:inherit}
    .frakon-legacy-migration__panel{border:1px solid rgba(122,162,208,.24);background:linear-gradient(145deg,rgba(14,22,32,.97),rgba(9,15,23,.96));border-radius:18px;padding:18px;box-shadow:0 16px 48px rgba(0,0,0,.2)}
    .frakon-legacy-migration__panel.success{border-color:rgba(72,196,137,.34)}
    .frakon-legacy-migration__header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}
    .frakon-legacy-migration__eyebrow{display:block;text-transform:uppercase;letter-spacing:.1em;font-size:11px;color:#8da8c4;margin-bottom:5px}
    .frakon-legacy-migration h3{margin:0;font-size:18px;line-height:1.3;color:#f4f7fb}
    .frakon-legacy-migration__badge{border:1px solid rgba(122,162,208,.32);border-radius:999px;padding:6px 10px;font-size:11px;color:#b9cbe0;background:rgba(122,162,208,.08)}
    .frakon-legacy-migration__badge.ready{border-color:rgba(245,191,80,.36);color:#f4ca76;background:rgba(245,191,80,.09)}
    .frakon-legacy-migration__badge.success{border-color:rgba(72,196,137,.4);color:#91dfb8;background:rgba(72,196,137,.09)}
    .frakon-legacy-migration__lead{margin:12px 0;color:#aebdce;font-size:13px;line-height:1.55}
    .frakon-legacy-migration__form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:14px 0}
    .frakon-legacy-migration__field{display:grid;gap:6px;font-size:12px;color:#9fb2c7}
    .frakon-legacy-migration__field input{box-sizing:border-box;width:100%;border:1px solid rgba(122,162,208,.25);border-radius:11px;background:#0c141e;color:#eef3f9;padding:10px 11px;font:inherit;color-scheme:dark}
    .frakon-legacy-migration__values{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:14px 0}
    .frakon-legacy-migration__value{display:grid;gap:3px;padding:10px 12px;border-radius:12px;background:rgba(255,255,255,.035);min-width:0}
    .frakon-legacy-migration__value span{font-size:11px;color:#8fa3b8}
    .frakon-legacy-migration__value b{font-size:13px;color:#ecf2f8;overflow-wrap:anywhere}
    .frakon-legacy-migration__notice{display:grid;gap:3px;margin:12px 0;padding:11px 12px;border-left:3px solid #6ea7d9;border-radius:8px;background:rgba(64,122,176,.08);font-size:12px;color:#aebdce}
    .frakon-legacy-migration__notice.warning{border-left-color:#d8a846;background:rgba(216,168,70,.08)}
    .frakon-legacy-migration__notice b{color:#e8eef6}
    .frakon-legacy-migration__error{color:#f0a7a7;white-space:pre-wrap}
    .frakon-legacy-migration__actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:14px}
    .frakon-legacy-migration button{border:0;border-radius:11px;padding:10px 14px;font:inherit;font-weight:700;cursor:pointer}
    .frakon-legacy-migration__primary{background:#d7e7f7;color:#0b1420}
    .frakon-legacy-migration__secondary{background:rgba(255,255,255,.08);color:#d9e5f0;border:1px solid rgba(255,255,255,.1)!important}
    .frakon-legacy-migration__safety{font-size:11px;color:#8196aa;line-height:1.4}
    @media (max-width:720px){.frakon-legacy-migration__form,.frakon-legacy-migration__values{grid-template-columns:1fr}.frakon-legacy-migration__actions button{width:100%}}
  `;
  document.head.append(style);
}

ensureStyle();
const observer = new MutationObserver(() => queueMicrotask(syncBridge));
observer.observe(document.documentElement, { childList: true, subtree: true });
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) syncBridge();
});
queueMicrotask(syncBridge);
