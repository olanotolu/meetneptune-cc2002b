"""Dev-only helper: record what the renderers currently produce.

NOT part of the runtime. cc2002b.py never imports this file.

The raster checks in cc2002b.py compare pixel counts against three constants
(_DARK, _MIN_DARK, _MAX_DARK_FRAC) that were calibrated by hand against
measured forgeries. Those numbers are only meaningful relative to a particular
renderer: a pymupdf or pdfium upgrade that anti-aliases a little lighter moves
every count without anything in this repo changing. The fingerprint gate
catches a changed *form*; nothing caught a changed *renderer*.

This writes the measurements to evidence/raster_calibration.json.
tests/test_raster_calibration.py holds them to account.

Usage
-----
    python tools/calibrate_raster.py            # rewrite the baseline
    python tools/calibrate_raster.py --print    # show it, write nothing
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import date
from pathlib import Path

import pymupdf
from pypdfium2.version import PDFIUM_INFO, PYPDFIUM_INFO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cc2002b as engine  # noqa: E402

BASELINE_PATH = ROOT / "evidence" / "raster_calibration.json"
SAMPLE = ROOT / "samples" / "01_party_short.json"
FILLED = ROOT / "outputs" / "01_party_short.pdf"
AS_OF = date(2026, 7, 26)


def measure() -> dict:
    """Per-field ink measurements from both renderers, plus the margins that
    decide whether the thresholds still discriminate."""
    app = engine.load_application(SAMPLE)
    flat = engine.form_values(app)
    fm = engine.SPEC["fields"]

    blank = pymupdf.open(str(engine.BLANK))
    filled = pymupdf.open(str(FILLED))
    try:
        _, dark, _, _, _ = engine._differential(blank[0], filled[0], fm)
        darkest = _darkest_ink_brightness(filled[0], fm, flat)
    finally:
        blank.close()
        filled.close()

    pdfium_dark, pdfium_stray = engine._pdfium_dark_counts(FILLED, fm)

    fields: dict[str, dict] = {}
    floor_multiples: list[tuple[float, str]] = []
    cap_fractions: list[tuple[float, str]] = []
    for name, field_spec in fm.items():
        if field_spec["type"] == "checkbox" or not field_spec.get("fill", True):
            continue
        if not engine.text_for(name, flat, as_of=AS_OF):
            continue
        x0, x1, y0, y1 = engine._devbox(field_spec)
        cap = int(engine._MAX_DARK_FRAC * (x1 - x0 + 1) * (y1 - y0 + 1))
        d = dark.get(name, 0)
        fields[name] = {
            "mupdf_dark": d,
            "pdfium_dark": pdfium_dark.get(name, 0),
            "cap": cap,
        }
        floor_multiples.append((d / engine._MIN_DARK, name))
        cap_fractions.append((d / cap, name))

    tightest_floor = min(floor_multiples)
    highest_cap = max(cap_fractions)

    return {
        "_comment": (
            "Regenerate with `make calibrate`. Baselines are only compared "
            "strictly on the platform they were measured on; everywhere else "
            "the test asserts margins, which is the property that matters."
        ),
        "calibrated_on": {
            "platform": platform.system().lower(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "renderers": {
            "pymupdf": pymupdf.VersionBind,
            "pypdfium2": str(PYPDFIUM_INFO),
            # The bundled binary is what actually rasterizes; the wrapper
            # version can move without it, and vice versa.
            "pdfium": str(PDFIUM_INFO),
        },
        "thresholds": {
            "dark": engine._DARK,
            "min_dark": engine._MIN_DARK,
            "max_dark_frac": engine._MAX_DARK_FRAC,
        },
        "sample": str(SAMPLE.relative_to(ROOT)),
        "as_of": AS_OF.isoformat(),
        "margins": {
            "tightest_floor_multiple": round(tightest_floor[0], 2),
            "tightest_floor_field": tightest_floor[1],
            "highest_cap_fraction": round(highest_cap[0], 4),
            "highest_cap_field": highest_cap[1],
            "darkest_ink_brightness": darkest,
            "pdfium_stray": pdfium_stray,
        },
        "fields": fields,
    }


def _darkest_ink_brightness(page, fm: dict, flat: dict) -> int:
    """The darkest pixel the renderer actually lays down. _DARK only means
    something if real ink sits well under it."""
    pix = engine._pix(page)
    best = 255
    for name, field_spec in fm.items():
        if field_spec["type"] == "checkbox" or not field_spec.get("fill", True):
            continue
        if not engine.text_for(name, flat, as_of=AS_OF):
            continue
        x0, x1, y0, y1 = engine._devbox(field_spec)
        for y in range(max(y0, 0), min(y1 + 1, pix.height)):
            row = pix.samples[y * pix.stride: (y + 1) * pix.stride]
            for x in range(max(x0, 0), min(x1 + 1, pix.width)):
                best = min(best, max(row[x * 3: x * 3 + 3]))
    return best


def main(argv: list[str]) -> int:
    baseline = measure()
    rendered = json.dumps(baseline, indent=2) + "\n"
    if "--print" in argv:
        print(rendered, end="")
        return 0
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(rendered)
    margins = baseline["margins"]
    print(f"Wrote {BASELINE_PATH.relative_to(ROOT)}")
    print(
        f"  tightest floor: {margins['tightest_floor_multiple']}x _MIN_DARK "
        f"({margins['tightest_floor_field']})"
    )
    print(
        f"  highest cap:    {margins['highest_cap_fraction']:.1%} of cap "
        f"({margins['highest_cap_field']})"
    )
    print(
        f"  darkest ink:    {margins['darkest_ink_brightness']} "
        f"(threshold {engine._DARK})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
