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
adversarial tests mutate a good pdf (fake signature, second checkbox, white cover, colored ink dark enough to pass a naive check) and assert the *named* check catches it. one test proves the independent renderer catches a signature tamper on its own. property-based fuzz tests (hypothesis) generate 50 random valid payloads end-to-end through fill + check with zero false rejects, and 50 random mutations (signature ink, extra checkbox, white cover) with zero false accepts.

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

inbound question came in: Sol Lee asked whether the form should accept month names/abbreviations (`"May"`, `"may"`) and normalize them to the numeric value, instead of forcing a strict integer inside the iso string. asked the cto, answer was "it can stay strict." that's what shipped — no name/abbreviation parsing anywhere on intake, `IsoDate` rejects all of it, the only place a month *name* ever appears is on the printed pdf itself, converted from the already-validated integer for display, not accepted as input.

## why cli, not http

pure function — json in, pdf + proof out. no auth, no sessions, nothing to keep alive. `fill_final`/`check_correctness` are already transport-agnostic if a real service boundary shows up later.

## where the form fought you

- **two checkbox glyph families.** the form uses wingdings for the top form-type boxes and ascii `(_)` for the sworn-statement group. same visual concept, two different internal representations — disambiguated by reading order, not by glyph code.
- **inline blanks aren't cells.** the relation and law-enforcement lines have ~47pt underscore runs inside printed sentences, not full table cells. a 10pt name fits; "granddaughter" doesn't. added a lower font-size floor (10/9/8/7/6) just for those two fields instead of rejecting valid input.
- **no vertical rules.** column boundaries come from label word edges, not drawn grid lines. measuring meant reading the form like a human, not parsing a table.
- **fee schedule contradicts itself.** page 3 says $15/$10 broadly, then $35/$30 for extended. picked the type-specific rate, called it loud. see step 6 above.

## next (two more weeks)

- **second form.** the spec.json + fingerprint gate pattern is already form-agnostic. onboarding a new form is a new spec file + field map + overlay review — `cc2002b.py` doesn't change. the brief said "if your design makes the second form easy, say so." it does.
- **cjk/arabic/rtl names.** DejaVu covers Latin Extended, Cyrillic, Greek. Noto Sans would cover the rest. the uninkable-char guard already fails loud — swapping the fallback font is a one-line change.
- **resolve `other` pricing.** needs a call to 311, not code.
- **http wrapper.** when there's a real service boundary with auth and persistence, not before. `fill_final` / `check_correctness` are already transport-agnostic.
