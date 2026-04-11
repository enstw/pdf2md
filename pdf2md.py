#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "pymupdf4llm",
#   "pymupdf",
#   "ocrmypdf; sys_platform != 'darwin'",
#   "pyobjc-framework-Vision; sys_platform == 'darwin'",
#   "pyobjc-framework-Cocoa; sys_platform == 'darwin'",
# ]
# ///
"""
PDF → Markdown with page markers and a tiered extraction strategy.

For each page we try, in order:
  1. pymupdf4llm  — structured Markdown (best case, preserves headings/tables)
  2. raw pymupdf  — plain page.get_text(), for PDFs whose text layer is fine
                    but confuses pymupdf4llm's layout heuristics
  3. OCR          — only when no usable text layer exists:
                      - macOS: Apple Vision (VNRecognizeTextRequest)
                      - Linux/other: ocrmypdf whole-PDF preprocess (deskew +
                        tesseract), then re-extract with tiers 1+2

Each page is annotated in the output with the tier that produced its text.
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import fitz
import pymupdf4llm

IS_MACOS = sys.platform == "darwin"

# BCP-47 (Vision) → ISO 639-3 (tesseract). Extend as needed.
_TESSERACT_LANG = {
    "zh-Hant": "chi_tra",
    "zh-Hans": "chi_sim",
    "en-US": "eng",
    "en-GB": "eng",
    "ja-JP": "jpn",
    "ko-KR": "kor",
    "fr-FR": "fra",
    "de-DE": "deu",
    "es-ES": "spa",
    "it-IT": "ita",
    "pt-BR": "por",
    "ru-RU": "rus",
}


# Unicode ranges per script family. Latin covers ASCII letters +
# Latin-1 Supplement + Latin Extended A/B (enough for most European
# languages). CJK ranges cover Unified Ideographs + Extension A +
# Compatibility Ideographs (Preview sometimes emits the latter).
_SCRIPT_RANGES: dict[str, list[tuple[int, int]]] = {
    "latin": [(0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F)],
    "cjk":   [(0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF)],
    "kana":  [(0x3040, 0x309F), (0x30A0, 0x30FF)],
    "hangul": [(0xAC00, 0xD7AF)],
    "cyrillic": [(0x0400, 0x04FF)],
}

# BCP-47 language prefix → list of _SCRIPT_RANGES keys.
_LANG_SCRIPTS: dict[str, list[str]] = {
    "zh": ["cjk"],
    "ja": ["cjk", "kana"],
    "ko": ["hangul", "cjk"],
    "ru": ["cyrillic"],
    "en": ["latin"], "fr": ["latin"], "de": ["latin"], "es": ["latin"],
    "it": ["latin"], "pt": ["latin"], "nl": ["latin"], "sv": ["latin"],
    "pl": ["latin"], "tr": ["latin"],
}


def _script_ranges_for_langs(langs: list[str]) -> list[tuple[int, int]]:
    seen: set[str] = set()
    ranges: list[tuple[int, int]] = []
    for lg in langs:
        prefix = lg.split("-", 1)[0].lower()
        for key in _LANG_SCRIPTS.get(prefix, []):
            if key in seen:
                continue
            seen.add(key)
            ranges.extend(_SCRIPT_RANGES[key])
    return ranges


def is_mostly_gibberish(text: str, langs: list[str] | None = None) -> bool:
    """Return True iff ``text`` is too unlike any expected script to trust.

    Rejects in order:

    1. Empty / whitespace-only text.
    2. Text with fewer than 5 alphanumeric characters — pymupdf4llm
       sometimes returns residual markdown markers like ``"##"`` for
       image-dominant pages; we don't want those to masquerade as
       successful extraction.
    3. Short texts (<50 non-whitespace chars) that cleared the alnum
       check are trusted — too little signal for script analysis.
    4. Longer texts are checked against the Unicode ranges of every
       language in ``langs`` (Latin for en/fr/de/..., CJK for zh/ja,
       Hangul for ko, Cyrillic for ru). A text is gibberish iff *none*
       of the expected scripts cover at least 20% of non-whitespace
       characters.
    """
    if not text:
        return True
    non_ws = [c for c in text if not c.isspace()]
    total = len(non_ws)
    if total == 0:
        return True

    alnum = sum(1 for c in non_ws if c.isalnum())
    if alnum < 5:
        return True

    if total < 50:
        return False

    ranges = _script_ranges_for_langs(langs or [])
    if not ranges:
        return False

    hits = 0
    for c in non_ws:
        o = ord(c)
        for lo, hi in ranges:
            if lo <= o <= hi:
                hits += 1
                break
    return hits / total < 0.2


# ---------------------------------------------------------------------------
# OCR backends
# ---------------------------------------------------------------------------

def _ocr_page_vision(page: fitz.Page, langs: list[str], zoom: float = 3.0) -> str:
    """OCR one page via Apple Vision (VNRecognizeTextRequest). macOS only."""
    import Vision  # pyobjc-framework-Vision
    from Foundation import NSData  # pyobjc-framework-Cocoa

    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    png_bytes = pix.tobytes("png")
    ns_data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLanguages_(langs)
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        ns_data, None
    )
    success, err = handler.performRequests_error_([request], None)
    if not success:
        raise RuntimeError(f"Vision performRequests failed: {err}")

    lines: list[str] = []
    for obs in request.results() or []:
        candidates = obs.topCandidates_(1)
        if candidates:
            lines.append(str(candidates[0].string()))
    return "\n".join(lines)


@contextmanager
def _ocrmypdf_preprocess(src: Path, langs: list[str], force_ocr: bool):
    """Yield a Path to an OCR-augmented copy of `src` (Linux/other path).

    --skip-text: pages with an existing text layer pass through unchanged;
    only scanned pages get OCR'd. --force-ocr: rasterize and OCR every page.
    """
    import ocrmypdf

    tess_langs = "+".join(_TESSERACT_LANG.get(lg, lg) for lg in langs)
    with tempfile.TemporaryDirectory(prefix="pdf2md_ocrmypdf_") as tmp:
        out = Path(tmp) / f"{src.stem}.ocr.pdf"
        kwargs: dict = dict(
            language=tess_langs,
            output_type="pdf",
            progress_bar=False,
            deskew=True,
        )
        if force_ocr:
            kwargs["force_ocr"] = True
        else:
            kwargs["skip_text"] = True
        try:
            ocrmypdf.ocr(str(src), str(out), **kwargs)
        except ocrmypdf.exceptions.MissingDependencyError as e:
            print(
                f"ocrmypdf missing dependency: {e}\n"
                f"Install tesseract + language data:\n"
                f"  Ubuntu/Debian: sudo apt install tesseract-ocr "
                f"tesseract-ocr-chi-tra tesseract-ocr-eng\n"
                f"  Fedora: sudo dnf install tesseract "
                f"tesseract-langpack-chi_tra tesseract-langpack-eng",
                file=sys.stderr,
            )
            raise
        yield out


# ---------------------------------------------------------------------------
# Tiered per-page extraction
# ---------------------------------------------------------------------------

def _extract_page(
    doc: fitz.Document,
    physical_idx: int,
    md_text: str,
    per_page_ocr,  # callable(fitz.Page) -> str, or None
    force_ocr: bool,
    langs: list[str],
) -> tuple[str, str]:
    """Return (text, tier) for one page. Tier ∈ {pymupdf4llm, pymupdf, ocr, empty}."""
    if not force_ocr:
        # Tier 1: pymupdf4llm
        t1 = (md_text or "").strip()
        if t1 and not is_mostly_gibberish(t1, langs):
            return t1, "pymupdf4llm"

        # Tier 2: raw pymupdf text
        t2 = (doc[physical_idx].get_text() or "").strip()
        if t2 and not is_mostly_gibberish(t2, langs):
            return t2, "pymupdf"
    else:
        t1 = (md_text or "").strip()
        t2 = (doc[physical_idx].get_text() or "").strip()

    # Tier 3: OCR
    if per_page_ocr is not None:
        try:
            ocr_text = (per_page_ocr(doc[physical_idx]) or "").strip()
            if ocr_text:
                return ocr_text, "ocr"
        except Exception as e:
            print(
                f"  - OCR failed on page index {physical_idx}: {e}",
                file=sys.stderr,
            )

    # Nothing worked cleanly. Return whatever non-empty text we've got, or
    # an empty string if the page is truly blank.
    fallback = t1 or t2
    return fallback, ("pymupdf4llm" if fallback == t1 and t1 else
                      "pymupdf" if fallback else "empty")


def _needs_ocr_scan(
    doc: fitz.Document, md_chunks: list[dict], langs: list[str]
) -> bool:
    """Do any pages fail both tier 1 and tier 2? (Pre-OCR scan.)"""
    for chunk in md_chunks:
        idx = chunk["metadata"].get("page_number", 1) - 1
        t1 = (chunk.get("text") or "").strip()
        if t1 and not is_mostly_gibberish(t1, langs):
            continue
        t2 = (doc[idx].get_text() or "").strip()
        if not t2 or is_mostly_gibberish(t2, langs):
            return True
    return False


# ---------------------------------------------------------------------------
# Markdown writer
# ---------------------------------------------------------------------------

def _write_markdown(
    extract_pdf: Path,
    label_pdf: Path,
    output_md: str,
    page_offset: int,
    force_ocr: bool,
    per_page_ocr,  # callable(fitz.Page) -> str, or None
    backend_label: str,
    langs: list[str],
):
    doc = fitz.open(str(extract_pdf))
    label_doc = doc if label_pdf == extract_pdf else fitz.open(str(label_pdf))
    # use_ocr=False: prevent pymupdf4llm from silently invoking tesseract on
    # pages where layout analysis can't find text. We want tier 1 to mean
    # "text layer was present and readable", nothing else — any OCR happens
    # explicitly at tier 3 (Vision on macOS, ocrmypdf elsewhere) so the tier
    # annotation in the output reflects reality.
    md_chunks = pymupdf4llm.to_markdown(doc, page_chunks=True, use_ocr=False)

    with open(output_md, "w", encoding="utf-8") as f:
        f.write(f"<!-- pdf2md: platform={sys.platform} ocr={backend_label} -->\n\n")
        prev_label: str | None = None

        for chunk in md_chunks:
            physical_idx = chunk["metadata"].get("page_number", 1) - 1

            try:
                current_label = label_doc[physical_idx].get_label()
            except Exception:
                current_label = None
            if not current_label:
                current_label = str(physical_idx + 1)

            if page_offset:
                try:
                    current_label = str(int(current_label) + page_offset)
                except ValueError:
                    pass  # non-numeric label (e.g. roman) — leave as-is

            if prev_label is None:
                f.write(f"**[Page {current_label} start]**\n\n")
            else:
                f.write(
                    f"\n\n**[Page {prev_label} end, "
                    f"Page {current_label} start]**\n\n"
                )

            text, tier = _extract_page(
                doc,
                physical_idx,
                chunk.get("text", ""),
                per_page_ocr=per_page_ocr,
                force_ocr=force_ocr,
                langs=langs,
            )

            if tier != "pymupdf4llm":
                f.write(f"<!-- tier={tier} -->\n")

            if not text:
                f.write(f"*[WARNING: No text found on page {current_label}.]*\n")
            else:
                f.write(text)

            prev_label = current_label

        if prev_label is not None:
            f.write(f"\n\n**[Page {prev_label} end]**\n")


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------

def convert_with_transition_markers(
    pdf_path: str,
    output_md_path: str,
    page_offset: int = 0,
    force_ocr: bool = False,
    langs: list[str] | None = None,
):
    langs = langs or ["zh-Hant", "en-US"]
    src = Path(pdf_path)

    # macOS: Vision is cheap and per-page; no preprocessing needed.
    if IS_MACOS:
        backend = f"Apple Vision ({','.join(langs)})"
        print(f"[pdf2md] backend={backend}", file=sys.stderr)
        _write_markdown(
            extract_pdf=src,
            label_pdf=src,
            output_md=output_md_path,
            page_offset=page_offset,
            force_ocr=force_ocr,
            per_page_ocr=lambda page: _ocr_page_vision(page, langs),
            backend_label=backend,
            langs=langs,
        )
        return

    # Linux/other: scan first; only invoke ocrmypdf if actually needed.
    if force_ocr:
        needs_ocr = True
    else:
        probe = fitz.open(str(src))
        try:
            probe_chunks = pymupdf4llm.to_markdown(
                probe, page_chunks=True, use_ocr=False
            )
            needs_ocr = _needs_ocr_scan(probe, probe_chunks, langs)
        finally:
            probe.close()

    if not needs_ocr:
        backend = "none (text layer already clean)"
        print(f"[pdf2md] backend={backend}", file=sys.stderr)
        _write_markdown(
            extract_pdf=src,
            label_pdf=src,
            output_md=output_md_path,
            page_offset=page_offset,
            force_ocr=False,
            per_page_ocr=None,
            backend_label=backend,
            langs=langs,
        )
        return

    mode = "force-ocr" if force_ocr else "skip-text"
    backend = f"ocrmypdf {mode} ({','.join(langs)})"
    print(f"[pdf2md] backend={backend}", file=sys.stderr)
    with _ocrmypdf_preprocess(src, langs, force_ocr=force_ocr) as ocr_pdf:
        # After ocrmypdf, scanned pages have a text layer — tiers 1+2 will
        # pick it up. per_page_ocr is None: no further re-OCR on Linux.
        _write_markdown(
            extract_pdf=ocr_pdf,
            label_pdf=src,  # preserve original page labels
            output_md=output_md_path,
            page_offset=page_offset,
            force_ocr=False,
            per_page_ocr=None,
            backend_label=backend,
            langs=langs,
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert PDF to Markdown with page markers. "
                    "Tiered extraction: pymupdf4llm → pymupdf → OCR "
                    "(Apple Vision on macOS, ocrmypdf on Linux).",
    )
    parser.add_argument("input", help="Input PDF path")
    parser.add_argument("output", help="Output Markdown path")
    parser.add_argument(
        "--offset", type=int, default=0,
        help="Page number offset (printed_page = physical + offset)",
    )
    parser.add_argument(
        "--force-ocr", action="store_true",
        help="Force OCR on every page, ignoring any existing text layer",
    )
    parser.add_argument(
        "--langs", default="zh-Hant,en-US",
        help="Comma-separated BCP-47 language codes (default: zh-Hant,en-US)",
    )

    args = parser.parse_args()
    lang_list = [lg.strip() for lg in args.langs.split(",") if lg.strip()]

    convert_with_transition_markers(
        args.input,
        args.output,
        page_offset=args.offset,
        force_ocr=args.force_ocr,
        langs=lang_list,
    )
