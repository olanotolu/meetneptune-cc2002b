# cc2002b

[![test](https://github.com/olanotolu/meetneptune-cc2002b/actions/workflows/test.yml/badge.svg)](https://github.com/olanotolu/meetneptune-cc2002b/actions/workflows/test.yml)

json in → flat, ink-on-paper nyc marriage-record request out. no fillable fields, no live model at fill time.

## the brief

[the packet](00_packet/neptune-takehome-form-fill-packet%20(7).pdf), 3 pages: brief, form, fee schedule. form is CC2002B — no acroform, boxes are ink targets from 2016.

## how it got built, in order

**1. pull the blank out of the packet.**
`pikepdf` structural extract of page 2 → `00_packet/cc2002b_blank.pdf`. not a screenshot, the actual object graph.

**2. measure every box.**
`tools/inspect_form.py` dumps word/glyph coordinates straight off the pdf. hand-measured 32 fields (30 fillable, 2 protected) against that dump, checked the result against a rendered overlay:

![field map](evidence/field_map_overlay.png)
![legend](evidence/field_map_legend.png)

**3. freeze it.**
coordinates + types + checkbox groupings + the blank's own sha-256 → `cc2002b.spec.json`. runtime reads this, never re-derives it.

**4. build the engine.** one file, `cc2002b.py`:

```text
json → pydantic schema → validate() → fingerprint check → fill_final() → check_correctness()
```

- schema: strict types, iso dates only, one sworn-statement kind by construction
- validate: pre-1950 refuses, boroughs are real, text has to fit its box, unrenderable glyphs fail loud
- fingerprint: re-hash the blank on disk before drawing anything, fail closed on drift
- fill: flat black ink, signature line never touched
- check: reopen the *saved* pdf and grade it three ways — semantic word/checkbox match (mupdf), raster darkness/color/paint-over (mupdf), same raster check again through pdfium so mupdf isn't grading its own homework

if the check fails, the pdf gets deleted. no proof receipt for a filing the checker itself doesn't believe in — you get a `.failed.json` instead.

**5. prove it doesn't lie to itself.**
adversarial tests mutate a good pdf (fake signature, second checkbox, white cover, colored ink dark enough to pass a naive check) and assert the *named* check catches it. one test proves the independent renderer catches a signature tamper on its own.

checkbox crossed, not ticked:
![checkbox](docs/visuals/03_form_type_checkbox.png)

signature area stays empty:
![auth and signature](docs/visuals/06_auth_and_signature.png)

**6. call the fee ambiguity out loud.**
page 3 quotes $15/$10 in one paragraph and $35/$30 in another for the same extended-form purchase. billed at the type-specific rate — every extended-form receipt writes an `AMBIGUOUS FEE` note with the reasoning and the $20 at stake if the other reading's right. never resolved silently.

**7. ship three real scenarios.**

| sample | fee | note |
|---|---:|---|
| [party/short](outputs/01_party_short.pdf) | $15 | baseline |
| [relation/extended](outputs/02_relation_extended.pdf) | $67 | fee flagged for review |
| [law enforcement](outputs/03_law_enforcement.pdf) | $15 | under-50 review note, not a rejection |

blank vs filled, same crop:
![blank vs filled](docs/visuals/08_blank_vs_filled_block.png)

full fill:
![filled](docs/visuals/07_filled_sol_lee.png)

**8. review pass before calling it done.**
ran it end to end like a pr review, found three real things, fixed them:
- pydantic's bare `date` type silently accepted epoch ints and full datetime strings → added a strict `YYYY-MM-DD`-only validator
- cli would write a proof receipt even for a filing that failed its own check → now deletes the pdf and writes `.failed.json` instead
- fee note wording implied a flat contradiction instead of describing the actual broad-then-specific structure → rewrote it

added regression tests, regenerated the three outputs, reran everything.

## a second form

out of scope on purpose, per the brief. but nothing here is CC2002B-specific except the spec it loads: coordinates, types, checkbox maps, a blank hash, all plain data in `cc2002b.spec.json`. onboarding a new form means measuring a new blank the same way `inspect_form.py` did this one, hand-reviewing the overlay, and freezing a new spec file. `cc2002b.py` itself wouldn't change.

## run it

```bash
uv run cc2002b.py samples/01_party_short.json outputs/01_party_short.pdf
uv run cc2002b.py --check outputs/01_party_short.pdf samples/01_party_short.json
uv run --with-requirements requirements.txt python -m unittest discover -s tests -v
```

manual venv, pinned to 3.12 (`pymupdf` has no 3.13/3.14 wheel yet):

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python cc2002b.py samples/01_party_short.json outputs/01_party_short.pdf
python -m unittest discover -s tests -v
```

`make test` / `make check` work once the venv's active.

## input shape

```json
{
  "certificate_type": "short",
  "marriage": {"date": "2025-05-30", "borough": "Manhattan", "license_no": "..."},
  "spouse_a": {"name": "Sol Lee", "birth_date": "1991-07-29"},
  "spouse_b": {"name": "...", "birth_date": "..."},
  "authorization": {"kind": "party"}
}
```

dates are `YYYY-MM-DD`, nothing else. `"May 30, 2025"`, `0`, `"2025-05-30T00:00:00"` all bounce at the schema layer.

## why cli, not http

pure function — json in, pdf + proof out. no auth, no sessions, nothing to keep alive. `fill_final`/`check_correctness` are already transport-agnostic if a real service boundary shows up later.

## next

- cjk/arabic/rtl name support
- property/mutation fuzz at scale
- resolve `other` pricing with 311
