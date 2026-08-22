from __future__ import annotations

import json
from pathlib import Path
import re
import sys

from release_notes import extract_release_notes

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?")


def fail(message: str) -> None:
    raise SystemExit(f"release preflight failed: {message}")


def main() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not VERSION_RE.fullmatch(version):
        fail(f"invalid VERSION: {version!r}")

    manifest = json.loads((ROOT / "custom_components/frakon_energy/manifest.json").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    if manifest.get("version") != version:
        fail(f"manifest version {manifest.get('version')!r} != VERSION {version!r}")
    if package.get("version") != version:
        fail(f"frontend version {package.get('version')!r} != VERSION {version!r}")
    if manifest.get("domain") != "frakon_energy":
        fail("manifest domain must be frakon_energy")
    if manifest.get("config_flow") is not True:
        fail("manifest config_flow must be true")
    if hacs.get("name") != manifest.get("name"):
        fail("hacs name must match manifest name")

    if hacs.get("zip_release") is not True:
        fail("hacs zip_release must be true so installations use validated release artifacts")
    if hacs.get("hide_default_branch") is not True:
        fail("hacs hide_default_branch must be true so commit SHAs are not offered as versions")
    if hacs.get("filename") != "frakon-energy.zip":
        fail("hacs filename must be frakon-energy.zip")

    try:
        release_notes = extract_release_notes(changelog, version)
    except ValueError as err:
        fail(str(err))
    if len(release_notes) < 80:
        fail(f"CHANGELOG.md release section for {version} is too short to be useful")

    frontend_index = ROOT / "custom_components/frakon_energy/frontend_app/index.html"
    if not frontend_index.is_file():
        fail("packaged frontend_app/index.html is missing")

    print(f"release preflight OK: FRAKON Energy {version}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, json.JSONDecodeError) as err:
        print(f"release preflight failed: {err}", file=sys.stderr)
        raise SystemExit(1) from err
