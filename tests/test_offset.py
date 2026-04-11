#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "pymupdf4llm",
#   "pymupdf",
# ]
# ///
"""Unit tests for pdf2md's smart page-offset detection.

Covers the pure confidence gate (``_score_offset_votes``), the
insertion-order contract in ``_lookup_language``, and end-to-end
offset detection / trivial-label detection against synthetic
in-memory PDFs built with fitz. Run directly::

    ./tests/test_offset.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import fitz

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import pdf2md  # noqa: E402

score = pdf2md._score_offset_votes
detect = pdf2md._detect_page_offset
has_labels = pdf2md._has_labels
lookup = pdf2md._lookup_language


def check(label: str, actual, expected) -> bool:
    if actual == expected:
        print(f"  OK   {label}")
        return True
    print(f"  FAIL {label}: expected {expected!r}, got {actual!r}")
    return False


# ---------------------------------------------------------------------------
# Synthetic PDF builders
# ---------------------------------------------------------------------------

def _make_pdf(header_footer):
    """Build an in-memory PDF from a list of (header, footer) strings.

    Header lands at y=30 (top ~4%), footer at y=760 (bottom ~96%) on a
    standard 792pt-tall page — well inside the 12% margin bands that
    ``_page_margin_lines`` considers header/footer territory.
    """
    doc = fitz.open()
    for header, footer in header_footer:
        page = doc.new_page(width=612, height=792)
        if header:
            page.insert_text(fitz.Point(72, 30), header)
        if footer:
            page.insert_text(fitz.Point(72, 760), footer)
    return doc


def _make_blank_pdf(n_pages: int):
    doc = fitz.open()
    for _ in range(n_pages):
        doc.new_page(width=612, height=792)
    return doc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def main() -> int:
    failures = 0

    def t(label: str, actual, expected) -> None:
        nonlocal failures
        if not check(label, actual, expected):
            failures += 1

    # --- _score_offset_votes: pure confidence gate -------------------------
    print("_score_offset_votes")

    # No votes at all.
    t("empty votes → no_candidates", score(Counter(), 0), (None, "no_candidates"))

    # Single winner, 100% confidence.
    t(
        "unanimous 10/10 → detected",
        score(Counter({4: 10}), 10),
        (4, "detected"),
    )

    # Majority rule: 6/10 is >=50%, accept regardless of support size.
    t(
        "majority 6/10 → detected",
        score(Counter({4: 6, 0: 4}), 10),
        (4, "detected"),
    )
    # Exact 50% confidence still >= 0.5, so the short-circuit accepts.
    t(
        "exact 5/10 → detected",
        score(Counter({4: 5, 0: 5}), 10),
        (4, "detected"),
    )

    # Minority but strong: support >= 5 AND runner-up <= half of support.
    t(
        "6/20 with runner-up 2 → detected",
        score(Counter({4: 6, 0: 2, 1: 1}), 20),
        (4, "detected"),
    )
    t(
        "5/20 with runner-up 2 → detected (runner_up == 0.4*support)",
        score(Counter({4: 5, 0: 2}), 20),
        (4, "detected"),
    )

    # Minority and too weak.
    t(
        "4/20 support<5 → low_confidence",
        score(Counter({4: 4, 0: 2, 1: 1}), 20),
        (None, "low_confidence"),
    )
    t(
        "6/20 but runner-up 4 → low_confidence (runner_up > 0.5*support)",
        score(Counter({4: 6, 0: 4}), 20),
        (None, "low_confidence"),
    )

    # --- _lookup_language: insertion-order contract ------------------------
    print("\n_lookup_language (LANGUAGES order is load-bearing)")

    # Bare "zh" must map to zh-Hant because zh-Hant appears first in
    # LANGUAGES. Alphabetizing the table would silently flip this.
    t("bare 'zh' → chi_tra", lookup("zh").tesseract, "chi_tra")
    t("bare 'en' → eng", lookup("en").tesseract, "eng")

    # Exact keys take precedence over prefix fallback.
    t("exact 'zh-Hans' → chi_sim", lookup("zh-Hans").tesseract, "chi_sim")
    t("exact 'en-GB' → eng", lookup("en-GB").tesseract, "eng")

    # Unknown codes return None.
    t("unknown 'xx-YY' → None", lookup("xx-YY"), None)

    # --- _detect_page_offset: end-to-end with synthetic PDFs ---------------
    print("\n_detect_page_offset (synthetic PDFs)")

    # Offset +4: physical page 1 is printed "5". Every page votes the
    # same offset, confidence is 100%.
    doc = _make_pdf([("", str(5 + i)) for i in range(10)])
    try:
        t("footer 5..14 → (+4, detected)", detect(doc), (4, "detected"))
    finally:
        doc.close()

    # Offset 0: physical = printed.
    doc = _make_pdf([("", str(1 + i)) for i in range(5)])
    try:
        t("footer 1..5 → (0, detected)", detect(doc), (0, "detected"))
    finally:
        doc.close()

    # No text at all: blank pages → no candidates.
    doc = _make_blank_pdf(4)
    try:
        t("blank pages → no_candidates", detect(doc), (None, "no_candidates"))
    finally:
        doc.close()

    # Scattered noise: each page shows a random-looking number with no
    # consistent offset. Only 3 pages → support<5 on any winner.
    doc = _make_pdf([("", "7"), ("", "42"), ("", "13")])
    try:
        result = detect(doc)
        t("scattered 3 pages → low_confidence", result[1], "low_confidence")
    finally:
        doc.close()

    # Noise suppression: every page has a year in the header plus real
    # footer numbers. The year produces a drifting offset (year-(i+1))
    # that doesn't cluster; the footer offset clusters at +9 and wins.
    doc = _make_pdf([("Published 2023", str(10 + i)) for i in range(8)])
    try:
        t(
            "header year + real footer 10..17 → (+9, detected)",
            detect(doc),
            (9, "detected"),
        )
    finally:
        doc.close()

    # Chapter-header noise: every page shows "Chapter 3" plus the real
    # footer number. "3" is a valid integer and would vote offsets
    # (3 - (i+1)) that drift with physical index; the footer offset
    # clusters at a single value and wins. Footer numbers kept <= 100
    # to stay inside the max_page plausibility bound for a 10-page doc
    # (max(n_pages*2, 100) = 100).
    doc = _make_pdf([("Chapter 3", str(50 + i)) for i in range(10)])
    try:
        t(
            "header 'Chapter 3' + footer 50..59 → (+49, detected)",
            detect(doc),
            (49, "detected"),
        )
    finally:
        doc.close()

    # --- _has_labels: trivial vs real --------------------------------------
    print("\n_has_labels")

    # Default: no labels set on the doc.
    doc = _make_blank_pdf(3)
    try:
        t("unlabeled PDF → False", has_labels(doc), False)
    finally:
        doc.close()

    # Decimal labels starting at 1 — equal to str(i+1), so trivial.
    doc = _make_blank_pdf(3)
    try:
        doc.set_page_labels([
            {"startpage": 0, "prefix": "", "style": "D", "firstpagenum": 1},
        ])
        t("1-indexed decimal labels → False (trivial)", has_labels(doc), False)
    finally:
        doc.close()

    # Roman labels — not mechanical, real labels.
    doc = _make_blank_pdf(3)
    try:
        doc.set_page_labels([
            {"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
        ])
        t("roman labels → True", has_labels(doc), True)
    finally:
        doc.close()

    # Mixed: 2 roman front-matter pages, then arabic. Any non-trivial
    # label means True.
    doc = _make_blank_pdf(5)
    try:
        doc.set_page_labels([
            {"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
            {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 1},
        ])
        t("mixed roman+arabic → True", has_labels(doc), True)
    finally:
        doc.close()

    print(f"\n{failures} failures" if failures else "\nall passing")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
