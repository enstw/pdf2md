# pdf2md tests

Smoke tests that run `pdf2md.py` against a small set of representative PDFs
and verify the output contains expected content.

```bash
./tests/smoke.py           # run all cases
./tests/smoke.py --quick   # skip the slow scanned-OCR case
./tests/smoke.py --keep    # keep output markdown for inspection
```

## Fixtures

Each PDF in `fixtures/` exercises a different branch of the tiered
extraction strategy:

| Fixture | What it exercises | Expected tier |
|---|---|---|
| `Farrell-WeaponizedInterdependence-2019.pdf` | Clean English text layer, standard JSTOR article | `pymupdf4llm` |
| `RL33740-US-Japan-Alliance.pdf` | Clean English text layer, CRS report with TOC and headings | `pymupdf4llm` |
| `Strange-PersistentMythLost-1987.pdf` | JSTOR page-image scan with a hidden text layer underneath. pymupdf4llm's layout analysis sees only the image and returns empty, but raw `page.get_text()` reads the text layer — exercises the tier-2 fallback. | `pymupdf` |
| `日美同盟-兼論台灣因應策略.pdf` | Fully scanned Traditional Chinese journal — no text layer at all, OCR required | `ocr` |

Together the four fixtures cover all three extraction tiers plus a
second sample for tier 1. The first three are fast (seconds); the
scanned Chinese PDF runs Apple Vision (macOS) or ocrmypdf (Linux) and
takes ~30s. Skip it with `--quick` during iteration.

## Tier expectations are soft checks

The tier each fixture lands on is an *implementation detail*, not a
contract. `smoke.py` will print a `WARN` if the dominant tier drifts
from the table above, but won't fail the suite — heuristics legitimately
evolve. Hard failures only happen when:

- `pdf2md.py` exits non-zero
- the output file isn't created
- expected substrings (title, author) are missing from the output

## Copyright note

`Farrell-*.pdf`, `Strange-*.pdf`, and `日美同盟-*.pdf` are third-party
academic works included here under fair use as test fixtures. They are
not redistributed as content, only used to verify extraction behavior.
`RL33740-*.pdf` is a Congressional Research Service report — U.S.
government work, not copyrighted.
