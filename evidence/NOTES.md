# How `cc2002b.spec.json`'s coordinates were approved

The field map is not re-derived at runtime. It was measured once, offline,
against the committed blank (`00_packet/cc2002b_blank.pdf`), reviewed against
an overlay, and frozen. `tools/inspect_form.py` is the dev-only tool used to
propose and eyeball candidates; it is never imported by `cc2002b.py`.

`field_map_overlay.png` (below) is that review artifact: every approved
rect from `cc2002b.spec.json`'s `fields` block, drawn on the rendered blank.
`field_map_legend.png` explains the color coding (blue = text, red =
checkbox, purple dashed = machine must never ink — the two `protected`
fields).

![field map overlay](field_map_overlay.png)
![legend](field_map_legend.png)

## Derivation rule per field family

- **Checkboxes** (`form_short`/`form_extended`/`form_other`,
  `auth_checkbox_1`–`5`) → glyph bounding box. The form uses two different
  glyph families for its boxes (Wingdings at the top, ASCII `(_)` at the
  bottom), disambiguated by which occurrence in reading order matches.
- **Inline blanks** (`auth_relation`, `auth_other_agency`) → the end of the
  underscore run inside the printed sentence — these are ~47pt wide by the
  form's own design, not full table cells, which is why they carry a lower
  font-size floor (`"sizes": [10,9,8,7,6]` in the spec) than every other
  field.
- **Table cells** (everything else fillable) → row bands derived from the
  form's own drawn hairline rules, intersected with label word edges. The
  form has almost no vertical rules, so column boundaries come from label
  position, not a drawn grid line.
- **Protected** (`signature`, `signature_date`) → coordinates kept for
  verification only ("is this region still blank"), never measured for
  filling and never re-derived — a human signs here, in black ink, and the
  form says so.

## Re-approval

Any edit to `cc2002b.spec.json`'s `fields` or `blank_sha256` needs a fresh
`tools/inspect_form.py overlay` render and a human look before merge — there
is no automated "do these coordinates still make sense against the PDF"
check anymore (that used to be `--measure`'s job; it was deleted along with
the rest of the geometry-derivation engine in favor of this smaller, human-
reviewed workflow). Treat this file and the overlay image as the paper trail
that replaces it.

Coordinates approved: 2026-07-27, against `00_packet/cc2002b_blank.pdf`
(sha256 `0a34ee82...796442ebca`, see `cc2002b.spec.json`).
