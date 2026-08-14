import React, { useEffect, useMemo, useState } from "react";
import { callHomeAssistantWs, findFrakonEnergyEntryId, type HomeAssistant } from "./home-assistant";
import "./tariff-wizard.css";

type TariffProduct = {
  supplier: string;
  product_name: string;
  contract_kind: "fixed" | "indefinite";
  source_resolution: "static_catalog" | "dynamic_resolver";
  requires_document_resolver: boolean;
  price_scope: "supplier_commercial";
};

type TariffSupplierGroup = {
  supplier: string;
  products: TariffProduct[];
};

type TariffCatalogResponse = {
  entry_id: string;
  suppliers: TariffSupplierGroup[];
  price_scope: string;
  download_performed: boolean;
  parsing_performed: boolean;
  persistence_performed: boolean;
  activation_performed: boolean;
};

type TariffCandidate = {
  fingerprint: string;
  supplier: string;
  product_name: string;
  source_url: string;
  valid_from: string;
  valid_to: string | null;
  match_score: number;
  match_reasons: string[];
  price_scope: string;
  document_sha256: string | null;
  document_date: string | null;
  download_performed: boolean;
  parsing_performed: boolean;
  persistence_performed: boolean;
  activation_performed: boolean;
};

type TariffDiscoveryResponse = {
  entry_id: string;
  contract_fingerprint: string;
  day: string;
  supported_suppliers: string[];
  candidates: TariffCandidate[];
  download_performed: boolean;
  parsing_performed: boolean;
  persistence_performed: boolean;
  activation_performed: boolean;
};

type WizardDraft = {
  supplier: string;
  productName: string;
  distributor: string;
  distributionTariff: string;
  breakerPhases: number;
  breakerAmperes: number;
  validFrom: string;
  fixationEnd: string;
  discoveryDay: string;
};

const SUPPLIER_LABELS: Record<string, string> = {
  cez: "ČEZ",
  eon: "E.ON",
  pre: "PRE",
  mnd: "MND",
};

const DISTRIBUTORS = [
  { value: "cez_distribuce", label: "ČEZ Distribuce" },
  { value: "eg_d", label: "EG.D" },
  { value: "pre_distribuce", label: "PREdistribuce" },
] as const;

const DISTRIBUTION_TARIFFS = ["D01d", "D02d", "D25d", "D26d", "D27d", "D35d", "D45d", "D56d", "D57d", "D61d"];
const BREAKER_AMPERES = [10, 16, 20, 25, 32, 40, 50, 63];

function localIsoDay(date = new Date()): string {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

const DEFAULT_DRAFT: WizardDraft = {
  supplier: "cez",
  productName: "",
  distributor: "cez_distribuce",
  distributionTariff: "D25d",
  breakerPhases: 3,
  breakerAmperes: 25,
  validFrom: localIsoDay(),
  fixationEnd: "",
  discoveryDay: localIsoDay(),
};

function readableError(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  if (error && typeof error === "object") {
    const candidate = error as { message?: unknown; error?: unknown };
    if (typeof candidate.message === "string") return candidate.message;
    if (typeof candidate.error === "string") return candidate.error;
  }
  return "Nepodařilo se dokončit požadavek.";
}

function contractPayload(draft: WizardDraft, product: TariffProduct): Record<string, unknown> {
  const fixed = product.contract_kind === "fixed";
  return {
    schema_version: 1,
    supplier: draft.supplier,
    distributor: draft.distributor,
    product_name: product.product_name,
    contract_kind: product.contract_kind,
    distribution_tariff: draft.distributionTariff,
    breaker: {
      phases: draft.breakerPhases,
      amperes: draft.breakerAmperes,
    },
    valid_from: draft.validFrom,
    valid_to: null,
    fixation_end: fixed ? draft.fixationEnd : null,
    customer_confirmed: false,
  };
}

function SourceStatus({ product }: { product: TariffProduct }) {
  if (product.requires_document_resolver) {
    return <span className="tariff-wizard__status pending">Zdroj podle PSČ · resolver se doplňuje</span>;
  }
  return <span className="tariff-wizard__status verified">Ověřený katalog dodavatele</span>;
}

function CandidateCard({ candidate }: { candidate: TariffCandidate }) {
  const validity = candidate.valid_to ? `${candidate.valid_from} → ${candidate.valid_to}` : `od ${candidate.valid_from}`;
  return (
    <article className="tariff-candidate">
      <div className="tariff-candidate__head">
        <div>
          <span className="eyebrow">Ověřený zdroj</span>
          <h4>{candidate.product_name}</h4>
        </div>
        <span className="tariff-candidate__score">{candidate.match_score}/100</span>
      </div>
      <div className="tariff-candidate__meta">
        <span>{SUPPLIER_LABELS[candidate.supplier] ?? candidate.supplier}</span>
        <span>{validity}</span>
        <span>{candidate.price_scope === "supplier_commercial" ? "Obchodní část" : candidate.price_scope}</span>
      </div>
      <ul>
        {candidate.match_reasons.map((reason) => <li key={reason}>{reason}</li>)}
      </ul>
      <div className="tariff-candidate__actions">
        <a href={candidate.source_url} target="_blank" rel="noreferrer">Otevřít oficiální ceník</a>
        <span>Fingerprint {candidate.fingerprint.slice(0, 12)}…</span>
      </div>
      <div className="tariff-candidate__safety">Jen k revizi · nic nebylo staženo, uloženo ani aktivováno.</div>
    </article>
  );
}

export function TariffSetupWizard({ hass }: { hass?: HomeAssistant }) {
  const [entryId, setEntryId] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<TariffCatalogResponse | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [draft, setDraft] = useState<WizardDraft>(DEFAULT_DRAFT);
  const [discovery, setDiscovery] = useState<TariffDiscoveryResponse | null>(null);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const [discovering, setDiscovering] = useState(false);

  useEffect(() => {
    let active = true;
    setEntryId(null);
    setCatalog(null);
    setCatalogError(null);
    if (!hass) return () => { active = false; };

    setLoadingCatalog(true);
    findFrakonEnergyEntryId(hass)
      .then(async (id) => {
        if (!active) return;
        if (!id) throw new Error("Integrace FRAKON Energy nebyla nalezena.");
        setEntryId(id);
        const response = await callHomeAssistantWs<TariffCatalogResponse>(hass, {
          type: "frakon_energy/tariff/catalog",
          entry_id: id,
        });
        if (!active) return;
        setCatalog(response);
        const firstGroup = response.suppliers[0];
        const firstProduct = firstGroup?.products[0];
        if (firstGroup && firstProduct) {
          setDraft((current) => ({ ...current, supplier: firstGroup.supplier, productName: firstProduct.product_name }));
        }
      })
      .catch((error) => { if (active) setCatalogError(readableError(error)); })
      .finally(() => { if (active) setLoadingCatalog(false); });

    return () => { active = false; };
  }, [hass]);

  const supplierGroup = useMemo(
    () => catalog?.suppliers.find((group) => group.supplier === draft.supplier) ?? null,
    [catalog, draft.supplier],
  );
  const products = supplierGroup?.products ?? [];
  const selectedProduct = products.find((product) => product.product_name === draft.productName) ?? products[0] ?? null;
  const fixed = selectedProduct?.contract_kind === "fixed";
  const mndBlocked = selectedProduct?.requires_document_resolver === true;
  const invalidDates = !draft.validFrom || !draft.discoveryDay || draft.discoveryDay < draft.validFrom || (fixed && (!draft.fixationEnd || draft.fixationEnd < draft.validFrom));

  const updateSupplier = (supplier: string) => {
    const nextGroup = catalog?.suppliers.find((group) => group.supplier === supplier);
    setDraft((current) => ({ ...current, supplier, productName: nextGroup?.products[0]?.product_name ?? "" }));
    setDiscovery(null);
    setDiscoveryError(null);
  };

  const discover = async () => {
    if (!hass || !entryId || !selectedProduct || invalidDates || mndBlocked) return;
    setDiscovering(true);
    setDiscovery(null);
    setDiscoveryError(null);
    try {
      const response = await callHomeAssistantWs<TariffDiscoveryResponse>(hass, {
        type: "frakon_energy/tariff/discover",
        entry_id: entryId,
        contract: contractPayload(draft, selectedProduct),
        day: draft.discoveryDay,
      });
      setDiscovery(response);
      if (response.candidates.length === 0) {
        setDiscoveryError("Pro tuto přesnou kombinaci smlouvy a data nebyl nalezen ověřený ceník. Nic se nepřiřadilo automaticky.");
      }
    } catch (error) {
      setDiscoveryError(readableError(error));
    } finally {
      setDiscovering(false);
    }
  };

  return (
    <section className="tariff-wizard">
      <div className="tariff-wizard__header">
        <div>
          <span className="eyebrow">Nastavení ceny elektřiny</span>
          <h2>Najít přesný oficiální ceník</h2>
          <p>Vyberte údaje ze smlouvy. FRAKON Energy hledá pouze přesnou shodu v ověřených zdrojích a nic bez potvrzení neaktivuje.</p>
        </div>
        <span className="tariff-wizard__safety">Fail-closed</span>
      </div>

      <div className="tariff-wizard__steps" aria-label="Průběh nastavení tarifu">
        <div className="active"><b>1</b><span>Smlouva</span></div>
        <div className={discovery?.candidates.length ? "active" : ""}><b>2</b><span>Ověření zdroje</span></div>
        <div><b>3</b><span>Potvrzení ceny</span></div>
      </div>

      {loadingCatalog ? <div className="tariff-wizard__notice">Načítám ověřený katalog dodavatelů…</div> : null}
      {catalogError ? <div className="tariff-wizard__notice error">{catalogError}</div> : null}

      {catalog ? (
        <>
          <div className="tariff-wizard__form-grid">
            <label>
              <span>Dodavatel</span>
              <select value={draft.supplier} onChange={(event) => updateSupplier(event.target.value)}>
                {catalog.suppliers.map((group) => <option key={group.supplier} value={group.supplier}>{SUPPLIER_LABELS[group.supplier] ?? group.supplier}</option>)}
              </select>
            </label>

            <label className="wide">
              <span>Produkt ze smlouvy</span>
              <select value={selectedProduct?.product_name ?? ""} onChange={(event) => { setDraft((current) => ({ ...current, productName: event.target.value })); setDiscovery(null); setDiscoveryError(null); }}>
                {products.map((product) => <option key={`${product.product_name}:${product.contract_kind}`} value={product.product_name}>{product.product_name}</option>)}
              </select>
              {selectedProduct ? <div className="tariff-wizard__field-meta"><span>{selectedProduct.contract_kind === "fixed" ? "Fixovaná smlouva" : "Na dobu neurčitou"}</span><SourceStatus product={selectedProduct} /></div> : null}
            </label>

            <label>
              <span>Distribuční území</span>
              <select value={draft.distributor} onChange={(event) => setDraft((current) => ({ ...current, distributor: event.target.value }))}>
                {DISTRIBUTORS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>

            <label>
              <span>Distribuční sazba</span>
              <select value={draft.distributionTariff} onChange={(event) => setDraft((current) => ({ ...current, distributionTariff: event.target.value }))}>
                {DISTRIBUTION_TARIFFS.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>

            <label>
              <span>Počet fází</span>
              <select value={draft.breakerPhases} onChange={(event) => setDraft((current) => ({ ...current, breakerPhases: Number(event.target.value) }))}>
                <option value={1}>1 fáze</option>
                <option value={3}>3 fáze</option>
              </select>
            </label>

            <label>
              <span>Hlavní jistič</span>
              <select value={draft.breakerAmperes} onChange={(event) => setDraft((current) => ({ ...current, breakerAmperes: Number(event.target.value) }))}>
                {BREAKER_AMPERES.map((item) => <option key={item} value={item}>{item} A</option>)}
              </select>
            </label>

            <label>
              <span>Smlouva platí od</span>
              <input type="date" value={draft.validFrom} onChange={(event) => setDraft((current) => ({ ...current, validFrom: event.target.value }))} />
            </label>

            {fixed ? (
              <label>
                <span>Fixace do</span>
                <input type="date" min={draft.validFrom} value={draft.fixationEnd} onChange={(event) => setDraft((current) => ({ ...current, fixationEnd: event.target.value }))} />
              </label>
            ) : (
              <div className="tariff-wizard__read-only"><span>Typ smlouvy</span><b>Bez fixace</b><small>Produkt je v ověřeném katalogu vedený na dobu neurčitou.</small></div>
            )}

            <label>
              <span>Ceník ověřit k datu</span>
              <input type="date" min={draft.validFrom} value={draft.discoveryDay} onChange={(event) => setDraft((current) => ({ ...current, discoveryDay: event.target.value }))} />
            </label>
          </div>

          {mndBlocked ? (
            <div className="tariff-wizard__notice pending">
              <b>MND vybírá ceník podle PSČ odběrného místa.</b>
              <span>Bez přesného MND resolveru FRAKON Energy URL ceníku nehádá. Backend je proto záměrně uzavřený, dokud nedoplníme PSČ-aware resolver.</span>
            </div>
          ) : null}

          {invalidDates ? <div className="tariff-wizard__notice error">Zkontrolujte datum začátku smlouvy, datum ověření a u fixovaného produktu také konec fixace.</div> : null}

          <div className="tariff-wizard__action-row">
            <button className="primary-action" disabled={!entryId || !selectedProduct || discovering || invalidDates || mndBlocked} onClick={discover}>
              {discovering ? "Ověřuji oficiální zdroje…" : "Najít ověřený ceník"}
            </button>
            <span>Žádná cena se tímto krokem nemění.</span>
          </div>

          {discoveryError ? <div className="tariff-wizard__notice error">{discoveryError}</div> : null}
          {discovery?.candidates.length ? (
            <div className="tariff-wizard__results">
              <div className="tariff-wizard__results-head">
                <div><span className="eyebrow">Krok 2</span><h3>Nalezené ověřené zdroje</h3></div>
                <span>{discovery.candidates.length} {discovery.candidates.length === 1 ? "shoda" : "shody"}</span>
              </div>
              {discovery.candidates.map((candidate) => <CandidateCard key={candidate.fingerprint} candidate={candidate} />)}
              <div className="tariff-wizard__confirmation-lock">
                <b>Potvrzení ceny ještě není aktivní.</b>
                <span>Další krok dostane vlastní backendový potvrzovací endpoint. Do té doby nelze z této obrazovky změnit aktivní cenu.</span>
              </div>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
