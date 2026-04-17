# pdf2md

> **Status: superseded by Claude Code.** Since early 2025, [Claude
> Code](https://claude.com/claude-code)'s `Read` tool natively renders PDF
> pages and transcribes them through Claude's vision weights, producing
> Markdown output that is materially better than any local CPU pipeline on
> scanned or mixed-language documents — and requires zero install,
> zero model downloads, and no extra process. For new projects, prefer
> dropping PDFs into a Claude Code session (or scripting the headless
> `claude -p "..."` mode) instead of running this tool.
>
> `pdf2md` is still useful as a **fully offline, quota-free** path for
> clean text-layer PDFs where sub-second extraction matters: the tier-1
> `pymupdf4llm` route is ~50× faster than any vision-based approach
> (benchmarked 2026-04-12 against [Marker](https://github.com/VikParuchuri/marker)),
> runs without a network, and incurs no subscription usage. The rest of
> this README documents that offline path.

Convert PDFs to Markdown with page markers, with a tiered extraction strategy
that gracefully degrades from structured text to OCR on scanned pages.

Single-file PEP 723 script — run it with [`uv`](https://docs.astral.sh/uv/)
and dependencies are resolved automatically.

## Extraction tiers

For each page:

1. **`pymupdf4llm`** — structured Markdown (headings, lists, tables).
   Used when the PDF has a clean text layer.
2. **raw `pymupdf`** — plain `page.get_text()`.
   Used when `pymupdf4llm` garbles the output but the underlying text layer
   is actually fine (e.g., unusual font encodings or layout heuristics).
3. **OCR** — only when no usable text layer exists:
   - **macOS** → Apple [Vision framework][vision]
     (`VNRecognizeTextRequest`, the same engine Preview's Live Text uses).
     GPU-accelerated, no external binaries.
   - **Linux / other** → [`ocrmypdf`][ocrmypdf], which adds deskew + noise
     cleanup on top of tesseract.

Each page in the output is annotated with the tier that produced its text, so
you can audit where the extractor fell back.

[vision]: https://developer.apple.com/documentation/vision/vnrecognizetextrequest
[ocrmypdf]: https://ocrmypdf.readthedocs.io/

## Usage

```bash
# Basic — page offset is auto-detected from the header/footer
./pdf2md.py input.pdf output.md

# Override auto-detection: book with printed page 1 on the 275th physical page
./pdf2md.py input.pdf output.md --offset 274

# Force OCR on every page (ignore existing text layer)
./pdf2md.py input.pdf output.md --force-ocr

# Non-default languages (BCP-47 codes; mapped to tesseract on Linux)
./pdf2md.py input.pdf output.md --langs en-US,ja-JP

# Suppress **[Page N start]** / **[Page N end]** boundary markers
./pdf2md.py input.pdf output.md --no-page-markers
```

Default languages: `zh-Hant,en-US`.

When `--offset` is omitted, `pdf2md` scans the header/footer zone of every
page, extracts integer candidates, and mode-votes the most likely
`printed_page − physical_index` offset. The detector refuses to guess when
candidates are scattered or the PDF has no text layer, falling back to
physical page numbers. Pass any explicit `--offset N` (including `0`) to
disable auto-detection. When detection fires, the chosen offset is logged
to stderr as `[pdf2md] smart-offset=+N (...)` so it's auditable.

The `--langs` flag does two things: it tells the OCR backend what
scripts to expect (Vision recognition languages on macOS; tesseract
language packs on Linux), and it drives the tier-1/tier-2 "is this
text real content" heuristic. Passing only `en-US` on a Chinese
document will cause every tier-1 extraction to be flagged as
gibberish and fall through to OCR.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) to run the script
- **macOS**: macOS 13+ (Ventura) for Traditional Chinese support in Vision.
  No other setup — `pyobjc-framework-Vision` is pulled in automatically.
- **Linux**: `tesseract` + language data on `PATH`. Example for Ubuntu/Debian:
  ```bash
  sudo apt install tesseract-ocr tesseract-ocr-chi-tra tesseract-ocr-eng
  ```

## Tests

A smoke test suite in `tests/` runs `pdf2md.py` against four fixture
PDFs covering all three extraction tiers:

```bash
./tests/smoke.py           # all cases (~33s on macOS)
./tests/smoke.py --quick   # skip the scanned-OCR case (~18s)
./tests/smoke.py --keep    # preserve output markdown for inspection
```

See [`tests/README.md`](tests/README.md) for what each fixture covers.

## Transcription workflow

For a structured workflow that uses `pdf2md.py` as the cheap default
path and falls back to Claude Code vision (via parallel subagent
fan-out) on PDFs where the text layer isn't recoverable, see:

- [`refs-transcription-protocol.md`](refs-transcription-protocol.md)
  — per-PDF procedure, quality check, vision fan-out fallback, page
  marker conventions.
- [`refs-transcription-benchmark.md`](refs-transcription-benchmark.md)
  — decision record behind the 3p × parallel fan-out default
  (sequential vs. parallel variants benchmarked on a 31-page PDF).

The accompanying `scripts/combine-ref-pages.sh` concatenates per-page
markdown produced by the vision fallback into a single `refs/<name>.md`.

## License

MIT
