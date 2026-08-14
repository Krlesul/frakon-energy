import React, { useEffect, useMemo, useRef, useState } from "react";
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

type SupplierTariffPreview = {
  supplier: string;
  product_name: string;
  valid_from: string;
  distribution_tariff: string;
  high_rate_czk_per_kwh: string;
  low_rate_czk_per_kwh: string | null;
  supplier_standing_czk_month: string;
  includes_vat: boolean;
  source_url: string;
  document_sha256: string;
  page_count: number;
  parser_name: string;
  extraction_method: string;
  extraction_confidence: number;
  validation_reasons: string[];
  price_scope: "supplier_commercial";
  parsing_performed: boolean;
  persistence_performed: boolean;
  activation_performed: boolean;
};

type TariffParsePreviewResponse = {
  entry_id: string;
  contract_fingerprint: string;
  candidate_fingerprint: string;
  checked_at: string;
  source_url: string;
  document_sha256: string;
  content_bytes: number;
  download_performed: boolean;
  parsing_performed: boolean;
  persistence_performed: boolean;
  activation_performed: boolean;
  preview: SupplierTariffPreview;
};

type AllInVariableComponent = {
  kind: string;
  name: string;
  high_rate_czk_per_kwh: string;
  low_rate_czk_per_kwh: string;
  includes_vat: boolean;
  vat_rate_percent: string;
  gross_vt_czk_per_kwh: string;
  gross_nt_czk_per_kwh: string;
};

type AllInFixedComponent = {
  kind: string;
  name: string;
  monthly_czk: string;
  includes_vat: boolean;
  vat_rate_percent: string;
  gross_monthly_czk: string;
};

type AllInTariffPreview = {
  supplier: string;
  product_name: string;
  distribution_tariff: string;
  breaker_code: string;
  valid_from: string;
  valid_to: string | null;
  all_in_vt_czk_kwh: string;
  all_in_nt_czk_kwh: string;
  fixed_monthly_total_czk: string;
  variable_components: AllInVariableComponent[];
  fixed_components: AllInFixedComponent[];
  supplier_source_url: string;
  supplier_document_sha256: string;
  regulated_source_url: string;
  regulated_checksum: string | null;
  provenance: unknown;
  validation_reasons: string[];
  all_in_ready: boolean;
  persistence_performed: boolean;
  activation_performed: boolean;
};

type CustomerTariffProposalResponse = {
  entry_id: string;
  proposal_fingerprint: string;
  contract_fingerprint: string;
  all_in_tariff_fingerprint: string;
  candidate_fingerprint: string;
  regulated_version_fingerprint: string;
  proposed_for_day: string;
  proposed_at: string;
  checked_at: string;
  source_url: string;
  document_sha256: string;
  content_bytes: number;
  download_performed: boolean;
  parsing_performed: boolean;
  all_in_preview_performed: boolean;
  persistence_performed: boolean;
  confirmation_performed: boolean;
  activation_performed: boolean;
  preview: AllInTariffPreview;
};

type CustomerTariffConfirmResponse = {
  entry_id: string;
  proposal_fingerprint: string;
  contract_fingerprint: string;
  all_in_tariff_fingerprint: string;
  regulated_version_fingerprint: string;
  confirmed: boolean;
  persistence_performed: boolean;
  confirmation_performed: boolean;
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

function CandidateCard({
  candidate,
  previewing,
  previewed,
  onPreview,
}: {
  candidate: TariffCandidate;
  previewing: boolean;
  previewed: boolean;
  onPreview: (candidate: TariffCandidate) => void;
}) {
  const validity = candidate.valid_to ? `${candidate.valid_from} → ${candidate.valid_to}` : `od ${candidate.valid_from}`;
  const parserAvailable = candidate.supplier === "cez";
  return (
    <article className={`tariff-candidate${previewed ? " selected" : ""}`}>
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
        {parserAvailable ? (
          <button type="button" className="tariff-candidate__preview-button" disabled={previewing} onClick={() => onPreview(candidate)}>
            {previewing ? "Stahuji a ověřuji PDF…" : previewed ? "Ceny ověřeny" : "Načíst ověřené ceny"}
          </button>
        ) : (
          <span className="tariff-candidate__parser-pending">Parser cen pro tohoto dodavatele ještě není aktivní.</span>
        )}
      </div>
      <div className="tariff-candidate__safety">
        {previewed
          ? "PDF bylo staženo a parsováno pouze pro náhled · nic nebylo uloženo ani aktivováno."
          : "Discovery nic nestahuje, neukládá ani neaktivuje. PDF se načte až po explicitním výběru."}
      </div>
    </article>
  );
}

function PricePreviewCard({
  response,
  preparingAllIn,
  onPrepareAllIn,
}: {
  response: TariffParsePreviewResponse;
  preparingAllIn: boolean;
  onPrepareAllIn: () => void;
}) {
  const preview = response.preview;
  return (
    <section className="tariff-price-preview">
      <div className="tariff-wizard__results-head">
        <div><span className="eyebrow">Krok 3 · read-only</span><h3>Ověřený návrh obchodní ceny</h3></div>
        <span>{preview.extraction_confidence}/100 exact guards</span>
      </div>

      <div className="tariff-price-preview__grid">
        <div><span>Vysoký tarif</span><b>{preview.high_rate_czk_per_kwh} Kč/kWh</b></div>
        <div><span>Nízký tarif</span><b>{preview.low_rate_czk_per_kwh ? `${preview.low_rate_czk_per_kwh} Kč/kWh` : "—"}</b></div>
        <div><span>Stálý plat dodavatele</span><b>{preview.supplier_standing_czk_month} Kč/měsíc</b></div>
        <div><span>DPH</span><b>{preview.includes_vat ? "Zahrnuta" : "Nezahrnuta"}</b></div>
      </div>

      <div className="tariff-price-preview__meta">
        <span><b>Produkt:</b> {preview.product_name}</span>
        <span><b>Sazba:</b> {preview.distribution_tariff}</span>
        <span><b>Platnost od:</b> {preview.valid_from}</span>
        <span><b>PDF:</b> {preview.page_count} str.</span>
        <span><b>Parser:</b> {preview.parser_name}</span>
        <span><b>SHA-256:</b> <code>{response.document_sha256}</code></span>
      </div>

      <a className="tariff-price-preview__source" href={response.source_url} target="_blank" rel="noreferrer">Otevřít přesný ověřený zdrojový dokument</a>

      <div className="tariff-price-preview__checks">
        <b>Prošlé validační kontroly</b>
        <ul>{preview.validation_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
      </div>

      <div className="tariff-wizard__notice pending">
        <b>Toto ještě není all-in cena.</b>
        <span>Jde pouze o obchodní část dodavatele. Kompletní návrh musí backend znovu spojit s potvrzenou regulatorní verzí pro přesnou sazbu, jistič a datum.</span>
      </div>
      <div className="tariff-wizard__proposal-action">
        <button type="button" className="tariff-wizard__prepare-button" disabled={preparingAllIn} onClick={onPrepareAllIn}>
          {preparingAllIn ? "Sestavuji a ověřuji all-in návrh…" : "Sestavit kompletní all-in návrh"}
        </button>
        <span>Backend znovu ověří oficiální PDF i regulované složky. Uloží se pouze nepotvrzený návrh; aktivace zůstane zamčená.</span>
      </div>
    </section>
  );
}

function AllInProposalCard({
  response,
  confirmation,
  confirming,
  onConfirm,
}: {
  response: CustomerTariffProposalResponse;
  confirmation: CustomerTariffConfirmResponse | null;
  confirming: boolean;
  onConfirm: () => void;
}) {
  const preview = response.preview;
  const validity = preview.valid_to ? `${preview.valid_from} → ${preview.valid_to}` : `od ${preview.valid_from}`;
  const confirmed = confirmation?.confirmed === true;
  return (
    <section className={`tariff-all-in${confirmed ? " confirmed" : ""}`}>
      <div className="tariff-wizard__results-head">
        <div>
          <span className="eyebrow">Krok 4 · serverově ověřený návrh</span>
          <h3>Kompletní all-in cena elektřiny</h3>
        </div>
        <span className={confirmed ? "tariff-all-in__badge confirmed" : "tariff-all-in__badge pending"}>
          {confirmed ? "Potvrzeno a aktivováno" : "Čeká na vaše potvrzení"}
        </span>
      </div>

      <div className="tariff-all-in__totals">
        <div><span>All-in VT</span><b>{preview.all_in_vt_czk_kwh} Kč/kWh</b><small>včetně všech variabilních složek</small></div>
        <div><span>All-in NT</span><b>{preview.all_in_nt_czk_kwh} Kč/kWh</b><small>včetně všech variabilních složek</small></div>
        <div><span>Fixní platby</span><b>{preview.fixed_monthly_total_czk} Kč/měsíc</b><small>dodavatel + regulované fixní složky</small></div>
      </div>

      <div className="tariff-all-in__context">
        <span><b>Produkt:</b> {preview.product_name}</span>
        <span><b>Dodavatel:</b> {SUPPLIER_LABELS[preview.supplier] ?? preview.supplier}</span>
        <span><b>Sazba:</b> {preview.distribution_tariff}</span>
        <span><b>Jistič:</b> {preview.breaker_code}</span>
        <span><b>Platnost:</b> {validity}</span>
        <span><b>Návrh pro den:</b> {response.proposed_for_day}</span>
      </div>

      <div className="tariff-all-in__components-grid">
        <div>
          <h4>Variabilní složky</h4>
          <div className="tariff-all-in__component-list">
            {preview.variable_components.map((component) => (
              <div key={`${component.kind}:${component.name}`}>
                <span>{component.name}</span>
                <b>VT {component.gross_vt_czk_per_kwh} · NT {component.gross_nt_czk_per_kwh} Kč/kWh</b>
              </div>
            ))}
          </div>
        </div>
        <div>
          <h4>Fixní složky</h4>
          <div className="tariff-all-in__component-list">
            {preview.fixed_components.map((component) => (
              <div key={`${component.kind}:${component.name}`}>
                <span>{component.name}</span>
                <b>{component.gross_monthly_czk} Kč/měsíc</b>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="tariff-all-in__sources">
        <div>
          <span>Obchodní část</span>
          <a href={preview.supplier_source_url} target="_blank" rel="noreferrer">Oficiální ceník dodavatele</a>
          <code>SHA {preview.supplier_document_sha256}</code>
        </div>
        <div>
          <span>Regulovaná část</span>
          <a href={preview.regulated_source_url} target="_blank" rel="noreferrer">Potvrzený regulatorní zdroj</a>
          <code>{preview.regulated_checksum ? `SHA ${preview.regulated_checksum}` : "Zdroj bez checksumu"}</code>
        </div>
      </div>

      <div className="tariff-price-preview__checks">
        <b>Backend před vytvořením návrhu ověřil</b>
        <ul>{preview.validation_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
      </div>

      <div className="tariff-all-in__fingerprints">
        <span><b>Proposal:</b> <code>{response.proposal_fingerprint}</code></span>
        <span><b>Smlouva:</b> <code>{response.contract_fingerprint}</code></span>
        <span><b>All-in:</b> <code>{response.all_in_tariff_fingerprint}</code></span>
        <span><b>Regulace:</b> <code>{response.regulated_version_fingerprint}</code></span>
      </div>

      {confirmed ? (
        <div className="tariff-wizard__notice success">
          <b>Tarif je potvrzený.</b>
          <span>Aktivní smlouva i přesně svázaná all-in verze byly potvrzeny jedním fingerprint-only krokem. Historické verze zůstaly zachované.</span>
        </div>
      ) : (
        <div className="tariff-all-in__confirm-box">
          <div>
            <b>Poslední krok je záměrně ruční.</b>
            <span>Potvrzením aktivujete pouze tento serverově ověřený návrh. Do potvrzovacího requestu se neposílá cena, URL ani obsah ceníku — jen fingerprint výše uvedeného proposal envelope.</span>
          </div>
          <button type="button" disabled={confirming} onClick={onConfirm}>
            {confirming ? "Potvrzuji přesný návrh…" : "Potvrdit a aktivovat tento tarif"}
          </button>
        </div>
      )}
    </section>
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
  const [pricePreview, setPricePreview] = useState<TariffParsePreviewResponse | null>(null);
  const [pricePreviewError, setPricePreviewError] = useState<string | null>(null);
  const [previewingFingerprint, setPreviewingFingerprint] = useState<string | null>(null);
  const [customerProposal, setCustomerProposal] = useState<CustomerTariffProposalResponse | null>(null);
  const [proposalError, setProposalError] = useState<string | null>(null);
  const [preparingAllIn, setPreparingAllIn] = useState(false);
  const [confirmation, setConfirmation] = useState<CustomerTariffConfirmResponse | null>(null);
  const [confirmationError, setConfirmationError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const reviewRevision = useRef(0);

  const clearProposalState = () => {
    setCustomerProposal(null);
    setProposalError(null);
    setPreparingAllIn(false);
    setConfirmation(null);
    setConfirmationError(null);
    setConfirming(false);
  };

  const invalidateReview = () => {
    reviewRevision.current += 1;
    setDiscovery(null);
    setDiscoveryError(null);
    setPricePreview(null);
    setPricePreviewError(null);
    setPreviewingFingerprint(null);
    clearProposalState();
  };

  const updateDraft = (patch: Partial<WizardDraft>) => {
    invalidateReview();
    setDraft((current) => ({ ...current, ...patch }));
  };

  useEffect(() => {
    let active = true;
    reviewRevision.current += 1;
    setEntryId(null);
    setCatalog(null);
    setCatalogError(null);
    setDiscovery(null);
    setDiscoveryError(null);
    setPricePreview(null);
    setPricePreviewError(null);
    setPreviewingFingerprint(null);
    setCustomerProposal(null);
    setProposalError(null);
    setConfirmation(null);
    setConfirmationError(null);
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
    updateDraft({ supplier, productName: nextGroup?.products[0]?.product_name ?? "" });
  };

  const discover = async () => {
    if (!hass || !entryId || !selectedProduct || invalidDates || mndBlocked) return;
    const revision = reviewRevision.current;
    setDiscovering(true);
    setDiscovery(null);
    setDiscoveryError(null);
    setPricePreview(null);
    setPricePreviewError(null);
    clearProposalState();
    try {
      const response = await callHomeAssistantWs<TariffDiscoveryResponse>(hass, {
        type: "frakon_energy/tariff/discover",
        entry_id: entryId,
        contract: contractPayload(draft, selectedProduct),
        day: draft.discoveryDay,
      });
      if (reviewRevision.current !== revision) return;
      setDiscovery(response);
      if (response.candidates.length === 0) {
        setDiscoveryError("Pro tuto přesnou kombinaci smlouvy a data nebyl nalezen ověřený ceník. Nic se nepřiřadilo automaticky.");
      }
    } catch (error) {
      if (reviewRevision.current === revision) setDiscoveryError(readableError(error));
    } finally {
      setDiscovering(false);
    }
  };

  const previewCandidate = async (candidate: TariffCandidate) => {
    if (!hass || !entryId || !selectedProduct || !discovery) return;
    if (candidate.supplier !== "cez") {
      setPricePreviewError("Automatický parser ceny je zatím aktivní pouze pro ČEZ.");
      return;
    }

    const revision = reviewRevision.current;
    setPreviewingFingerprint(candidate.fingerprint);
    setPricePreview(null);
    setPricePreviewError(null);
    clearProposalState();
    try {
      const response = await callHomeAssistantWs<TariffParsePreviewResponse>(hass, {
        type: "frakon_energy/tariff/parse_preview",
        entry_id: entryId,
        contract: contractPayload(draft, selectedProduct),
        day: draft.discoveryDay,
        candidate_fingerprint: candidate.fingerprint,
      });
      if (reviewRevision.current !== revision) return;
      if (response.candidate_fingerprint !== candidate.fingerprint) {
        throw new Error("Backend vrátil náhled pro jiný fingerprint ceníku.");
      }
      if (response.contract_fingerprint !== discovery.contract_fingerprint) {
        throw new Error("Backend vrátil náhled pro jinou verzi smlouvy.");
      }
      if (
        response.persistence_performed ||
        response.activation_performed ||
        response.preview.persistence_performed ||
        response.preview.activation_performed
      ) {
        throw new Error("Read-only náhled nesmí ukládat ani aktivovat tarif.");
      }
      setPricePreview(response);
    } catch (error) {
      if (reviewRevision.current === revision) setPricePreviewError(readableError(error));
    } finally {
      if (reviewRevision.current === revision) setPreviewingFingerprint(null);
    }
  };

  const prepareCustomerProposal = async () => {
    if (!hass || !entryId || !selectedProduct || !discovery || !pricePreview) return;
    const revision = reviewRevision.current;
    const candidateFingerprint = pricePreview.candidate_fingerprint;
    setPreparingAllIn(true);
    setCustomerProposal(null);
    setProposalError(null);
    setConfirmation(null);
    setConfirmationError(null);
    try {
      const response = await callHomeAssistantWs<CustomerTariffProposalResponse>(hass, {
        type: "frakon_energy/tariff/customer/propose",
        entry_id: entryId,
        contract: contractPayload(draft, selectedProduct),
        day: draft.discoveryDay,
        candidate_fingerprint: candidateFingerprint,
      });
      if (reviewRevision.current !== revision) return;
      if (response.candidate_fingerprint !== candidateFingerprint) {
        throw new Error("Backend vytvořil návrh pro jiný fingerprint ceníku.");
      }
      if (response.contract_fingerprint !== discovery.contract_fingerprint) {
        throw new Error("Backend vytvořil návrh pro jinou verzi smlouvy.");
      }
      if (response.source_url !== response.preview.supplier_source_url || response.document_sha256 !== response.preview.supplier_document_sha256) {
        throw new Error("All-in návrh nemá shodnou identitu ověřeného dodavatelského dokumentu.");
      }
      if (
        !response.download_performed ||
        !response.parsing_performed ||
        !response.all_in_preview_performed ||
        !response.preview.all_in_ready ||
        response.confirmation_performed ||
        response.activation_performed ||
        response.preview.persistence_performed ||
        response.preview.activation_performed
      ) {
        throw new Error("Backend nevrátil bezpečný nepotvrzený all-in proposal.");
      }
      setCustomerProposal(response);
    } catch (error) {
      if (reviewRevision.current === revision) setProposalError(readableError(error));
    } finally {
      if (reviewRevision.current === revision) setPreparingAllIn(false);
    }
  };

  const confirmCustomerProposal = async () => {
    if (!hass || !entryId || !customerProposal || confirmation?.confirmed) return;
    const revision = reviewRevision.current;
    const proposalFingerprint = customerProposal.proposal_fingerprint;
    setConfirming(true);
    setConfirmationError(null);
    try {
      const response = await callHomeAssistantWs<CustomerTariffConfirmResponse>(hass, {
        type: "frakon_energy/tariff/customer/confirm",
        entry_id: entryId,
        proposal_fingerprint: proposalFingerprint,
      });
      if (reviewRevision.current !== revision) return;
      if (response.proposal_fingerprint !== customerProposal.proposal_fingerprint) {
        throw new Error("Backend potvrdil jiný proposal fingerprint.");
      }
      if (
        response.contract_fingerprint !== customerProposal.contract_fingerprint ||
        response.all_in_tariff_fingerprint !== customerProposal.all_in_tariff_fingerprint ||
        response.regulated_version_fingerprint !== customerProposal.regulated_version_fingerprint
      ) {
        throw new Error("Potvrzené immutable reference neodpovídají zobrazenému návrhu.");
      }
      if (!response.confirmed) {
        throw new Error("Backend nepotvrdil zákaznický tarif.");
      }
      setConfirmation(response);
    } catch (error) {
      if (reviewRevision.current === revision) setConfirmationError(readableError(error));
    } finally {
      if (reviewRevision.current === revision) setConfirming(false);
    }
  };

  return (
    <section className="tariff-wizard">
      <div className="tariff-wizard__header">
        <div>
          <span className="eyebrow">Nastavení ceny elektřiny</span>
          <h2>Najít, ověřit a potvrdit přesný tarif</h2>
          <p>Vyberte údaje ze smlouvy. FRAKON Energy hledá pouze přesnou shodu v ověřených zdrojích, sestaví kompletní all-in cenu a aktivuje ji až po samostatném potvrzení konkrétního fingerprintu.</p>
        </div>
        <span className="tariff-wizard__safety">Fail-closed</span>
      </div>

      <div className="tariff-wizard__steps" aria-label="Průběh nastavení tarifu">
        <div className="active"><b>1</b><span>Smlouva</span></div>
        <div className={discovery?.candidates.length ? "active" : ""}><b>2</b><span>Ověření zdroje</span></div>
        <div className={pricePreview ? "active" : ""}><b>3</b><span>Obchodní cena</span></div>
        <div className={customerProposal ? "active" : ""}><b>4</b><span>All-in návrh</span></div>
        <div className={confirmation?.confirmed ? "active confirmed" : ""}><b>5</b><span>Potvrzení</span></div>
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
              <select value={selectedProduct?.product_name ?? ""} onChange={(event) => updateDraft({ productName: event.target.value })}>
                {products.map((product) => <option key={`${product.product_name}:${product.contract_kind}`} value={product.product_name}>{product.product_name}</option>)}
              </select>
              {selectedProduct ? <div className="tariff-wizard__field-meta"><span>{selectedProduct.contract_kind === "fixed" ? "Fixovaná smlouva" : "Na dobu neurčitou"}</span><SourceStatus product={selectedProduct} /></div> : null}
            </label>

            <label>
              <span>Distribuční území</span>
              <select value={draft.distributor} onChange={(event) => updateDraft({ distributor: event.target.value })}>
                {DISTRIBUTORS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>

            <label>
              <span>Distribuční sazba</span>
              <select value={draft.distributionTariff} onChange={(event) => updateDraft({ distributionTariff: event.target.value })}>
                {DISTRIBUTION_TARIFFS.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>

            <label>
              <span>Počet fází</span>
              <select value={draft.breakerPhases} onChange={(event) => updateDraft({ breakerPhases: Number(event.target.value) })}>
                <option value={1}>1 fáze</option>
                <option value={3}>3 fáze</option>
              </select>
            </label>

            <label>
              <span>Hlavní jistič</span>
              <select value={draft.breakerAmperes} onChange={(event) => updateDraft({ breakerAmperes: Number(event.target.value) })}>
                {BREAKER_AMPERES.map((item) => <option key={item} value={item}>{item} A</option>)}
              </select>
            </label>

            <label>
              <span>Smlouva platí od</span>
              <input type="date" value={draft.validFrom} onChange={(event) => updateDraft({ validFrom: event.target.value })} />
            </label>

            {fixed ? (
              <label>
                <span>Fixace do</span>
                <input type="date" min={draft.validFrom} value={draft.fixationEnd} onChange={(event) => updateDraft({ fixationEnd: event.target.value })} />
              </label>
            ) : (
              <div className="tariff-wizard__read-only"><span>Typ smlouvy</span><b>Bez fixace</b><small>Produkt je v ověřeném katalogu vedený na dobu neurčitou.</small></div>
            )}

            <label>
              <span>Ceník ověřit k datu</span>
              <input type="date" min={draft.validFrom} value={draft.discoveryDay} onChange={(event) => updateDraft({ discoveryDay: event.target.value })} />
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
          {pricePreviewError ? <div className="tariff-wizard__notice error">{pricePreviewError}</div> : null}
          {proposalError ? <div className="tariff-wizard__notice error">{proposalError}</div> : null}
          {confirmationError ? <div className="tariff-wizard__notice error">{confirmationError}</div> : null}

          {discovery?.candidates.length ? (
            <div className="tariff-wizard__results">
              <div className="tariff-wizard__results-head">
                <div><span className="eyebrow">Krok 2</span><h3>Nalezené ověřené zdroje</h3></div>
                <span>{discovery.candidates.length} {discovery.candidates.length === 1 ? "shoda" : "shody"}</span>
              </div>
              {discovery.candidates.map((candidate) => (
                <CandidateCard
                  key={candidate.fingerprint}
                  candidate={candidate}
                  previewing={previewingFingerprint === candidate.fingerprint}
                  previewed={pricePreview?.candidate_fingerprint === candidate.fingerprint}
                  onPreview={previewCandidate}
                />
              ))}
            </div>
          ) : null}

          {pricePreview ? (
            <PricePreviewCard
              response={pricePreview}
              preparingAllIn={preparingAllIn}
              onPrepareAllIn={prepareCustomerProposal}
            />
          ) : null}

          {customerProposal ? (
            <AllInProposalCard
              response={customerProposal}
              confirmation={confirmation}
              confirming={confirming}
              onConfirm={confirmCustomerProposal}
            />
          ) : null}
        </>
      ) : null}
    </section>
  );
}
