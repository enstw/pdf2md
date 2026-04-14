# refs-transcription-benchmark.md

> **Status (2026-04-14):** resolved. **3p × parallel fan-out** adopted
> as the default in `refs-transcription-protocol.md`. This file is
> retained as a decision record and raw-data archive in case the
> protocol needs to be re-benchmarked.

Benchmark comparing subagent-delegated transcription workflows on the
same PDF. Parent Claude offloads the vision work to `general-purpose`
subagents; the variables are (a) how each subagent batches its
`Read` + `Write` calls and (b) whether one long-running subagent or
many parallel short-running subagents do the work.

## Test subject

- PDF: `refs/09-10 美日安保與亞太安全.pdf`
- 31 physical pages, printed pp. 275–305
- 楊永明, "美日安保與亞太安全", 政治科學論叢 第九期 (1998)
- Mixed Chinese body with Japanese footnote titles

## Variants

**A. Per-page** — subagent loops over physical pages 2–31, each
iteration is one `Read pages:"K"` + one `Write refs/<name>/pNNN.md`.
Aggregated afterwards by `scripts/combine-ref-pages.sh`.

**B. Per-20-pages** — subagent calls `Read pages:"1-20"`, then
`Read pages:"21-31"`, holds the full transcription in working memory,
writes the whole book with one `Write refs/<name>.md`. (Not "whole book
in one Read" — the `Read` tool caps at 20 pages per call, so even the
maximal-batch variant needs ⌈pages/20⌉ reads.)

**C. 3 pages/subagent, parallel fan-out** — parent spawns 11
subagents concurrently (⌈31/3⌉). Each reads its 3-page range and
writes 3 per-page files to `refs/<name>-benchmark-3p/pNNNN.md`.

**D. 5 pages/subagent, parallel fan-out** — parent spawns 7
subagents concurrently (⌈31/5⌉). Each reads its 5-page range and
writes 5 per-page files to `refs/<name>-benchmark-5p/pNNNN.md`.

For C and D, "duration" is wall-clock (max across parallel subagents),
"tool calls" and "tokens" are sums across all subagents.

## Results

| Metric                      | A. Per-page  | B. Per-20-pages | C. 3p × 11 parallel | D. 5p × 7 parallel |
|----------------------------|--------------|-----------------|---------------------|--------------------|
| Subagents                  | 1            | 1               | 11                  | 7                  |
| Tool calls (total)         | 62           | 3               | 53                  | 43                 |
| Subagent tokens (total)    | 100,372      | 86,798          | 274,586             | 202,196            |
| Duration (wall-clock)      | ~16.5 min    | ~10.6 min       | ~1.6 min            | ~3.9 min           |
| Files written              | 30 (+1 pre-existing) | 1        | 31                  | 31                 |
| Explicit illegible flags   | 7 pages      | 0               | 3 pages             | 1 page             |
| Silent elisions observed   | —            | JP footnote glyphs on notes 16/17/20/23/31 | not yet spot-checked | not yet spot-checked |
| Interrupt recovery         | save-point per page | all-or-nothing | per-chunk save-points | per-chunk save-points |
| Parent context offload     | full         | full            | full                | full               |

Among sequential (A, B), per-20-pages is ~40% faster and ~14% cheaper
in tokens. Parallel fan-out (C, D) trades tokens for wall-clock:
3p×11 finishes in ~1.6 min (10× faster than B) but burns 3.2× the
tokens, because each subagent pays its own context-setup overhead.
5p×7 lands in the middle: ~3.9 min and 2.3× B's tokens. All variants
equally protect parent context — the book stays inside the subagents.

## Quality note

Illegibility flag counts (higher = more honest, assuming the book has
*some* hard spots): A=7, B=0, C=3, D=1. B reported zero WARNINGs but
admitted silent elision of Japanese footnote glyphs on notes
16/17/20/23/31. Parallel fan-out (C, D) sits between A and B on
explicit flags — not yet spot-checked whether the unflagged pages
transcribed JP glyphs cleanly or silently elided them.

## Recommendation

Default: **3p × parallel fan-out** when wall-clock matters. ~1.6 min
for 31 pages is hard to beat, per-page save-points come for free, and
the ~3× token premium vs. per-20-pages is acceptable for any PDF
worth transcribing interactively.

Use **per-20-pages** single subagent when:
- Token cost matters more than wall-clock (batch/background job)
- You're bulk-converting many PDFs in one session and want to keep
  total spend down

Use **5p × parallel fan-out** when you want a middle ground —
fewer concurrent subagents (less orchestration overhead) while still
finishing in minutes.

Use **per-page single subagent** only when:
- PDF > ~40 pages AND you specifically need one resumable stream
  with visible per-page progress (rare)
- Most of those benefits are now covered by parallel fan-out variants

## Raw data

- **A. Per-page** (1 subagent): 62 tool_uses, 100,372 total_tokens, 988,843 ms wall
- **B. Per-20-pages** (1 subagent): 3 tool_uses, 86,798 total_tokens, 638,362 ms wall
- **C. 3p × 11 parallel**: 53 tool_uses (sum), 274,586 total_tokens (sum), 96,270 ms wall (max)
  - Per-subagent range: 2–6 tool uses, 19k–26k tokens, 18–96s
- **D. 5p × 7 parallel**: 43 tool_uses (sum), 202,196 total_tokens (sum), 234,064 ms wall (max)
  - Per-subagent range: 2–8 tool uses, 19k–31k tokens, 21–234s
- All runs: `general-purpose` subagent, same PDF (`refs/09-10 美日安保與亞太安全.pdf`),
  same protocol instructions (adapted for batching / parallelism)
- Per-page outputs from A retained at `refs/09-10 美日安保與亞太安全.md` (aggregated).
- B output retained at `refs/09-10 美日安保與亞太安全--benchmark.md` for
  side-by-side comparison.
- C output at `refs/09-10-benchmark-3p/pNNNN.md` (4-digit padding).
- D output at `refs/09-10-benchmark-5p/pNNNN.md` (4-digit padding).
- Delete C, D, and B benchmark outputs after quality review.
