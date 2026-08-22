from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
RELEASE_HEADER_RE = re.compile(r"(?m)^##\s+(?:\[)?(?P<version>[^\]\s]+)(?:\])?\s*$")


def extract_release_notes(changelog: str, version: str) -> str:
    """Return the Markdown body for exactly one release section."""

    matches = list(RELEASE_HEADER_RE.finditer(changelog))
    target_index = next(
        (index for index, match in enumerate(matches) if match.group("version") == version),
        None,
    )
    if target_index is None:
        raise ValueError(f"CHANGELOG.md has no release section for {version}")

    start = matches[target_index].end()
    end = matches[target_index + 1].start() if target_index + 1 < len(matches) else len(changelog)
    notes = changelog[start:end].strip()
    if not notes:
        raise ValueError(f"CHANGELOG.md release section for {version} is empty")
    return notes


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    version = args[0] if args else (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    try:
        notes = extract_release_notes(changelog, version)
    except ValueError as err:
        print(f"release notes extraction failed: {err}", file=sys.stderr)
        return 1
    print(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
