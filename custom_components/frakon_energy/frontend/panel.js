class FrakonEnergyPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = undefined;
    this._iframe = undefined;
  }

  set hass(value) {
    this._hass = value;
    this._syncHass();
  }

  get hass() {
    return this._hass;
  }

  connectedCallback() {
    if (this._iframe) return;

    this.style.display = "block";
    this.style.width = "100%";
    this.style.height = "100%";
    this.style.minHeight = "100vh";
    this.style.background = "#071019";

    const iframe = document.createElement("iframe");
    iframe.src = "/frakon-energy-app-static/index.html";
    iframe.title = "FRAKON Energy";
    iframe.style.display = "block";
    iframe.style.width = "100%";
    iframe.style.height = "100%";
    iframe.style.minHeight = "100vh";
    iframe.style.border = "0";
    iframe.style.background = "#071019";
    iframe.setAttribute("allow", "clipboard-read; clipboard-write");
    iframe.addEventListener("load", () => this._syncHass());

    this._iframe = iframe;
    this.replaceChildren(iframe);
  }

  disconnectedCallback() {
    this._iframe = undefined;
  }

  _syncHass() {
    if (!this._iframe?.contentWindow || !this._hass) return;
    try {
      this._iframe.contentWindow.__FRAKON_ENERGY_HASS__ = this._hass;
      this._iframe.contentWindow.dispatchEvent(new CustomEvent("frakon-energy-hass-updated"));
    } catch (error) {
      console.warn("FRAKON Energy: Home Assistant state bridge is not ready", error);
    }
  }
}

if (!customElements.get("frakon-energy-panel")) {
  customElements.define("frakon-energy-panel", FrakonEnergyPanel);
}
