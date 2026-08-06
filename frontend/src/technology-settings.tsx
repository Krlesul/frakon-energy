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
  confirmed_entity_id?: string | null;
  recommended?: Candidate | null;
  candidates?: Candidate[];
  requires_confirmation?: boolean;
};

type TechnologySuggestion = {
  technology: string;
  label?: string;
  enabled?: boolean;
  configured?: number;
  required?: number;
  roles?: RoleSuggestion[];
};

type DiscoverySnapshot = {
  technologies?: TechnologySuggestion[];
  scanned_entities?: number;
  usable_entities?: number;
};

type ConfigEntry = { entry_id: string; domain?: string; title?: string };
type WsConnection = {
  sendMessagePromise?: <T>(message: Record<string, unknown>) => Promise<T>;
};

async function callWs<T>(hass: HomeAssistant, message: Record<string, unknown>): Promise<T> {
  const connection = hass.connection as WsConnection | undefined;
  if (!connection?.sendMessagePromise) throw new Error("WebSocket Home Assistantu není dostupný.");
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

export function TechnologySettings({ hass }: { hass?: HomeAssistant }) {
  const [entry, setEntry] = useState<ConfigEntry | null>(null);
  const [snapshot, setSnapshot] = useState<DiscoverySnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (rescan = false) => {
    if (!hass) return;
    setBusy(true);
    setError(null);
    try {
      const activeEntry = entry ?? await findEntry(hass);
      if (!activeEntry) throw new Error("Nebyla nalezena položka integrace FRAKON Energy.");
      setEntry(activeEntry);
      const result = await callWs<DiscoverySnapshot>(hass, {
        type: rescan ? "frakon_energy/entity_discovery/rescan" : "frakon_energy/entity_discovery/get",
        entry_id: activeEntry.entry_id,
      });
      setSnapshot(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Načtení technologií se nezdařilo.");
    } finally {
      setBusy(false);
    }
  }, [entry, hass]);

  useEffect(() => { void load(false); }, [load]);

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
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Uložení mapování se nezdařilo.");
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
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Odebrání mapování se nezdařilo.");
    } finally {
      setBusy(false);
    }
  };

  return <article className="chart-card technology-settings">
    <div className="technology-settings__header">
      <div><span className="eyebrow">Technologie domu</span><h2>Existující zařízení a entity</h2></div>
      <button className="secondary-action" disabled={busy || !hass} onClick={() => void load(true)}>{busy ? "Pracuji…" : "Znovu vyhledat"}</button>
    </div>
    <p className="settings-copy">FRAKON nejprve použije entity, které už v Home Assistantu existují. Nové entity vytvoří jen pro vlastní výpočty.</p>
    {snapshot ? <div className="discovery-summary"><span>Prohledáno <b>{snapshot.scanned_entities ?? 0}</b></span><span>Použitelných <b>{snapshot.usable_entities ?? 0}</b></span></div> : null}
    {error ? <div className="settings-error">{error}</div> : null}
    {!hass ? <p className="missing-reason">Čekám na připojení k Home Assistantu.</p> : null}
    {hass && !snapshot && !error ? <p className="missing-reason">Načítám technologie a doporučené entity…</p> : null}
    <div className="technology-list">{technologies.map((technology) => <section className="technology-item" key={technology.technology}>
      <div className="technology-item__title"><div><h3>{technology.label ?? technology.technology}</h3><small>{technology.configured ?? 0} z {technology.required ?? technology.roles?.length ?? 0} položek nastaveno</small></div><span className={technology.enabled ? "technology-state enabled" : "technology-state"}>{technology.enabled ? "Zapnuto" : "Vypnuto"}</span></div>
      <div className="role-list">{(technology.roles ?? []).map((role) => {
        const recommended = role.recommended ?? role.candidates?.[0] ?? null;
        const selected = role.confirmed_entity_id ?? recommended?.entity_id ?? "";
        const match = confidence(recommended);
        return <div className="role-row" key={role.role}>
          <div className="role-row__label"><b>{role.label ?? role.role}</b>{recommended ? <small>{recommended.device_name || recommended.name || recommended.integration || recommended.entity_id}{match !== null ? ` · shoda ${match} %` : ""}</small> : <small>Nebyla nalezena vhodná entita</small>}</div>
          <select value={selected} onChange={(event) => void save(technology.technology, role.role, event.target.value)} disabled={busy}>
            <option value="">Vyberte entitu</option>
            {(role.candidates ?? (recommended ? [recommended] : [])).map((candidate) => <option key={candidate.entity_id} value={candidate.entity_id}>{candidate.entity_id}</option>)}
          </select>
          <div className="role-actions">{recommended && !role.confirmed_entity_id ? <button disabled={busy} onClick={() => void save(technology.technology, role.role, recommended.entity_id)}>Použít doporučené</button> : null}{role.confirmed_entity_id ? <button disabled={busy} onClick={() => void remove(technology.technology, role.role)}>Odebrat</button> : null}</div>
        </div>;
      })}</div>
    </section>)}</div>
  </article>;
}
