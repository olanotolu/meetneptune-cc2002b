# cc2002b

[![test](https://github.com/olanotolu/meetneptune-cc2002b/actions/workflows/test.yml/badge.svg)](https://github.com/olanotolu/meetneptune-cc2002b/actions/workflows/test.yml)

Take JSON. Get a flat, ink-on-paper NYC marriage-record request.

The input is ordinary JSON. The output is form CC2002B — page 2 of [this packet](00_packet/neptune-takehome-form-fill-packet%20(7).pdf) — filled in black ink, no fillable fields, no signature.

```text
JSON → schema check → business rules → fingerprint check → black ink → reopen and prove
```

No model in the hot path. No API at runtime. No OCR, LangChain, FastAPI, or sessions.

## What this does, once and at runtime

**Offline, once:**

- `00_packet/cc2002b_blank.pdf` is page 2 extracted with `pikepdf`.
- Field coordinates are frozen in `cc2002b.spec.json`.
- `tools/inspect_form.py` dumps word/glyph geometry if you ever need to re-derive.
- `tools/compile_form.py` is an experimental offline compiler. GPT-4o vision proposes a candidate spec, draws an overlay, and the same 3-layer checker grades it. It never writes the approved spec. It doesn't propose `form_checkboxes`/`authorization_checkboxes` groupings yet — a human still wires those by hand before freezing a spec.

For CC2002B specifically, the geometry path wins. The form already has real coordinates; vision is ~460× slower and no more accurate here. Keep the compiler for the next scanned form with no extractable structure.

**At runtime, `cc2002b.py`:**

1. Loads JSON and enforces shape with Pydantic — strict ISO dates, exactly one sworn-statement type, no extra fields.
2. `validate()` applies business rules: pre-1950 refuses, borough must be real, text must fit, fonts must be renderable.
3. `fill_final()` draws flat black ink. It never touches the signature line.
4. `check_correctness()` reopens the PDF and checks it three ways:
   - **semantic (mupdf)** — right words and checkbox Xs?
   - **raster (mupdf)** — stray ink, color, paint-over?
   - **raster (pdfium)** — same raster checks, different renderer.

Every fill writes a `*.proof.json` with template/input/output hashes, fee, notes, and a pass/fail per check. If the checker fails, the PDF is deleted, not released — you get a `*.pdf.failed.json` debug report instead of a proof receipt.

## Run it

```bash
# fill one sample
uv run cc2002b.py samples/01_party_short.json outputs/01_party_short.pdf

# verify an existing output
uv run cc2002b.py --check outputs/01_party_short.pdf samples/01_party_short.json

# tests
uv run --with-requirements requirements.txt python -m unittest discover -s tests -v
```

Or with a normal venv pinned to 3.12 (`pymupdf` doesn't have wheels for 3.13/3.14 yet):

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python cc2002b.py samples/01_party_short.json outputs/01_party_short.pdf
python -m unittest discover -s tests -v
```

`make test` and `make check` also work once the venv is active.

## Input shape

See [`samples/01_party_short.json`](samples/01_party_short.json):

```json
{
  "certificate_type": "short",
  "marriage": {"date": "2025-05-30", "borough": "Manhattan", "license_no": "..."},
  "spouse_a": {"name": "Sol Lee", "birth_date": "1991-07-29"},
  "spouse_b": {"name": "...", "birth_date": "..."},
  "authorization": {"kind": "party"}
}
```

Dates are `YYYY-MM-DD` only — not a datetime string, not an epoch int. `"May 30, 2025"`, `"05/30/2025"`, `"2025-05-30T00:00:00"`, and `0` all fail immediately at the schema layer. That's on purpose: one fact, one representation.

## Validation policy

- Marriage year `< 1950` → Municipal Archives, not City Clerk.
- Names must be inkable. Latin-1 stays on Helvetica. Greek/Cyrillic/etc. falls back to the bundled `fonts/DejaVuSans.ttf`. CJK, Arabic, and emoji fail loud with the exact code point.
- Text must fit the measured box. The relation/law-enforcement inline blanks are narrower, so they get a 6 pt floor; everything else bottoms at 8 pt.
- `certificate_type: "other"` has no listed price → refuse.
- `relation` or `law_enforcement` on a record under 50 years old gets a review note, not a rejection.

## Fee note

Page 2 first gives a broad certified-copy rate ($15 initial / $10 additional), then later prices the extended form separately at $35/$30. We treat the later, type-specific rule as controlling. Because the earlier language is broad enough to conflict, every extended-form proof receipt records the interpretation, flags for review, and shows the $20 difference under the other reading. Nothing is resolved silently.

## Sample outputs

| sample | output | fee |
|---|---|---:|
| party / short | [outputs/01_party_short.pdf](outputs/01_party_short.pdf) | $15 |
| relation / extended | [outputs/02_relation_extended.pdf](outputs/02_relation_extended.pdf) | $67 |
| law enforcement | [outputs/03_law_enforcement.pdf](outputs/03_law_enforcement.pdf) | $15 |

## Why CLI, not HTTP

It's a pure function: JSON in, PDF + proof out. No auth, no sessions, no persistence. A FastAPI wrapper would be a thin mechanical layer later; the engine is already transport-agnostic.

## Next

- Turn `tools/compile_form.py` into a multi-proposer loop: geometry + vision both propose, the same checker grades, human activates the winner.
- CJK/Arabic/RTL name support.
- Property/mutation fuzz at scale.
- Resolve the `other` fee ambiguity with 311.
- HTTP wrapper if a second consumer actually needs it.
