import json
import re
from pathlib import Path

SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:alpha|beta|rc)\.(0|[1-9]\d*))?$"
)


def test_versions_are_semantic_and_synchronized() -> None:
    canonical = Path("VERSION").read_text(encoding="utf-8").strip()
    manifest = json.loads(
        Path("custom_components/frakon_energy/manifest.json").read_text(encoding="utf-8")
    )
    package = json.loads(Path("frontend/package.json").read_text(encoding="utf-8"))

    assert SEMVER.fullmatch(canonical), (
        "Use MAJOR.MINOR.PATCH or a prerelease such as 1.0.0-beta.1 / 1.0.0-rc.2"
    )
    assert manifest["version"] == canonical
    assert package["version"] == canonical
