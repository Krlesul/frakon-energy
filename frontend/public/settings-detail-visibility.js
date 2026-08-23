const DISPLAY_CHANGED_EVENT = "frakon-energy-dashboard-display-changed";
const HASS_UPDATED_EVENT = "frakon-energy-hass-updated";
const PROFILE_CHANGED_EVENT = "frakon-energy-technology-profile-changed";

let scheduled = false;

function isDisabled(name) {
  return document.documentElement.classList.contains(`frakon-hide-${name}`);
}

function setHidden(element, hidden) {
  if (!element || element.hidden === hidden) return;
  element.hidden = hidden;
}

function normalizeLabel(value) {
  return String(value ?? "")
    .toLocaleLowerCase("cs-CZ")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function isPhotovoltaicsItem(element) {
  const title = normalizeLabel(element.querySelector("h3")?.textContent);
  return title.includes("fotovolta") || /(^| )fve( |$)/.test(title) || title.includes("photovolta");
}

function settingsGrid() {
  return Array.from(document.querySelectorAll(".settings-grid"))
    .find((grid) => grid.querySelector(".dashboard-display-settings")) ?? null;
}

function applyCoreSettingsVisibility(grid) {
  const displayCard = Array.from(grid.children)
    .find((element) => element.classList.contains("dashboard-display-settings"));
  const billingCard = displayCard?.nextElementSibling;
  const hideBilling = isDisabled("billing-estimate")
    && isDisabled("daily-consumption")
    && isDisabled("monthly-consumption");

  if (billingCard?.classList.contains("chart-card")) {
    setHidden(billingCard, hideBilling);
  }

  const spotStack = Array.from(grid.children)
    .find((element) => element.classList.contains("settings-stack--full"));
  setHidden(spotStack, isDisabled("spot-prices"));
}

function applyTechnologySettingsVisibility() {
  const host = document.getElementById("frakon-technology-settings-host");
  if (!host) return;

  const showTechnology = !isDisabled("technology-overview");
  const showPhotovoltaics = !isDisabled("photovoltaics");
  const showEnergyFlow = !isDisabled("energy-flow");
  const showAnyTechnologyDetail = showTechnology || showPhotovoltaics || showEnergyFlow;

  setHidden(host, !showAnyTechnologyDetail);
  if (!showAnyTechnologyDetail) return;

  const cards = Array.from(host.children);
  const technologyCard = cards.find((element) =>
    element.classList.contains("technology-settings")
      && !element.classList.contains("site-capacity-settings")
  );

  if (technologyCard) {
    setHidden(technologyCard, false);

    const topology = Array.from(technologyCard.children)
      .find((element) => element.classList.contains("technology-item"));
    setHidden(topology, !showEnergyFlow);

    const technologyList = Array.from(technologyCard.children)
      .find((element) => element.classList.contains("technology-list"));
    if (technologyList) {
      for (const item of Array.from(technologyList.children)) {
        if (!item.classList.contains("technology-item")) continue;
        const visible = isPhotovoltaicsItem(item)
          ? showPhotovoltaics
          : showTechnology || showEnergyFlow;
        setHidden(item, !visible);
      }
    }
  }

  for (const card of host.querySelectorAll(".site-capacity-settings")) {
    setHidden(card, !showEnergyFlow);
  }
}

function applyVisibility() {
  scheduled = false;
  const grid = settingsGrid();
  if (!grid) return;
  applyCoreSettingsVisibility(grid);
  applyTechnologySettingsVisibility();
}

function scheduleVisibility() {
  if (scheduled) return;
  scheduled = true;
  window.requestAnimationFrame(applyVisibility);
}

const observer = new MutationObserver(scheduleVisibility);
observer.observe(document.documentElement, { childList: true, subtree: true });

window.addEventListener(DISPLAY_CHANGED_EVENT, scheduleVisibility);
window.addEventListener(HASS_UPDATED_EVENT, scheduleVisibility);
window.addEventListener(PROFILE_CHANGED_EVENT, scheduleVisibility);
window.addEventListener("load", scheduleVisibility);

scheduleVisibility();
