const HASS_UPDATED_EVENT = "frakon-energy-hass-updated";
const DISPLAY_CHANGED_EVENT = "frakon-energy-dashboard-display-changed";
const REQUEST_TIMEOUT_MS = 12000;

let scheduled = false;
let cachedDay = null;
let cachedSnapshot = null;
let cachedEntryId = null;
let pending = null;

function currentHass() {
  return window.__FRAKON_ENERGY_HASS__ ?? window.hass ?? null;
}

function localIsoDay(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function callWs(hass, message) {
  const promise = hass?.callWS
    ? hass.callWS(message)
    : hass?.connection?.sendMessagePromise
      ? hass.connection.sendMessagePromise(message)
      : Promise.reject(new Error("WebSocket Home Assistantu není dostupný."));

  let timer;
  const timeout = new Promise((_, reject) => {
    timer = window.setTimeout(
      () => reject(new Error("Vypršel čas pro načtení potvrzené all-in ceny.")),
      REQUEST_TIMEOUT_MS,
    );
  });

  return Promise.race([promise, timeout]).finally(() => window.clearTimeout(timer));
}

async function primaryEntryId(hass) {
  if (cachedEntryId) return cachedEntryId;
  const primary = await callWs(hass, { type: "frakon_energy/entry/primary" });
  if (!primary?.loaded || !primary.entry_id) {
    throw new Error("Primární FRAKON Energy entry není dostupná.");
  }
  cachedEntryId = String(primary.entry_id);
  return cachedEntryId;
}

async function loadSnapshot() {
  const hass = currentHass();
  if (!hass) throw new Error("Home Assistant není dostupný.");
  const day = localIsoDay();
  if (cachedSnapshot && cachedDay === day) return cachedSnapshot;
  if (pending) return pending;

  pending = (async () => {
    const entryId = await primaryEntryId(hass);
    const snapshot = await callWs(hass, {
      type: "frakon_energy/tariff/diagnostics",
      entry_id: entryId,
      day,
    });
    if (!snapshot || snapshot.read_only !== true) {
      throw new Error("Potvrzená all-in diagnostika není dostupná.");
    }
    cachedDay = day;
    cachedSnapshot = snapshot;
    return snapshot;
  })().finally(() => {
    pending = null;
  });

  return pending;
}

function confirmedPrice(snapshot, tariff) {
  const raw = tariff === "NT"
    ? snapshot?.all_in_nt_czk_kwh
    : tariff === "VT"
      ? snapshot?.all_in_vt_czk_kwh
      : null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function formatPrice(value) {
  return `${value.toLocaleString("cs-CZ", {
    minimumFractionDigits: 3,
    maximumFractionDigits: 3,
  })} Kč/kWh`;
}

function tariffCards() {
  return Array.from(document.querySelectorAll(".tariff-card"));
}

function applySnapshot(snapshot) {
  for (const card of tariffCards()) {
    const tariff = card.querySelector("h2")?.textContent?.trim().toUpperCase() ?? "?";
    const target = card.querySelector(".current-price b");
    if (!target) continue;

    const price = confirmedPrice(snapshot, tariff);
    const nextText = price === null
      ? tariff === "NT" || tariff === "VT"
        ? "Chybí potvrzená all-in cena"
        : "Čekám na tarif HDO"
      : formatPrice(price);

    if (target.textContent !== nextText) target.textContent = nextText;
    target.dataset.priceAuthority = price === null ? "unavailable" : "confirmed_all_in";
    if (snapshot?.all_in_tariff_fingerprint) {
      target.title = `Potvrzený all-in · ${snapshot.all_in_tariff_fingerprint}`;
    }
  }
}

function applyUnavailable() {
  for (const card of tariffCards()) {
    const target = card.querySelector(".current-price b");
    if (!target) continue;
    if (target.textContent !== "Chybí potvrzená all-in cena") {
      target.textContent = "Chybí potvrzená all-in cena";
    }
    target.dataset.priceAuthority = "unavailable";
  }
}

async function reconcile() {
  scheduled = false;
  if (!tariffCards().length) return;
  try {
    applySnapshot(await loadSnapshot());
  } catch {
    applyUnavailable();
  }
}

function scheduleReconcile() {
  if (scheduled) return;
  scheduled = true;
  window.requestAnimationFrame(() => void reconcile());
}

const observer = new MutationObserver(scheduleReconcile);
observer.observe(document.documentElement, { childList: true, subtree: true });

window.addEventListener(HASS_UPDATED_EVENT, scheduleReconcile);
window.addEventListener(DISPLAY_CHANGED_EVENT, scheduleReconcile);
window.addEventListener("load", scheduleReconcile);
window.setInterval(() => {
  if (cachedDay !== localIsoDay()) {
    cachedDay = null;
    cachedSnapshot = null;
  }
  scheduleReconcile();
}, 60000);

scheduleReconcile();
