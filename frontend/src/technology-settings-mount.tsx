import React from "react";
import { createRoot, type Root } from "react-dom/client";
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

function mount(): void {
  const grid = settingsGrid();
  if (!grid) {
    const stale = document.getElementById(HOST_ID);
    if (stale && !document.body.contains(stale)) root = null;
    return;
  }

  let host = document.getElementById(HOST_ID);
  if (!host) {
    host = document.createElement("section");
    host.id = HOST_ID;
    host.className = "technology-settings-host";
    grid.insertAdjacentElement("afterend", host);
    root = createRoot(host);
  }

  const hass = currentHass();
  root?.render(<><TechnologySettings hass={hass} /><SiteCapacitySettings hass={hass} /></>);
}

const observer = new MutationObserver(mount);
observer.observe(document.documentElement, { childList: true, subtree: true });
window.addEventListener("frakon-energy-hass-updated", mount);
window.addEventListener("load", mount);
mount();
