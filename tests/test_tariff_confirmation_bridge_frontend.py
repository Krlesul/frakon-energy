from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_INDEX = ROOT / "frontend" / "index.html"
PACKAGED_INDEX = ROOT / "custom_components" / "frakon_energy" / "frontend_app" / "index.html"
SOURCE_BRIDGE = ROOT / "frontend" / "public" / "tariff-confirmation-bridge-v2.js"
PACKAGED_BRIDGE = ROOT / "custom_components" / "frakon_energy" / "frontend_app" / "tariff-confirmation-bridge-v2.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_v2_tariff_bridge_is_loaded_in_source_and_packaged_frontend() -> None:
    source_index = _read(SOURCE_INDEX)
    packaged_index = _read(PACKAGED_INDEX)

    assert "tariff-confirmation-bridge-v2.js" in source_index
    assert "tariff-confirmation-bridge-v2.js" in packaged_index
    assert 'src="/tariff-confirmation-bridge.js"' not in source_index
    assert 'src="./tariff-confirmation-bridge.js"' not in packaged_index


def test_packaged_tariff_bridge_matches_source() -> None:
    assert _read(SOURCE_BRIDGE) == _read(PACKAGED_BRIDGE)


def test_tariff_bridge_ignores_its_own_dom_mutations() -> None:
    bridge = _read(SOURCE_BRIDGE)

    assert "mutationTouchesOnlyBridge" in bridge
    assert "mutations.every((mutation) => mutationTouchesOnlyBridge(mutation, root))" in bridge
    assert "new MutationObserver(() => queueMicrotask(syncBridge))" not in bridge
    assert "requestAnimationFrame" in bridge


def test_tariff_bridge_websocket_calls_are_bounded() -> None:
    bridge = _read(SOURCE_BRIDGE)

    assert "LOCAL_WS_TIMEOUT_MS" in bridge
    assert "DOWNLOAD_WS_TIMEOUT_MS" in bridge
    assert "Promise.race" in bridge
    assert "nic nebylo aktivováno" in bridge
