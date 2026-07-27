"""Property-based fuzz tests using hypothesis.

Two properties:
1. Any valid payload fills and checks cleanly — no false rejects.
2. Any mutation of a filled PDF is caught by the checker — no false accepts.
"""

from __future__ import annotations

import json
import string
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pymupdf
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import cc2002b as app

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
AS_OF = date(2026, 7, 26)

# Letters, digits, space — all clear the raster legibility floor (8 dark
# pixels). Thin punctuation (backtick, period) can pass validation but
# fail the pixel count at short lengths.
_SAFE = string.ascii_letters + string.digits + " "


def _text(max_size):
    return st.text(alphabet=_SAFE, min_size=1, max_size=max_size).filter(
        lambda s: s.strip()
    )


def _iso_date(year_min, year_max):
    return st.builds(
        lambda y, m, d: f"{y:04d}-{m:02d}-{d:02d}",
        st.integers(min_value=year_min, max_value=year_max),
        st.integers(min_value=1, max_value=12),
        st.integers(min_value=1, max_value=28),
    )


def _phone():
    return st.builds(
        lambda a, b, c: f"{a:03d}-{b:03d}-{c:04d}",
        st.integers(min_value=200, max_value=999),
        st.integers(min_value=200, max_value=999),
        st.integers(min_value=0, max_value=9999),
    )


def _zip():
    return st.one_of(
        st.builds(lambda d: f"{d:05d}", st.integers(0, 99999)),
        st.builds(
            lambda d, p: f"{d:05d}-{p:04d}",
            st.integers(0, 99999),
            st.integers(0, 9999),
        ),
    )


@st.composite
def valid_payload(draw):
    """A payload dict that passes schema and validate()."""
    cert = draw(st.sampled_from(["short", "extended"]))
    marr_date = draw(_iso_date(1950, 2025))
    borough = draw(
        st.sampled_from(
            ["Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"]
        )
    )
    license_no = draw(_text(12))
    search_years = draw(
        st.lists(st.integers(1950, 2026), max_size=5, unique=True)
    )

    spouse_a_name = draw(_text(20))
    spouse_b_name = draw(_text(20))
    birth_a = draw(_iso_date(1900, 2025))
    birth_b = draw(_iso_date(1900, 2025))

    reason = draw(_text(30))
    copies = draw(st.integers(1, 10))
    requester_name = draw(_text(20))
    requester_rel = draw(_text(20))
    phone = draw(_phone())

    street = draw(_text(20))
    apt = draw(st.one_of(st.none(), _text(8)))
    city = draw(_text(15))
    state = draw(st.sampled_from(sorted(app.USPS_STATES)))
    zip_code = draw(_zip())

    auth_kind = draw(
        st.sampled_from(
            ["party", "written_authorization", "attorney", "relation",
             "law_enforcement"]
        )
    )
    auth: dict = {"kind": auth_kind}
    if auth_kind == "relation":
        auth["relation"] = draw(_text(8))
    elif auth_kind == "law_enforcement":
        auth["agency_or_title"] = draw(_text(12))

    return {
        "certificate_type": cert,
        "marriage": {
            "date": marr_date,
            "borough": borough,
            "license_no": license_no,
            "additional_search_years": search_years,
        },
        "spouse_a": {"name": spouse_a_name, "birth_date": birth_a},
        "spouse_b": {"name": spouse_b_name, "birth_date": birth_b},
        "reason": reason,
        "copies": copies,
        "requester_name": requester_name,
        "requester_relationship": requester_rel,
        "telephone": phone,
        "address": {
            "street": street,
            "apartment": apt,
            "city": city,
            "state": state,
            "zip_code": zip_code,
        },
        "authorization": auth,
    }


# --- Mutation strategy -------------------------------------------------------

_TEXT_FIELDS = [
    "full_legal_name_before_marriage",
    "reason_search_copy_are_needed",
    "name_of_person_requesting_search",
    "street",
    "license_no",
]

_UNCHECKED_BOXES = [
    "form_extended", "form_other",
    "auth_checkbox_2", "auth_checkbox_3", "auth_checkbox_4", "auth_checkbox_5",
]


@st.composite
def mutation(draw):
    """One tamper to apply to a filled PDF."""
    kind = draw(
        st.sampled_from(["signature_ink", "extra_checkbox", "white_cover"])
    )
    if kind == "signature_ink":
        return {"type": "signature_ink"}
    if kind == "extra_checkbox":
        return {
            "type": "extra_checkbox",
            "box": draw(st.sampled_from(_UNCHECKED_BOXES)),
        }
    return {
        "type": "white_cover",
        "field": draw(st.sampled_from(_TEXT_FIELDS)),
    }


def _apply_mutation(pdf_path: Path, mut: dict) -> Path:
    """Apply a tamper to a filled PDF and return the path to the result."""
    tampered = pdf_path.parent / "tampered.pdf"
    doc = pymupdf.open(str(pdf_path))
    page = doc[0]

    if mut["type"] == "signature_ink":
        page.insert_text((150, 683), "X", fontsize=10, color=(0, 0, 0))
    elif mut["type"] == "extra_checkbox":
        app._xmark(page, app.FIELDS[mut["box"]])
    elif mut["type"] == "white_cover":
        f = app.FIELDS[mut["field"]]
        page.draw_rect(
            pymupdf.Rect(f["x"], f["y"], f["x1"], f["y1"]),
            color=(1, 1, 1),
            fill=(1, 1, 1),
            overlay=True,
        )

    doc.save(str(tampered))
    doc.close()
    return tampered


# --- Properties --------------------------------------------------------------


class FuzzTests(unittest.TestCase):
    @given(valid_payload())
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_valid_payload_always_fills_and_checks_cleanly(self, payload):
        app_obj = app.Application.model_validate(payload)
        result = app.validate(app_obj, as_of=AS_OF)
        self.assertTrue(result.valid, result.errors)

        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / "filled.pdf"
            app.fill_final(app.form_values(app_obj), output, as_of=AS_OF)
            payload_path = Path(d) / "payload.json"
            payload_path.write_text(json.dumps(payload))
            checked = app.check_correctness(
                output, payload_path, as_of=AS_OF
            )
            self.assertTrue(
                checked["passed"],
                [c for c in checked["checks"] if not c["passed"]],
            )

    @given(mutation())
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_mutated_filled_pdf_always_fails_check(self, mut):
        party = json.loads(
            (SAMPLES / "01_party_short.json").read_text()
        )
        with tempfile.TemporaryDirectory() as d:
            app_obj = app.Application.model_validate(party)
            output = Path(d) / "filled.pdf"
            app.fill_final(app.form_values(app_obj), output, as_of=AS_OF)
            payload_path = Path(d) / "payload.json"
            payload_path.write_text(json.dumps(party))

            tampered = _apply_mutation(output, mut)
            checked = app.check_correctness(
                tampered, payload_path, as_of=AS_OF
            )
            self.assertFalse(
                checked["passed"],
                f"mutation {mut} was not caught",
            )


if __name__ == "__main__":
    unittest.main()
