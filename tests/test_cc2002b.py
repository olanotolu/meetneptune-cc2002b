"""Mechanical tests for the CC2002B filing engine.

Named checks must fail for named forgeries. A check that only says "failed"
is ceremonial.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pymupdf

import cc2002b as app

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
AS_OF = date(2026, 7, 26)


class CC2002BTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.party_path = SAMPLES / "01_party_short.json"
        cls.party = json.loads(cls.party_path.read_text())

    def payload(self, **updates):
        candidate = copy.deepcopy(self.party)
        candidate.update(updates)
        return candidate

    def fill(self, payload, directory, name="filled.pdf"):
        result = app.validate(payload, as_of=AS_OF)
        self.assertTrue(result.valid, result.errors)
        output = Path(directory) / name
        app.fill_final(payload, output, as_of=AS_OF)
        payload_path = Path(directory) / f"{Path(name).stem}.json"
        payload_path.write_text(json.dumps(payload))
        return output, payload_path

    def test_blank_is_one_page_no_acroform(self):
        blank = pymupdf.open(str(app.BLANK))
        self.assertEqual(blank.page_count, 1)
        self.assertFalse(bool(blank.is_form_pdf))
        blank.close()

    def test_packet_is_three_pages_when_present(self):
        packet = ROOT / "neptune-takehome-form-fill-packet (7).pdf"
        if not packet.exists():
            self.skipTest("packet PDF not in workspace")
        doc = pymupdf.open(str(packet))
        self.assertEqual(doc.page_count, 3)
        doc.close()

    def test_approved_fingerprint_resolves(self):
        spec = app.resolve_form_spec(app.BLANK)
        self.assertEqual(spec.form_id, "CC2002B")
        self.assertEqual(spec.fingerprint.sha256, app.EXPECTED_BLANK_SHA256)

    def test_party_payload_is_valid(self):
        self.assertTrue(app.validate(self.party, as_of=AS_OF).valid)

    def test_month_is_strict_integer_only(self):
        """CTO: strict intake — integers 1–12 only; still ink English name."""
        as_int = app.validate(self.payload(month=5), as_of=AS_OF)
        as_name = app.validate(self.payload(month="May"), as_of=AS_OF)
        as_str_num = app.validate(self.payload(month="5"), as_of=AS_OF)
        as_bool = app.validate(self.payload(month=True), as_of=AS_OF)
        as_bad = app.validate(self.payload(month=13), as_of=AS_OF)
        self.assertTrue(as_int.valid, as_int.errors)
        self.assertFalse(as_name.valid)
        self.assertFalse(as_str_num.valid)
        self.assertFalse(as_bool.valid)
        self.assertFalse(as_bad.valid)
        self.assertTrue(any("integer 1–12" in e for e in as_name.errors))
        self.assertEqual(
            app.text_for("month", self.payload(month=5), as_of=AS_OF),
            "May",
        )

    def test_blank_hash_matches_formspec(self):
        import hashlib

        digest = hashlib.sha256(app.BLANK.read_bytes()).hexdigest()
        self.assertEqual(digest, app.EXPECTED_BLANK_SHA256)
        self.assertEqual(digest, app.CC2002B_2016.fingerprint.sha256)
        self.assertIn(digest, app.APPROVED_FORMS)

    def test_pre_1950_is_rejected(self):
        result = app.validate(self.payload(year=1949), as_of=AS_OF)
        self.assertFalse(result.valid)
        self.assertTrue(any("before 1950" in error for error in result.errors))

    def test_duplicate_auth_is_rejected_without_crashing(self):
        result = app.validate(self.payload(auth_checkbox=[1, 2]), as_of=AS_OF)
        self.assertFalse(result.valid)
        self.assertTrue(any("integer 1-5" in error for error in result.errors))

    def test_missing_auth_is_rejected(self):
        result = app.validate(self.payload(auth_checkbox=None), as_of=AS_OF)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("auth_checkbox is required" in error for error in result.errors)
        )

    def test_relation_is_required_only_for_option_four(self):
        missing = app.validate(
            self.payload(auth_checkbox=4, auth_relation=""), as_of=AS_OF
        )
        leaked = app.validate(
            self.payload(auth_checkbox=1, auth_relation="child"), as_of=AS_OF
        )
        valid = app.validate(
            self.payload(auth_checkbox=4, auth_relation="child"), as_of=AS_OF
        )
        self.assertFalse(missing.valid)
        self.assertFalse(leaked.valid)
        self.assertTrue(valid.valid, valid.errors)

    def test_agency_is_required_only_for_option_five(self):
        missing = app.validate(
            self.payload(auth_checkbox=5, auth_other_agency=""), as_of=AS_OF
        )
        leaked = app.validate(
            self.payload(auth_checkbox=1, auth_other_agency="NYPD"), as_of=AS_OF
        )
        valid = app.validate(
            self.payload(auth_checkbox=5, auth_other_agency="NYPD"), as_of=AS_OF
        )
        self.assertFalse(missing.valid)
        self.assertFalse(leaked.valid)
        self.assertTrue(valid.valid, valid.errors)

    def test_birth_dates_are_required_and_parsed(self):
        missing = app.validate(
            self.payload(spouse_a_birth_date=""), as_of=AS_OF
        )
        malformed = app.validate(
            self.payload(spouse_b_birth_date="tomorrow"), as_of=AS_OF
        )
        written = app.validate(
            self.payload(spouse_a_birth_date="July 29, 1991"), as_of=AS_OF
        )
        self.assertFalse(missing.valid)
        self.assertFalse(malformed.valid)
        self.assertTrue(written.valid, written.errors)

    def test_relationship_is_required(self):
        result = app.validate(self.payload(relationship=""), as_of=AS_OF)
        self.assertFalse(result.valid)
        self.assertTrue(any("relationship" in error for error in result.errors))

    def test_borough_and_state_are_constrained(self):
        bad_borough = app.validate(self.payload(borough="Mars"), as_of=AS_OF)
        bad_state = app.validate(self.payload(state="ZZ"), as_of=AS_OF)
        self.assertFalse(bad_borough.valid)
        self.assertFalse(bad_state.valid)

    def test_search_year_list_is_both_inked_and_billed(self):
        payload = self.payload(years_searched=[2023, 2024])
        result = app.validate(payload, as_of=AS_OF)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.fee["breakdown"]["years_searched"], 3)
        self.assertEqual(
            app.text_for(
                "if_uncertain_specify_other_years_you_want_searched",
                payload,
                as_of=AS_OF,
            ),
            "2023, 2024",
        )

    def test_conflicting_year_representations_are_rejected(self):
        result = app.validate(
            self.payload(years_searched=[2023], years_searched_text="2024"),
            as_of=AS_OF,
        )
        self.assertFalse(result.valid)
        self.assertTrue(any("disagree" in error for error in result.errors))

    def test_text_overflow_is_a_validation_error(self):
        result = app.validate(self.payload(spouse_a_name="X" * 100), as_of=AS_OF)
        self.assertFalse(result.valid)
        self.assertTrue(any("does not fit" in error for error in result.errors))

    def test_latin1_guard(self):
        result = app.validate(
            self.payload(spouse_a_name="Harold前 Baker"), as_of=AS_OF
        )
        self.assertFalse(result.valid)
        self.assertTrue(any("latin" in e.lower() or "U+" in e for e in result.errors))

    def test_form_type_other_is_rejected(self):
        result = app.validate(self.payload(form_type="other"), as_of=AS_OF)
        self.assertFalse(result.valid)
        self.assertTrue(any("other" in e for e in result.errors))

    def test_fee_math_and_ambiguity(self):
        relation = json.loads(
            (SAMPLES / "02_relation_extended.json").read_text()
        )
        result = app.validate(relation, as_of=AS_OF)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.fee["copy_fee"], 65.0)
        self.assertEqual(result.fee["search_fee"], 2.0)
        self.assertEqual(result.fee["total"], 67.0)
        self.assertTrue(result.fee["requires_review"])
        self.assertTrue(any("AMBIGUOUS FEE" in note for note in result.notes))

    def test_form_example_four_year_search_short_is_17(self):
        # Page 2 example: four year search + one certified short copy = $17.
        payload = self.payload(
            form_type="short",
            year=2020,
            years_searched=[2019, 2021, 2022],
            number_of_copies_requested=1,
        )
        result = app.validate(payload, as_of=AS_OF)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.fee["total"], 17.0)

    def test_under_50_auth_notes_not_reject(self):
        le = json.loads((SAMPLES / "03_law_enforcement.json").read_text())
        result = app.validate(le, as_of=AS_OF)
        self.assertTrue(result.valid, result.errors)
        self.assertTrue(any("under 50" in note for note in result.notes))

    def test_all_three_sample_outputs_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            for stem in (
                "01_party_short",
                "02_relation_extended",
                "03_law_enforcement",
            ):
                source = SAMPLES / f"{stem}.json"
                payload = json.loads(source.read_text())
                output, payload_path = self.fill(payload, directory, f"{stem}.pdf")
                checked = app.check_correctness(
                    output, payload_path, as_of=AS_OF
                )
                self.assertTrue(
                    checked["passed"],
                    [c for c in checked["checks"] if not c["passed"]],
                )

    def test_signature_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output, payload_path = self.fill(self.party, directory)
            tampered = Path(directory) / "signature.pdf"
            doc = pymupdf.open(str(output))
            doc[0].insert_text((150, 683), "SOL LEE", fontsize=10)
            doc.save(str(tampered))
            doc.close()
            checked = app.check_correctness(tampered, payload_path, as_of=AS_OF)
            self.assertFalse(checked["passed"])
            self.assertTrue(
                any(
                    check["check"] == "no_ink:signature" and not check["passed"]
                    for check in checked["checks"]
                )
            )

    def test_double_checkbox_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output, payload_path = self.fill(self.party, directory)
            tampered = Path(directory) / "double.pdf"
            doc = pymupdf.open(str(output))
            app._xmark(doc[0], app.FIELDS["auth_checkbox_2"])
            doc.save(str(tampered))
            doc.close()
            checked = app.check_correctness(tampered, payload_path, as_of=AS_OF)
            self.assertFalse(checked["passed"])
            self.assertTrue(
                any(
                    "auth_checkbox_2" in check["check"] and not check["passed"]
                    for check in checked["checks"]
                )
            )

    def test_second_renderer_independently_catches_signature_tamper(self):
        """mupdf must not grade its own ink alone — pdfium has to agree."""
        with tempfile.TemporaryDirectory() as directory:
            output, payload_path = self.fill(self.party, directory)
            tampered = Path(directory) / "signature_pdfium.pdf"
            doc = pymupdf.open(str(output))
            doc[0].insert_text((150, 683), "SOL LEE", fontsize=10)
            doc.save(str(tampered))
            doc.close()
            checked = app.check_correctness(tampered, payload_path, as_of=AS_OF)
            self.assertFalse(checked["passed"])
            pdfium_checks = [
                c for c in checked["checks"]
                if c["check"].startswith("pdfium_cross_check")
            ]
            self.assertTrue(pdfium_checks, "pdfium cross-check did not run")
            self.assertTrue(
                any(
                    c["check"] == "pdfium_cross_check:no_ink:signature"
                    and not c["passed"]
                    for c in pdfium_checks
                ),
                pdfium_checks,
            )

    def test_white_cover_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output, payload_path = self.fill(self.party, directory)
            tampered = Path(directory) / "covered.pdf"
            doc = pymupdf.open(str(output))
            field = app.FIELDS["full_legal_name_before_marriage"]
            doc[0].draw_rect(
                pymupdf.Rect(field["x"], field["y"], field["x1"], field["y1"]),
                color=(1, 1, 1),
                fill=(1, 1, 1),
                overlay=True,
            )
            doc.save(str(tampered))
            doc.close()
            checked = app.check_correctness(tampered, payload_path, as_of=AS_OF)
            self.assertFalse(checked["passed"])
            self.assertTrue(
                any(
                    check["check"].startswith("nothing_drawn_over")
                    and not check["passed"]
                    for check in checked["checks"]
                )
            )


if __name__ == "__main__":
    unittest.main()
