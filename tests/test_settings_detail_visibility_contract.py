from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend" / "public" / "settings-detail-visibility.js"
PACKAGED = ROOT / "custom_components" / "frakon_energy" / "frontend_app" / "settings-detail-visibility.js"
SOURCE_INDEX = ROOT / "frontend" / "index.html"
PACKAGED_INDEX = ROOT / "custom_components" / "frakon_energy" / "frontend_app" / "index.html"


def test_settings_detail_visibility_bridge_is_packaged_and_loaded() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    packaged = PACKAGED.read_text(encoding="utf-8")
    assert source == packaged
    assert "settings-detail-visibility.js" in SOURCE_INDEX.read_text(encoding="utf-8")
    assert "settings-detail-visibility.js" in PACKAGED_INDEX.read_text(encoding="utf-8")


def test_disabled_modules_hide_only_their_detailed_settings() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    for guard in (
        "billing-estimate",
        "daily-consumption",
        "monthly-consumption",
        "spot-prices",
        "technology-overview",
        "photovoltaics",
        "energy-flow",
    ):
        assert f'isDisabled("{guard}")' in source
    assert "site-capacity-settings" in source
    assert "isPhotovoltaicsItem" in source
    assert "element.hidden = hidden" in source


def test_visibility_reconciliation_cannot_feedback_on_its_own_attribute_writes() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "requestAnimationFrame(applyVisibility)" in source
    assert 'observer.observe(document.documentElement, { childList: true, subtree: true })' in source
    assert "attributes: true" not in source
