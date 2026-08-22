from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

REQUIRED_ROOT_FILES = {
    "__init__.py",
    "manifest.json",
    "panel.py",
    "frontend/panel.js",
    "frontend_app/index.html",
}


def validate_hacs_release(path: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"HACS release validation failed: missing archive {path}")

    with ZipFile(path) as archive:
        names = {name.rstrip("/") for name in archive.namelist() if not name.endswith("/")}

    missing = sorted(REQUIRED_ROOT_FILES - names)
    if missing:
        raise SystemExit(
            "HACS release validation failed: required root files are missing: "
            + ", ".join(missing)
        )

    nested_domain = sorted(name for name in names if name.startswith("frakon_energy/"))
    if nested_domain:
        raise SystemExit(
            "HACS release validation failed: archive must be relative to the integration "
            "directory, not contain a top-level frakon_energy/ directory"
        )

    generated = sorted(
        name for name in names if "/__pycache__/" in f"/{name}" or name.endswith(".pyc")
    )
    if generated:
        raise SystemExit(
            "HACS release validation failed: generated Python cache files are present"
        )

    print(f"HACS release archive OK: {path}")


def main() -> None:
    validate_hacs_release(Path("frakon-energy.zip"))


if __name__ == "__main__":
    main()
