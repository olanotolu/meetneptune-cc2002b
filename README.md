# cc2002b

[![test](https://github.com/olanotolu/meetneptune-cc2002b/actions/workflows/test.yml/badge.svg)](https://github.com/olanotolu/meetneptune-cc2002b/actions/workflows/test.yml)

json in → flat, ink-on-paper nyc marriage-record request out. no fillable fields, no live model at fill time. same input, same bytes, every time — and the receipt is signed.

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
json → pydantic schema → validate() → fingerprint check → fill_final() → check_correctness() → signed receipt
```

- schema: strict types, iso dates only, one sworn-statement kind by construction
- validate: pre-1950 refuses, boroughs are real, text has to fit its box, unrenderable glyphs fail loud
- fingerprint: re-hash the blank on disk before drawing anything, fail closed on drift
- fill: flat black ink, signature line never touched, saved with a content-derived `/ID`
- check: reopen the *saved* pdf and grade it three ways — semantic word/checkbox match (mupdf), raster darkness/color/paint-over (mupdf), same raster check again through pdfium so mupdf isn't grading its own homework
- receipt: hashes of input, output, blank and spec — signed ed25519 if a key is present

if the check fails, the pdf gets deleted. no proof receipt for a filing the checker itself doesn't believe in — you get a `.failed.json` instead.

**5. prove it doesn't lie to itself.**
adversarial tests mutate a good pdf (fake signature, second checkbox, white cover, colored ink dark enough to pass a naive check) and assert the *named* check catches it. one test proves the independent renderer catches a signature tamper on its own. property-based fuzz tests (hypothesis) generate 50 random valid payloads end-to-end through fill + check with zero false rejects, and 50 random mutations (signature ink, extra checkbox, white cover) with zero false accepts.

checkbox crossed, not ticked:
![checkbox](docs/visuals/03_form_type_checkbox.png)

signature area stays empty:
![auth and signature](docs/visuals/06_auth_and_signature.png)

**6. call the fee ambiguity out loud.**
page 3 quotes $15/$10 in one paragraph and $35/$30 in another for the same extended-form purchase. billed at the type-specific rate — every affected receipt writes an `AMBIGUOUS FEE` note with the reasoning and a priced-out `alternative_reading` showing exactly what the other reading would have cost. never resolved silently.

prices live in [cc2002b.fees.json](cc2002b.fees.json), not in python. page 3 is a different document from page 2 on a different clock: the city clerk can reprice without redrawing the form, so the schedule is its own versioned artifact, validated by pydantic at import and stamped into every receipt as `fee_schedule_version`. `calculate_fee` reads rates; it doesn't know any.

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

**9. second pass: make the proof mean something, and gate the toolchain.**
- output wasn't reproducible — same payload, three runs, three different hashes. fixed at the save path; see below
- receipts proved integrity but not authorship — added ed25519 detached signatures and a standalone `--verify`
- three places declared dependencies (pep 723 block, `requirements.txt`, makefile), one of them annotated "keep in sync" — collapsed to `pyproject.toml` + `uv.lock`
- no lint or type gate existed — added `ruff` and `mypy` to ci. six lint findings, two real type findings, all fixed
- `.python-version` was gitignored, which defeats the point of pinning an interpreter
- the raster thresholds had no guard against the renderer they were calibrated against changing under them — added a calibration baseline and a drift test; see below
- the fee schedule was the last thing still hardcoded, and hardcoding it had produced a wrong number — the `AMBIGUOUS FEE` note said "$20.00 difference" always, but that's only true for an order of one. sample 02 is a 2-copy filing: billed $65 against an alternative reading of $25, so the real exposure was **$40**. the note now prices the competing reading instead of quoting a constant

## the receipt has to be checkable by someone who doesn't trust you

a hash of a file you also wrote proves nothing on its own. two things make the
proof receipt worth reading:

**the output is byte-deterministic.** mupdf stamps a fresh random `/ID` on every
save, so filling the same payload twice used to produce two different sha-256s —
`output_hash` could only ever say "this exact file wasn't touched since i wrote
it," never "here's the filing, rebuild it yourself." `fill_final` now re-saves
through pikepdf with `deterministic_id=True` (the same discipline the blank
extraction already used) and strips date metadata. same payload → same bytes,
across runs and machines. `test_same_payload_fills_to_identical_bytes` fails
without it.

**the receipt is signed.** ed25519 over the canonical receipt json, detached to
`<pdf>.proof.json.sig`. set `CC2002B_SIGNING_KEY` and filings get signed;
leave it unset and the run says so out loud rather than pretending.

```bash
uv run python cc2002b.py --verify outputs/01_party_short.pdf $(cat SIGNING_KEY.pub)
```

that checks three things without the payload, the blank, or this repo: the pdf's
bytes are the ones the receipt describes, the receipt carries a valid signature,
and the signer is the key you pinned. the third one matters — a forger can edit
a receipt and re-sign it with their own key perfectly consistently, so
verification with no pinned key is reported as a **failure**, not a pass.
`test_resigning_with_another_key_fails_the_pin` is that exact attack.

the three committed samples are signed with the demo key in
[SIGNING_KEY.pub](SIGNING_KEY.pub). its private half was generated in a temp dir
and never entered the repo.

## the thresholds only mean something against a renderer

`_DARK`, `_MIN_DARK` and `_MAX_DARK_FRAC` were calibrated by hand against
measured forgeries. those numbers are relative to one rasterizer: a pymupdf or
pdfium bump that anti-aliases a shade lighter moves every pixel count in the
repo without a line of it changing, and it fails quietly — a valid filing slips
under the floor and gets rejected, or a mark drifts over `_DARK` and stops
counting as ink at all. the fingerprint gate catches a changed *form*. nothing
caught a changed *renderer*.

[evidence/raster_calibration.json](evidence/raster_calibration.json) records
what the renderers actually produce — per-field ink counts from both engines,
the pinned versions (including pdfium's bundled binary, which is what really
rasterizes), and the margins that decide whether the thresholds still
discriminate:

```text
tightest floor: 2.75x _MIN_DARK (number_of_copies_requested)
highest cap:    14.0% of cap (license_no)
darkest ink:    0 (threshold 128)
```

the tight side is the floor, and it's a single-glyph field: a copy count lays
down ~22 dark pixels against a floor of 8. the cap and the darkness threshold
have room to spare.

`tests/test_raster_calibration.py` holds that. it asserts the margins hold on
any platform, and separately compares per-field counts to the baseline — but
only on the platform the baseline was measured on, because cross-platform
raster equality is not something this repo has verified and a guard that goes
red for the wrong reason is worse than no guard. a renderer version bump fails
loudly and tells you to `make calibrate` and read the diff.

verified it actually fires, rather than trusting that it would:

| simulated drift | caught by |
|---|---|
| 75% less ink laid down | floor margin |
| 6× more ink laid down | cap margin |
| 50% shift, still inside thresholds | baseline comparison |
| pymupdf bumped to 1.27.0 | version pin |
| threshold edited without recalibrating | threshold pin |

## the underspecified parts, and what i decided

the brief says several parts of the spec are underspecified on purpose. these
are the calls, and none of them is resolved silently — each one shows up in the
output as a note, a refusal, or a priced alternative.

**records under fifty years old.** the form's NOTE releases them "only" to
(a) a party, (b) someone with written authorization, (c) an attorney. but the
sworn-statement group below has *five* options — (d) relation and (e) law
enforcement aren't in that list. taken literally, a granddaughter requesting a
20-year-old record should be refused.

i note it and file anyway. the form prices its own exceptions: option (d)
carries "(RELEASE OF RECORD UNDER THIS SECTION MUST BE APPROVED BY LEGAL
DEPT.)" and option (e) "(LAW ENFORCEMENT PERSONNEL ONLY)". those parentheticals
describe a *routing* path, not a refusal — a rule that never applied would not
need a named approver. rejecting would invent a rule the form doesn't state;
filing quietly would hide one it implies. so every such filing carries a note
naming the review it should expect. `03_law_enforcement` is that case.

**"if you do not specify the form you desire you will be sent a short form."**
this makes `certificate_type` arguably optional with a default. i require it.
that sentence describes what a clerk does with an incomplete *mailed* form, not
what an automated filer should submit — and defaulting silently would hide a
$20–$40 fee difference behind an omission. a missing `certificate_type` is a
schema rejection, not a short form.

**"other" is a checkbox with no price.** the schedule prices short and extended
and never mentions it. i refuse to fill rather than invent a fee; the error
names 311. see `unpriced_types` in the fee schedule.

**the fee schedule contradicts itself.** covered in step 6 — type-specific rate
billed, both readings priced.

**month names on intake.** asked; the answer was to stay strict. see below.

## a second form

out of scope on purpose, per the brief. but nothing here is CC2002B-specific except the two data files it loads:

- `cc2002b.spec.json` — coordinates, field types, checkbox maps, the blank's hash
- `cc2002b.fees.json` — copy rates, search rates, which types are unpriced, which are ambiguous

onboarding a new form means measuring a new blank the same way `inspect_form.py` did this one, hand-reviewing the overlay, and freezing a new pair of files. `cc2002b.py` itself wouldn't change. `test_reprice_needs_no_code_change` and `test_unpriced_type_is_driven_by_the_schedule` hold that line — both swap a modified schedule into `validate()` and assert the behaviour follows the data.

## run it

one source of dependency truth: `pyproject.toml`, resolved into `uv.lock`.
`.python-version` pins the interpreter. nothing to keep in sync by hand.

if you don't have `uv` (this is the only prerequisite — it fetches python 3.12
itself, so nothing else needs to be installed first):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # or: brew install uv
```

```bash
uv sync
```

**1. check the committed filings before building anything.** these three are
signed; you're verifying my artifacts, not your own build:

```bash
uv run python cc2002b.py --verify outputs/01_party_short.pdf $(cat SIGNING_KEY.pub)
```

**2. rebuild one yourself and confirm you got my exact bytes.** this is the
determinism claim, and it's the whole reason the receipt's `output_hash` is
worth anything:

```bash
uv run python cc2002b.py samples/01_party_short.json /tmp/mine.pdf
shasum -a 256 /tmp/mine.pdf outputs/01_party_short.pdf   # same hash
uv run python cc2002b.py --check /tmp/mine.pdf samples/01_party_short.json
```

filling writes to `/tmp` here on purpose. pointing it at `outputs/` would
overwrite the signed originals with unsigned rebuilds — the pdf bytes would
still match, but the receipt records the run date, so re-filing on a later day
rewrites the receipt and the committed signature stops matching it. the
evidence is the committed pair; regenerate it somewhere else.

**3. sign one with your own key:**

```bash
export CC2002B_SIGNING_KEY=$(uv run python cc2002b.py --keygen)
uv run python cc2002b.py samples/01_party_short.json /tmp/mine.pdf
uv run python cc2002b.py --verify /tmp/mine.pdf $(uv run python cc2002b.py --pubkey)
```

**4. or just run everything:** `make test` / `make lint` / `make types` /
`make check` / `make sign-check` — all five run in ci on every push.
`sign-check` does the whole loop on a throwaway key: sign a filing, then verify
it the way a stranger would.

python is capped at 3.12 by **pikepdf** 8.7.1, which ships no cp313 wheel.
(pymupdf isn't the constraint — its `cp310-abi3` wheels run anywhere ≥3.10.)

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
