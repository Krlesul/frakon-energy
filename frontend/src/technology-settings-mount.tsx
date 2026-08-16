import React from "react";
import { createRoot, type Root } from "react-dom/client";
import { PhaseSettlementStatus } from "./phase-settlement-status";
import { SiteCapacitySettings } from "./site-capacity-settings";
import { TechnologySettings } from "./technology-settings";
import type { HomeAssistant } from "./home-assistant";
import "./technology-settings.css";

declare global {
  interface Window {
    __FRAKON_ENERGY_HASS__?: HomeAssistant;
    hass?: HomeAssistant;
  }
}

const HOST_ID = "frakon-technology-settings-host";
let root: Root | null = null;

function currentHass(): HomeAssistant | undefined {
  return window.__FRAKON_ENERGY_HASS__ ?? window.hass;
}

function settingsGrid(): HTMLElement | null {
  const grids = Array.from(document.querySelectorAll<HTMLElement>(".settings-grid"));
  return grids.find((grid) => grid.querySelector(".settings-copy")) ?? null;
}

function removeHost(host: HTMLElement): void {
  root?.unmount();
  root = null;
  host.remove();
}

function mount(): void {
  const grid = settingsGrid();
  let host = document.getElementById(HOST_ID);

  if (!grid) {
    if (host) removeHost(host);
    return;
  }

  if (host && host.previousElementSibling !== grid) {
    removeHost(host);
    host = null;
  }

  if (!host) {
    host = document.createElement("section");
    host.id = HOST_ID;
    host.className = "technology-settings-host";
    grid.insertAdjacentElement("afterend", host);
    root = createRoot(host);
  }

  const hass = currentHass();
  root?.render(<><TechnologySettings hass={hass} /><SiteCapacitySettings hass={hass} /><PhaseSettlementStatus hass={hass} /></>);
}

function reconcileStructure(): void {
  const grid = settingsGrid();
  const host = document.getElementById(HOST_ID);
  const mountedCorrectly = Boolean(grid && host && host.previousElementSibling === grid);
  const absentCorrectly = !grid && !host;
  if (mountedCorrectly || absentCorrectly) return;
  mount();
}

const observer = new MutationObserver(reconcileStructure);
observer.observe(document.documentElement, { childList: true, subtree: true });
window.addEventListener("frakon-energy-hass-updated", mount);
window.addEventListener("load", mount);
mount();
