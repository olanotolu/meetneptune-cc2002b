"""Renderer-drift guard.

The fingerprint gate catches a changed form. Nothing caught a changed
*renderer* — and _DARK / _MIN_DARK / _MAX_DARK_FRAC are only meaningful
relative to one. A pymupdf or pdfium upgrade that anti-aliases a shade lighter
moves every pixel count in this repo without a line of it changing, and the
failure mode is quiet: a legitimately inked field slips under _MIN_DARK and a
valid filing gets rejected, or a mark drifts over _DARK and stops counting as
ink at all.

Two layers here, because they fail for different reasons:

  * margin assertions — the property that actually has to hold, on any
    platform. These are the real gate.
  * baseline comparison — exact-ish regression detection, but only asserted on
    the platform the baseline was measured on, since cross-platform
    rasterization equality is not something this repo has verified.

Regenerate the baseline with `make calibrate` and read the diff before
committing it. That diff *is* the review artifact for a renderer bump.
"""

from __future__ import annotations

import json
import platform
import unittest
from datetime import date
from pathlib import Path

import pymupdf
from pypdfium2.version import PDFIUM_INFO, PYPDFIUM_INFO

import cc2002b as app

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "evidence" / "raster_calibration.json"

# How much headroom the thresholds must keep. Measured margins at calibration
# were 2.75x the floor and 14% of the cap, so these leave real room to move
# while still failing before a drifting renderer reaches the thresholds.
MIN_FLOOR_MULTIPLE = 1.75
MAX_CAP_FRACTION = 0.35
MAX_INK_BRIGHTNESS = 64  # real ink must sit well under _DARK (128)
BASELINE_TOLERANCE = 0.25  # +/-25% per field, same-platform only


class RasterCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = json.loads(BASELINE_PATH.read_text())
        cls.sample = ROOT / cls.baseline["sample"]
        cls.as_of = date.fromisoformat(cls.baseline["as_of"])
        cls.filled = ROOT / "outputs" / "01_party_short.pdf"

        application = app.load_application(cls.sample)
        cls.flat = app.form_values(application)
        cls.fm = app.SPEC["fields"]

        blank = pymupdf.open(str(app.BLANK))
        filled = pymupdf.open(str(cls.filled))
        try:
            _, cls.dark, _, _, _ = app._differential(blank[0], filled[0], cls.fm)
        finally:
            blank.close()
            filled.close()
        cls.pdfium_dark, cls.pdfium_stray = app._pdfium_dark_counts(
            cls.filled, cls.fm
        )

    def populated(self):
        for name, field_spec in self.fm.items():
            if field_spec["type"] == "checkbox" or not field_spec.get("fill", True):
                continue
            if not app.text_for(name, self.flat, as_of=self.as_of):
                continue
            yield name, field_spec

    # --- the thresholds still discriminate -------------------------------

    def test_every_inked_field_clears_the_floor_with_margin(self):
        """The tight side. A single-glyph field like a copy count is the whole
        exposure: it lays down ~22 dark pixels against a floor of 8."""
        for name, _ in self.populated():
            multiple = self.dark.get(name, 0) / app._MIN_DARK
            self.assertGreaterEqual(
                multiple,
                MIN_FLOOR_MULTIPLE,
                f"{name}: {self.dark.get(name, 0)} dark px is only "
                f"{multiple:.1f}x _MIN_DARK ({app._MIN_DARK}). The renderer is "
                f"laying down less ink than when these thresholds were "
                f"calibrated; a valid filing is about to be rejected.",
            )

    def test_no_inked_field_approaches_the_cap(self):
        for name, field_spec in self.populated():
            x0, x1, y0, y1 = app._devbox(field_spec)
            cap = int(app._MAX_DARK_FRAC * (x1 - x0 + 1) * (y1 - y0 + 1))
            fraction = self.dark.get(name, 0) / cap
            self.assertLessEqual(
                fraction,
                MAX_CAP_FRACTION,
                f"{name}: {fraction:.1%} of the paint-over cap. The renderer "
                f"is laying down more ink than at calibration.",
            )

    def test_real_ink_sits_far_below_the_darkness_threshold(self):
        """_DARK is a brightness cutoff. If a renderer's gamma shifted enough
        that black text rendered near 128, ink would stop counting as ink."""
        pix = app._pix(pymupdf.open(str(self.filled))[0])
        darkest = 255
        for _, field_spec in self.populated():
            x0, x1, y0, y1 = app._devbox(field_spec)
            for y in range(max(y0, 0), min(y1 + 1, pix.height)):
                row = pix.samples[y * pix.stride: (y + 1) * pix.stride]
                for x in range(max(x0, 0), min(x1 + 1, pix.width)):
                    darkest = min(darkest, max(row[x * 3: x * 3 + 3]))
        self.assertLessEqual(
            darkest,
            MAX_INK_BRIGHTNESS,
            f"darkest rendered ink is {darkest}; _DARK is {app._DARK}. Ink "
            f"this pale is one renderer bump away from being invisible to "
            f"the raster checks.",
        )

    def test_both_renderers_agree_a_field_has_ink(self):
        """If mupdf and pdfium diverge on where ink is, one of them drifted and
        the cross-check has quietly stopped being independent evidence."""
        for name, _ in self.populated():
            self.assertGreater(self.pdfium_dark.get(name, 0), 0, name)
            self.assertGreater(self.dark.get(name, 0), 0, name)
        self.assertEqual(self.pdfium_stray, 0)

    # --- the calibrated environment hasn't moved underneath us ------------

    def test_renderer_versions_match_the_calibrated_ones(self):
        recorded = self.baseline["renderers"]
        current = {
            "pymupdf": pymupdf.VersionBind,
            "pypdfium2": str(PYPDFIUM_INFO),
            "pdfium": str(PDFIUM_INFO),
        }
        self.assertEqual(
            recorded,
            current,
            "raster thresholds were calibrated against the recorded "
            "renderers. Re-run `make calibrate`, read the margin diff, and "
            "commit it deliberately — do not just update the pin.",
        )

    def test_thresholds_match_the_calibrated_ones(self):
        self.assertEqual(
            self.baseline["thresholds"],
            {
                "dark": app._DARK,
                "min_dark": app._MIN_DARK,
                "max_dark_frac": app._MAX_DARK_FRAC,
            },
            "a threshold moved without the baseline being regenerated",
        )

    def test_per_field_counts_match_the_baseline_on_the_same_platform(self):
        recorded = self.baseline["calibrated_on"]
        if (
            recorded["platform"] != platform.system().lower()
            or recorded["machine"] != platform.machine()
        ):
            self.skipTest(
                f"baseline measured on {recorded['platform']}/"
                f"{recorded['machine']}; cross-platform raster equality is "
                f"unverified, so the margin assertions are the gate here"
            )
        for name, expected in self.baseline["fields"].items():
            for engine, actual in (
                ("mupdf", self.dark.get(name, 0)),
                ("pdfium", self.pdfium_dark.get(name, 0)),
            ):
                want = expected[f"{engine}_dark"]
                low, high = want * (1 - BASELINE_TOLERANCE), want * (
                    1 + BASELINE_TOLERANCE
                )
                self.assertTrue(
                    low <= actual <= high,
                    f"{name} ({engine}): {actual} dark px, baseline {want} "
                    f"(±{BASELINE_TOLERANCE:.0%}). Rendering changed — "
                    f"`make calibrate` and review the diff.",
                )


if __name__ == "__main__":
    unittest.main()
