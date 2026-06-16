"""Enforce the per-layer red/green screenshot rule.

For every test module `tests/test_<label>.py`, this requires:
  - docs/test-evidence/<label>-red.png   (the failing run)
  - docs/test-evidence/<label>-green.png (the passing run)
  - both PNGs referenced somewhere in README.md

Exits non-zero with a clear report if anything is missing. Used by the
pre-commit hook and by CI, so a layer cannot land without its evidence.

What this CANNOT verify: that the red was captured live, before the code
existed. That stays a discipline rule in CLAUDE.md — the machine checks the
artifacts exist and are documented; it can't check the order they were made.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
EVIDENCE_DIR = ROOT / "docs" / "test-evidence"
README = ROOT / "README.md"


def labels_from_tests() -> list[str]:
    """Each tests/test_<label>.py contributes one layer label."""
    return sorted(
        p.stem[len("test_"):]
        for p in TESTS_DIR.glob("test_*.py")
    )


def main() -> int:
    readme_text = README.read_text(encoding="utf-8") if README.exists() else ""
    problems: list[str] = []

    labels = labels_from_tests()
    if not labels:
        print("check_evidence: no tests/test_*.py found — nothing to enforce yet.")
        return 0

    for label in labels:
        for state in ("red", "green"):
            png = EVIDENCE_DIR / f"{label}-{state}.png"
            rel = png.relative_to(ROOT).as_posix()
            if not png.exists():
                problems.append(f"missing screenshot: {rel}")
            elif rel not in readme_text:
                problems.append(f"screenshot not referenced in README: {rel}")

    if problems:
        print("FAIL - test-evidence rule not satisfied:\n")
        for p in problems:
            print(f"  - {p}")
        print(
            "\nEvery tests/test_<label>.py needs <label>-red.png and "
            "<label>-green.png in docs/test-evidence/, both linked in README.md.\n"
            "See CLAUDE.md -> 'Test evidence - mandatory, every layer'.\n"
            "Intentional work-in-progress commit? Bypass once with: git commit --no-verify"
        )
        return 1

    print(f"OK - evidence present and documented for: {', '.join(labels)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
