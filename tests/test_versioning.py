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
        "Use MAJOR.MINOR.PATCH or a prerelease such as 1.0.0-beta.1 / 1.0.0-rc.3"
    )
    assert manifest["version"] == canonical
    assert package["version"] == canonical


def test_hacs_uses_versioned_release_artifacts() -> None:
    hacs = json.loads(Path("hacs.json").read_text(encoding="utf-8"))

    assert hacs["zip_release"] is True
    assert hacs["hide_default_branch"] is True
    assert hacs["filename"] == "frakon-energy.zip"


def test_current_version_has_meaningful_release_notes() -> None:
    version = Path("VERSION").read_text(encoding="utf-8").strip()
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    header = re.search(
        rf"(?m)^##\s+(?:\[)?{re.escape(version)}(?:\])?\s*$",
        changelog,
    )

    assert header is not None
    next_header = re.search(r"(?m)^##\s+", changelog[header.end() :])
    end = header.end() + next_header.start() if next_header else len(changelog)
    notes = changelog[header.end() : end].strip()
    assert len(notes) >= 80
