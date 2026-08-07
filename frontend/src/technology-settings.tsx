import React, { useCallback, useEffect, useMemo, useState } from "react";
import type { HomeAssistant } from "./home-assistant";

type Candidate = {
  entity_id: string;
  name?: string;
  device_name?: string;
  integration?: string;
  score?: number;
  confidence?: number;
};

type RoleSuggestion = {
  role: string;
  label?: string;
  selected_entity_id?: string | null;
  confirmed_entity_id?: string | null;
  configured?: boolean;
  recommended?: Candidate | null;
  candidates?: Candidate[];
  requires_confirmation?: boolean;
};

type TechnologySuggestion = {
  technology: string;
  label?: string;
  enabled?: boolean;
  configured?: number;
  configured_roles?: number;
  required?: number;
  total_roles?: number;
  roles?: RoleSuggestion[];
};

type DiscoverySnapshot = {
  technologies?: TechnologySuggestion[];
  scanned_entities?: number;
  usable_entities?: number;
};

type BatteryPowerSign = "unknown" | "positive_is_charge" | "positive_is_discharge";
type GridMeterScope = "unknown" | "whole_house" | "inverter_branch";
type PvPowerScope = "unknown" | "gross_generation" | "inverter_net";
type EvWallboxRelation = "unknown" | "same_flow" | "separate";
type EnergyFlowSettings = {
  battery_power_sign: BatteryPowerSign;
  grid_meter_scope: GridMeterScope;
  pv_power_scope: PvPowerScope;
  ev_wallbox_relation: EvWallboxRelation;
};
type ConfigEntry = { entry_id: string; domain?: string; title?: string };
type WsConnection = {
  sendMessagePromise?: <T>(message: Record<string, unknown>) => Promise<T>;
};

const PROFILE_CHANGED_EVENT = "frakon-energy-technology-profile-changed";
const DEFAULT_FLOW_SETTINGS: EnergyFlowSettings = {
  battery_power_sign: "unknown",
  grid_meter_scope: "unknown",
  pv_power_scope: "unknown",
  ev_wallbox_relation: "unknown",
};

function announceProfileChanged(): void {
  window.dispatchEvent(new CustomEvent(PROFILE_CHANGED_EVENT));
}

async function callWs<T>(hass: HomeAssistant, message: Record<string, unknown>): Promise<T> {
  const connection = hass.connection as WsConnection | undefined;
  if (!connection?.sendMessagePromise) {
    throw new Error("WebSocket Home Assistantu není dostupný.");
  }
  return connection.sendMessagePromise<T>(message);
}

async function findEntry(hass: HomeAssistant): Promise<ConfigEntry | null> {
  const entries = await callWs<ConfigEntry[]>(hass, { type: "config_entries/get" });
  return entries.find((entry) => entry.domain === "frakon_energy") ?? null;
}

function confidence(candidate?: Candidate | null): number | null {
  const value = candidate?.confidence ?? candidate?.score;
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value <= 1 ? Math.round(value * 100) : Math.round(value);
}

function errorMessage(reason: unknown, fallback: string): string {
  if (reason instanceof Error && reason.message) return reason.message;
  if (typeof reason === "object" && reason !== null && "message" in reason) {
    const message = String((reason as { message?: unknown }).message ?? "");
    if (message) return message;
  }
  return fallback;
}

export function TechnologySettings({ hass }: { hass?: HomeAssistant }) {
  const [entry, setEntry] = useState<ConfigEntry | null>(null);
  const [snapshot, setSnapshot] = useState<DiscoverySnapshot | null>(null);
  const [flowSettings, setFlowSettings] = useState<EnergyFlowSettings>(DEFAULT_FLOW_SETTINGS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (rescan = false) => {
    if (!hass) return;
    setBusy(true);
    setError(null);
    try {
      const activeEntry = await findEntry(hass);
      if (!activeEntry) {
        throw new Error("Nebyla nalezena položka integrace FRAKON Energy.");
      }
      setEntry(activeEntry);
      const [result, settings] = await Promise.all([
        callWs<DiscoverySnapshot>(hass, {
          type: rescan
            ? "frakon_energy/entity_discovery/rescan"
            : "frakon_energy/entity_discovery/get",
          entry_id: activeEntry.entry_id,
        }),
        callWs<EnergyFlowSettings>(hass, {
          type: "frakon_energy/energy_flow/get",
          entry_id: activeEntry.entry_id,
        }),
      ]);
      setSnapshot(result);
      setFlowSettings({ ...DEFAULT_FLOW_SETTINGS, ...settings });
      if (rescan) announceProfileChanged();
    } catch (reason) {
      setError(errorMessage(reason, "Načtení technologií se nezdařilo."));
    } finally {
      setBusy(false);
    }
  }, [hass]);

  useEffect(() => {
    void load(false);
  }, [load]);

  const technologies = useMemo(() => snapshot?.technologies ?? [], [snapshot]);

  const save = async (technology: string, role: string, entityId: string) => {
    if (!hass || !entry || !entityId) return;
    setBusy(true);
    setError(null);
    try {
      const result = await callWs<DiscoverySnapshot>(hass, {
        type: "frakon_energy/entity_discovery/save",
        entry_id: entry.entry_id,
        technology,
        role,
        entity_id: entityId,
      });
      setSnapshot(result);
      announceProfileChanged();
    } catch (reason) {
      setError(errorMessage(reason, "Uložení mapování se nezdařilo."));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (technology: string, role: string) => {
    if (!hass || !entry) return;
    setBusy(true);
    setError(null);
    try {
      const result = await callWs<DiscoverySnapshot>(hass, {
        type: "frakon_energy/entity_discovery/remove",
        entry_id: entry.entry_id,
        technology,
        role,
      });
      setSnapshot(result);
      announceProfileChanged();
    } catch (reason) {
      setError(errorMessage(reason, "Odebrání mapování se nezdařilo."));
    } finally {
      setBusy(false);
    }
  };

  const setTechnologyEnabled = async (technology: string, enabled: boolean) => {
    if (!hass || !entry) return;
    setBusy(true);
    setError(null);
    try {
      await callWs(hass, {
        type: "frakon_energy/technology/set_enabled",
        entry_id: entry.entry_id,
        technology,
        enabled,
      });
      setSnapshot((current) => current ? {
        ...current,
        technologies: (current.technologies ?? []).map((item) =>
          item.technology === technology ? { ...item, enabled } : item
        ),
      } : current);
      await load(false);
      announceProfileChanged();
    } catch (reason) {
      setError(errorMessage(reason, "Změnu technologie se nepodařilo uložit."));
    } finally {
      setBusy(false);
    }
  };

  const setFlowSetting = async <K extends keyof EnergyFlowSettings>(key: K, value: EnergyFlowSettings[K]) => {
    if (!hass || !entry) return;
    setBusy(true);
    setError(null);
    try {
      const settings = await callWs<EnergyFlowSettings>(hass, {
        type: "frakon_energy/energy_flow/set",
        entry_id: entry.entry_id,
        [key]: value,
      });
      setFlowSettings({ ...DEFAULT_FLOW_SETTINGS, ...settings });
      announceProfileChanged();
    } catch (reason) {
      setError(errorMessage(reason, "Nastavení energetického toku se nepodařilo uložit."));
    } finally {
      setBusy(false);
    }
  };

  return <article className="chart-card technology-settings">
    <div className="technology-settings__header">
      <div><span className="eyebrow">Technologie domu</span><h2>Existující zařízení a entity</h2></div>
      <button className="secondary-action" disabled={busy || !hass} onClick={() => void load(true)}>
        {busy ? "Pracuji…" : "Znovu vyhledat"}
      </button>
    </div>
    <p className="settings-copy">FRAKON nejprve použije entity, které už v Home Assistantu existují. Nové entity vytvoří jen pro vlastní výpočty.</p>
    {snapshot ? <div className="discovery-summary"><span>Prohledáno <b>{snapshot.scanned_entities ?? 0}</b></span><span>Použitelných <b>{snapshot.usable_entities ?? 0}</b></span></div> : null}
    {error ? <div className="settings-error">{error}</div> : null}
    {!hass ? <p className="missing-reason">Čekám na připojení k Home Assistantu.</p> : null}
    {hass && !snapshot && !error ? <p className="missing-reason">Načítám technologie a doporučené entity…</p> : null}

    {snapshot ? <section className="technology-item">
      <div className="technology-item__title">
        <div><h3>Topologie energetických měření</h3><small>Určuje, která měření lze bezpečně sčítat a která by se započítala dvakrát.</small></div>
      </div>
      <div className="role-list">
        <div className="role-row">
          <div className="role-row__label"><b>Elektroměr sítě</b><small>Kde je měřen odběr a přetok vůči distribuční síti.</small></div>
          <select value={flowSettings.grid_meter_scope} disabled={busy} onChange={(event) => void setFlowSetting("grid_meter_scope", event.target.value as GridMeterScope)}>
            <option value="unknown">Nevím / určit později</option>
            <option value="whole_house">Hlavní měření celého domu</option>
            <option value="inverter_branch">Jen větev FVE / měniče</option>
          </select>
          <div className="role-actions" />
        </div>
        <div className="role-row">
          <div className="role-row__label"><b>Výkon FVE</b><small>Pomůže určit, zda jde o výrobu panelů nebo už čistý tok měniče.</small></div>
          <select value={flowSettings.pv_power_scope} disabled={busy} onChange={(event) => void setFlowSetting("pv_power_scope", event.target.value as PvPowerScope)}>
            <option value="unknown">Nevím / určit později</option>
            <option value="gross_generation">Hrubá AC výroba FVE</option>
            <option value="inverter_net">Čistý výkon měniče po baterii</option>
          </select>
          <div className="role-actions" />
        </div>
        <div className="role-row">
          <div className="role-row__label"><b>Elektromobil a wallbox</b><small>Zabraňuje dvojímu započítání stejného nabíjecího výkonu.</small></div>
          <select value={flowSettings.ev_wallbox_relation} disabled={busy} onChange={(event) => void setFlowSetting("ev_wallbox_relation", event.target.value as EvWallboxRelation)}>
            <option value="unknown">Nevím / určit později</option>
            <option value="same_flow">Měří stejný nabíjecí tok</option>
            <option value="separate">Jsou to oddělená měření</option>
          </select>
          <div className="role-actions" />
        </div>
      </div>
    </section> : null}

    <div className="technology-list">{technologies.map((technology) => {
      const configured = technology.configured_roles ?? technology.configured ?? 0;
      const required = technology.total_roles ?? technology.required ?? technology.roles?.length ?? 0;
      const enabled = Boolean(technology.enabled);
      return <section className="technology-item" key={technology.technology}>
        <div className="technology-item__title">
          <div><h3>{technology.label ?? technology.technology}</h3><small>{configured} z {required} položek nastaveno</small></div>
          <label className="technology-toggle">
            <input
              type="checkbox"
              checked={enabled}
              disabled={busy}
              onChange={(event) => void setTechnologyEnabled(technology.technology, event.target.checked)}
            />
            <span>{enabled ? "Zapnuto" : "Vypnuto"}</span>
          </label>
        </div>
        {enabled && technology.technology === "home_battery" ? <div className="role-row">
          <div className="role-row__label"><b>Směr výkonu baterie</b><small>Určuje, co znamená kladná hodnota výkonové entity.</small></div>
          <select value={flowSettings.battery_power_sign} disabled={busy} onChange={(event) => void setFlowSetting("battery_power_sign", event.target.value as BatteryPowerSign)}>
            <option value="unknown">Nevím / určit později</option>
            <option value="positive_is_charge">Kladná hodnota = nabíjení</option>
            <option value="positive_is_discharge">Kladná hodnota = vybíjení</option>
          </select>
          <div className="role-actions" />
        </div> : null}
        {enabled ? <div className="role-list">{(technology.roles ?? []).map((role) => {
          const recommended = role.recommended ?? role.candidates?.[0] ?? null;
          const confirmed = role.selected_entity_id ?? role.confirmed_entity_id ?? null;
          const selected = confirmed ?? recommended?.entity_id ?? "";
          const match = confidence(recommended);
          return <div className="role-row" key={role.role}>
            <div className="role-row__label"><b>{role.label ?? role.role}</b>{recommended ? <small>{recommended.device_name || recommended.name || recommended.integration || recommended.entity_id}{match !== null ? ` · shoda ${match} %` : ""}</small> : <small>Nebyla nalezena vhodná entita</small>}</div>
            <select value={selected} onChange={(event) => void save(technology.technology, role.role, event.target.value)} disabled={busy}>
              <option value="">Vyberte entitu</option>
              {(role.candidates ?? (recommended ? [recommended] : [])).map((candidate) => <option key={candidate.entity_id} value={candidate.entity_id}>{candidate.entity_id}</option>)}
            </select>
            <div className="role-actions">
              {recommended && !confirmed ? <button disabled={busy} onClick={() => void save(technology.technology, role.role, recommended.entity_id)}>Použít doporučené</button> : null}
              {confirmed ? <button disabled={busy} onClick={() => void remove(technology.technology, role.role)}>Odebrat</button> : null}
            </div>
          </div>;
        })}</div> : <p className="missing-reason">Technologie je vypnutá. Přiřazené entity zůstávají uložené.</p>}
      </section>;
    })}</div>
  </article>;
}
