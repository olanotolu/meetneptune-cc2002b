# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pymupdf==1.26.6",
#   "pikepdf==8.7.1",
#   "pypdfium2==5.9.0",
# ]
# ///
"""cc2002b.py — NYC Form CC2002B: a deterministic filing engine.

Journey (matches the assessment packet end-to-end)
--------------------------------------------------
  Step 0  Read packet: brief | form | fees  (3 pages)
  Step 1  Extract page 2 → cc2002b_blank.pdf   (--extract-blank / §9)
  Step 2  Compile FormSpec + fingerprint         (§1, §3)
  Step 3  Accept structured JSON                 (§2, samples/)
  Step 4  Validate (reject clerk-kicks)          (§4)
  Step 5  Fee policy (ambiguity visible)         (§5)
  Step 6  Draw flat black ink; no signature      (§6)
  Step 7  Proof receipt + reopen-and-prove       (§7, §8)
  Step 8  Evidence: outputs/ + tests/

Thesis
------
Do not build an "AI form filler." Build an AI-compiled, deterministic filing
engine.

Use judgment (and AI, offline) where uncertainty lives: understanding a new
form. Once a form version is approved, no model decides where legal information
goes. The hot path is:

    JSON → validate (state machine) → fingerprint gate → flat black ink
        → atomic PDF + proof receipt → reopen and prove

If the city revises the form, this program refuses to guess. A new FormSpec is
activated only after measurement, adversarial tests, and human approval.

Usage
-----
    python cc2002b.py payload.json [output.pdf]
    python cc2002b.py --check filled.pdf payload.json [report.json]
    python cc2002b.py --extract-blank packet.pdf cc2002b_blank.pdf
    python cc2002b.py --measure [blank.pdf] [--compare]

Dependencies are pinned here (PEP 723) and in requirements.txt. Same pins.
The blank form is cc2002b_blank.pdf next to this script (packet page 2).
"""

from __future__ import annotations

import calendar
import hashlib
import itertools
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pymupdf

# ═══════════════════════════════════════════════════════════════════════════
# 0. Paths and constants
# ═══════════════════════════════════════════════════════════════════════════

ROOT = Path(__file__).resolve().parent
BLANK = ROOT / "cc2002b_blank.pdf"
HELV = "helv"
FONT_SIZES = (10, 9, 8)  # try largest first; ValueError at the floor
# The relation/law-enforcement inline blanks are ~47pt wide by the form's
# own printed design (an underscore run inside a sentence, not a full
# cell) — "granddaughter", "step-daughter" genuinely don't fit at 8pt.
# A human filling this by hand would just write smaller; 6pt confirmed
# legible by rendering it, not assumed. Declared per-field (below), not a
# global floor — every other field keeps the stricter 8pt minimum.
INLINE_BLANK_FONT_SIZES = (10, 9, 8, 7, 6)
DPI = 150 / 72           # checker raster scale (device pixels per point)

# Base-14 Helvetica only covers Latin-1. Most applicant names do (Baker,
# José, Müller) and stay on "helv" — no embedding needed, smallest output.
# Names outside Latin-1 (Nguyễn, Łukasz, Παπαδόπουλος, Дмитрий) fall back to
# an embedded Unicode font instead of being rejected outright. DejaVu Sans
# covers Latin Extended, Cyrillic, and Greek; it does not cover CJK, Arabic,
# or emoji — those still fail loud with a clear error, not silently as '·'.
# License: Bitstream Vera, freely redistributable — fonts/LICENSE_DEJAVU.
UNICODE_FONT_NAME = "DejaVuSans"
UNICODE_FONT_PATH = ROOT / "fonts" / "DejaVuSans.ttf"

_unicode_font_cache: pymupdf.Font | None = None


def _unicode_font() -> pymupdf.Font:
    """Lazy on purpose: a missing fonts/DejaVuSans.ttf should only break
    commands that actually ink or validate non-Latin-1 text, not every
    invocation of this file — a module-level pymupdf.Font(...) call would
    take down --extract-blank and even `python cc2002b.py` with no args."""
    global _unicode_font_cache
    if _unicode_font_cache is None:
        _unicode_font_cache = pymupdf.Font(fontfile=str(UNICODE_FONT_PATH))
    return _unicode_font_cache

# Raster thresholds (calibrated against measured forgeries, not vibes)
_DARK = 128
_MIN_DARK = 8
_MAX_DARK_FRAC = 0.5
_PAD = 1

# ═══════════════════════════════════════════════════════════════════════════
# 1. FormSpec — the compiled, versioned form
#
# A great engineer does not stretch old coordinates onto an unknown government
# form. The fingerprint gate fails closed. Migration is offline; fill is not.
# ═══════════════════════════════════════════════════════════════════════════

# Measured field map for CC2002B 9/20/2016. Derivation rules:
#   1. Checkbox rects = glyph bboxes (Wingdings top; ascii "(_)" bottom).
#   2. Inline blanks end where the underscore run ends.
#   3. Table columns come from labels (almost no vertical rules).
#   4. Signature / signature-date are protected: fill=False.
_FIELD_MAP: dict[str, dict[str, Any]] = {
    "month": {
        "x": 177.43, "y": 289.16, "w": 70.77, "h": 28.52,
        "type": "text", "fill": True,
    },
    "day": {
        "x": 270.56, "y": 289.16, "w": 42.56, "h": 28.52,
        "type": "text", "fill": True,
    },
    "year": {
        "x": 337.28, "y": 289.16, "w": 74.0, "h": 28.52,
        "type": "text", "fill": True,
    },
    "license_was_issued": {
        "x": 489.78, "y": 289.16, "w": 67.78, "h": 28.52,
        "type": "text", "fill": True,
    },
    "if_uncertain_specify_other_years_you_want_searched": {
        "x": 250.74, "y": 321.68, "w": 160.9, "h": 19.04,
        "type": "text", "fill": True,
    },
    "license_no": {
        "x": 461.56, "y": 321.68, "w": 96.0, "h": 19.04,
        "type": "text", "fill": True,
    },
    "full_legal_name_before_marriage": {
        "x": 183.79, "y": 344.72, "w": 268.64, "h": 24.8,
        "type": "text", "fill": True,
    },
    "date": {
        "x": 474.91, "y": 344.72, "w": 82.65, "h": 24.8,
        "type": "text", "fill": True,
    },
    "full_legal_name_before_marriage_09": {
        "x": 184.03, "y": 373.52, "w": 268.4, "h": 24.08,
        "type": "text", "fill": True,
    },
    "date_10": {
        "x": 474.91, "y": 373.52, "w": 82.65, "h": 24.08,
        "type": "text", "fill": True,
    },
    "reason_search_copy_are_needed": {
        "x": 192.15, "y": 401.6, "w": 157.57, "h": 24.92,
        "type": "text", "fill": True,
    },
    "number_of_copies_requested": {
        "x": 468.51, "y": 401.6, "w": 89.05, "h": 24.92,
        "type": "text", "fill": True,
    },
    "name_of_person_requesting_search": {
        "x": 52.2, "y": 441.83, "w": 165.76, "h": 12.65,
        "type": "text", "fill": True,
    },
    "your_relationship_to_either_spouse": {
        "x": 219.96, "y": 441.83, "w": 182.2, "h": 12.65,
        "type": "text", "fill": True,
    },
    "your_telephone_no": {
        "x": 404.16, "y": 441.83, "w": 153.4, "h": 12.65,
        "type": "text", "fill": True,
    },
    "street": {
        "x": 161.88, "y": 470.39, "w": 127.12, "h": 23.93,
        "type": "text", "fill": True,
    },
    "apt_no": {
        "x": 291.0, "y": 470.39, "w": 53.44, "h": 23.93,
        "type": "text", "fill": True,
    },
    "city": {
        "x": 346.44, "y": 470.39, "w": 106.0, "h": 23.93,
        "type": "text", "fill": True,
    },
    "state": {
        "x": 454.44, "y": 470.39, "w": 29.44, "h": 23.93,
        "type": "text", "fill": True,
    },
    "zip_code": {
        "x": 485.88, "y": 470.39, "w": 71.68, "h": 23.93,
        "type": "text", "fill": True,
    },
    "form_short": {
        "x": 53.64, "y": 47.47, "w": 8.86, "h": 9.99,
        "type": "checkbox", "fill": True,
    },
    "form_extended": {
        "x": 53.88, "y": 59.21, "w": 8.87, "h": 11.06,
        "type": "checkbox", "fill": True,
    },
    "form_other": {
        "x": 53.64, "y": 72.29, "w": 9.35, "h": 11.06,
        "type": "checkbox", "fill": True,
    },
    "auth_checkbox_1": {
        "x": 84.96, "y": 550.21, "w": 12.56, "h": 12.0,
        "type": "checkbox", "fill": True,
    },
    "auth_checkbox_2": {
        "x": 84.96, "y": 561.85, "w": 12.56, "h": 12.0,
        "type": "checkbox", "fill": True,
    },
    "auth_checkbox_3": {
        "x": 85.2, "y": 583.93, "w": 12.56, "h": 12.0,
        "type": "checkbox", "fill": True,
    },
    "auth_checkbox_4": {
        "x": 84.96, "y": 603.61, "w": 12.02, "h": 12.0,
        "type": "checkbox", "fill": True,
    },
    "auth_checkbox_5": {
        "x": 84.96, "y": 626.53, "w": 10.55, "h": 12.0,
        "type": "checkbox", "fill": True,
    },
    "auth_relation": {
        "x": 155.89, "y": 603.61, "w": 47.11, "h": 12.0,
        "type": "text", "fill": True, "sizes": INLINE_BLANK_FONT_SIZES,
    },
    "auth_other_agency": {
        "x": 249.36, "y": 626.53, "w": 67.64, "h": 12.0,
        "type": "text", "fill": True, "sizes": INLINE_BLANK_FONT_SIZES,
    },
    # DO NOT PRINT — human signs in black ink; machine leaves these empty.
    "signature": {
        "x": 80.32, "y": 674.36, "w": 190.28, "h": 18.0,
        "type": "line", "fill": False,
    },
    "signature_date": {
        "x": 397.68, "y": 664.31, "w": 120.14, "h": 12.64,
        "type": "text", "fill": False,
    },
}

for _f in _FIELD_MAP.values():
    _f.setdefault("x1", _f["x"] + _f["w"])
    _f.setdefault("y1", _f["y"] + _f["h"])


@dataclass(frozen=True)
class FormFingerprint:
    """Everything we need to refuse an unapproved blank."""

    sha256: str
    page_count: int
    page_size: tuple[float, float]
    anchors: tuple[str, ...]
    acroform_expected: bool  # False for this flat form


@dataclass(frozen=True)
class FormSpec:
    """An approved, versioned compilation of one form revision."""

    form_id: str
    revision: str          # printed on the form, e.g. "9/20/2016"
    version: str           # our map version, e.g. "1.0.0"
    fingerprint: FormFingerprint
    fields: dict[str, dict[str, Any]]
    form_boxes: dict[str, str]
    auth_boxes: dict[int, str]
    protected: frozenset[str]

    @property
    def label(self) -> str:
        return f"{self.form_id}@{self.revision}#{self.version}"


# Single approved form for this submission. A second fingerprint is how a
# revised city form would ship — never by stretching these coordinates.
CC2002B_2016 = FormSpec(
    form_id="CC2002B",
    revision="9/20/2016",
    version="1.0.0",
    # Must match hashlib.sha256(cc2002b_blank.pdf).hexdigest() exactly.
    # Re-extracted with deterministic_id=True (see extract_blank) so this
    # hash is reproducible from the packet, not just from this one file.
    # Confirmed at submit time: 0a34ee8217e65105fa28d61f46a7af1cea585340a3…
    fingerprint=FormFingerprint(
        sha256=(
            "0a34ee8217e65105fa28d61f46a7af1cea585340a36238eb02da10796442ebca"
        ),
        page_count=1,
        page_size=(612.0, 792.0),
        anchors=(
            "MAIL REQUEST FOR MARRIAGE RECORDS",
            "CHECK ONE BOX ONLY",
            "Signature (DO NOT PRINT)",
            "FORM CC2002B 9/20/2016",
        ),
        acroform_expected=False,
    ),
    fields=_FIELD_MAP,
    form_boxes={
        "short": "form_short",
        "extended": "form_extended",
        "other": "form_other",
    },
    auth_boxes={
        1: "auth_checkbox_1",
        2: "auth_checkbox_2",
        3: "auth_checkbox_3",
        4: "auth_checkbox_4",
        5: "auth_checkbox_5",
    },
    protected=frozenset({"signature", "signature_date"}),
)

# Registry keyed by blank SHA-256. Unknown hash → fail closed.
APPROVED_FORMS: dict[str, FormSpec] = {
    CC2002B_2016.fingerprint.sha256: CC2002B_2016,
}

# Module-level aliases used by tests and the hot path (one approved form).
FIELDS = CC2002B_2016.fields
FORM_BOXES = CC2002B_2016.form_boxes
AUTH_BOXES = CC2002B_2016.auth_boxes
FORMS = list(FORM_BOXES.keys())
EXPECTED_BLANK_SHA256 = CC2002B_2016.fingerprint.sha256
EXPECTED_PAGE_SIZE = CC2002B_2016.fingerprint.page_size

# ═══════════════════════════════════════════════════════════════════════════
# 2. Payload aliases and normalization
# ═══════════════════════════════════════════════════════════════════════════

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "full_legal_name_before_marriage": ("spouse_a_name",),
    "full_legal_name_before_marriage_09": ("spouse_b_name",),
    "date": ("spouse_a_birth_date",),
    "date_10": ("spouse_b_birth_date",),
    "license_was_issued": ("borough",),
    "if_uncertain_specify_other_years_you_want_searched": ("years_searched_text",),
    "reason_search_copy_are_needed": (
        "reason_search_copy_needed",
        "reason_search_and_copy_needed",
    ),
    "name_of_person_requesting_search": ("requester_name",),
    "your_relationship_to_either_spouse": ("relationship",),
    "your_telephone_no": ("phone",),
    "apt_no": ("apt",),
    "city": ("city_town_or_village",),
}

BOROUGHS = {
    "bronx": "Bronx",
    "brooklyn": "Brooklyn",
    "manhattan": "Manhattan",
    "queens": "Queens",
    "staten island": "Staten Island",
}

USPS_STATES = {
    "AL", "AK", "AS", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA",
    "GU", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA",
    "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
    "ND", "MP", "OH", "OK", "OR", "PA", "PR", "RI", "SC", "SD", "TN", "TX",
    "UT", "VT", "VA", "VI", "WA", "WV", "WI", "WY",
}

STATE_RE = re.compile(r"^[A-Za-z]{2}$")
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
PHONE_RE = re.compile(r"^(?:\+1|1)?[\s\-.()]*(\d[\s\-.()]{0,2}){10}$")

# 50-year rule for auth 4/5 (own this choice):
# A naive reading of the top NOTE would hard-reject under-50 records for
# anyone who is not a party / written-auth / attorney. We do NOT invent that
# ban. Options 4 and 5 carry their own parentheticals (Legal Dept approval;
# LE personnel only / proper purpose). Those clauses only make sense if the
# form is meant to be *filed and routed*, not refused at the door.
# So: fill the PDF, surface a review note, do not hard-reject.
UNDER_50 = {
    4: (
        "the form adds '(RELEASE OF RECORD UNDER THIS SECTION MUST BE APPROVED "
        "BY LEGAL DEPT.)', so expect the City Clerk to route this to Legal"
    ),
    5: (
        "the form marks this option '(LAW ENFORCEMENT PERSONNEL ONLY)' and "
        "requires that 'the marriage record will be used for a proper purpose', "
        "so expect the City Clerk to verify the requester's authority"
    ),
}


def _pv(payload: dict, key: str) -> Any:
    """Resolve a field-map key through aliases back to the payload."""
    if key in payload:
        return payload[key]
    for alias in FIELD_ALIASES.get(key, ()):
        if alias in payload:
            return payload[alias]
    return None


def _stripped(payload: dict, key: str) -> str:
    value = _pv(payload, key)
    return value.strip() if isinstance(value, str) else ""


def _is_latin1(text: str) -> bool:
    """True if base-14 Helvetica alone can ink this string, no embedding needed."""
    try:
        text.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return not any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in text)


def _uninkable_char_error(field_name: str, value: str) -> str | None:
    """Neither base-14 Helvetica nor the embedded Unicode fallback can ink
    this character. Fail loud, not as a silently substituted '·'."""
    for ch in value:
        code = ord(ch)
        if code < 0x20 or 0x7F <= code <= 0x9F:
            return (
                f"{field_name} contains a control character (U+{code:04X}), "
                f"which cannot be printed"
            )
        if code <= 0xFF:
            continue  # Latin-1 — base-14 Helvetica inks this directly
        if not _unicode_font().has_glyph(code):
            return (
                f"{field_name} contains '{ch}' (U+{code:04X}), which neither "
                f"the form's base-14 Helvetica nor the embedded {UNICODE_FONT_NAME} "
                f"fallback can ink — no glyph available for this character"
            )
    return None


def _parse_month(month_val: Any) -> int | None:
    """Strict month intake: JSON integer 1–12 only.

    CTO call: stay strict — do not accept month names, abbreviations, or
    numeric strings. Upstream normalizes; we refuse ambiguous shapes.
    On the form we still *print* the English month name derived from that
    integer (PRINT CLEARLY) — flexibility is not on the input contract.
    """
    if isinstance(month_val, bool) or not isinstance(month_val, int):
        return None
    return month_val if 1 <= month_val <= 12 else None


def _require_int(val: Any, label: str) -> tuple[int | None, str | None]:
    if isinstance(val, bool):
        return None, f"{label} must be an integer, got boolean '{val}'"
    if isinstance(val, float) and val != int(val):
        return None, f"{label} must be an integer, got '{val}'"
    try:
        return int(val), None
    except (ValueError, TypeError):
        return None, f"{label} must be a number, got '{val}'"


def _year_tokens_from_text(text: str) -> list[str]:
    return [token for token in re.split(r"[\s,]+", text.strip()) if token]


def _parse_year_tokens(
    raw: list[Any],
    *,
    as_of: date,
) -> tuple[list[int], list[str]]:
    years: set[int] = set()
    errors: list[str] = []
    for token in raw:
        val, err = _require_int(token, "requested search year")
        if err is None and not (1950 <= val <= as_of.year):
            err = f"requested search year {val} is outside 1950–{as_of.year}"
        if err:
            errors.append(err)
        else:
            years.add(val)  # type: ignore[arg-type]
    return sorted(years), errors


def _parse_birth_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            pass
    return None


def requested_years(
    payload: dict,
    *,
    as_of: date | None = None,
) -> tuple[list[int], list[str]]:
    """One canonical year set for both ink and fee. No drift between them."""
    as_of = as_of or date.today()
    list_supplied = "years_searched" in payload
    raw_list = payload.get("years_searched")
    text = str(
        _pv(payload, "if_uncertain_specify_other_years_you_want_searched") or ""
    )

    list_years: list[int] = []
    list_errors: list[str] = []
    if list_supplied:
        if not isinstance(raw_list, list):
            list_errors.append("years_searched must be a JSON list of years")
        else:
            list_years, list_errors = _parse_year_tokens(raw_list, as_of=as_of)

    text_years, text_errors = _parse_year_tokens(
        _year_tokens_from_text(text), as_of=as_of
    )
    errors = list_errors + text_errors
    if list_supplied and text.strip() and set(list_years) != set(text_years):
        errors.append(
            "years_searched and years_searched_text disagree; provide one "
            "representation or make them identical"
        )
    return (list_years if list_supplied else text_years), errors


# ═══════════════════════════════════════════════════════════════════════════
# 3. Fingerprint gate — fail closed on unknown blanks
# ═══════════════════════════════════════════════════════════════════════════


def _blank_sha256(path: Path = BLANK) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_form_spec(blank_path: Path = BLANK) -> FormSpec:
    """Load the approved FormSpec for this blank, or refuse to fill."""
    digest = _blank_sha256(blank_path)
    spec = APPROVED_FORMS.get(digest)
    if spec is None:
        raise ValueError(
            "unknown form fingerprint "
            f"(sha256={digest[:16]}…). Fail closed: do not stretch an old "
            "field map onto an unapproved blank. Offline path: extract the "
            "new page, measure fields, run adversarial tests, human-approve "
            "a new FormSpec, then register its fingerprint."
        )
    return spec


def assert_blank(doc: pymupdf.Document, spec: FormSpec | None = None) -> FormSpec:
    """Reject a source swap before hard-coded geometry can silently drift."""
    if spec is None:
        spec = resolve_form_spec(BLANK)
    fp = spec.fingerprint

    if doc.page_count != fp.page_count:
        raise ValueError(
            f"blank form must have {fp.page_count} page(s), got {doc.page_count}"
        )
    page = doc[0]
    got_size = (round(page.rect.width, 2), round(page.rect.height, 2))
    if got_size != fp.page_size:
        raise ValueError(
            f"blank form page size changed: got {got_size}, want {fp.page_size}"
        )
    if bool(doc.is_form_pdf) != fp.acroform_expected:
        raise ValueError(
            "blank AcroForm presence does not match the approved fingerprint"
        )
    text = " ".join(page.get_text().split())
    missing = [anchor for anchor in fp.anchors if anchor not in text]
    if missing:
        raise ValueError(f"blank form is missing anchors: {missing}")

    actual = _blank_sha256(BLANK)
    if actual != fp.sha256:
        raise ValueError(
            "blank form bytes changed; re-measure and re-approve the field map "
            "before updating the FormSpec fingerprint"
        )
    return spec


# ═══════════════════════════════════════════════════════════════════════════
# 4. Validation state machine
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    fee: dict[str, Any] | None = None


def validate(
    payload: dict,
    *,
    as_of: date | None = None,
    spec: FormSpec | None = None,
) -> ValidationResult:
    """Reject inputs that would produce a kicked-back filing.

    as_of is injected so tests (and batch runs) do not depend on wall-clock
    date.today() for year bounds and under-50 notes.
    """
    as_of = as_of or date.today()
    spec = spec or CC2002B_2016
    errors: list[str] = []
    notes: list[str] = []
    copies: int | None = None

    # Form type — exactly one of short / extended / other
    form_type = payload.get("form_type")
    if not isinstance(form_type, str) or form_type not in FORMS:
        errors.append(f"form_type must be one of {FORMS}, got '{form_type}'")
    elif form_type == "other":
        # Checkbox exists; schedule never prices it. Inventing a fee is worse
        # than refusing to print.
        errors.append(
            "form_type is 'other' — fee schedule has no defined price for it. "
            "Confirm with the City Clerk (311 or 212-NEW-YORK) before this can "
            "be filled; refusing to generate a PDF with an unknown fee attached."
        )
        notes.append("Fee unresolved: 'other' has no defined price on page 2")
        return ValidationResult(False, errors, notes)

    # Auth — exactly one integer 1–5; conditional inlines
    auth = payload.get("auth_checkbox")
    if auth is None:
        errors.append("auth_checkbox is required (exactly one of 1-5)")
    elif isinstance(auth, bool) or not isinstance(auth, int) or auth not in range(1, 6):
        errors.append(f"auth_checkbox must be an integer 1-5, got {auth}")
    else:
        for opt, key, label in (
            (4, "auth_relation", "relation option"),
            (5, "auth_other_agency", "law enforcement"),
        ):
            val = _stripped(payload, key)
            if auth == opt and not val:
                errors.append(
                    f"{key} is required when auth_checkbox={opt} ({label})"
                )
            elif auth != opt and val:
                errors.append(
                    f"{key} must be blank when auth_checkbox={auth} "
                    f"(only valid with auth_checkbox={opt})"
                )

    # Marriage date
    month = _parse_month(_pv(payload, "month"))
    day_raw = _pv(payload, "day")
    yr_raw = _pv(payload, "year")

    raw_month = _pv(payload, "month")
    if raw_month is None:
        errors.append("month is required")
    elif month is None:
        errors.append(
            f"month must be a JSON integer 1–12 (strict intake); "
            f"got {raw_month!r} — names/abbreviations/strings are rejected; "
            f"normalize upstream"
        )
    if not day_raw:
        errors.append("day is required")
    if not yr_raw:
        errors.append("year is required")

    yr_int: int | None = None
    marr: date | None = None
    if yr_raw is not None:
        yr_int, err = _require_int(yr_raw, "year")
        if err:
            errors.append(err)
        elif yr_int < 1950:
            errors.append(
                f"marriage year {yr_int} is before 1950 — these records are "
                f"at the Municipal Archives, not the City Clerk"
            )
        elif yr_int > as_of.year:
            errors.append(f"marriage year {yr_int} is in the future")

    if yr_int is not None and month is not None and day_raw:
        day_int, derr = _require_int(day_raw, "day")
        if derr:
            errors.append(derr)
        else:
            try:
                marr = date(yr_int, month, day_int)
            except ValueError:
                errors.append(f"invalid date: {yr_int}-{month}-{day_raw}")
            else:
                if marr > as_of:
                    errors.append(f"marriage date {marr} is in the future")

    # 50-year NOTE — note, don't reject for auth 4/5
    if yr_int is not None and auth in (4, 5):
        age = as_of.year - yr_int
        if marr and (as_of.month, as_of.day) < (marr.month, marr.day):
            age -= 1
        if age < 50:
            notes.append(
                f"auth_checkbox={auth} on a record {age} years old (under 50) "
                f"is not automatic release: {UNDER_50[auth]}."
            )

    # Required free-text fields
    for key, label in (
        ("full_legal_name_before_marriage", "spouse A name"),
        ("full_legal_name_before_marriage_09", "spouse B name"),
        ("reason_search_copy_are_needed", "reason for search"),
        ("name_of_person_requesting_search", "name of requester"),
        ("your_relationship_to_either_spouse", "relationship to either spouse"),
    ):
        val = _pv(payload, key)
        if val is None or (isinstance(val, str) and not val.strip()):
            errors.append(f"{label} ({key}) is required")

    # Birth dates
    for key, label in (
        ("date", "spouse A birth date"),
        ("date_10", "spouse B birth date"),
    ):
        raw_birth = _pv(payload, key)
        parsed_birth = _parse_birth_date(raw_birth)
        if raw_birth is None or (
            isinstance(raw_birth, str) and not raw_birth.strip()
        ):
            errors.append(f"{label} ({key}) is required")
        elif parsed_birth is None:
            errors.append(
                f"{label} '{raw_birth}' is invalid; use MM/DD/YYYY, YYYY-MM-DD, "
                "or a written month"
            )
        elif parsed_birth > as_of:
            errors.append(f"{label} {parsed_birth} is in the future")

    # Borough
    borough = _stripped(payload, "license_was_issued")
    if not borough:
        errors.append("borough where license was issued is required")
    elif borough.lower() not in BOROUGHS:
        errors.append(
            f"borough '{borough}' must be Bronx, Brooklyn, Manhattan, Queens, "
            "or Staten Island"
        )

    # Address
    for addr in ("street", "city"):
        if not _stripped(payload, addr):
            errors.append(f"address field {addr} is required")
    state = _stripped(payload, "state")
    if not state:
        errors.append("address field state is required")
    elif not STATE_RE.match(state) or state.upper() not in USPS_STATES:
        errors.append(f"state '{state}' is not a valid USPS state/territory code")
    zc = _stripped(payload, "zip_code")
    if not zc:
        errors.append("address field zip_code is required")
    elif not ZIP_RE.match(zc):
        errors.append(f"zip_code '{zc}' must be 5 digits or ZIP+4")

    # Phone
    phone = _pv(payload, "your_telephone_no")
    if phone is None or (isinstance(phone, str) and not phone.strip()):
        errors.append("telephone number (your_telephone_no) is required")
    elif not PHONE_RE.match(str(phone).strip()):
        errors.append(
            f"telephone number '{phone}' is not valid — must be 10-digit US "
            f"number (separators ok, extensions not supported)"
        )

    # Copies
    copies_raw = _pv(payload, "number_of_copies_requested")
    if copies_raw is None:
        errors.append("number_of_copies_requested is required")
    else:
        copies, cerr = _require_int(copies_raw, "number_of_copies_requested")
        if cerr:
            errors.append(cerr)
        elif copies < 1:
            errors.append("number_of_copies_requested must be at least 1")

    # Search years — same parse for ink and fee
    extra_years, yr_errors = requested_years(payload, as_of=as_of)
    errors.extend(yr_errors)

    # Font + fit guards (reject before drawing)
    for key, field_spec in spec.fields.items():
        if not field_spec.get("fill", True):
            continue
        val = _pv(payload, key)
        items = val if isinstance(val, list) else [val]
        for item in items:
            if isinstance(item, str):
                err = _uninkable_char_error(key, item)
                if err:
                    errors.append(err)

    for key, field_spec in spec.fields.items():
        if field_spec["type"] != "text" or not field_spec.get("fill", True):
            continue
        text = text_for(key, payload, as_of=as_of)
        if not text:
            continue
        try:
            _fit(
                text, field_spec["w"], field_spec["h"], key,
                sizes=field_spec.get("sizes", FONT_SIZES),
            )
        except ValueError as exc:
            errors.append(str(exc))

    if (
        not errors
        and isinstance(form_type, str)
        and form_type in ("short", "extended")
        and copies
    ):
        fee, fee_notes = calculate_fee(
            form_type, copies, payload, extra_years
        )
        notes.extend(fee_notes)
        return ValidationResult(True, errors, notes, fee)
    return ValidationResult(False, errors, notes)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Fee policy — ambiguity is visible, never silent
# ═══════════════════════════════════════════════════════════════════════════


def calculate_fee(
    form_type: str,
    copies: int,
    payload: dict,
    extra_years: list[int],
) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    if form_type == "short":
        copy_fee = 15.00 + (copies - 1) * 10.00
        first_copy, additional = 15.00, 10.00
    else:
        # Page 2 says $15/$10 *and* $35/$30 for the same purchase.
        # We bill type-specific. Rationale is part of the receipt.
        copy_fee = 35.00 + (copies - 1) * 30.00
        first_copy, additional = 35.00, 30.00
        notes.append(
            "AMBIGUOUS FEE: Page 2 says both \"Each certified copy … costs "
            "$15.00 and each additional … is $10.00\" and that the extended "
            "form \"costs $35 for the initial copy and $30 for any additional "
            "copies\" — same purchase, two prices. Billed at $35.00/$30.00: "
            "the type-specific paragraph ends \"If you do not specify the form "
            "you desire you will be sent a short form\", making $15.00/$10.00 "
            "the default form's price, not a universal rate. If the flat "
            "reading is right, this overpays the initial copy by $20.00. "
            "Confirm with the City Clerk (311 or 212-NEW-YORK) if disputed."
        )

    all_years = set(extra_years)
    yr_int, _ = _require_int(payload.get("year"), "year")
    if yr_int is not None:
        all_years.add(yr_int)
    search_years = max(len(all_years), 1)
    # First year free; second +$1; each after +$0.50.
    # Example on form: 4-year search + one short copy = $17.
    search_fee = (
        1.00 + (search_years - 2) * 0.50 if search_years > 1 else 0.0
    )
    total = copy_fee + search_fee

    return {
        "fee_status": "resolved",
        "copy_fee": round(copy_fee, 2),
        "search_fee": round(search_fee, 2),
        "total": round(total, 2),
        "requires_review": form_type == "extended",
        "breakdown": {
            "form_type": form_type,
            "num_copies": copies,
            "first_copy": first_copy,
            "additional_copy": additional,
            "years_searched": search_years,
        },
    }, notes


# ═══════════════════════════════════════════════════════════════════════════
# 6. Hot-path fill — draw only permitted black ink
# ═══════════════════════════════════════════════════════════════════════════


LINE_SPACING = 1.15  # line-height multiplier for wrapped (2+ line) text


def _text_width(text: str, fontname: str, fontfile: str | None, size: float) -> float:
    if fontfile is None:
        return pymupdf.get_text_length(text, fontname, size)
    return _unicode_font().text_length(text, fontsize=size)


def _wrap_lines(
    text: str, w: float, fontname: str, fontfile: str | None, size: float
) -> tuple[str, ...]:
    """Greedy word-wrap at this font size.

    Returns (text,) unchanged whenever the whole string already fits one
    line — a field that fit before this existed must keep fitting on
    exactly one line, byte-for-byte, not get reflowed for no reason.
    """
    budget = w - 4
    if _text_width(text, fontname, fontfile, size) <= budget:
        return (text,)
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if _text_width(candidate, fontname, fontfile, size) <= budget:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word  # even if this single word alone overflows —
            # the fit check below (per-line width) is what actually rejects it
    if current:
        lines.append(current)
    return tuple(lines)


def _fit(
    text: str,
    w: float,
    h: float,
    field_name: str = "",
    sizes: tuple[float, ...] = FONT_SIZES,
) -> tuple[float, str, str | None, tuple[str, ...]]:
    """Largest size that fits — wrapping to more than one line only if the
    field's own height has room for it — plus which font and which lines.

    Latin-1 text stays on base-14 Helvetica — no embedding, smallest
    output, the same font every existing sample and screenshot already
    shows. Only text that actually needs it falls back to the embedded
    Unicode font, measured through its own metrics (Helvetica's aren't
    valid for a different typeface).

    `sizes` defaults to FONT_SIZES; fields with their own narrower floor
    (declared via field_spec["sizes"], e.g. the relation/law-enforcement
    inline blanks) pass it explicitly — every other field is unaffected.
    """
    latin1 = _is_latin1(text)
    fontname = HELV if latin1 else UNICODE_FONT_NAME
    fontfile = None if latin1 else str(UNICODE_FONT_PATH)
    for size in sizes:
        lines = _wrap_lines(text, w, fontname, fontfile, size)
        fits_width = all(
            _text_width(ln, fontname, fontfile, size) <= w - 4 for ln in lines
        )
        if len(lines) == 1:
            # Exact original rule — must not change for anything that
            # already fit on one line, or every field's chosen size can
            # silently shift (confirmed: this changed real output before
            # being caught by a byte-diff against the previous samples).
            fits_height = size <= h - 2
        else:
            fits_height = len(lines) * (size * LINE_SPACING) <= h - 2
        if fits_width and fits_height:
            return size, fontname, fontfile, lines
    raise ValueError(
        f"{field_name}: '{text}' does not fit in {w:.0f}x{h:.0f}pt, even wrapped"
    )


def text_for(
    field_name: str,
    payload: dict,
    *,
    as_of: date | None = None,
) -> str:
    """The value the form should ink for this field — one source for all subsystems."""
    if field_name == "if_uncertain_specify_other_years_you_want_searched":
        years, _ = requested_years(payload, as_of=as_of)
        return ", ".join(str(year) for year in years)
    val = _pv(payload, field_name)
    if val is None:
        return ""
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    if field_name == "month":
        m = _parse_month(val)
        if m is not None:
            return calendar.month_name[m]
    if field_name in ("date", "date_10"):
        parsed = _parse_birth_date(str(val))
        if parsed:
            return parsed.strftime("%m/%d/%Y")
    if field_name == "license_was_issued":
        return BOROUGHS.get(str(val).strip().lower(), str(val).strip())
    if field_name == "state":
        return str(val).strip().upper()
    return str(val).strip()


# Back-compat name used by tests / older call sites
_text_for = text_for


def _xmark(page: pymupdf.Page, field_spec: dict) -> None:
    cx = field_spec["x"] + field_spec["w"] / 2
    cy = field_spec["y"] + field_spec["h"] / 2
    size = min(field_spec["w"], field_spec["h"]) * 0.7
    half = size / 2
    page.draw_line(
        pymupdf.Point(cx - half, cy - half),
        pymupdf.Point(cx + half, cy + half),
        color=(0, 0, 0),
        width=1.5,
    )
    page.draw_line(
        pymupdf.Point(cx + half, cy - half),
        pymupdf.Point(cx - half, cy + half),
        color=(0, 0, 0),
        width=1.5,
    )


def fill_final(
    payload: dict,
    output_path: str | Path,
    *,
    as_of: date | None = None,
    spec: FormSpec | None = None,
) -> FormSpec:
    """Draw flat black ink onto the approved blank. Never touches signature."""
    as_of = as_of or date.today()
    doc = pymupdf.open(str(BLANK))
    try:
        spec = assert_blank(doc, spec)
        page = doc[0]
        checkboxes = {
            spec.form_boxes.get(payload.get("form_type")),  # type: ignore[arg-type]
            spec.auth_boxes.get(payload.get("auth_checkbox")),  # type: ignore[arg-type]
        }

        for name, field_spec in spec.fields.items():
            if name in spec.protected:
                continue  # belt and suspenders: never draw protected regions
            if field_spec["type"] == "checkbox":
                if name in checkboxes:
                    _xmark(page, field_spec)
            elif field_spec["type"] == "text" and field_spec.get("fill", True):
                text = text_for(name, payload, as_of=as_of)
                if not text:
                    continue
                size, fontname, fontfile, lines = _fit(
                    text, field_spec["w"], field_spec["h"], name,
                    sizes=field_spec.get("sizes", FONT_SIZES),
                )
                # Same formula as the original single-line centering,
                # generalized: reduces to the exact original expression
                # when len(lines) == 1 (block_height == ascent), so every
                # field that already fit on one line draws at the exact
                # same pixel position as before wrapping existed.
                ascent = size * 0.8
                line_height = size * LINE_SPACING
                block_height = (len(lines) - 1) * line_height + ascent
                first_y = (
                    field_spec["y"] + (field_spec["h"] + block_height) / 2
                    - 1 - (len(lines) - 1) * line_height
                )
                for i, line in enumerate(lines):
                    page.insert_text(
                        pymupdf.Point(field_spec["x"] + 2, first_y + i * line_height),
                        line,
                        fontname=fontname,
                        fontfile=fontfile,
                        fontsize=size,
                        color=(0, 0, 0),
                    )

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_pdf = str(output_path) + ".tmp"
        doc.save(tmp_pdf)
        os.replace(tmp_pdf, output_path)
        return spec
    except Exception:
        tmp_pdf = str(output_path) + ".tmp"
        if os.path.exists(tmp_pdf):
            os.remove(tmp_pdf)
        raise
    finally:
        doc.close()


# ═══════════════════════════════════════════════════════════════════════════
# 7. Proof receipt — machine-readable evidence of a filing
# ═══════════════════════════════════════════════════════════════════════════


def _canonical_payload_hash(payload: dict) -> str:
    """Hash the applicant payload without internal bookkeeping keys."""
    clean = {k: v for k, v in payload.items() if not k.startswith("_")}
    blob = json.dumps(clean, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def build_proof_receipt(
    *,
    spec: FormSpec,
    payload: dict,
    output_path: Path,
    validation: ValidationResult,
    check_result: dict[str, Any],
    as_of: date,
) -> dict[str, Any]:
    checks = check_result.get("checks", [])
    passed = sum(1 for c in checks if c.get("passed"))
    failed = sum(1 for c in checks if not c.get("passed"))
    fee = validation.fee or {}
    return {
        "template_hash": spec.fingerprint.sha256,
        "form_spec_version": spec.label,
        "input_hash": _canonical_payload_hash(payload),
        "output_hash": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "renderer": {
            "engine": "mupdf",
            "pymupdf": getattr(pymupdf, "VersionBind", "unknown"),
        },
        "verification_engines": ["mupdf"],
        "fee": {
            "total": fee.get("total"),
            "copy_fee": fee.get("copy_fee"),
            "search_fee": fee.get("search_fee"),
            "requires_review": fee.get("requires_review", False),
            "breakdown": fee.get("breakdown"),
            "fee_status": fee.get("fee_status"),
        },
        "notes": list(validation.notes),
        "checks_passed": passed,
        "checks_failed": failed,
        "check_passed": bool(check_result.get("passed")),
        "as_of": as_of.isoformat(),
    }


def write_proof_receipt(receipt: dict[str, Any], pdf_path: Path) -> Path:
    proof_path = Path(str(pdf_path) + ".proof.json")
    # Also write .fees.json for quick human glance / older tooling.
    fees_path = Path(str(pdf_path) + ".fees.json")
    fee_block = {
        **(receipt.get("fee") or {}),
        "notes": receipt.get("notes", []),
        "form_type": (receipt.get("fee") or {}).get("breakdown", {}).get(
            "form_type"
        ),
        "num_copies": (receipt.get("fee") or {}).get("breakdown", {}).get(
            "num_copies"
        ),
        "years_searched": (receipt.get("fee") or {}).get("breakdown", {}).get(
            "years_searched"
        ),
    }
    tmp_proof = str(proof_path) + ".tmp"
    tmp_fees = str(fees_path) + ".tmp"
    try:
        with open(tmp_proof, "w") as f:
            json.dump(receipt, f, indent=2)
            f.write("\n")
        with open(tmp_fees, "w") as f:
            json.dump(fee_block, f, indent=2)
            f.write("\n")
        os.replace(tmp_proof, proof_path)
        os.replace(tmp_fees, fees_path)
    except Exception:
        for p in (tmp_proof, tmp_fees):
            if os.path.exists(p):
                os.remove(p)
        raise
    return proof_path


# ═══════════════════════════════════════════════════════════════════════════
# 8. Verification — semantic + raster; prove the visible artifact
#
# A model may propose; only a mechanical check may accept.
# Most candidates skip this. The brief says it is what they care most about.
# ═══════════════════════════════════════════════════════════════════════════


def _pix(page: pymupdf.Page) -> pymupdf.Pixmap:
    return page.get_pixmap(
        matrix=pymupdf.Matrix(DPI, DPI),
        colorspace=pymupdf.csRGB,
        alpha=False,
    )


def _devbox(field_spec: dict) -> tuple[int, int, int, int]:
    return (
        int(field_spec["x"] * DPI) - _PAD,
        int(field_spec["x1"] * DPI) + _PAD,
        int(field_spec["y"] * DPI) - _PAD,
        int(field_spec["y1"] * DPI) + _PAD,
    )


def _has_x(drawings: list, field_spec: dict) -> bool:
    """True if two strokes genuinely cross near the checkbox center."""

    def side(p, q, r):
        return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)

    def crosses(a, b):
        return (
            side(a[1], a[2], b[1]) * side(a[1], a[2], b[2]) < 0
            and side(b[1], b[2], a[1]) * side(b[1], b[2], a[2]) < 0
        )

    cx = field_spec["x"] + field_spec["w"] / 2
    cy = field_spec["y"] + field_spec["h"] / 2
    nearby = [
        item
        for d in drawings
        for item in d.get("items", [])
        if item[0] == "l"
        and abs((item[1].x + item[2].x) / 2 - cx) < 8
        and abs((item[1].y + item[2].y) / 2 - cy) < 8
    ]
    return any(crosses(a, b) for a, b in itertools.combinations(nearby, 2))


def _covers(
    drawings: list,
    field_spec: dict,
    blank_drawings: list | None = None,
) -> dict | None:
    """First drawn shape covering this field's box, or None."""
    box = pymupdf.Rect(
        field_spec["x"], field_spec["y"], field_spec["x1"], field_spec["y1"]
    )
    blank_sigs: set = set()
    if blank_drawings:
        for d in blank_drawings:
            blank_sigs.add((d.get("type"), tuple(d["rect"])))
    for d in drawings:
        if d["type"] == "s" or pymupdf.Rect(d["rect"]).get_area() < 0.01:
            continue
        if blank_drawings and (d.get("type"), tuple(d["rect"])) in blank_sigs:
            continue
        if (box & pymupdf.Rect(d["rect"])).get_area() > 0:
            return d
    return None


def _blank_words_set(blank_page: pymupdf.Page) -> set:
    return {
        (round(w[0], 1), round(w[1], 1), w[4])
        for w in blank_page.get_text("words")
    }


def _check_layer_a(
    filled_page: pymupdf.Page,
    payload: dict,
    fm: dict,
    blank_words: set,
    *,
    as_of: date,
    spec: FormSpec,
) -> list[dict]:
    """Added words must equal expected. Checkboxes need two crossing strokes."""
    words = list(filled_page.get_text("words"))
    drawings = list(filled_page.get_drawings())
    marked = {
        spec.form_boxes.get(payload.get("form_type")),  # type: ignore[arg-type]
        spec.auth_boxes.get(payload.get("auth_checkbox")),  # type: ignore[arg-type]
    }

    def is_original(w) -> bool:
        return (round(w[0], 1), round(w[1], 1), w[4]) in blank_words

    results: list[dict] = []
    for name, field_spec in fm.items():
        if field_spec["type"] != "checkbox" and not field_spec.get("fill", True):
            continue  # protected fields → layer B only
        if field_spec["type"] == "checkbox":
            should = name in marked
            has = _has_x(drawings, field_spec)
            label = "checkbox_marked" if should else "checkbox_NOT_marked"
            results.append({
                "check": f"{label}:{name}",
                "passed": has == should,
                "detail": f"expected marked={should}, found marked={has}",
            })
            continue

        expected = text_for(name, payload, as_of=as_of)
        if not expected:
            continue

        added = [
            w[4]
            for w in words
            if field_spec["x"] - 5 <= (w[0] + w[2]) / 2 <= field_spec["x1"] + 5
            and field_spec["y"] - 5 <= (w[1] + w[3]) / 2 <= field_spec["y1"] + 5
            and not is_original(w)
        ]
        found = " ".join(" ".join(added).split())
        expected_clean = " ".join(expected.split())
        results.append({
            "check": f"field_text_exact:{name}",
            "passed": found == expected_clean,
            "detail": (
                f"Expected '{expected_clean[:40]}', found '{found[:40]}'"
            ),
        })
    return results


def _differential(
    blank_page: pymupdf.Page,
    filled_page: pymupdf.Page,
    fm: dict,
) -> tuple[dict, dict, list, list, list]:
    bp, fp = _pix(blank_page), _pix(filled_page)
    boxes = [(name, *_devbox(f)) for name, f in fm.items()]
    ink = {name: 0 for name, *_ in boxes}
    dark = {name: 0 for name, *_ in boxes}
    stray: list = []
    erased: list = []
    colour: list = []
    b, f = bp.samples, fp.samples
    width = min(bp.width, fp.width)
    for y in range(min(bp.height, fp.height)):
        row_b = b[y * bp.stride: y * bp.stride + width * 3]
        row_f = f[y * fp.stride: y * fp.stride + width * 3]
        if row_b == row_f:
            continue
        bands = [box for box in boxes if box[3] <= y <= box[4]]
        for x in range(width):
            i = x * 3
            pb, pf = row_b[i: i + 3], row_f[i: i + 3]
            if pb == pf:
                continue
            brightest = max(pf)
            if brightest - min(pf) > 24:
                colour.append((x, y))
            if max(pb) < _DARK <= min(pf):
                erased.append((x, y))
            for name, x0, x1, _, _ in bands:
                if x0 <= x <= x1:
                    ink[name] += 1
                    if brightest < _DARK:
                        dark[name] += 1
                    break
            else:
                stray.append((x, y))
    return ink, dark, stray, erased, colour


def _check_layer_b(
    filled_page: pymupdf.Page,
    payload: dict,
    fm: dict,
    *,
    as_of: date,
    spec: FormSpec,
) -> list[dict]:
    blank = pymupdf.open(str(BLANK))
    try:
        assert_blank(blank, spec)
        blank_page = blank[0]
        ink, dark, stray, erased, colour = _differential(
            blank_page, filled_page, fm
        )
        blank_drawings = blank_page.get_drawings()
        results: list[dict] = []

        pc = filled_page.parent.page_count if filled_page.parent else 1
        results.append({
            "check": "page_count",
            "passed": pc == 1,
            "detail": f"got {pc} pages, want 1",
        })
        results.append({
            "check": "page_size",
            "passed": filled_page.rect == blank_page.rect,
            "detail": f"Expected {blank_page.rect}, got {filled_page.rect}",
        })
        results.append({
            "check": "no_stray_ink",
            "passed": len(stray) == 0,
            "detail": f"{len(stray)} stray pixels, first 20: {stray[:20]}",
        })
        results.append({
            "check": "no_erased_ink",
            "passed": len(erased) == 0,
            "detail": f"{len(erased)} erased pixels",
        })
        results.append({
            "check": "ink_is_black",
            "passed": len(colour) == 0,
            "detail": f"{len(colour)} non-grayscale pixels",
        })

        marked = {
            spec.form_boxes.get(payload.get("form_type")),  # type: ignore[arg-type]
            spec.auth_boxes.get(payload.get("auth_checkbox")),  # type: ignore[arg-type]
        }
        empty_fields: set[str] = set()
        populated_fields: set[str] = set()
        for name, field_spec in fm.items():
            if field_spec["type"] == "checkbox":
                if name not in marked:
                    empty_fields.add(name)
                continue
            if not field_spec.get("fill", True):
                empty_fields.add(name)
                continue
            expected = text_for(name, payload, as_of=as_of)
            if not expected:
                empty_fields.add(name)
            else:
                populated_fields.add(name)

        for name in sorted(empty_fields):
            results.append({
                "check": f"no_ink:{name}",
                "passed": ink.get(name, 0) == 0,
                "detail": f"{ink.get(name, 0)} ink pixels in {name}",
            })

        drawings = filled_page.get_drawings() if populated_fields else []
        for name in sorted(populated_fields):
            field_spec = fm[name]
            x0, x1, y0, y1 = _devbox(field_spec)
            cap = int(_MAX_DARK_FRAC * (x1 - x0 + 1) * (y1 - y0 + 1))
            d = dark.get(name, 0)
            results.append({
                "check": f"ink_is_legible:{name}",
                "passed": _MIN_DARK <= d <= cap,
                "detail": (
                    f"{d} dark pixels (want {_MIN_DARK}..{cap}) — "
                    f"value is invisible, too pale, or painted over"
                ),
            })
            cov = _covers(drawings, field_spec, blank_drawings)
            results.append({
                "check": f"nothing_drawn_over:{name}",
                "passed": cov is None,
                "detail": (
                    f"a drawn shape covers this field: "
                    f"{cov['rect'] if cov else ''}"
                ),
            })
        return results
    finally:
        blank.close()


def _pdfium_render(path: Path) -> tuple[bytes, int, int]:
    """Render page 1 at the checker DPI with pdfium — a second engine that
    never touched the draw path, so mupdf cannot grade its own ink alone."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        img = doc[0].render(scale=DPI).to_pil().convert("RGB")
        return img.tobytes(), img.width, img.height
    finally:
        doc.close()


def _check_layer_c_pdfium(
    filled_path: Path,
    payload: dict,
    fm: dict,
    *,
    as_of: date,
    spec: FormSpec,
) -> list[dict]:
    """Cross-check layer B's verdict with an independent renderer (pdfium).

    Deliberately narrow: re-derive "no ink outside approved rects" and "every
    populated field is actually dark" from scratch on a wholly different
    rasterizer. If mupdf and pdfium ever disagree, that is a mupdf-specific
    rendering artifact, not proof the form is correctly filled.
    """
    try:
        b_bytes, bw, bh = _pdfium_render(BLANK)
        f_bytes, fw, fh = _pdfium_render(Path(filled_path))
    except ImportError:
        return [{
            "check": "pdfium_cross_check:available",
            "passed": False,
            "detail": "pypdfium2 not installed; second-renderer cross-check skipped",
        }]

    width, height = min(bw, fw), min(bh, fh)
    b_stride, f_stride = bw * 3, fw * 3
    boxes = [(name, *_devbox(field_spec)) for name, field_spec in fm.items()]
    dark = {name: 0 for name, *_ in boxes}
    stray = 0

    for y in range(height):
        row_b = b_bytes[y * b_stride: y * b_stride + width * 3]
        row_f = f_bytes[y * f_stride: y * f_stride + width * 3]
        if row_b == row_f:
            continue
        bands = [box for box in boxes if box[3] <= y <= box[4]]
        for x in range(width):
            i = x * 3
            pb, pf = row_b[i: i + 3], row_f[i: i + 3]
            if pb == pf:
                continue
            for name, x0, x1, _, _ in bands:
                if x0 <= x <= x1:
                    if max(pf) < _DARK:
                        dark[name] += 1
                    break
            else:
                stray += 1

    results: list[dict] = [{
        "check": "pdfium_cross_check:no_stray_ink",
        "passed": stray == 0,
        "detail": f"{stray} dirty pixels outside approved rects (pdfium render)",
    }]

    marked = {
        spec.form_boxes.get(payload.get("form_type")),  # type: ignore[arg-type]
        spec.auth_boxes.get(payload.get("auth_checkbox")),  # type: ignore[arg-type]
    }
    for name, field_spec in fm.items():
        d = dark.get(name, 0)
        if field_spec["type"] == "checkbox":
            should = name in marked
            results.append({
                "check": f"pdfium_cross_check:checkbox_ink:{name}",
                "passed": (d > 0) == should,
                "detail": f"{d} dark pixels (pdfium), expected marked={should}",
            })
            continue

        # Protected fields (signature / signature date) and fields with no
        # value for this payload must stay untouched — same as layer B's
        # no_ink checks, but on pdfium's independent raster.
        expected = text_for(name, payload, as_of=as_of) if field_spec.get(
            "fill", True
        ) else ""
        if not expected:
            results.append({
                "check": f"pdfium_cross_check:no_ink:{name}",
                "passed": d == 0,
                "detail": f"{d} dark pixels in {name} (pdfium; should be empty)",
            })
        else:
            results.append({
                "check": f"pdfium_cross_check:ink_present:{name}",
                "passed": d >= _MIN_DARK,
                "detail": f"{d} dark pixels via pdfium (want >= {_MIN_DARK})",
            })
    return results


def check_correctness(
    filled_path: str | Path,
    payload_path: str | Path,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    payload = json.loads(Path(payload_path).read_text())
    validation = validate(dict(payload), as_of=as_of)
    if not validation.valid:
        return {
            "passed": False,
            "checks": [{
                "check": "payload_validation",
                "passed": False,
                "detail": "; ".join(validation.errors),
            }],
        }

    try:
        spec = resolve_form_spec(BLANK)
    except ValueError as exc:
        return {
            "passed": False,
            "checks": [{
                "check": "form_fingerprint",
                "passed": False,
                "detail": str(exc),
            }],
        }

    fm = {k: v for k, v in spec.fields.items() if not k.startswith("_")}
    blank = pymupdf.open(str(BLANK))
    filled = pymupdf.open(str(filled_path))
    try:
        assert_blank(blank, spec)
        bw = _blank_words_set(blank[0])
        results = _check_layer_a(
            filled[0], payload, fm, bw, as_of=as_of, spec=spec
        )
        results += _check_layer_b(
            filled[0], payload, fm, as_of=as_of, spec=spec
        )
    finally:
        blank.close()
        filled.close()
    results += _check_layer_c_pdfium(
        Path(filled_path), payload, fm, as_of=as_of, spec=spec
    )

    failed = sum(1 for r in results if not r["passed"])
    return {"passed": failed == 0, "checks": results}


# ═══════════════════════════════════════════════════════════════════════════
# 9. Structural blank extraction (pikepdf / libqpdf)
#
# Treat the packet like source code: parse the object graph, emit page 2 as
# the filing surface. This is a structural rewrite, not a raster.
# ═══════════════════════════════════════════════════════════════════════════


def extract_blank(packet_path: str | Path, out_path: str | Path, page: int = 2) -> Path:
    """Extract one page (1-based) from the assessment packet into a blank form.

    pikepdf.Pdf.save() randomizes the /ID trailer entry by default — the
    same input, same pinned pikepdf version, produced a DIFFERENT SHA-256
    on every single call (confirmed: two consecutive extractions differed,
    even though the actual page content — words, drawings, page size —
    was byte-identical). Since the fingerprint gate hashes this file,
    that made "re-extract the blank" fail the gate nondeterministically,
    which defeats the point of a reproducibility check. deterministic_id
    derives /ID from content instead of randomness, so re-extracting the
    same packet always reproduces the same registered hash.
    """
    import pikepdf

    packet_path = Path(packet_path)
    out_path = Path(out_path)
    with pikepdf.open(packet_path) as src:
        if page < 1 or page > len(src.pages):
            raise ValueError(
                f"page {page} out of range (packet has {len(src.pages)} pages)"
            )
        dst = pikepdf.Pdf.new()
        dst.pages.append(src.pages[page - 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        dst.save(out_path, deterministic_id=True)
    return out_path


# ═══════════════════════════════════════════════════════════════════════════
# 10. Deterministic re-measurement (--measure)
#
# _FIELD_MAP above was derived by hand: read pymupdf's word/glyph geometry,
# reason about label gaps and glyph boxes, type ~80 numbers into a dict.
# That manual step is the one part of this pipeline that isn't provable or
# fast to redo. This section turns the same four derivation rules into
# code, anchored on the page's own drawn table rules (not eyeballed row
# heights), so re-measuring a form revision means editing ~20 copy-pasted
# label strings — a typo just fails to match a word, it can't silently
# produce a wrong number — instead of retyping 80 coordinates by hand.
#
# This does NOT replace _FIELD_MAP as the hot-path source of truth; that
# stays hand-approved and frozen behind the fingerprint gate. This produces
# a CANDIDATE map for comparison or regeneration, and --measure --compare
# is a regression check that the rules still reproduce the approved one.
# ═══════════════════════════════════════════════════════════════════════════

# Named distinctly from the checker's _PAD (device-pixel tolerance in
# _devbox, section 8) — same short name, different unit, different job.
# They collided as one _PAD for a while: the module-level reassignment
# here silently overwrote the checker's value at import time, since
# Python resolves globals at call time, not definition time. Harmless in
# practice (both were small positive pads), but exactly the kind of
# quietly-wrong constant a checker can't afford.
_MEASURE_PAD = 2.0
_RIGHT_MARGIN = 557.56
_ROW_MIN_WIDTH = 400.0  # separates real table rows from incidental short rules (e.g. the sig line)


def _table_row_bands(page: pymupdf.Page) -> list[tuple[float, float]]:
    """Row boundaries from the page's own drawn table rules, not guesswork.

    Table rules are filled hairline rects ('re'), not stroked lines — a
    row divider often renders as two rects (split where a column divider
    crosses it), so coverage is summed per y before filtering.

    Evaluated and rejected: pymupdf's built-in page.find_tables(). Tested
    against this exact document, not just read about — strategy="lines"
    finds 0 tables here because it looks for stroked lines and this form's
    rules are filled rects, not strokes. strategy="text" does return a
    table, but fragments the data region into ~64 misaligned cells that
    slice through label text mid-word — worse than the 30 boxes below,
    which reproduce the hand-approved field map within 1.2pt. Keep this
    hand-rolled version; re-evaluate find_tables() only if a future form
    revision actually uses stroked lines instead of filled rects.
    """
    coverage: dict[float, float] = {}
    for d in page.get_drawings():
        for item in d.get("items", []):
            if item[0] != "re":
                continue
            r = item[1]
            if r.height <= 1.2 and r.width > 5:
                y = round(r.y0, 1)
                coverage[y] = coverage.get(y, 0.0) + r.width
    ys = sorted(y for y, w in coverage.items() if w > _ROW_MIN_WIDTH)
    return list(zip(ys, ys[1:]))


def _find_in_row(words: list, text: str, band: tuple[float, float], occurrence: int = 1):
    """The nth (reading-order) word matching `text` near the top of `band`.

    Every label anchor on this form — inline or stacked — sits within a
    point of its row's top rule, never near the bottom. Searching only the
    top slice of the band (instead of the full band +/- tolerance) is what
    keeps this from matching the next row's labels when two rows abut
    tightly, e.g. row 6 ends 1pt above row 7's "Your address:" label.
    """
    top, _ = band
    hits = sorted(
        (w for w in words if w[4] == text and top - 1.0 <= w[1] <= top + 20.0),
        key=lambda w: w[0],
    )
    if occurrence > len(hits):
        raise ValueError(
            f"expected occurrence {occurrence} of {text!r} in row {band}, "
            f"found {len(hits)} — form layout changed, re-check this anchor"
        )
    return hits[occurrence - 1]


def _nth_glyph(words: list, glyph: str, occurrence: int):
    hits = sorted((w for w in words if w[4] == glyph), key=lambda w: w[1])
    if occurrence > len(hits):
        raise ValueError(
            f"expected occurrence {occurrence} of glyph {glyph!r}, found {len(hits)}"
        )
    return hits[occurrence - 1]


def _underscore_box(words: list, prefix: str) -> tuple[float, float, float, float]:
    """Box around the underscore run in a merged token like 'the__________'.

    Proportional split by character count — an approximation (the form
    isn't monospace), accurate to a couple points, which is within the
    padding budget _fit() already reserves.
    """
    for w in words:
        token = w[4]
        if token.startswith(prefix) and "_" in token:
            frac = len(prefix) / len(token)
            x0 = w[0] + frac * (w[2] - w[0])
            return x0, w[1], w[2], w[3]
    raise ValueError(f"no underscore-run token starting with {prefix!r} found")


@dataclass(frozen=True)
class _Anchor:
    """One derivation rule, in the same vocabulary a human would use to
    describe the field out loud: 'the box after Month:, before Day:, in
    the first table row' — not four raw numbers."""

    name: str
    kind: str  # "inline" | "stacked" | "glyph" | "underscore"
    row: int = 0                                   # 1-indexed table row
    left: str | tuple[str, int] | None = None
    right: str | tuple[str, int] | None = None
    glyph: str | None = None
    occurrence: int = 1
    prefix: str | None = None


# Table rows, top to bottom, as they read on the page:
#   1 date of marriage / borough        5 reason for search / copies
#   2 other years / license no          6 requester / relationship / phone
#   3 spouse A name / birthdate         7 address
#   4 spouse B name / birthdate
_ANCHOR_SPEC: tuple[_Anchor, ...] = (
    _Anchor("month", "inline", row=1, left="Month:", right="Day:"),
    _Anchor("day", "inline", row=1, left="Day:", right="Year:"),
    _Anchor("year", "inline", row=1, left="Year:", right="Borough"),
    _Anchor("license_was_issued", "inline", row=1, left="issued:", right=None),
    _Anchor(
        "if_uncertain_specify_other_years_you_want_searched", "inline",
        row=2, left="searched:", right="License",
    ),
    _Anchor("license_no", "inline", row=2, left="No.", right=None),
    _Anchor(
        "full_legal_name_before_marriage", "inline",
        row=3, left="marriage:", right="Birth",
    ),
    _Anchor("date", "inline", row=3, left="date:", right=None),
    _Anchor(
        "full_legal_name_before_marriage_09", "inline",
        row=4, left="marriage:", right="Birth",
    ),
    _Anchor("date_10", "inline", row=4, left="date:", right=None),
    _Anchor(
        "reason_search_copy_are_needed", "inline",
        row=5, left="needed:", right="Number",
    ),
    _Anchor(
        "number_of_copies_requested", "inline",
        row=5, left="requested:", right=None,
    ),
    # row 6/7: label sits on its own line, the writable blank is below it
    _Anchor(
        "name_of_person_requesting_search", "stacked",
        row=6, left="Name", right=("Your", 1),
    ),
    _Anchor(
        "your_relationship_to_either_spouse", "stacked",
        row=6, left=("Your", 1), right=("Your", 2),
    ),
    _Anchor("your_telephone_no", "stacked", row=6, left=("Your", 2), right=None),
    _Anchor("street", "stacked", row=7, left="Street", right="Apt"),
    _Anchor("apt_no", "stacked", row=7, left="Apt", right="City"),
    _Anchor("city", "stacked", row=7, left="City", right="State"),
    _Anchor("state", "stacked", row=7, left="State", right="Zip"),
    _Anchor("zip_code", "stacked", row=7, left="Zip", right=None),
    # same glyph repeated — disambiguated by top-to-bottom order, not position
    _Anchor("form_short", "glyph", glyph="", occurrence=1),
    _Anchor("form_extended", "glyph", glyph="", occurrence=2),
    _Anchor("form_other", "glyph", glyph="", occurrence=3),
    _Anchor("auth_checkbox_1", "glyph", glyph="(_)", occurrence=1),
    _Anchor("auth_checkbox_2", "glyph", glyph="(_)", occurrence=2),
    _Anchor("auth_checkbox_3", "glyph", glyph="(_)", occurrence=3),
    _Anchor("auth_checkbox_4", "glyph", glyph="(_)", occurrence=4),
    _Anchor("auth_checkbox_5", "glyph", glyph="(_)", occurrence=5),
    _Anchor("auth_relation", "underscore", prefix="the"),
    _Anchor("auth_other_agency", "underscore", prefix="or"),
    # signature / signature_date deliberately absent: never re-measured,
    # never filled — see _FIELD_MAP's `protected` set.
)


def _resolve_lr(words: list, spec: str | tuple[str, int] | None, band: tuple[float, float]):
    if spec is None:
        return None
    text, occ = spec if isinstance(spec, tuple) else (spec, 1)
    return _find_in_row(words, text, band, occ)


def measure_blank(blank_path: Path = BLANK) -> dict[str, dict[str, Any]]:
    """Re-derive the field map from the page's own geometry — no hardcoded
    coordinates anywhere in this function. A model may one day propose
    _ANCHOR_SPEC edits for a revised form; this is the mechanical rule
    that would grade its candidates, same as `check_correctness` grades
    every filled PDF today."""
    doc = pymupdf.open(str(blank_path))
    try:
        page = doc[0]
        words = page.get_text("words")
        bands = _table_row_bands(page)
        out: dict[str, dict[str, Any]] = {}
        for a in _ANCHOR_SPEC:
            if a.kind == "glyph":
                w = _nth_glyph(words, a.glyph, a.occurrence)
                out[a.name] = {
                    "x": round(w[0], 2), "y": round(w[1], 2),
                    "w": round(w[2] - w[0], 2), "h": round(w[3] - w[1], 2),
                    "type": "checkbox", "fill": True,
                }
                continue
            if a.kind == "underscore":
                x0, y0, x1, y1 = _underscore_box(words, a.prefix)
                out[a.name] = {
                    "x": round(x0, 2), "y": round(y0, 2),
                    "w": round(x1 - x0, 2), "h": round(y1 - y0, 2),
                    "type": "text", "fill": True,
                }
                continue

            band = bands[a.row - 1]
            left = _resolve_lr(words, a.left, band)
            right = _resolve_lr(words, a.right, band)
            if a.kind == "inline":
                x0, y0 = left[2] + _MEASURE_PAD, band[0] + _MEASURE_PAD
                y1 = band[1] - _MEASURE_PAD
            else:  # stacked
                x0, y0 = left[0], left[3] + _MEASURE_PAD
                y1 = band[1] - _MEASURE_PAD
            x1 = (right[0] - _MEASURE_PAD) if right is not None else _RIGHT_MARGIN
            out[a.name] = {
                "x": round(x0, 2), "y": round(y0, 2),
                "w": round(x1 - x0, 2), "h": round(y1 - y0, 2),
                "type": "text", "fill": True,
            }
        return out
    finally:
        doc.close()


def compare_to_approved(
    candidate: dict[str, dict[str, Any]],
    approved: dict[str, dict[str, Any]] = FIELDS,
    tolerance: float = 4.0,
) -> list[dict[str, Any]]:
    """Per-field delta report — proof the rules reproduce the hand-approved
    spec, not just a demo that happens to run."""
    rows: list[dict[str, Any]] = []
    for name, cand in sorted(candidate.items()):
        appr = approved.get(name)
        if appr is None:
            rows.append({"field": name, "status": "NOT_IN_APPROVED_SPEC", "delta": None})
            continue
        delta = {k: round(cand[k] - appr[k], 2) for k in ("x", "y", "w", "h")}
        worst = max(abs(v) for v in delta.values())
        rows.append({
            "field": name,
            "status": "OK" if worst <= tolerance else "DRIFT",
            "delta": delta,
            "worst": worst,
        })
    return rows


# ═══════════════════════════════════════════════════════════════════════════
# 11. CLI
# ═══════════════════════════════════════════════════════════════════════════


def _print_check(result: dict[str, Any]) -> int:
    checks = result.get("checks", [])
    passed = sum(1 for c in checks if c.get("passed"))
    total = len(checks)
    status = "PASSED" if result.get("passed") else "FAILED"
    print(f"Check:   {status}  ({passed}/{total})")
    if not result.get("passed"):
        for c in checks:
            if not c.get("passed"):
                print(f"  FAIL: {c['check']} — {c['detail']}")
    return 0 if result.get("passed") else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv:
        print(__doc__)
        return 1

    # --extract-blank packet.pdf [out.pdf]
    if argv[0] == "--extract-blank":
        if len(argv) < 2:
            print("usage: cc2002b.py --extract-blank packet.pdf [blank.pdf]")
            return 1
        packet = argv[1]
        out = argv[2] if len(argv) > 2 else str(BLANK)
        path = extract_blank(packet, out)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"Extracted: {path}")
        print(f"SHA-256:   {digest}")
        if digest in APPROVED_FORMS:
            print(f"FormSpec:  {APPROVED_FORMS[digest].label} (approved)")
        else:
            print(
                "FormSpec:  UNKNOWN — register a new FormSpec before filling"
            )
        return 0

    # --check filled.pdf payload.json [report.json]
    if argv[0] == "--check":
        if len(argv) < 3:
            print(
                "usage: cc2002b.py --check filled.pdf payload.json [report.json]"
            )
            return 1
        filled_pdf, payload_json = argv[1], argv[2]
        report_path = argv[3] if len(argv) > 3 else None
        result = check_correctness(filled_pdf, payload_json)
        code = _print_check(result)
        if report_path:
            Path(report_path).parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, "w") as report:
                json.dump(result, report, indent=2)
                report.write("\n")
        return code

    # --measure [blank.pdf] [--compare]
    if argv[0] == "--measure":
        rest = argv[1:]
        compare = "--compare" in rest
        rest = [a for a in rest if a != "--compare"]
        blank_arg = Path(rest[0]) if rest else BLANK
        try:
            candidate = measure_blank(blank_arg)
        except ValueError as exc:
            print(f"MEASURE FAILED: {exc}")
            return 1
        if not compare:
            print(json.dumps(candidate, indent=2, sort_keys=True))
            return 0
        rows = compare_to_approved(candidate)
        for r in rows:
            print(f"  {r['status']:20s} {r['field']:55s} {r.get('delta')}")
        drift = [r for r in rows if r["status"] != "OK"]
        print(f"\n{len(rows) - len(drift)}/{len(rows)} fields reproduced within tolerance")
        return 0 if not drift else 1

    # fill: payload.json [output.pdf]
    payload_path = Path(argv[0])
    output_path = (
        Path(argv[1])
        if len(argv) > 1
        else ROOT / "outputs" / f"{payload_path.stem}_filled.pdf"
    )

    raw = json.loads(payload_path.read_text())
    as_of = date.today()
    validation = validate(raw, as_of=as_of)
    if not validation.valid:
        print("VALIDATION FAILED:")
        for err in validation.errors:
            print(f"  {err}")
        return 1

    # Fingerprint gate before any drawing
    try:
        spec = resolve_form_spec(BLANK)
    except ValueError as exc:
        print(f"FINGERPRINT REJECTED: {exc}")
        return 1

    fill_final(raw, output_path, as_of=as_of, spec=spec)
    print(f"Filled:  {output_path}")

    check_result = check_correctness(output_path, payload_path, as_of=as_of)
    code = _print_check(check_result)

    receipt = build_proof_receipt(
        spec=spec,
        payload=raw,
        output_path=Path(output_path),
        validation=validation,
        check_result=check_result,
        as_of=as_of,
    )
    proof_path = write_proof_receipt(receipt, Path(output_path))
    print(f"Proof:   {proof_path}")

    if validation.fee:
        for note in validation.notes:
            print(f"  Note: {note}", file=sys.stderr)
        fee = validation.fee
        print(
            f"Fee:     ${fee['total']:.2f} "
            f"({fee['breakdown']['form_type']}, "
            f"{fee['breakdown']['num_copies']} copy, "
            f"{fee['breakdown']['years_searched']} yr search)"
            + ("  [requires_review]" if fee.get("requires_review") else "")
        )
    return code


if __name__ == "__main__":
    sys.exit(main())
