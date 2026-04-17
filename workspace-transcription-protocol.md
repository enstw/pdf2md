# workspace-transcription-protocol.md

Protocol for transcribing reference materials in `workspace/` to Markdown so
that citation locators (`p.`, `pp.`, `para.`) can be verified during
writing without re-rendering PDFs on every lookup.

The transcribed `.md` is consumed by a downstream AI reader, not a
human, so "quality" means "a language model can still extract the
right claim and page from this file" — not "pixel-perfect typography."

Two-tier flow:

1. **Default path** — `./pdf2md.py` extracts text via
   `pymupdf4llm` with a per-page tesseract OCR fallback for gibberish
   pages. Zero vision tokens; fast.
2. **Fallback path** — if the quality check on the `pdf2md.py` output
   fails, re-transcribe the PDF through Claude's vision via the
   parallel-subagent fan-out described below.

Most PDFs (born-digital journal articles, clean scans) pass the first
tier. Vision is reserved for PDFs that genuinely need it.

## When this protocol runs

**Paper-branch startup** — when the agent starts work on a paper
branch, after reading `PROGRESS.md` but **before** any writing,
bulk-convert every `workspace/*.pdf` that has no matching `workspace/<name>.md`.
Pay the vision cost once (only on the PDFs that need it) so every
later citation lookup is a cheap plaintext read.

## Per-PDF procedure

1. **Pick the output name.** Use the PDF stem:
   `workspace/<pdf-basename>.md`.

2. **Run `./pdf2md.py` (default path).**
   ```
   ./pdf2md.py workspace/<pdf>.pdf workspace/<name>.md
   ```
   `pdf2md.py` is a PEP 723 single-file script — the shebang invokes
   it with `uv run --script`, so dependencies are resolved in a
   throwaway uv env on first run. **Do not** `pip install` the deps,
   and do not invoke with `python pdf2md.py`; always run the
   script directly so the uv-managed env is used.

   The script writes `**[Page N start]**` markers using each page's
   printed label (auto-detecting the physical→printed offset from
   headers/footers), and falls back through tiered extraction:
   `pymupdf4llm` → raw `pymupdf` → OCR (Apple Vision on macOS,
   `ocrmypdf` + tesseract elsewhere). Each page is annotated with
   the tier that produced its text, which the quality check in step
   3 uses as a hint.

   Default languages are `zh-Hant,en-US`. Pass `--langs` for other
   sources, `--offset N` to override the auto-detected page offset,
   or `--force-ocr` to bypass the text-layer tiers entirely.

3. **Quality check (graded, 3-page sample).**
   - Pick three physical pages: first content page, middle page,
     last content page. Skip covers and blank versos.
   - Spawn one `general-purpose` subagent. It `Read`s those three
     pages from the PDF, reads the corresponding page spans in
     `workspace/<name>.md`, and returns one of:
     - `PASS` — downstream AI can recover the printed text from the
       `.md` (minor whitespace/ligature noise OK, tables as fences
       OK, Chinese punctuation variance OK).
     - `FAIL: <reason>` — broken structure (missing pages, page
       markers misaligned), systematic OCR garble, or key content
       (footnotes, tables, quote marks) unreadable.
   - The subagent returns only the verdict + reason to the parent.
     Transcribed content never enters parent context.

4. **If PASS, stop.** Commit `workspace/<name>.md`. Done.

5. **If FAIL, fall back to vision.** Delete `workspace/<name>.md` and run
   the [Vision fan-out fallback](#vision-fan-out-fallback) below.

6. **Spot-check the final output** (regardless of which path
   produced it):
   - Page markers are monotonic (no backwards jumps except at known
     front-matter → body transitions).
   - The first and last visible printed page numbers match what the
     PDF shows.
   - Footnote numbers are preserved and their text is captured.

## Vision fan-out fallback

Only runs when step 3 returned FAIL. Produces the same
`workspace/<name>.md` format as `pdf2md.py` (same page-marker convention)
so downstream consumers don't care which path ran.

1. **Fan out to parallel subagents, 3 pages per subagent.**
   - Parent spawns `⌈pages/3⌉` `general-purpose` subagents
     concurrently in a single tool-call batch. Each subagent handles
     one contiguous 3-page range (the last one may be 1–3 pages).
   - Each subagent calls `Read pages:"X-Y"` once for its 3-page
     range, transcribes all 3 pages, then issues 3 separate
     `Write workspace/<name>/pNNNN.md` calls — one per printed page,
     4-digit zero-padded (`p0001.md`, `p0275.md`).
   - Per-page `Write`s preserve save-points (a subagent that dies
     mid-range still leaves completed pages on disk) while the
     single 3-page `Read` amortizes vision-call overhead.
   - Subagents return only a minimal status line to the parent
     (e.g. `done: physical 4-6, 3 files, 0 illegible, p0278-p0280`)
     so transcribed content never enters parent context.
   - Rationale: benchmarked against sequential per-page and
     per-20-page variants on a 31-page PDF, 3p × parallel finished
     in ~1.6 min vs. ~10.6 min (per-20-page) and ~16.5 min
     (per-page). See `workspace-transcription-benchmark.md`.

2. **Transcribe each page as Markdown**, preserving:
   - Headings and subheadings
   - Paragraphs (one blank line between)
   - Footnotes (as `¹ ...` / `² ...` inline, keeping the superscript
     reference in the body text)
   - Block quotes (as `> ...`)
   - Lists (ordered or unordered, matching the original)
   - Tables (GFM pipe-table syntax)
   - Inline emphasis (*italic*, **bold**) where visible
   - Romanized proper names and English inline terms exactly as
     printed

3. **Insert page markers.**
   - At each page boundary, insert `**[Page N start]**` using the
     **printed** page number visible in the page's header or footer
     — *not* the physical PDF index. For journal reprints this is
     often something like 275, 276, … rather than 1, 2, ….
   - If a page has no printed page number (title page, blank verso,
     figure-only page, cover), continue the previous page's
     numbering with `+1` and annotate the inference:
     `**[Page 276 start]** <!-- inferred, no printed number -->`
   - Front matter with roman numerals (i, ii, iii, …) should use
     the roman numerals exactly as printed.

4. **After the last page, combine.** Run:
   ```
   ./scripts/combine-workspace-pages.sh "<name>"
   ```
   This concatenates `workspace/<name>/p*.md` (lexical = numeric because
   of zero-padding) into `workspace/<name>.md` and removes the per-page
   directory.

## Edge cases

- **Paywalled or inaccessible source**: note that in the `.md` file
  and include whatever metadata is available (title, author, year,
  abstract). Do not fabricate content.
- **Scanned PDF with illegible pages**: transcribe what you can
  read; mark unreadable spans with
  `*[WARNING: illegible span on page N.]*` and continue. Do not guess.
- **Mixed-language document**: transcribe in the original language
  exactly as printed. Do not translate.
- **Non-PDF source** (DOCX, HTML save, EPUB): fetch the fulltext
  through the appropriate tool and save to `workspace/<name>.md`
  preserving paragraph structure. Page markers don't apply unless
  the source has them.

## Bulk-convert on paper-branch startup

```
1. List workspace/*.pdf
2. For each PDF:
     - Determine output name (PDF stem)
     - If workspace/<name>.md already exists and is non-empty, skip
     - Otherwise run the per-PDF procedure above (pdf2md.py →
       quality check → vision fallback if needed)
3. Commit the new .md files alongside the PDFs in a single commit
   with message like `workspace: transcribe N PDFs before writing`
```

Do not start writing paper content until this step is complete.

## Progress tracking

One task per PDF. Mark `in_progress` before running `pdf2md.py`,
`completed` once the quality check passes (or the vision fallback
finishes and its spot-check passes). Record the path taken in the
task title so skim-review of what needed vision is easy:
`Transcribe <name> (pdf2md)` vs. `Transcribe <name> (vision)`.

## Why this protocol exists

Re-reading a PDF during writing costs 1,500–3,000 text tokens *plus*
image tokens per page, every time. Reading the pre-transcribed
Markdown costs only the text tokens and bypasses the vision pipeline
entirely. For a thesis with 30+ references this is the difference
between a writing session that fits in context and one that doesn't.

The hybrid flow keeps the common case (clean PDFs) on the cheap
`pdf2md.py` path and only pays vision tokens for the sources that
actually need them.
