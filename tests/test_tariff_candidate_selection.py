from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types


def load_modules():
    for name in (
        "custom_components",
        "custom_components.frakon_energy",
        "custom_components.frakon_energy.tariff_sources",
        "custom_components.frakon_energy.tariff_candidate_selection",
    ):
        sys.modules.pop(name, None)

    for name in ("custom_components", "custom_components.frakon_energy"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package

    def load(name: str, path: str):
        spec = importlib.util.spec_from_file_location(name, Path(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    sources = load(
        "custom_components.frakon_energy.tariff_sources",
        "custom_components/frakon_energy/tariff_sources.py",
    )
    selection = load(
        "custom_components.frakon_energy.tariff_candidate_selection",
        "custom_components/frakon_energy/tariff_candidate_selection.py",
    )
    return sources, selection


def _candidate(
    sources,
    *,
    url: str = "https://www.cez.cz/file/cenik.pdf",
    discovered_at: datetime | None = None,
    score: int = 95,
    reasons=("exact product",),
    valid_from: date = date(2026, 1, 1),
    document_date: date | None = None,
    sha256: str | None = None,
):
    return sources.TariffDocumentCandidate(
        document=sources.OfficialTariffDocument(
            supplier="cez",
            source_url=url,
            discovered_at=discovered_at
            or datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc),
            document_date=document_date,
            sha256=sha256,
            content_type="application/pdf",
        ),
        product_name="Basic",
        valid_from=valid_from,
        match_score=score,
        match_reasons=tuple(reasons),
        price_scope=sources.PRICE_SCOPE_SUPPLIER_COMMERCIAL,
    )


def test_selection_identity_ignores_discovery_ranking_metadata() -> None:
    sources, selection = load_modules()
    first = _candidate(sources, score=95, reasons=("exact",))
    reranked = _candidate(
        sources,
        discovered_at=datetime(2026, 8, 15, 4, 0, tzinfo=timezone.utc),
        score=80,
        reasons=("adapter ranking changed",),
    )

    assert selection.tariff_candidate_selection_fingerprint(first) == (
        selection.tariff_candidate_selection_fingerprint(reranked)
    )


def test_selection_identity_changes_when_source_or_version_changes() -> None:
    sources, selection = load_modules()
    original = _candidate(sources)
    different_url = _candidate(sources, url="https://www.cez.cz/file/jiny-cenik.pdf")
    different_version = _candidate(sources, valid_from=date(2026, 7, 1))
    dated_document = _candidate(sources, document_date=date(2026, 6, 30))
    pinned_content = _candidate(sources, sha256="a" * 64)

    identities = {
        selection.tariff_candidate_selection_fingerprint(item)
        for item in (
            original,
            different_url,
            different_version,
            dated_document,
            pinned_content,
        )
    }
    assert len(identities) == 5


def test_review_items_are_read_only_and_selection_requires_exact_fingerprint() -> None:
    sources, selection = load_modules()
    first = _candidate(sources, url="https://www.cez.cz/file/first.pdf", score=100)
    second = _candidate(sources, url="https://www.cez.cz/file/second.pdf", score=90)

    review = selection.candidate_review_items((first, second))
    assert [item.match_score for item in review] == [100, 90]
    assert all(item.download_performed is False for item in review)
    assert all(item.parsing_performed is False for item in review)
    assert all(item.persistence_performed is False for item in review)
    assert all(item.activation_performed is False for item in review)

    chosen = selection.select_tariff_candidate(
        (first, second),
        fingerprint=review[1].fingerprint,
    )
    assert chosen is second


def test_unknown_or_malformed_selection_fingerprint_is_rejected() -> None:
    sources, selection = load_modules()
    candidate = _candidate(sources)

    try:
        selection.select_tariff_candidate((candidate,), fingerprint="not-a-sha")
    except ValueError as err:
        assert "SHA-256" in str(err)
    else:
        raise AssertionError("Malformed selection identity must be rejected")

    try:
        selection.select_tariff_candidate((candidate,), fingerprint="0" * 64)
    except LookupError:
        pass
    else:
        raise AssertionError("Unknown selection identity must be rejected")


def test_duplicate_candidate_identity_fails_closed() -> None:
    sources, selection = load_modules()
    first = _candidate(sources, score=100)
    duplicate = _candidate(sources, score=80, reasons=("same document, different rank",))

    for operation in (
        lambda: selection.candidate_review_items((first, duplicate)),
        lambda: selection.select_tariff_candidate(
            (first, duplicate),
            fingerprint=selection.tariff_candidate_selection_fingerprint(first),
        ),
    ):
        try:
            operation()
        except ValueError as err:
            assert "duplicate tariff candidate identity" in str(err)
        else:
            raise AssertionError("Duplicate candidate identity must fail closed")
