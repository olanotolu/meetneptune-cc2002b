# cc2002b

[![test](https://github.com/olanotolu/meetneptune-cc2002b/actions/workflows/test.yml/badge.svg)](https://github.com/olanotolu/meetneptune-cc2002b/actions/workflows/test.yml)

json in → flat nyc marriage-record request out. one form. proved.

source packet: [`neptune-takehome-form-fill-packet (7).pdf`](neptune-takehome-form-fill-packet%20(7).pdf)

```text
packet → extract p2 → FormSpec → validate → black ink → prove
```

![hot path](docs/visuals/09_pipeline.png)

---

## step 0 — the packet

three pages. only one is a filing surface.

| p1 | p2 | p3 |
|---|---|---|
| assignment | **form cc2002b** | fees + instructions |

![three-page packet](docs/visuals/00_packet_three_pages.png)

no acroform on p2. boxes are ink targets, not fields you can `set_value` on.

---

## step 1 — split page 2 only

**tool:** `pikepdf` (libqpdf) — structural object-graph rewrite, not a raster.

```bash
python cc2002b.py --extract-blank \
  "neptune-takehome-form-fill-packet (7).pdf" \
  cc2002b_blank.pdf
```

output is one letter page (`612×792`), committed as `cc2002b_blank.pdf`.

sha-256 (must match `FormSpec` or fill refuses):

```text
0a34ee8217e65105fa28d61f46a7af1cea585340a36238eb02da10796442ebca
```

reproducible: `--extract-blank` passes `deterministic_id=True` to pikepdf's
save, so re-running it against the packet always reproduces this exact
hash. (It didn't always — pikepdf randomizes the PDF `/ID` trailer entry
by default, so the same command produced a different hash on every call,
even on the same machine with the same pinned version. Found by actually
re-extracting and diffing, not by inspection.)

![blank filing surface](docs/visuals/01_blank_page2.png)

---

## step 2 — measure the boxes (FormSpec)

**tool:** `pymupdf` word/glyph geometry on the blank.

how rects were derived (not eyeballed once and forgotten):

1. **checkboxes** → glyph bboxes (wingdings at top; ascii `(_)` at bottom — two families)
2. **inline blanks** (relation / le agency) → end of underscore run
3. **table cells** → row bands + label edges (almost no vertical rules)
4. **signature / signature-date** → protected: `fill=False`

compiled into a versioned `FormSpec` keyed by blank hash. unknown hash → **fail closed**. no stretching old coords onto a new city form.

![measured field map](docs/visuals/02_field_map_overlay.png)

![legend](docs/visuals/02b_field_map_legend.png)

blue = text · red = checkbox · purple dashed = machine must never ink

---

## step 3 — intake (strict month)

json payload. aliases allowed (`spouse_a_name` → form field, etc.).

**month (cto):** json **integer** `1`–`12` only.  
`"May"`, `"5"`, abbreviations → validation error, **no pdf**.  
print side still draws english name from that int (`5` → `May`).

sample (sol lee — the intake path you care about):

[`samples/01_party_short.json`](samples/01_party_short.json)

---

## step 4 — validate before ink

state machine. rejects what a clerk should never receive:

- year &lt; 1950 → municipal archives, not city clerk  
- not exactly one form type / one sworn box  
- relation / le inline text only when auth is 4 / 5  
- inkable only — latin-1 stays on base-14 helvetica; broader names (Nguyễn, Łukasz, Παπαδόπουλος, Дмитрий) fall back to an embedded Unicode font (`fonts/DejaVuSans.ttf`, Bitstream Vera license — see `fonts/LICENSE_DEJAVU`); anything neither font can render (CJK, Arabic, emoji) fails loud with the exact character and codepoint, never a silent `·`  
- text must fit the measured cell — wrapped across as many lines as the cell's own height allows first (greedy word-wrap, largest font that fits), only refused once it doesn't fit even wrapped. A single-line field's chosen size/position is provably unaffected by wrapping existing — pinned by `test_single_line_fields_are_unaffected_by_wrapping` after a real regression (found by diffing the three committed samples byte-for-byte, not by inspection) where the height-fit rule briefly changed semantics for text that already fit on one line. the relation/law-enforcement inline blanks (`auth_relation`, `auth_other_agency`) are ~47pt wide by the form's own printed design — a real applicant's word ("granddaughter", "step-daughter") would reject at the standard 8pt floor, so those two fields specifically get a lower floor down to 6pt (confirmed legible by rendering it, not assumed), declared per-field so every other field keeps the stricter minimum. `great-granddaughter` still correctly rejects — the cell has a real bottom  
- `form_type: other` → no price on schedule → refuse  

**50-year + auth 4/5:** note, do not invent a ban. form parentheticals are adjudication paths (legal / le). fill + review note.

**fee:** short `$15/$10`, extended `$35/$30`, search years as on p3.  
schedule contradicts itself — we bill type-specific and write the ambiguity into the proof receipt. never silent.

---

## step 5 — put black ink on paper

**tool:** `pymupdf` only on the hot path.

- open blank → assert fingerprint  
- largest of `{10,9,8}pt` that fits  
- pure black rgb  
- checkboxes = two crossing strokes (real X, not parallel ticks)  
- **signature + signature date empty** (do not print)

![form type X](docs/visuals/03_form_type_checkbox.png)

![marriage row](docs/visuals/04_marriage_row_inked.png)

![spouses](docs/visuals/05_spouses_inked.png)

![sworn + empty signature](docs/visuals/06_auth_and_signature.png)

blank vs filled (same crop):

![blank vs filled](docs/visuals/08_blank_vs_filled_block.png)

---

## step 6 — prove the visible artifact

most of the assessment weight lives here. reopen the pdf; do not trust process memory.

| layer | what |
|---|---|
| a · semantic (mupdf) | added words **equal** expected; checkboxes must **cross** |
| b · raster @ 150dpi (mupdf) | every dirty pixel must fall inside an approved rect |
| c · raster @ 150dpi (**pdfium**) | independent re-render, independent verdict — mupdf never grades its own ink alone |
| reject | stray ink · white erase · color · pale ink · paint-over · signature ink · extra page |

adversarial tests mutate a good pdf (signature scribble, second auth box, white cover) and assert the **named** check fails — including a dedicated test that layer c catches the signature tamper on its own, with a wholly different rasterizer (`tests/test_cc2002b.py::test_second_renderer_independently_catches_signature_tamper`).

layer c is deliberately narrow: it re-derives only "no ink outside approved rects" and "every populated field is dark, every empty one isn't" from raw pdfium pixels. It does not re-implement checkbox crossing-geometry (that's a vector check, not a raster one) — its job is to catch the exact failure mode layer b's own rendering bugs could hide, not to duplicate layer a.

---

## step 7 — evidence (committed)

| sample | path | exercises | fee |
|---|---|---|---:|
| party / short | [`outputs/01_party_short.pdf`](outputs/01_party_short.pdf) | auth 1, empty inlines | $15 |
| relation / extended | [`outputs/02_relation_extended.pdf`](outputs/02_relation_extended.pdf) | auth 4, multi-year, fee note | $67 |
| law enforcement | [`outputs/03_law_enforcement.pdf`](outputs/03_law_enforcement.pdf) | auth 5, under-50 note | $15 |

each fill also writes `*.proof.json` (hashes, fee, check counts) and `*.fees.json`.

![filled sol lee](docs/visuals/07_filled_sol_lee.png)

---

## run

```bash
uv run cc2002b.py samples/01_party_short.json outputs/01_party_short.pdf
uv run cc2002b.py --check outputs/01_party_short.pdf samples/01_party_short.json
uv run --with-requirements requirements.txt python -m unittest discover -s tests -v
```

Recommended path — `uv` resolves a compatible Python itself from the PEP 723
header below, regardless of what `python3` defaults to on your machine.
Confirmed on a genuinely fresh clone with no system Python assumptions. The
test command needs `--with-requirements` explicitly: the PEP 723 header only
attaches its pins when `uv run` targets `cc2002b.py` directly — pointed at
plain `python` instead, `uv run` gives you a bare interpreter with nothing
installed, and the suite fails on `ModuleNotFoundError: pymupdf`. Confirmed
both ways on a fresh clone before writing this sentence.

or, manual venv — **pin the interpreter explicitly**, don't rely on
whatever `python3` resolves to:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python cc2002b.py samples/01_party_short.json outputs/01_party_short.pdf
python cc2002b.py --check outputs/01_party_short.pdf samples/01_party_short.json
python -m unittest discover -s tests -v
```

`pymupdf==1.26.6` has no published wheel yet for Python 3.13/3.14 — on a
machine where plain `python3 -m venv` resolves to one of those (a real,
reproduced failure, not hypothetical: a stock macOS install with a recent
Xcode CLT defaults `python3` to 3.14), `pip install -r requirements.txt`
fails outright. Pin to a Python version pymupdf has published wheels for
(3.10–3.12) — and if that Python's own `ensurepip`/`pip` bootstrap is
broken (happens with some standalone/managed Python installs), that's a
local interpreter problem, not a pin problem; `uv run` above sidesteps
both failure modes entirely and is the one actually verified clean here.

```text
pymupdf==1.26.6    hot path + verify (layers a/b)
pikepdf==8.7.1     structural page extract only
pypdfium2==5.9.0   independent re-render for verify (layer c)
```

`make test` / `make fill` / `make check` are shortcuts once a venv is
active. `make check` deliberately depends on `test`, not `fill` — it
verifies the three *committed* sample PDFs, not freshly regenerated
ones. That's intentional: it's the check a reviewer running this
top-to-bottom actually wants (does what's in the repo hold up?), not a
missing dependency.

no fastapi · no langchain · no ocr · no live model on the final filing.

---

## cli, not http — and why

the brief leaves the transport open ("your call — justify it"). this ships as
a cli on purpose:

- the task is a **pure function** — json payload in, pdf + proof receipt out.
  no session, no auth, no persistence (all explicitly out of scope). wrapping
  that in an http server adds a process to keep alive and a port to secure
  for zero behavioral benefit at this stage.
- the **fingerprint gate and validation state machine are the product**, not
  the transport. `validate()`, `fill_final()`, and `check_correctness()` are
  already transport-agnostic — a five-line fastapi route (`payload =
  request.json(); return fill_final(payload, tmp)`) is a thin, mechanical
  wrapper whenever a service boundary is actually needed (batch intake queue,
  a UI, a second form).
- a cli composes directly with how a filing engine like this actually gets
  used first: batch jobs, cron, a human running `--check` against a stack of
  intake json before anything gets mailed. that's the operational reality of
  "mail request for marriage records," not a live request/response UI.

if the next step is a service, the seam is already there — `main()` is the
only http-shaped code in the file; everything above section 11 doesn't know
the cli exists.

---

## layout of `cc2002b.py`

read top → bottom:

| § | job |
|---|---|
| 1 | FormSpec + fingerprint |
| 2–3 | normalize + fail-closed gate |
| 4–5 | validate + fee |
| 6–7 | fill + proof receipt |
| 8 | semantic + raster (mupdf) + independent raster (pdfium) checker |
| 9 | extract-blank (pikepdf structural extract) |
| 10 | `--measure` — deterministic re-derivation of the field map |
| 11 | cli |

---

## two more weeks (not built)

CJK/Arabic/RTL name support · property/mutation fuzz at scale · resolve `other` pricing with 311 · http wrapper around `fill_final`/`check_correctness` if a second consumer needs it (see [cli, not http — and why](#cli-not-http--and-why))

CJK/Arabic specifically is the most likely real complaint at the actual City Clerk's office — already reasoned through in the Unicode note below (shaping + bidi are a different, larger problem than font coverage, not a bigger font), listed here explicitly rather than only as a caveat on the Unicode fix.

~~second renderer (pdfium) so mupdf does not grade itself alone~~ — built: layer c in step 6.
~~`measure` command for the next revision~~ — built: `--measure [blank.pdf] [--compare]`. Re-derives the field map from the page's own drawn table rules + label geometry — no hardcoded coordinates. Reproduces all 30 hand-approved fields within 1.2pt. Re-measuring a form revision is now "edit ~20 copy-pasted label strings," not "retype 80 coordinates." Still produces a *candidate* map only — `_FIELD_MAP` stays the hand-approved, fingerprint-gated source of truth for the hot path.
~~unicode font embed~~ — built: names outside Latin-1 fall back to an embedded DejaVu Sans (Latin Extended, Cyrillic, Greek). CJK/Arabic/emoji still rejected loud, not silently substituted — different problem (shaping, bidi), intentionally out of scope.

evaluated and rejected: pymupdf's built-in `page.find_tables()` as a replacement for the hand-rolled row-band detection in `--measure` — tested against the real form, not just the docs. see `docs/visuals/10_find_tables_evaluated_and_rejected.png`.
