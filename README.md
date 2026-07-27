# cc2002b

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
f192ef937026ac99220d3f8816aa1b056dce5c32e6dc67c990e7dec6a309025f
```

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
- latin-1 only (base-14 helvetica; no silent `·` substitution)  
- text must fit the measured cell  
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
| a · semantic | added words **equal** expected; checkboxes must **cross** |
| b · raster @ 150dpi | every dirty pixel must fall inside an approved rect |
| reject | stray ink · white erase · color · pale ink · paint-over · signature ink · extra page |

adversarial tests mutate a good pdf (signature scribble, second auth box, white cover) and assert the **named** check fails.

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
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python cc2002b.py samples/01_party_short.json outputs/01_party_short.pdf
python cc2002b.py --check outputs/01_party_short.pdf samples/01_party_short.json
python -m unittest discover -s tests -v
```

or: `uv run cc2002b.py samples/01_party_short.json outputs/01_party_short.pdf`  
(deps pinned in pep 723 header + `requirements.txt` — same pins)

```text
pymupdf==1.26.6   hot path + verify
pikepdf==8.7.1    structural page extract only
```

no fastapi · no langchain · no ocr · no live model on the final filing.

---

## layout of `cc2002b.py`

read top → bottom:

| § | job |
|---|---|
| 1 | FormSpec + fingerprint |
| 2–3 | normalize + fail-closed gate |
| 4–5 | validate + fee |
| 6–7 | fill + proof receipt |
| 8 | semantic + raster checker |
| 9–10 | extract-blank + cli |

---

## two more weeks (not built)

unicode font embed · `measure` command for the next revision · second renderer (pdfium) so mupdf does not grade itself alone · property/mutation fuzz at scale · resolve `other` pricing with 311
