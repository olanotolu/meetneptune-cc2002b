"""Dev-only helper: propose field coordinates for human review.

NOT part of the runtime. cc2002b.py never imports this file. It prints every
word + its bounding box from a blank PDF page, and (optionally) draws a
rectangle overlay for a candidate field map so a human can eyeball it
against the rendered page before coordinates are frozen into
cc2002b.spec.json.

This deliberately does not grade, compare against a tolerance, or derive
anchors automatically — that was `measure.py`'s job, and it grew past the
point where a human could hold the whole derivation-rule set in their head.
If this script starts growing anchor rules, row-band detection, or a
--compare flag, that's the signal it's turning back into measure.py: stop,
and do that comparison by eye against the overlay instead.

Usage
-----
    python tools/inspect_form.py words blank.pdf
    python tools/inspect_form.py overlay blank.pdf candidate_fields.json out.png

`candidate_fields.json` has the same shape as cc2002b.spec.json's "fields"
block: {"field_name": {"x":.., "y":.., "w":.., "h":.., "type": "text"|"checkbox"}, ...}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf

OVERLAY_DPI = 150 / 72
COLORS = {"checkbox": (1, 0, 0), "text": (0, 0, 1), "line": (0.6, 0, 0.6)}


def dump_words(blank_path: Path) -> None:
    doc = pymupdf.open(str(blank_path))
    try:
        for w in doc[0].get_text("words"):
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
            print(f"{x0:8.2f} {y0:8.2f} {x1:8.2f} {y1:8.2f}  {text}")
    finally:
        doc.close()


def draw_overlay(blank_path: Path, fields_json: Path, out_png: Path) -> None:
    fields = json.loads(fields_json.read_text())
    doc = pymupdf.open(str(blank_path))
    try:
        page = doc[0]
        for name, f in fields.items():
            rect = pymupdf.Rect(f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"])
            color = COLORS.get(f.get("type", "text"), (0, 0, 1))
            page.draw_rect(rect, color=color, width=1.2)
            page.insert_text((rect.x0, rect.y0 - 2), name, fontsize=5, color=color)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(OVERLAY_DPI, OVERLAY_DPI))
        pix.save(str(out_png))
    finally:
        doc.close()
    print(f"wrote {out_png}")


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("words", "overlay"):
        print(__doc__)
        return 1
    if argv[0] == "words":
        if len(argv) < 2:
            print("usage: inspect_form.py words blank.pdf")
            return 1
        dump_words(Path(argv[1]))
        return 0
    if len(argv) < 4:
        print("usage: inspect_form.py overlay blank.pdf candidate_fields.json out.png")
        return 1
    draw_overlay(Path(argv[1]), Path(argv[2]), Path(argv[3]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
