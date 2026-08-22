from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "custom_components" / "frakon_energy"
ARCHIVE = ROOT / "frakon-energy.zip"


def _should_package(path: Path) -> bool:
    if not path.is_file():
        return False
    if "__pycache__" in path.parts:
        return False
    if path.suffix == ".pyc":
        return False
    return True


def build_hacs_release() -> Path:
    if not SOURCE.is_dir():
        raise SystemExit(f"missing integration source directory: {SOURCE}")

    with ZipFile(ARCHIVE, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(SOURCE.rglob("*")):
            if _should_package(path):
                # HACS extracts zip_release archives directly into
                # /config/custom_components/<domain>.  Paths therefore MUST be
                # relative to the integration directory itself.  A top-level
                # frakon_energy/ directory would create a nested package and
                # leave the previously installed integration files untouched.
                archive.write(path, path.relative_to(SOURCE))

    print(ARCHIVE.name)
    return ARCHIVE


def main() -> None:
    build_hacs_release()


if __name__ == "__main__":
    main()
