#!/usr/bin/env -S uv run --script
# /// script
# dependencies = []
# ///
"""Smoke tests for pdf2md.py against fixture PDFs.

Runs ``pdf2md.py`` on each fixture, asserts the output contains expected
content, and reports the tier distribution observed on each PDF. Tier
expectations are soft-checked: failures print a warning but don't fail
the suite — tiers are an implementation detail that can legitimately
shift as heuristics evolve.

Usage:
    ./tests/smoke.py            # run all cases
    ./tests/smoke.py --quick    # skip the slow scanned-OCR case
    ./tests/smoke.py --keep     # don't delete output markdown
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PDF2MD = REPO / "pdf2md.py"
FIXTURES = REPO / "tests" / "fixtures"

CASES = [
    {
        "name": "clean-english-1",
        "pdf": FIXTURES / "Farrell-WeaponizedInterdependence-2019.pdf",
        "expect_tier": "pymupdf4llm",
        "expect_substrings": ["Weaponized Interdependence", "Farrell"],
        "quick": True,
    },
    {
        "name": "clean-english-2",
        "pdf": FIXTURES / "RL33740-US-Japan-Alliance.pdf",
        "expect_tier": "pymupdf4llm",
        "expect_substrings": ["U.S.-Japan Alliance", "Congressional Research"],
        "quick": True,
    },
    {
        "name": "hidden-text-layer",
        # JSTOR-style scan: every page is an image, but there's a hidden
        # text layer underneath. pymupdf4llm's layout analysis looks at
        # the image and declares the page empty (tier 1 fails), but raw
        # page.get_text() reads the text layer cleanly (tier 2 wins).
        "pdf": FIXTURES / "Strange-PersistentMythLost-1987.pdf",
        "expect_tier": "pymupdf",
        "expect_substrings": ["Persistent Myth", "Susan Strange"],
        "quick": True,
    },
    {
        "name": "scanned-chinese",
        "pdf": FIXTURES / "日美同盟-兼論台灣因應策略.pdf",
        "expect_tier": "ocr",
        "expect_substrings": ["日美同盟", "台灣"],
        "quick": False,
    },
]

TIER_RE = re.compile(r"<!--\s*tier=([a-z0-9:]+)\s*-->")


def run_case(case: dict, keep: bool) -> tuple[bool, list[str]]:
    """Run pdf2md on one fixture. Returns (passed, messages)."""
    messages: list[str] = []
    pdf = case["pdf"]
    if not pdf.exists():
        return False, [f"fixture missing: {pdf}"]

    out_dir = Path(tempfile.mkdtemp(prefix="pdf2md_smoke_"))
    out_md = out_dir / f"{case['name']}.md"

    t0 = time.monotonic()
    proc = subprocess.run(
        [str(PDF2MD), str(pdf), str(out_md)],
        capture_output=True,
        text=True,
    )
    elapsed = time.monotonic() - t0

    if proc.returncode != 0:
        messages.append(f"pdf2md.py exited {proc.returncode}")
        messages.append(f"stderr tail: {proc.stderr[-500:]}")
        return False, messages

    if not out_md.exists():
        return False, ["output file not created"]

    text = out_md.read_text(encoding="utf-8")

    # Tier distribution (soft check).
    tiers = Counter(TIER_RE.findall(text))
    # Pages without a tier marker are tier 1 (pymupdf4llm); count those
    # by comparing against page-start markers.
    page_starts = len(re.findall(r"\*\*\[Page .+? start\]\*\*", text))
    tier1_implicit = page_starts - sum(tiers.values())
    if tier1_implicit > 0:
        tiers["pymupdf4llm"] += tier1_implicit

    dominant = tiers.most_common(1)[0][0] if tiers else "unknown"
    messages.append(
        f"{elapsed:5.1f}s  pages={page_starts:3}  tiers={dict(tiers)}"
    )

    ok = True

    # Hard check: expected substrings present.
    missing = [s for s in case["expect_substrings"] if s not in text]
    if missing:
        messages.append(f"  FAIL: missing substrings: {missing}")
        ok = False

    # Soft check: dominant tier matches expectation.
    if dominant != case["expect_tier"]:
        messages.append(
            f"  WARN: expected dominant tier={case['expect_tier']!r}, "
            f"got {dominant!r}"
        )

    if keep:
        messages.append(f"  output kept: {out_md}")
    else:
        out_md.unlink(missing_ok=True)
        out_dir.rmdir()

    return ok, messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quick", action="store_true",
        help="Skip slow cases (scanned OCR).",
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="Keep output markdown files for inspection.",
    )
    args = parser.parse_args()

    cases = [c for c in CASES if not args.quick or c["quick"]]
    print(f"[smoke] running {len(cases)}/{len(CASES)} cases\n")

    failures: list[str] = []
    for case in cases:
        print(f"=== {case['name']} ({case['pdf'].name}) ===")
        ok, messages = run_case(case, keep=args.keep)
        for msg in messages:
            print(msg)
        if not ok:
            failures.append(case["name"])
        print()

    if failures:
        print(f"[smoke] FAILED: {failures}")
        return 1
    print(f"[smoke] OK: {len(cases)} cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
