# AGENTS.md

Notes for AI agents working on this repo. The codebase is small enough
that you should read `pdf2md.py` in full before making changes — this
file only captures things that aren't obvious from the code.

## Overview

`pdf2md.py` is a single-file PEP 723 script. Three-tier per-page
extraction: `pymupdf4llm` → raw `pymupdf` → OCR (Apple Vision on macOS,
`ocrmypdf` on Linux). Each output page is annotated with the tier that
produced its text.

## Run it

```bash
./pdf2md.py input.pdf output.md
./pdf2md.py input.pdf output.md --offset 274 --langs zh-Hant,en-US
./pdf2md.py input.pdf output.md --force-ocr
```

Use `uv` for everything Python. Never `pip` / `pipx` / `poetry` /
`python -m venv`. Dependencies live in the inline `# /// script` block
with `sys_platform` markers — do not split them into a requirements
file or `pyproject.toml`.

## Test it

```bash
./tests/smoke.py --quick   # all fast cases (~18s)
./tests/smoke.py           # full suite (~33s, includes scanned OCR)
./tests/smoke.py --keep    # preserve output markdown for inspection
```

Run the full suite before committing any change to the extraction
pipeline. Fixture PDFs are committed under `tests/fixtures/` (~10 MB
total) — **do not add `*.pdf` to `.gitignore`**. See `tests/README.md`
for what each fixture covers.

## Don't break this

These invariants look like minor details but silently corrupt output
or tier annotations if changed:

- **`use_ocr=False` on both `pymupdf4llm.to_markdown()` calls is
  load-bearing.** Without it, pymupdf4llm silently invokes tesseract
  on image-dominant pages and returns the OCR output as if it were
  the text layer. Tier 1 would start absorbing hidden OCR and the
  `<!-- tier=... -->` annotations would become lies. A cleanup agent
  is very likely to delete this flag thinking it's a default — don't.

- **`is_mostly_gibberish` must stay language-aware and threaded
  through `_extract_page` / `_needs_ocr_scan` / `_write_markdown`.**
  The three thresholds (≥5 alphanumeric chars, ≥50 non-whitespace
  chars for script analysis, ≥20% script coverage) are empirically
  tuned against the fixtures. Don't adjust them without re-running
  `tests/smoke.py` on all four fixtures and confirming the tier
  distribution is unchanged.

- **Tier annotation comments (`<!-- tier=pymupdf -->` etc.) are part
  of the output contract.** Callers parse them. Don't strip them to
  "clean up" the markdown.

- **On the Linux/ocrmypdf path, `label_pdf` and `extract_pdf` are
  intentionally different in `_write_markdown`.** `ocrmypdf` can
  re-encode PDF page labels during OCR preprocessing, so we read
  content from the OCR'd copy but page labels from the original.
  Don't "simplify" them to be the same.

- **Single file, PEP 723.** Don't split `pdf2md.py` into a package,
  don't add `pyproject.toml`, don't break the
  `#!/usr/bin/env -S uv run --script` shebang.

## Open questions / known gaps

These are acknowledged gaps. Don't preemptively "fix" them; bring them
up with the user before investing in any of them.

- **No fixture exercises the Linux `ocrmypdf` path.** Only macOS
  Apple Vision is smoke-tested. If you have a Linux environment,
  running `--force-ocr` against the scanned Chinese fixture is a
  useful ad-hoc smoke test.

- **Tier 2 (raw `page.get_text()`) loses layout.** For JSTOR-style
  scans with hidden text layers, we drop all heading/italic structure.
  An improvement path is to coax `pymupdf4llm` into reading hidden
  text layers (maybe via `ignore_images=True` or similar) so Strange
  can return to tier 1 with structure intact. Attempt only with a
  plan and the full smoke suite ready to re-verify.

- **Competing with / forking `pymupdf4llm`** is aspirational, not the
  current direction. Don't vendor its layout logic, don't fork, don't
  rewrite the extraction core unless explicitly asked.

## Commit hygiene

Separate fixes from feature additions into distinct commits so each
can be reverted independently. See `git log` for the house style —
descriptive subject under ~70 chars, body explains the "why," not
just the "what."
