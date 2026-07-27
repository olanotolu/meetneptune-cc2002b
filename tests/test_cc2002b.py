"""Mechanical tests for the CC2002B filing engine.

Named checks must fail for named forgeries. Two layers reject bad payloads:
schema (Pydantic) and business rules (validate()). Each test targets one.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

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

    def build(self, **overrides):
        """Deep-copy the party sample and merge overrides one level deep."""
        candidate = copy.deepcopy(self.party)
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(candidate.get(key), dict):
                candidate[key] = {**candidate[key], **value}
            else:
                candidate[key] = value
        return candidate

    def app_from(self, payload_dict: dict) -> app.Application:
        return app.Application.model_validate(payload_dict)

    def assertRejectsAtSchema(self, payload_dict, msg_substr=None):
        with self.assertRaises(app.ValidationError) as ctx:
            self.app_from(payload_dict)
        if msg_substr:
            self.assertIn(msg_substr, str(ctx.exception))
        return ctx.exception

    def assertRejectsAtValidate(self, payload_dict, msg_substr=None):
        result = app.validate(self.app_from(payload_dict), as_of=AS_OF)
        self.assertFalse(result.valid)
        if msg_substr:
            self.assertTrue(any(msg_substr in e for e in result.errors))
        return result

    def fill(self, payload_dict, directory, name="filled.pdf"):
        app_obj = self.app_from(payload_dict)
        result = app.validate(app_obj, as_of=AS_OF)
        self.assertTrue(result.valid, result.errors)
        output = Path(directory) / name
        app.fill_final(app.form_values(app_obj), output, as_of=AS_OF)
        payload_path = Path(directory) / f"{Path(name).stem}.json"
        payload_path.write_text(json.dumps(payload_dict))
        return output, payload_path

    def test_blank_is_one_page_no_acroform(self):
        blank = pymupdf.open(str(app.BLANK))
        self.assertEqual(blank.page_count, 1)
        self.assertFalse(bool(blank.is_form_pdf))
        blank.close()

    def test_packet_is_three_pages_when_present(self):
        packet = ROOT / "00_packet" / "neptune-takehome-form-fill-packet (7).pdf"
        if not packet.exists():
            self.skipTest("packet PDF not in workspace")
        doc = pymupdf.open(str(packet))
        self.assertEqual(doc.page_count, 3)
        doc.close()

    def test_approved_fingerprint_resolves(self):
        spec = app.resolve_form_spec(app.BLANK)
        self.assertEqual(spec["form_id"], "CC2002B")
        self.assertEqual(spec["blank_sha256"], app.SPEC["blank_sha256"])

    def test_party_payload_is_valid(self):
        self.assertTrue(app.validate(self.app_from(self.party), as_of=AS_OF).valid)

    def test_marriage_date_is_strict_iso_only(self):
        """Strict ISO date — month name is display-only, never accepted as input."""
        as_iso = app.validate(
            self.app_from(self.build(marriage={"date": "2025-05-30"})), as_of=AS_OF
        )
        self.assertTrue(as_iso.valid, as_iso.errors)

        # Wrong shape or invalid calendar value — both rejected at schema
        for bad_date in ("May 30, 2025", "05/30/2025", 20250530, True, "2025-13-40"):
            self.assertRejectsAtSchema(self.build(marriage={"date": bad_date}))

        flat = app.form_values(
            self.app_from(self.build(marriage={"date": "2025-05-30"}))
        )
        self.assertEqual(app.text_for("month", flat, as_of=AS_OF), "May")

    def test_dates_reject_epoch_ints_and_iso_datetime_strings(self):
        # Bare pydantic date accepts epoch ints and datetime strings — reject both
        for bad_date in (0, 1748563200, "2025-05-30T00:00:00", "2025-05-30T00:00:00Z"):
            self.assertRejectsAtSchema(self.build(marriage={"date": bad_date}))
            self.assertRejectsAtSchema(self.build(spouse_a={"birth_date": bad_date}))

    def test_boolean_date_and_copies_are_rejected_by_strict_schema(self):
        # Field(strict=True) prevents bool->int coercion
        self.assertRejectsAtSchema(self.build(marriage={"date": True}))
        self.assertRejectsAtSchema(self.build(copies=True))

    def test_blank_hash_matches_formspec(self):
        import hashlib

        digest = hashlib.sha256(app.BLANK.read_bytes()).hexdigest()
        self.assertEqual(digest, app.SPEC["blank_sha256"])

    def test_pre_1950_is_rejected(self):
        self.assertRejectsAtValidate(
            self.build(marriage={"date": "1949-05-30"}), msg_substr="before 1950"
        )

    def test_unknown_authorization_kind_is_rejected_by_schema(self):
        # Discriminated union — unknown kind is a schema rejection
        self.assertRejectsAtSchema(self.build(authorization={"kind": "both"}))

    def test_missing_authorization_is_rejected_by_schema(self):
        payload = self.build()
        del payload["authorization"]
        self.assertRejectsAtSchema(payload)

    def test_relation_is_required_only_for_relation_kind(self):
        # missing
        self.assertRejectsAtSchema(
            self.build(authorization={"kind": "relation"})
        )
        # blank
        self.assertRejectsAtSchema(
            self.build(authorization={"kind": "relation", "relation": ""})
        )
        # leaked onto a variant that shouldn't carry it (extra="forbid")
        self.assertRejectsAtSchema(
            self.build(authorization={"kind": "party", "relation": "child"})
        )
        valid = app.validate(
            self.app_from(
                self.build(authorization={"kind": "relation", "relation": "child"})
            ),
            as_of=AS_OF,
        )
        self.assertTrue(valid.valid, valid.errors)

    def test_longer_relation_words_fit_via_the_inline_blank_size_floor(self):
        # auth_relation has a narrower size floor (10/9/8/7/6) for longer words
        for relation in ("granddaughter", "step-daughter"):
            payload = self.build(
                authorization={"kind": "relation", "relation": relation}
            )
            result = app.validate(self.app_from(payload), as_of=AS_OF)
            self.assertTrue(result.valid, (relation, result.errors))
            with tempfile.TemporaryDirectory() as directory:
                output, payload_path = self.fill(payload, directory)
                checked = app.check_correctness(output, payload_path, as_of=AS_OF)
                self.assertTrue(
                    checked["passed"],
                    (relation, [c for c in checked["checks"] if not c["passed"]]),
                )

        # But the floor has a bottom — this doesn't fit even at 6pt
        too_long = app.validate(
            self.app_from(
                self.build(
                    authorization={
                        "kind": "relation",
                        "relation": "great-granddaughter",
                    }
                )
            ),
            as_of=AS_OF,
        )
        self.assertFalse(too_long.valid)
        self.assertTrue(any("does not fit" in e for e in too_long.errors))

    def test_agency_is_required_only_for_law_enforcement_kind(self):
        self.assertRejectsAtSchema(
            self.build(authorization={"kind": "law_enforcement"})
        )
        self.assertRejectsAtSchema(
            self.build(
                authorization={"kind": "law_enforcement", "agency_or_title": ""}
            )
        )
        self.assertRejectsAtSchema(
            self.build(authorization={"kind": "party", "agency_or_title": "NYPD"})
        )
        valid = app.validate(
            self.app_from(
                self.build(
                    authorization={
                        "kind": "law_enforcement",
                        "agency_or_title": "NYPD",
                    }
                )
            ),
            as_of=AS_OF,
        )
        self.assertTrue(valid.valid, valid.errors)

    def test_birth_dates_must_be_iso_dates(self):
        # Presence and calendar validity are schema concerns
        self.assertRejectsAtSchema(self.build(spouse_a={"birth_date": ""}))
        self.assertRejectsAtSchema(self.build(spouse_b={"birth_date": "tomorrow"}))
        self.assertRejectsAtSchema(
            self.build(spouse_a={"birth_date": "July 29, 1991"})
        )
        valid = app.validate(
            self.app_from(self.build(spouse_a={"birth_date": "1991-07-29"})),
            as_of=AS_OF,
        )
        self.assertTrue(valid.valid, valid.errors)

    def test_birth_date_in_the_future_is_a_business_rule(self):
        future = date(AS_OF.year + 1, 1, 1).isoformat()
        self.assertRejectsAtValidate(self.build(spouse_b={"birth_date": future}))

    def test_relationship_is_required(self):
        self.assertRejectsAtValidate(
            self.build(requester_relationship=""), msg_substr="relationship"
        )

    def test_borough_and_state_are_constrained(self):
        bad_borough = app.validate(
            self.app_from(self.build(marriage={"borough": "Mars"})), as_of=AS_OF
        )
        bad_state = app.validate(
            self.app_from(self.build(address={"state": "ZZ"})), as_of=AS_OF
        )
        self.assertFalse(bad_borough.valid)
        self.assertFalse(bad_state.valid)

    def test_search_year_list_is_both_inked_and_billed(self):
        payload = self.build(marriage={"additional_search_years": [2023, 2024]})
        app_obj = self.app_from(payload)
        result = app.validate(app_obj, as_of=AS_OF)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.fee["breakdown"]["years_searched"], 3)
        flat = app.form_values(app_obj)
        self.assertEqual(
            app.text_for(
                "if_uncertain_specify_other_years_you_want_searched",
                flat,
                as_of=AS_OF,
            ),
            "2023, 2024",
        )

    def test_text_overflow_is_a_validation_error(self):
        self.assertRejectsAtValidate(
            self.build(spouse_a={"name": "X" * 100}), msg_substr="does not fit"
        )

    def test_wrappable_reason_text_fills_instead_of_rejecting(self):
        # A valid reason that doesn't fit on one line should wrap, not reject
        long_reason = "Needed for an immigration benefit application filed with USCIS"
        payload = self.build(reason=long_reason)
        result = app.validate(self.app_from(payload), as_of=AS_OF)
        self.assertTrue(result.valid, result.errors)
        with tempfile.TemporaryDirectory() as directory:
            output, payload_path = self.fill(payload, directory)
            checked = app.check_correctness(output, payload_path, as_of=AS_OF)
            self.assertTrue(
                checked["passed"],
                [c for c in checked["checks"] if not c["passed"]],
            )

    def test_single_line_fields_are_unaffected_by_wrapping(self):
        # Wrapping must not change size/position for text that fits on one line
        field = app.FIELDS["name_of_person_requesting_search"]
        size, _fontname, _fontfile, lines = app._fit(
            "Sol Lee", field["w"], field["h"], "name_of_person_requesting_search"
        )
        self.assertEqual(size, 10)
        self.assertEqual(lines, ("Sol Lee",))

    def test_even_wrapped_text_can_still_overflow(self):
        field = app.FIELDS["reason_search_copy_are_needed"]
        too_long = (
            "Needed for an immigration benefit application filed with the "
            "United States Citizenship and Immigration Services office "
            "located in downtown Manhattan New York"
        )
        with self.assertRaises(ValueError):
            app._fit(too_long, field["w"], field["h"], "reason")

    def test_latin1_guard(self):
        self.assertRejectsAtValidate(
            self.build(spouse_a={"name": "Harold前 Baker"}), msg_substr="U+"
        )

    def test_emoji_is_still_rejected_by_the_unicode_fallback(self):
        self.assertRejectsAtValidate(
            self.build(spouse_a={"name": "Harold🙂 Baker"}), msg_substr="U+1F642"
        )

    def test_non_latin1_names_fill_via_embedded_unicode_font(self):
        payload = self.build(
            spouse_a={"name": "Nguyễn Thị Phương"},
            spouse_b={"name": "Łukasz Kowalski"},
            requester_name="Παπαδόπουλος",
        )
        result = app.validate(self.app_from(payload), as_of=AS_OF)
        self.assertTrue(result.valid, result.errors)
        with tempfile.TemporaryDirectory() as directory:
            output, payload_path = self.fill(payload, directory)
            checked = app.check_correctness(output, payload_path, as_of=AS_OF)
            self.assertTrue(
                checked["passed"],
                [c for c in checked["checks"] if not c["passed"]],
            )

    def test_certificate_type_other_is_rejected(self):
        # 'other' has no fee schedule price — refuse to print
        self.assertRejectsAtValidate(
            self.build(certificate_type="other"), msg_substr="other"
        )

    def test_unknown_top_level_field_is_rejected(self):
        self.assertRejectsAtSchema(self.build(surprise_field="unexpected"))

    def test_fee_math_and_ambiguity(self):
        relation = app.load_application(SAMPLES / "02_relation_extended.json")
        result = app.validate(relation, as_of=AS_OF)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.fee["copy_fee"], 65.0)
        self.assertEqual(result.fee["search_fee"], 2.0)
        self.assertEqual(result.fee["total"], 67.0)
        self.assertTrue(result.fee["requires_review"])
        self.assertTrue(any("AMBIGUOUS FEE" in note for note in result.notes))

    def test_ambiguity_exposure_scales_with_copies(self):
        """The note used to hardcode '$20.00', which is only right for an order
        of one. This 2-copy filing is billed $65 against an alternative reading
        of $25 — the exposure is $40, and the receipt has to say so."""
        relation = app.load_application(SAMPLES / "02_relation_extended.json")
        result = app.validate(relation, as_of=AS_OF)
        self.assertEqual(result.fee["breakdown"]["num_copies"], 2)
        self.assertEqual(result.fee["alternative_reading"]["copy_fee"], 25.0)
        self.assertEqual(result.fee["alternative_reading"]["difference"], 40.0)
        note = next(n for n in result.notes if "AMBIGUOUS FEE" in n)
        self.assertIn("$40.00 difference", note)
        self.assertNotIn("$20.00", note)

    def test_single_copy_ambiguity_is_still_twenty(self):
        payload = self.build(certificate_type="extended", copies=1)
        result = app.validate(self.app_from(payload), as_of=AS_OF)
        self.assertEqual(result.fee["alternative_reading"]["difference"], 20.0)
        note = next(n for n in result.notes if "AMBIGUOUS FEE" in n)
        self.assertIn("$20.00 difference", note)
        self.assertIn("1 copy", note)

    def test_reprice_needs_no_code_change(self):
        """The whole point of moving the schedule into data: a fee change is a
        data edit, not a patch to calculate_fee."""
        repriced = app.FEES.model_copy(deep=True)
        repriced.copy_rates["short"].first_copy = 25.0
        repriced.copy_rates["short"].additional_copy = 20.0
        payload = self.build(certificate_type="short", copies=3)

        billed = app.validate(self.app_from(payload), as_of=AS_OF, fees=repriced)
        self.assertEqual(billed.fee["copy_fee"], 65.0)  # 25 + 20 + 20
        self.assertEqual(billed.fee["breakdown"]["first_copy"], 25.0)

        unchanged = app.validate(self.app_from(payload), as_of=AS_OF)
        self.assertEqual(unchanged.fee["copy_fee"], 35.0)  # 15 + 10 + 10

    def test_unpriced_type_is_driven_by_the_schedule(self):
        """'other' isn't special-cased in code — it's absent from copy_rates."""
        stripped = app.FEES.model_copy(deep=True)
        del stripped.copy_rates["extended"]
        stripped.unpriced_types["extended"] = "withdrawn pending review"
        result = app.validate(
            self.app_from(self.build(certificate_type="extended")),
            as_of=AS_OF,
            fees=stripped,
        )
        self.assertFalse(result.valid)
        self.assertTrue(
            any("withdrawn pending review" in e for e in result.errors),
            result.errors,
        )

    def test_receipt_records_which_schedule_priced_the_filing(self):
        relation = app.load_application(SAMPLES / "02_relation_extended.json")
        result = app.validate(relation, as_of=AS_OF)
        self.assertEqual(
            result.fee["fee_schedule_version"],
            f"{app.FEES.schedule_id}#{app.FEES.version}",
        )

    def test_malformed_fee_schedule_fails_at_load(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "fees.json"
            broken.write_text(json.dumps({"schedule_id": "x", "version": "1"}))
            with self.assertRaises(app.ValidationError):
                app._load_fee_schedule(broken)

    def test_form_example_four_year_search_short_is_17(self):
        # Worked example from the fee schedule (packet page 3): a four year
        # search and one certified copy would cost $17.00.
        payload = self.build(
            certificate_type="short",
            marriage={
                "date": "2020-05-30",
                "additional_search_years": [2019, 2021, 2022],
            },
            copies=1,
        )
        result = app.validate(self.app_from(payload), as_of=AS_OF)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.fee["total"], 17.0)

    def test_under_50_auth_notes_not_reject(self):
        le = app.load_application(SAMPLES / "03_law_enforcement.json")
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

    def test_same_payload_fills_to_identical_bytes(self):
        """A receipt's output_hash is only worth anything if someone else can
        rebuild the same bytes. MuPDF stamps a random /ID per save, so this
        would fail without the deterministic re-save in fill_final.
        """
        with tempfile.TemporaryDirectory() as directory:
            first, _ = self.fill(self.party, directory, "a.pdf")
            second, _ = self.fill(self.party, directory, "b.pdf")
            self.assertEqual(
                first.read_bytes(),
                second.read_bytes(),
                "identical payloads must produce byte-identical PDFs",
            )

    def test_different_payload_changes_output_bytes(self):
        """Guards the obvious way to fake the test above."""
        with tempfile.TemporaryDirectory() as directory:
            first, _ = self.fill(self.party, directory, "a.pdf")
            other = self.build(requester_name="Someone Else Entirely")
            second, _ = self.fill(other, directory, "b.pdf")
            self.assertNotEqual(first.read_bytes(), second.read_bytes())

    def test_cli_deletes_pdf_and_skips_proof_when_check_fails(self):
        """The CLI must never release a PDF the checker itself rejects."""
        with tempfile.TemporaryDirectory() as directory:
            payload_path = Path(directory) / "payload.json"
            payload_path.write_text(json.dumps(self.party))
            output_path = Path(directory) / "out.pdf"

            forced_failure = {
                "passed": False,
                "checks": [
                    {"check": "forced", "passed": False, "detail": "forced failure"}
                ],
            }
            with mock.patch.object(
                app, "check_correctness", return_value=forced_failure
            ):
                code = app.main([str(payload_path), str(output_path)])

            self.assertEqual(code, 1)
            self.assertFalse(output_path.exists(), "unverified PDF must be deleted")
            failed_path = Path(str(output_path) + ".failed.json")
            self.assertTrue(failed_path.exists())
            self.assertEqual(json.loads(failed_path.read_text()), forced_failure)
            proof_path = Path(str(output_path) + ".proof.json")
            self.assertFalse(
                proof_path.exists(), "no proof receipt for a rejected filing"
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

    def test_colored_ink_evades_pdfium_but_not_mupdf(self):
        """mupdf's ink_is_black is the one check pdfium's darkness-only raster
        cross-check can't reproduce: a mark dark enough to read as legitimate
        ink to a brightness threshold still isn't grayscale. This asserts
        pdfium_cross_check specifically does NOT catch it — proving the check
        is real, not redundant with the second-renderer layer."""
        with tempfile.TemporaryDirectory() as directory:
            output, payload_path = self.fill(self.party, directory)
            field = app.FIELDS["reason_search_copy_are_needed"]
            flat = app.form_values(self.app_from(self.party))
            expected = app.text_for(
                "reason_search_copy_are_needed", flat, as_of=AS_OF
            )
            tampered = Path(directory) / "colored.pdf"
            doc = pymupdf.open(str(output))
            page = doc[0]
            rect = pymupdf.Rect(field["x"], field["y"], field["x1"], field["y1"])
            # Redact the black text and reinsert the same words in dark
            # green — same content, same position, only the color changes.
            page.add_redact_annot(rect, fill=(1, 1, 1))
            page.apply_redactions()
            page.insert_text(
                (field["x"] + 2, field["y"] + field["h"] / 2 + 3),
                expected,
                fontname="helv",
                fontsize=10,
                color=(0, 0.31, 0),
            )
            doc.save(str(tampered))
            doc.close()
            checked = app.check_correctness(tampered, payload_path, as_of=AS_OF)
            self.assertFalse(checked["passed"])
            failed = [c["check"] for c in checked["checks"] if not c["passed"]]
            self.assertIn("ink_is_black", failed)
            self.assertFalse(
                any(c.startswith("pdfium_cross_check") for c in failed),
                "this dark-green mark should be dark enough to read as "
                "legitimate ink to pdfium's brightness-only check — if "
                "pdfium also flags it, this test isn't isolating what "
                "ink_is_black catches that the second renderer can't",
            )


class ReceiptSigningTests(unittest.TestCase):
    """A hash proves nothing was altered. A signature proves who wrote it."""

    @classmethod
    def setUpClass(cls):
        cls.payload_path = SAMPLES / "01_party_short.json"
        cls.private_hex, cls.public_hex = app.generate_signing_key()

    def file_signed(self, directory, name="signed.pdf"):
        """Run the real CLI with a signing key set, as a filer would."""
        output = Path(directory) / name
        with mock.patch.dict(os.environ, {app.SIGNING_KEY_ENV: self.private_hex}):
            code = app.main([str(self.payload_path), str(output)])
        self.assertEqual(code, 0)
        return output

    def test_signed_filing_verifies_against_the_pinned_key(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.file_signed(directory)
            result = app.verify_filing(output, expected_public_key=self.public_hex)
            self.assertTrue(
                result["passed"],
                [c for c in result["checks"] if not c["passed"]],
            )

    def test_altered_pdf_breaks_the_output_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.file_signed(directory)
            raw = bytearray(output.read_bytes())
            raw[len(raw) // 2] ^= 0x01
            output.write_bytes(bytes(raw))
            result = app.verify_filing(output, expected_public_key=self.public_hex)
            self.assertFalse(result["passed"])
            failed = [c["check"] for c in result["checks"] if not c["passed"]]
            self.assertIn("output_hash_matches_pdf", failed)

    def test_edited_receipt_breaks_the_signature(self):
        """Rewriting the fee and leaving the signature in place must not pass."""
        with tempfile.TemporaryDirectory() as directory:
            output = self.file_signed(directory)
            proof_path = Path(str(output) + ".proof.json")
            receipt = json.loads(proof_path.read_text())
            receipt["fee"]["total"] = 0.0
            proof_path.write_text(json.dumps(receipt, indent=2))
            result = app.verify_filing(output, expected_public_key=self.public_hex)
            self.assertFalse(result["passed"])
            failed = [c["check"] for c in result["checks"] if not c["passed"]]
            self.assertIn("signature_valid", failed)

    def test_resigning_with_another_key_fails_the_pin(self):
        """A forger can produce a self-consistent receipt+signature pair;
        pinning the expected key is what actually rejects it."""
        with tempfile.TemporaryDirectory() as directory:
            output = self.file_signed(directory)
            proof_path = Path(str(output) + ".proof.json")
            receipt = json.loads(proof_path.read_text())
            receipt["fee"]["total"] = 0.0
            proof_path.write_text(json.dumps(receipt, indent=2))

            attacker_hex, _ = app.generate_signing_key()
            with mock.patch.dict(os.environ, {app.SIGNING_KEY_ENV: attacker_hex}):
                forged = app.sign_receipt(receipt, app.load_signing_key())
            app.write_signature(forged, output)

            unpinned = app.verify_filing(output)
            self.assertTrue(
                all(
                    c["passed"]
                    for c in unpinned["checks"]
                    if c["check"] == "signature_valid"
                ),
                "a forger's own signature is internally consistent",
            )
            pinned = app.verify_filing(output, expected_public_key=self.public_hex)
            self.assertFalse(pinned["passed"])
            failed = [c["check"] for c in pinned["checks"] if not c["passed"]]
            self.assertIn("signer_is_expected_key", failed)

    def test_verification_without_a_trusted_key_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.file_signed(directory)
            result = app.verify_filing(output)
            self.assertFalse(
                result["passed"], "an unpinned signature proves no authorship"
            )

    def test_unsigned_filing_is_reported_as_unsigned(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "unsigned.pdf"
            with mock.patch.dict(os.environ, {app.SIGNING_KEY_ENV: ""}):
                self.assertEqual(app.main([str(self.payload_path), str(output)]), 0)
            self.assertFalse(Path(str(output) + ".proof.json.sig").exists())
            result = app.verify_filing(output)
            self.assertFalse(result["passed"])
            failed = [c["check"] for c in result["checks"] if not c["passed"]]
            self.assertIn("signature_present", failed)

    def test_malformed_signing_key_is_rejected_before_filing(self):
        """Fail before drawing, so a bad key never leaves a stray PDF behind."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.pdf"
            with mock.patch.dict(os.environ, {app.SIGNING_KEY_ENV: "not-hex-at-all"}):
                self.assertEqual(app.main([str(self.payload_path), str(output)]), 1)
            self.assertFalse(output.exists())
            self.assertFalse(Path(str(output) + ".proof.json").exists())

    def test_third_party_can_rebuild_the_signed_hash(self):
        """The point of the whole exercise: someone holding only the payload
        and the signed receipt can regenerate the PDF and get the same bytes.
        Determinism is what makes the signature worth checking."""
        with tempfile.TemporaryDirectory() as directory:
            output = self.file_signed(directory)
            receipt = json.loads(Path(str(output) + ".proof.json").read_text())
            rebuilt = Path(directory) / "rebuilt.pdf"
            app_obj = app.load_application(self.payload_path)
            app.fill_final(
                app.form_values(app_obj),
                rebuilt,
                as_of=date.fromisoformat(receipt["as_of"]),
            )
            self.assertEqual(
                hashlib.sha256(rebuilt.read_bytes()).hexdigest(),
                receipt["output_hash"],
            )


if __name__ == "__main__":
    unittest.main()
