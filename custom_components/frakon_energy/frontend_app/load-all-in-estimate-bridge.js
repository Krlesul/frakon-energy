const BRIDGE_ID = "frakon-load-all-in-estimate-bridge";
const STYLE_ID = `${BRIDGE_ID}-style`;
const SHA256_RE = /^[0-9a-f]{64}$/;
const ISO_DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const ALLOWED_AUTHORITY = new Set(["verified_parser", "manual_user_entry"]);

let revision = 0;
let busy = false;
let activeProfileId = null;
let activeResult = null;

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
  if (error && typeof error === "object" && typeof error.message === "string") return error.message;
  return "All-in odhad se nepodařilo bezpečně načíst.";
}

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? "" : String(value);
  return node;
}

function finiteNonNegative(value, field) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) throw new Error(`Backend vrátil neplatné pole ${field}.`);
  return number;
}

function formatPrice(value) {
  return `${Number(value).toLocaleString("cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 6 })} Kč/kWh`;
}

function formatMoney(value) {
  return `${Number(value).toLocaleString("cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Kč`;
}

function formatEnergy(value) {
  return `${Number(value).toLocaleString("cs-CZ", { minimumFractionDigits: 0, maximumFractionDigits: 3 })} kWh`;
}

function profileIdFromItem(item) {
  const main = item?.querySelector(".load-profile-item__main");
  const profileId = main?.querySelector("small")?.textContent?.trim();
  if (!profileId) throw new Error("Nelze bezpečně určit ID profilu.");
  return profileId;
}

function runtimeIsoValues() {
  const inputs = document.querySelectorAll('.load-profile-runtime input[type="datetime-local"]');
  const toIso = (input) => {
    const raw = input?.value?.trim();
    if (!raw) return null;
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) throw new Error("Časové okno profilu není platné.");
    return parsed.toISOString();
  };
  return {
    earliestStart: toIso(inputs[0]),
    deadline: toIso(inputs[1]),
  };
}

async function resolveEntryForProfile(profileId) {
  const primary = await callWs({ type: "frakon_energy/entry/primary" });
  if (!primary || primary.provider !== "visionq" || primary.loaded !== true || typeof primary.entry_id !== "string") {
    throw new Error("Backend neurčil aktivní VisionQ konfiguraci FRAKON Energy.");
  }
  const entryId = primary.entry_id;
  const response = await callWs({ type: "frakon_energy/load_profiles/list", entry_id: entryId });
  if (!response || response.entry_id !== entryId || !Array.isArray(response.profiles) || response.read_only_execution !== true) {
    throw new Error("Backend nevrátil platný seznam profilů aktivní VisionQ konfigurace.");
  }
  const matches = response.profiles.filter((profile) => profile?.profile_id === profileId);
  if (matches.length !== 1) throw new Error("Profil není v aktivní VisionQ konfiguraci právě jednou.");
  return { entryId, profile: matches[0] };
}

function validateTariffRecord(record) {
  if (!record || typeof record !== "object") throw new Error("All-in odhad obsahuje neplatný tarifní záznam.");
  if (!ISO_DAY_RE.test(record.day ?? "")) throw new Error("All-in tarifní záznam nemá platný den.");
  finiteNonNegative(record.energy_kwh, "tariffs.energy_kwh");
  finiteNonNegative(record.vt_czk_kwh, "tariffs.vt_czk_kwh");
  finiteNonNegative(record.nt_czk_kwh, "tariffs.nt_czk_kwh");
  if (!SHA256_RE.test(record.all_in_tariff_fingerprint ?? "")) throw new Error("All-in tarifní fingerprint není platný SHA-256.");
  if (!ALLOWED_AUTHORITY.has(record.authority_method)) throw new Error("All-in tarif má nepovolenou authority metodu.");
  if (typeof record.supplier !== "string" || !record.supplier.trim()) throw new Error("All-in tarif nemá dodavatele.");
  if (typeof record.product_name !== "string" || !record.product_name.trim()) throw new Error("All-in tarif nemá produkt.");
}

function validateAllInEstimate(estimate, plan) {
  if (!estimate || typeof estimate !== "object") throw new Error("Backend nevrátil all-in odhad.");
  if (estimate.source !== "confirmed_all_in" || estimate.fixed_monthly_excluded !== true) {
    throw new Error("All-in odhad nemá potvrzenou cenovou autoritu.");
  }
  if (estimate.available === false) {
    if (estimate.reason !== "confirmed_customer_all_in_unavailable") throw new Error("Backend vrátil neznámý důvod nedostupnosti all-in ceny.");
    return estimate;
  }
  if (estimate.available !== true) throw new Error("All-in odhad nemá platný stav dostupnosti.");
  const energy = finiteNonNegative(estimate.estimated_energy_kwh, "all_in_estimate.estimated_energy_kwh");
  const planEnergy = finiteNonNegative(plan.estimated_energy_kwh, "plan.estimated_energy_kwh");
  if (Math.abs(energy - planEnergy) > 0.002) throw new Error("All-in odhad energie neodpovídá spot plánu.");
  finiteNonNegative(estimate.vt_average_czk_kwh, "all_in_estimate.vt_average_czk_kwh");
  finiteNonNegative(estimate.nt_average_czk_kwh, "all_in_estimate.nt_average_czk_kwh");
  finiteNonNegative(estimate.vt_cost_czk, "all_in_estimate.vt_cost_czk");
  finiteNonNegative(estimate.nt_cost_czk, "all_in_estimate.nt_cost_czk");
  if (!Array.isArray(estimate.tariffs) || estimate.tariffs.length === 0) throw new Error("All-in odhad nemá tarifní provenance záznamy.");
  for (const record of estimate.tariffs) validateTariffRecord(record);
  return estimate;
}

function validatePreviewResponse(response, entryId, profileId) {
  if (!response || typeof response !== "object") throw new Error("Backend nevrátil profilový preview.");
  if (response.read_only !== true) throw new Error("Profilový preview není read-only.");
  if (!response.profile || response.profile.profile_id !== profileId) throw new Error("Backend vrátil jiný profil.");
  if (response.available !== true || !response.plan) throw new Error("Pro profil není dostupný plán.");
  const plan = response.plan;
  if (plan.read_only !== true) throw new Error("Load plan není read-only.");
  if (typeof plan.starts_at !== "string" || typeof plan.ends_at !== "string") throw new Error("Load plan nemá platné časové hranice.");
  finiteNonNegative(plan.average_czk_kwh, "plan.average_czk_kwh");
  finiteNonNegative(plan.minimum_czk_kwh, "plan.minimum_czk_kwh");
  finiteNonNegative(plan.maximum_czk_kwh, "plan.maximum_czk_kwh");
  finiteNonNegative(plan.estimated_cost_czk, "plan.estimated_cost_czk");
  return { entryId, profileId, plan, estimate: validateAllInEstimate(plan.all_in_estimate, plan) };
}

function bridgeRoot() {
  return document.getElementById(BRIDGE_ID);
}

function removeRoot() {
  bridgeRoot()?.remove();
}

function previewNode() {
  return document.querySelector(".load-profile-preview");
}

function ensureRoot() {
  const preview = previewNode();
  if (!preview?.parentNode) return null;
  let root = bridgeRoot();
  if (!root) {
    root = document.createElement("section");
    root.id = BRIDGE_ID;
    root.className = "frakon-load-all-in-bridge";
  }
  if (root.previousElementSibling !== preview) preview.parentNode.insertBefore(root, preview.nextSibling);
  return root;
}

function renameSpotLabels() {
  const preview = previewNode();
  if (!preview) return;
  const replacements = new Map([
    ["Průměrná cena", "Spot optimalizační průměr"],
    ["Rozsah ceny", "Spot rozsah ceny"],
    ["Odhad ceny", "Spot optimalizační náklad"],
  ]);
  for (const label of preview.querySelectorAll(".load-profile-preview__metrics span")) {
    const replacement = replacements.get(label.textContent?.trim() ?? "");
    if (replacement) label.textContent = replacement;
  }
}

function addMetric(parent, label, value, detail = null) {
  const box = text("div", "", "");
  box.append(text("span", "", label), text("b", "", value));
  if (detail) box.append(text("small", "", detail));
  parent.append(box);
}

function renderResult() {
  renameSpotLabels();
  const root = ensureRoot();
  if (!root || !activeResult) return;
  root.replaceChildren();
  const panel = text("div", "frakon-load-all-in__panel", "");
  panel.append(text("span", "frakon-load-all-in__eyebrow", "Potvrzený zákaznický all-in tarif"));
  const estimate = activeResult.estimate;
  if (estimate.available) {
    panel.append(text("h3", "", "Reálný náklad běhu podle VT / NT scénáře"));
    const metrics = text("div", "frakon-load-all-in__metrics", "");
    addMetric(metrics, "All-in VT náklad", formatMoney(estimate.vt_cost_czk), formatPrice(estimate.vt_average_czk_kwh));
    addMetric(metrics, "All-in NT náklad", formatMoney(estimate.nt_cost_czk), formatPrice(estimate.nt_average_czk_kwh));
    addMetric(metrics, "Energie běhu", formatEnergy(estimate.estimated_energy_kwh));
    addMetric(metrics, "Spot optimalizační náklad", formatMoney(activeResult.plan.estimated_cost_czk));
    panel.append(metrics);

    const note = text("p", "frakon-load-all-in__note", "Fixní měsíční platby nejsou přiřazené jednomu běhu. Skutečný náklad mezi VT a NT závisí na distribučním/HDO režimu během běhu; spotová cena zůstává pouze optimalizační signál pro volbu intervalu.");
    panel.append(note);
    const provenance = text("div", "frakon-load-all-in__provenance", "");
    for (const record of estimate.tariffs) {
      const row = text("div", "", "");
      row.append(
        text("span", "", `${record.day} · ${record.supplier} · ${record.product_name}`),
        text("code", "", `${record.authority_method} · ${record.all_in_tariff_fingerprint}`),
      );
      provenance.append(row);
    }
    panel.append(provenance);
  } else {
    panel.append(text("h3", "", "Potvrzený all-in tarif pro tento běh není dostupný"));
    panel.append(text("p", "frakon-load-all-in__note warning", "Spot optimalizační náklad není konečná zákaznická cena. Potvrďte přesný zákaznický all-in tarif, aby FRAKON mohl zobrazit VT/NT náklad EV, bojleru nebo jiného profilu."));
  }
  root.append(panel);
}

function renderLoading() {
  renameSpotLabels();
  const root = ensureRoot();
  if (!root) return;
  root.replaceChildren(text("div", "frakon-load-all-in__loading", "Ověřuji potvrzený all-in tarif pro tento profil…"));
}

function renderError(error) {
  renameSpotLabels();
  const root = ensureRoot();
  if (!root) return;
  root.replaceChildren(text("div", "frakon-load-all-in__error", readableError(error)));
}

async function loadEstimate(profileId, startRevision) {
  busy = true;
  activeProfileId = profileId;
  activeResult = null;
  queueMicrotask(renderLoading);
  try {
    const resolved = await resolveEntryForProfile(profileId);
    const runtime = runtimeIsoValues();
    const message = {
      type: "frakon_energy/load_plan/preview_profile",
      entry_id: resolved.entryId,
      profile_id: profileId,
    };
    if (runtime.earliestStart) message.earliest_start = runtime.earliestStart;
    if (runtime.deadline) message.deadline = runtime.deadline;
    const response = await callWs(message);
    if (revision !== startRevision || activeProfileId !== profileId) return;
    activeResult = validatePreviewResponse(response, resolved.entryId, profileId);
    busy = false;
    renderResult();
  } catch (error) {
    if (revision !== startRevision || activeProfileId !== profileId) return;
    busy = false;
    renderError(error);
  }
}

function reset() {
  revision += 1;
  busy = false;
  activeProfileId = null;
  activeResult = null;
  removeRoot();
  queueMicrotask(renameSpotLabels);
}

function previewButtonFromEvent(event) {
  if (!(event.target instanceof Element)) return null;
  const button = event.target.closest(".load-profile-item__actions button");
  if (!button) return null;
  return button.textContent?.trim() === "Spočítat preview" ? button : null;
}

document.addEventListener("click", (event) => {
  const button = previewButtonFromEvent(event);
  if (!button) return;
  let profileId;
  try {
    profileId = profileIdFromItem(button.closest(".load-profile-item"));
  } catch (error) {
    reset();
    queueMicrotask(() => renderError(error));
    return;
  }
  revision += 1;
  const startRevision = revision;
  void loadEstimate(profileId, startRevision);
}, true);

document.addEventListener("change", (event) => {
  if (!(event.target instanceof Element)) return;
  if (event.target.closest(".load-profile-runtime, .load-profile-form")) reset();
}, true);

function installStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
.frakon-load-all-in-bridge{margin:14px 0 0}.frakon-load-all-in__panel{display:grid;gap:14px;padding:18px;border:1px solid rgba(34,197,94,.24);border-radius:18px;background:linear-gradient(145deg,rgba(20,83,45,.13),rgba(2,6,23,.86));color:#e2e8f0}.frakon-load-all-in__panel h3{margin:0;font-size:18px}.frakon-load-all-in__eyebrow{color:#86efac;font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}.frakon-load-all-in__metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.frakon-load-all-in__metrics>div{display:flex;flex-direction:column;gap:5px;padding:13px;border-radius:12px;background:rgba(2,6,23,.44);border:1px solid rgba(148,163,184,.11)}.frakon-load-all-in__metrics span,.frakon-load-all-in__metrics small{color:#94a3b8;font-size:11px}.frakon-load-all-in__metrics b{font-size:16px}.frakon-load-all-in__note{margin:0;color:#cbd5e1;font-size:12px;line-height:1.55}.frakon-load-all-in__note.warning{color:#fde68a}.frakon-load-all-in__provenance{display:grid;gap:7px}.frakon-load-all-in__provenance>div{display:flex;justify-content:space-between;gap:12px;padding-top:7px;border-top:1px solid rgba(148,163,184,.08);font-size:11px}.frakon-load-all-in__provenance span{color:#94a3b8}.frakon-load-all-in__provenance code{color:#86efac;overflow-wrap:anywhere;text-align:right}.frakon-load-all-in__loading,.frakon-load-all-in__error{padding:13px 15px;border-radius:13px;font-size:12px}.frakon-load-all-in__loading{color:#dbeafe;background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.18)}.frakon-load-all-in__error{color:#fecaca;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2)}@media(max-width:900px){.frakon-load-all-in__metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:640px){.frakon-load-all-in__metrics{grid-template-columns:1fr}.frakon-load-all-in__provenance>div{flex-direction:column}.frakon-load-all-in__provenance code{text-align:left}}
`;
  document.head.append(style);
}

installStyles();
const observer = new MutationObserver(() => {
  renameSpotLabels();
  if (activeResult && previewNode() && !bridgeRoot()) renderResult();
  else if (busy && previewNode() && !bridgeRoot()) renderLoading();
});
observer.observe(document.documentElement, { childList: true, subtree: true });
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", renameSpotLabels, { once: true });
else renameSpotLabels();
