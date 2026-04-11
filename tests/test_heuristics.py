#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "pymupdf4llm",
#   "pymupdf",
# ]
# ///
"""Unit tests for pdf2md's pure heuristic functions.

These pin the empirically-tuned thresholds in ``is_mostly_gibberish``
and ``_script_ranges_for_langs`` so future refactors can't silently
shift them. Run directly::

    ./tests/test_heuristics.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import pdf2md  # noqa: E402

gib = pdf2md.is_mostly_gibberish
ranges_for = pdf2md._script_ranges_for_langs


# Representative text samples.
EN_LONG = (
    "The quick brown fox jumps over the lazy dog. " * 10
)  # ~460 chars, Latin-heavy
ZH_LONG = "日美同盟之發展與抉擇兼論台灣因應策略進入二十一世紀" * 5  # all CJK
JA_LONG = "これは日本語のテストです。かなと漢字が混ざっています。" * 5
KO_LONG = "대한민국 서울특별시에서 온 테스트 문장입니다. " * 5
RU_LONG = "Это тестовое предложение на русском языке. " * 5


def check(label: str, actual: bool, expected: bool) -> bool:
    if actual == expected:
        print(f"  OK   {label}")
        return True
    print(f"  FAIL {label}: expected {expected}, got {actual}")
    return False


def main() -> int:
    failures = 0

    def t(label: str, actual: bool, expected: bool) -> None:
        nonlocal failures
        if not check(label, actual, expected):
            failures += 1

    # --- Trivial rejections (no langs needed) ---
    print("trivial rejections")
    t("empty string", gib(""), True)
    t("whitespace only", gib("   \n\t  "), True)
    t('"##" (markdown residue)', gib("##"), True)
    t('"---" (markdown residue)', gib("---"), True)
    t("3 alnum chars", gib("abc"), True)
    t("exactly 5 alnum (threshold)", gib("abcde"), False)

    # --- Short-but-meaningful text is trusted ---
    print("\nshort meaningful")
    t('"# Chapter 5"', gib("# Chapter 5"), False)
    t('"Introduction"', gib("Introduction"), False)
    t('"Figure 1.2"', gib("Figure 1.2"), False)
    t("under 50 chars with content, no langs", gib("Hello world! 2025."), False)

    # --- Long English, right language ---
    print("\nlong English")
    t("en-US langs", gib(EN_LONG, ["en-US"]), False)
    t("en-GB langs", gib(EN_LONG, ["en-GB"]), False)
    t("fr-FR langs (Latin script shared)", gib(EN_LONG, ["fr-FR"]), False)
    t("multi langs [zh-Hant, en-US]", gib(EN_LONG, ["zh-Hant", "en-US"]), False)

    # --- Long English, wrong language ---
    print("\nEnglish but wrong declared language")
    t("zh-Hant only (no Latin expected)", gib(EN_LONG, ["zh-Hant"]), True)
    t("ja-JP only", gib(EN_LONG, ["ja-JP"]), True)
    t("ru-RU only", gib(EN_LONG, ["ru-RU"]), True)

    # --- Long CJK, right language ---
    print("\nlong Chinese/Japanese/Korean")
    t("Chinese with zh-Hant", gib(ZH_LONG, ["zh-Hant"]), False)
    t("Chinese with zh-Hans", gib(ZH_LONG, ["zh-Hans"]), False)
    t("Japanese with ja-JP", gib(JA_LONG, ["ja-JP"]), False)
    t("Korean with ko-KR", gib(KO_LONG, ["ko-KR"]), False)
    t("Russian with ru-RU", gib(RU_LONG, ["ru-RU"]), False)

    # --- CJK with wrong declared language ---
    print("\nCJK but wrong declared language")
    t("Chinese with en-US only", gib(ZH_LONG, ["en-US"]), True)
    t("Russian with en-US only", gib(RU_LONG, ["en-US"]), True)

    # --- Empty / unknown langs fall through to content-only check ---
    print("\nunknown / empty langs")
    t("empty langs list", gib(EN_LONG, []), False)  # no script filter
    t("None langs", gib(EN_LONG, None), False)
    t("unknown lang code", gib(EN_LONG, ["xx-YY"]), False)

    # --- Garbage symbol sequences with wrong language ---
    print("\ngarbage text")
    t(
        "symbol soup with zh-Hant",
        gib("░▒▓■□●○◆◇▲△▼▽★☆♠♣♥♦" * 20, ["zh-Hant"]),
        True,
    )
    t(
        "symbol soup with en-US",
        gib("░▒▓■□●○◆◇▲△▼▽★☆♠♣♥♦" * 20, ["en-US"]),
        True,
    )

    # --- _script_ranges_for_langs shape ---
    print("\n_script_ranges_for_langs")
    t("empty returns empty list", ranges_for([]) == [], True)
    t("en-US returns Latin ranges", len(ranges_for(["en-US"])) >= 1, True)
    t(
        "zh-Hant + en-US merges both scripts (no duplicates)",
        len(ranges_for(["zh-Hant", "en-US"]))
        == len(ranges_for(["zh-Hant"])) + len(ranges_for(["en-US"])),
        True,
    )
    t(
        "ja-JP includes both CJK and kana",
        len(ranges_for(["ja-JP"])) > len(ranges_for(["zh-Hant"])),
        True,
    )
    t("unknown lang ignored", ranges_for(["xx-YY"]) == [], True)

    # --- Real regression cases from the smoke suite ---
    print("\nsmoke-regression fixtures")
    # pymupdf4llm-returned "##" on image-dominant Strange pages
    t('Strange p1 tier1 "##"', gib("##", ["zh-Hant", "en-US"]), True)
    # Scanned-Chinese p3 tesseract-as-English garbage that used to pass
    garbage_zh_as_en = (
        "DAAMZER RAR SBA RRS 101 , 1950 ERBERMER > BUS LR SA IES "
        "ICS RA ANS SEN BAH» ANTS Al ANSE VE ES EBA ° 11 A SE BIEHN "
    ) * 3
    # This is 67% Latin letters — passes the relaxed 20% threshold, so
    # it WILL pass as "valid English". The real defense is that we
    # disable pymupdf4llm's hidden OCR upstream (use_ocr=False), not
    # is_mostly_gibberish. Documented here to make the non-coverage
    # explicit.
    t(
        "garbage-chinese-OCR'd-as-English is NOT rejected by gibberish alone",
        gib(garbage_zh_as_en, ["zh-Hant", "en-US"]),
        False,
    )

    print(f"\n{failures} failures" if failures else "\nall passing")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
