"""NYC Form CC2002B — deterministic filing engine.

JSON → strict schema → validate → fingerprint gate → flat black ink →
byte-identical PDF + signed proof receipt → reopen and prove.

Usage
-----
    python cc2002b.py payload.json [output.pdf]
    python cc2002b.py --check filled.pdf payload.json [report.json]
    python cc2002b.py --verify filled.pdf [pubkey_hex]
    python cc2002b.py --keygen [key.hex]
    python cc2002b.py --pubkey
    python cc2002b.py --extract-blank packet.pdf cc2002b_blank.pdf

Dependencies live in pyproject.toml, pinned by uv.lock. One source of truth.
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
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import pikepdf
import pymupdf
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

# ═══════════════════════════════════════════════════════════════════════════
# 0. Paths and constants
# ═══════════════════════════════════════════════════════════════════════════

ROOT = Path(__file__).resolve().parent
BLANK = ROOT / "00_packet" / "cc2002b_blank.pdf"
SPEC_PATH = ROOT / "cc2002b.spec.json"
FEES_PATH = ROOT / "cc2002b.fees.json"
HELV = "helv"
FONT_SIZES = (10, 9, 8)  # try largest first; ValueError at the floor
DPI = 150 / 72           # checker raster scale (device pixels per point)

# Base-14 Helvetica covers Latin-1. Non-Latin-1 falls back to DejaVu Sans
# (Latin Extended, Cyrillic, Greek). CJK/Arabic/emoji fail loud, not as '·'.
# License: Bitstream Vera — fonts/LICENSE_DEJAVU.
UNICODE_FONT_NAME = "DejaVuSans"
UNICODE_FONT_PATH = ROOT / "fonts" / "DejaVuSans.ttf"

_unicode_font_cache: pymupdf.Font | None = None


def _unicode_font() -> pymupdf.Font:
    """Lazy: a missing font file shouldn't break commands that don't need it."""
    global _unicode_font_cache
    if _unicode_font_cache is None:
        _unicode_font_cache = pymupdf.Font(fontfile=str(UNICODE_FONT_PATH))
    return _unicode_font_cache

# Raster thresholds (calibrated against measured forgeries, not vibes)
_DARK = 128
_MIN_DARK = 8
_MAX_DARK_FRAC = 0.5
_PAD = 1

# Borough / state / phone / zip constants
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
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_iso_date_string(value: Any) -> Any:
    """Reject anything that isn't a YYYY-MM-DD string."""
    if not isinstance(value, str) or not ISO_DATE_RE.fullmatch(value):
        raise ValueError("must be an ISO date string in YYYY-MM-DD format")
    return value


IsoDate = Annotated[date, BeforeValidator(_require_iso_date_string)]

# 50-year rule: note, don't reject. The form's own parentheticals (Legal
# Dept approval; LE personnel only) imply routing, not refusal.
UNDER_50 = {
    "relation": (
        "the form adds '(RELEASE OF RECORD UNDER THIS SECTION MUST BE APPROVED "
        "BY LEGAL DEPT.)', so expect the City Clerk to route this to Legal"
    ),
    "law_enforcement": (
        "the form marks this option '(LAW ENFORCEMENT PERSONNEL ONLY)' and "
        "requires that 'the marriage record will be used for a proper purpose', "
        "so expect the City Clerk to verify the requester's authority"
    ),
}


def _is_latin1(text: str) -> bool:
    """True if base-14 Helvetica can ink this without embedding."""
    try:
        text.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return not any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in text)


def _uninkable_char_error(field_name: str, value: str) -> str | None:
    """Return error message if a character can't be inked by either font."""
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


# ═══════════════════════════════════════════════════════════════════════════
# 1. Approved spec (cc2002b.spec.json), fingerprint gate, and the strict
#    Pydantic intake schema.
#
# The fingerprint gate fails closed. Coordinates were measured once, offline,
# with human sign-off. The runtime only reads the approved spec, never
# re-derives it.
# ═══════════════════════════════════════════════════════════════════════════


def validate_field_geometry(spec: dict[str, Any]) -> None:
    """Reject malformed or unsafe field maps before any PDF drawing."""
    page_width, page_height = spec["page_size"]
    fields = spec["fields"]
    protected = set(spec["protected"])
    if not protected <= fields.keys():
        raise ValueError("protected fields must exist in the field map")
    for name, field_spec in fields.items():
        x, y, width, height = (
            field_spec[key] for key in ("x", "y", "w", "h")
        )
        if width <= 0 or height <= 0:
            raise ValueError(f"field {name!r} must have positive dimensions")
        if x < 0 or y < 0 or x + width > page_width or y + height > page_height:
            raise ValueError(f"field {name!r} falls outside the page bounds")
        if field_spec["type"] not in {"text", "checkbox", "line"}:
            raise ValueError(f"field {name!r} has an unknown type")
    if len(fields) != spec["measurement"]["field_count"]:
        raise ValueError("measurement field_count does not match the field map")
    if len(fields) - len(protected) != spec["measurement"]["fillable_count"]:
        raise ValueError("measurement fillable_count does not match the field map")
    if len(protected) != spec["measurement"]["protected_count"]:
        raise ValueError("measurement protected_count does not match the field map")


def _load_spec_json(path: Path = SPEC_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text())
    data["page_size"] = tuple(data["page_size"])
    data["anchors"] = tuple(data["anchors"])
    data["protected"] = frozenset(data["protected"])
    for f in data["fields"].values():
        f.setdefault("fill", True)
        f.setdefault("x1", f["x"] + f["w"])
        f.setdefault("y1", f["y"] + f["h"])
    validate_field_geometry(data)
    return data


SPEC: dict[str, Any] = _load_spec_json()
FIELDS = SPEC["fields"]  # module alias — kept because tests reach in directly


def _blank_sha256(path: Path = BLANK) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_form_spec(blank_path: Path = BLANK) -> dict[str, Any]:
    """Hash the blank on disk vs. the approved spec. Fail closed."""
    digest = _blank_sha256(blank_path)
    if digest != SPEC["blank_sha256"]:
        raise ValueError(
            "unknown form fingerprint "
            f"(sha256={digest[:16]}…, approved={SPEC['blank_sha256'][:16]}…). "
            "Fail closed: do not stretch an old field map onto an unapproved "
            "blank. Offline path: extract the new page, propose coordinates "
            "with tools/inspect_form.py, get human sign-off, freeze a new "
            "cc2002b.spec.json (new blank_sha256, fields, anchors)."
        )
    return SPEC


def assert_blank(
    doc: pymupdf.Document, spec: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Reject a source swap before geometry can drift."""
    spec = spec or resolve_form_spec(BLANK)
    if doc.page_count != spec["page_count"]:
        raise ValueError(
            f"blank form must have {spec['page_count']} page(s), got {doc.page_count}"
        )
    page = doc[0]
    got_size = (round(page.rect.width, 2), round(page.rect.height, 2))
    if got_size != spec["page_size"]:
        raise ValueError(
            f"blank form page size changed: got {got_size}, want {spec['page_size']}"
        )
    if bool(doc.is_form_pdf) != spec["acroform_expected"]:
        raise ValueError(
            "blank AcroForm presence does not match the approved fingerprint"
        )
    text = " ".join(page.get_text().split())
    missing = [a for a in spec["anchors"] if a not in text]
    if missing:
        raise ValueError(f"blank form is missing anchors: {missing}")
    actual = _blank_sha256(BLANK)
    if actual != spec["blank_sha256"]:
        raise ValueError(
            "blank form bytes changed; re-measure and re-approve the field map "
            "before updating cc2002b.spec.json's blank_sha256"
        )
    return spec


# --- Strict intake schema ---------------------------------------------------


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")
    street: str
    apartment: str | None = None
    city: str
    state: str
    zip_code: str


class Person(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    birth_date: IsoDate


class Marriage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: IsoDate
    borough: str
    license_no: str
    additional_search_years: list[Annotated[int, Field(strict=True)]] = Field(
        default_factory=list
    )


class AuthParty(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["party"]


class AuthWrittenAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["written_authorization"]


class AuthAttorney(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["attorney"]


class AuthRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["relation"]
    relation: str

    @field_validator("relation")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("relation is required when authorization kind is 'relation'")
        return v


class AuthLawEnforcement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["law_enforcement"]
    agency_or_title: str

    @field_validator("agency_or_title")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError(
                "agency_or_title is required when authorization kind is 'law_enforcement'"
            )
        return v


# extra="forbid" makes leaked fields (e.g. {"kind": "party", "relation": "child"})
# a schema rejection instead of a silent drop.
Authorization = Annotated[
    AuthParty
    | AuthWrittenAuthorization
    | AuthAttorney
    | AuthRelation
    | AuthLawEnforcement,
    Field(discriminator="kind"),
]


class Application(BaseModel):
    model_config = ConfigDict(extra="forbid")
    certificate_type: Literal["short", "extended", "other"]
    marriage: Marriage
    spouse_a: Person
    spouse_b: Person
    reason: str
    copies: Annotated[int, Field(strict=True, ge=1)]
    requester_name: str
    requester_relationship: str
    telephone: str
    address: Address
    authorization: Authorization


def load_application(path: str | Path) -> Application:
    return Application.model_validate_json(Path(path).read_text())


def form_values(app: Application) -> dict[str, Any]:
    """Flat dict keyed by spec field names — the seam between Application
    and the draw/verify code."""
    auth = app.authorization
    years = sorted(set(app.marriage.additional_search_years))
    return {
        "month": app.marriage.date.month,
        "day": app.marriage.date.day,
        "year": app.marriage.date.year,
        "license_was_issued": app.marriage.borough,
        "if_uncertain_specify_other_years_you_want_searched": ", ".join(
            str(y) for y in years
        ),
        "license_no": app.marriage.license_no,
        "full_legal_name_before_marriage": app.spouse_a.name,
        "date": app.spouse_a.birth_date.strftime("%m/%d/%Y"),
        "full_legal_name_before_marriage_09": app.spouse_b.name,
        "date_10": app.spouse_b.birth_date.strftime("%m/%d/%Y"),
        "reason_search_copy_are_needed": app.reason,
        "number_of_copies_requested": app.copies,
        "name_of_person_requesting_search": app.requester_name,
        "your_relationship_to_either_spouse": app.requester_relationship,
        "your_telephone_no": app.telephone,
        "street": app.address.street,
        "apt_no": app.address.apartment or "",
        "city": app.address.city,
        "state": app.address.state,
        "zip_code": app.address.zip_code,
        "auth_relation": auth.relation if isinstance(auth, AuthRelation) else "",
        "auth_other_agency": (
            auth.agency_or_title if isinstance(auth, AuthLawEnforcement) else ""
        ),
        "_certificate_type": app.certificate_type,
        "_authorization_kind": auth.kind,
    }


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
    app: Application,
    *,
    as_of: date | None = None,
    spec: dict[str, Any] | None = None,
    fees: FeeSchedule | None = None,
) -> ValidationResult:
    """Business-rule layer. Schema shape is already guaranteed by Pydantic;
    this catches what a validly-shaped payload can still get wrong.

    as_of is injected so tests don't depend on wall-clock date.today().
    """
    as_of = as_of or date.today()
    spec = spec or SPEC
    fees = fees or FEES
    errors: list[str] = []
    notes: list[str] = []

    if not fees.is_priced(app.certificate_type):
        # The checkbox exists; the schedule never prices it. Inventing a fee is
        # worse than refusing to print.
        errors.append(
            f"certificate_type is '{app.certificate_type}' — "
            f"{fees.unpriced_reason(app.certificate_type)}. Confirm with the "
            f"City Clerk ({fees.ambiguity.contact}) before this can be filled; "
            f"refusing to generate a PDF with an unknown fee attached."
        )
        notes.append(
            f"Fee unresolved: '{app.certificate_type}' has no defined price in "
            f"{fees.schedule_id}#{fees.version}"
        )
        return ValidationResult(False, errors, notes)

    auth = app.authorization
    marriage = app.marriage
    marr = marriage.date

    if marr.year < 1950:
        errors.append(
            f"marriage year {marr.year} is before 1950 — these records are "
            f"at the Municipal Archives, not the City Clerk"
        )
    elif marr > as_of:
        errors.append(f"marriage date {marr} is in the future")

    # 50-year NOTE — note, don't reject, for relation / law_enforcement auth
    if auth.kind in ("relation", "law_enforcement"):
        age = as_of.year - marr.year
        if (as_of.month, as_of.day) < (marr.month, marr.day):
            age -= 1
        if age < 50:
            notes.append(
                f"authorization={auth.kind} on a record {age} years old (under "
                f"50) is not automatic release: {UNDER_50[auth.kind]}."
            )

    # Required free-text fields
    for value, label in (
        (app.spouse_a.name, "spouse A name"),
        (app.spouse_b.name, "spouse B name"),
        (app.reason, "reason for search"),
        (app.requester_name, "name of requester"),
        (app.requester_relationship, "relationship to either spouse"),
    ):
        if not value or not value.strip():
            errors.append(f"{label} is required")

    # Birth dates — only future check remains (schema handles the rest)
    for birth_date, label in (
        (app.spouse_a.birth_date, "spouse A birth date"),
        (app.spouse_b.birth_date, "spouse B birth date"),
    ):
        if birth_date > as_of:
            errors.append(f"{label} {birth_date} is in the future")

    # Borough
    borough = marriage.borough.strip()
    if not borough:
        errors.append("borough where license was issued is required")
    elif borough.lower() not in BOROUGHS:
        errors.append(
            f"borough '{borough}' must be Bronx, Brooklyn, Manhattan, Queens, "
            "or Staten Island"
        )

    # Address
    addr = app.address
    for value, label in ((addr.street, "street"), (addr.city, "city")):
        if not value or not value.strip():
            errors.append(f"address field {label} is required")
    state = (addr.state or "").strip()
    if not state:
        errors.append("address field state is required")
    elif not STATE_RE.match(state) or state.upper() not in USPS_STATES:
        errors.append(f"state '{state}' is not a valid USPS state/territory code")
    zc = (addr.zip_code or "").strip()
    if not zc:
        errors.append("address field zip_code is required")
    elif not ZIP_RE.match(zc):
        errors.append(f"zip_code '{zc}' must be 5 digits or ZIP+4")

    # Phone
    phone = (app.telephone or "").strip()
    if not phone:
        errors.append("telephone number is required")
    elif not PHONE_RE.match(phone):
        errors.append(
            f"telephone number '{app.telephone}' is not valid — must be "
            f"10-digit US number (separators ok, extensions not supported)"
        )

    # Search years
    for yr in marriage.additional_search_years:
        if not (1950 <= yr <= as_of.year):
            errors.append(f"requested search year {yr} is outside 1950–{as_of.year}")

    flat = form_values(app)

    # Font + fit guards (reject before drawing)
    for key, field_spec in spec["fields"].items():
        if not field_spec.get("fill", True):
            continue
        val = flat.get(key)
        items = val if isinstance(val, list) else [val]
        for item in items:
            if isinstance(item, str):
                err = _uninkable_char_error(key, item)
                if err:
                    errors.append(err)

    for key, field_spec in spec["fields"].items():
        if field_spec["type"] != "text" or not field_spec.get("fill", True):
            continue
        text = text_for(key, flat, as_of=as_of)
        if not text:
            continue
        try:
            _fit(
                text, field_spec["w"], field_spec["h"], key,
                sizes=field_spec.get("sizes", FONT_SIZES),
            )
        except ValueError as exc:
            errors.append(str(exc))

    if not errors:
        fee, fee_notes = calculate_fee(
            app.certificate_type, app.copies, marr.year,
            marriage.additional_search_years, fees=fees,
        )
        notes.extend(fee_notes)
        return ValidationResult(True, errors, notes, fee)
    return ValidationResult(False, errors, notes)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Fee policy — ambiguity is visible, never silent
#
# Prices come from page 3 of the packet, which is a different document on a
# different clock than the blank on page 2: the City Clerk can reprice without
# redrawing the form. So the schedule is its own versioned, validated artifact
# rather than constants in this file — the last thing here that wasn't data.
# ═══════════════════════════════════════════════════════════════════════════


class CopyRate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_copy: float
    additional_copy: float

    def total_for(self, copies: int) -> float:
        return self.first_copy + (copies - 1) * self.additional_copy


class SearchRate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_year: float
    second_year: float
    each_year_after: float

    def total_for(self, years: int) -> float:
        if years <= 1:
            return self.first_year
        return self.second_year + (years - 2) * self.each_year_after


class FeeAmbiguity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    applies_to: list[str]
    alternative_rate: CopyRate
    reasoning: str
    contact: str


class FeeSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule_id: str
    version: str
    source: str
    currency: str
    copy_rates: dict[str, CopyRate]
    unpriced_types: dict[str, str]
    search: SearchRate
    ambiguity: FeeAmbiguity

    def is_priced(self, certificate_type: str) -> bool:
        return certificate_type in self.copy_rates

    def unpriced_reason(self, certificate_type: str) -> str:
        return self.unpriced_types.get(
            certificate_type, "the schedule defines no price for this type"
        )


def _load_fee_schedule(path: Path = FEES_PATH) -> FeeSchedule:
    """Validated at import: a malformed schedule fails here, not mid-filing."""
    return FeeSchedule.model_validate_json(path.read_text())


FEES: FeeSchedule = _load_fee_schedule()


def calculate_fee(
    certificate_type: str,
    copies: int,
    marriage_year: int,
    extra_years: list[int],
    *,
    fees: FeeSchedule | None = None,
) -> tuple[dict[str, Any], list[str]]:
    fees = fees or FEES
    notes: list[str] = []
    rate = fees.copy_rates[certificate_type]
    copy_fee = rate.total_for(copies)

    ambiguous = certificate_type in fees.ambiguity.applies_to
    alternative: float | None = None
    if ambiguous:
        # The competing reading priced out, rather than a hand-written figure
        # in the note text: the exposure scales with copies, and a constant
        # would only ever be right for an order of one.
        alternative = fees.ambiguity.alternative_rate.total_for(copies)
        notes.append(
            f"AMBIGUOUS FEE: {fees.ambiguity.reasoning} Billed "
            f"${copy_fee:.2f} for {copies} "
            f"cop{'y' if copies == 1 else 'ies'}; the alternative reading "
            f"gives ${alternative:.2f}, a ${abs(copy_fee - alternative):.2f} "
            f"difference. Confirm with the City Clerk "
            f"({fees.ambiguity.contact}) if disputed."
        )

    all_years = set(extra_years)
    all_years.add(marriage_year)
    search_years = max(len(all_years), 1)
    search_fee = fees.search.total_for(search_years)
    total = copy_fee + search_fee

    fee: dict[str, Any] = {
        "fee_status": "resolved",
        "fee_schedule_version": f"{fees.schedule_id}#{fees.version}",
        "currency": fees.currency,
        "copy_fee": round(copy_fee, 2),
        "search_fee": round(search_fee, 2),
        "total": round(total, 2),
        "requires_review": ambiguous,
        "breakdown": {
            "form_type": certificate_type,
            "num_copies": copies,
            "first_copy": rate.first_copy,
            "additional_copy": rate.additional_copy,
            "years_searched": search_years,
        },
    }
    if alternative is not None:
        fee["alternative_reading"] = {
            "copy_fee": round(alternative, 2),
            "total": round(alternative + search_fee, 2),
            "difference": round(abs(copy_fee - alternative), 2),
        }
    return fee, notes


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
    """Greedy word-wrap. Returns (text,) unchanged if it already fits."""
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
            current = word
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
    """Largest size that fits, wrapping if height allows. Latin-1 stays on
    base-14 Helvetica; non-Latin-1 falls back to the embedded Unicode font.
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
    """Display value for a field. payload is form_values() output."""
    val = payload.get(field_name)
    if val is None:
        return ""
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    if field_name == "month":
        return calendar.month_name[val]
    if field_name == "license_was_issued":
        return BOROUGHS.get(str(val).strip().lower(), str(val).strip())
    if field_name == "state":
        return str(val).strip().upper()
    return str(val).strip()


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


def _marked_checkboxes(payload: dict, spec: dict[str, Any]) -> set[str | None]:
    return {
        spec["form_checkboxes"].get(payload.get("_certificate_type")),
        spec["authorization_checkboxes"].get(payload.get("_authorization_kind")),
    }


def _save_deterministic(doc: pymupdf.Document, output_path: Path) -> None:
    """Atomic save with a content-derived /ID.

    MuPDF stamps a fresh random /ID on every save, so two runs over the same
    payload produced different bytes — and a receipt whose output_hash nobody
    else could reproduce. pikepdf derives /ID from document content instead,
    the same discipline extract_blank already applied to the blank.
    """
    staged = Path(str(output_path) + ".stage")
    tmp = Path(str(output_path) + ".tmp")
    try:
        doc.save(str(staged))
        with pikepdf.open(staged) as pdf:
            # A wall-clock stamp would defeat the point as surely as a random /ID.
            info = pdf.trailer.get("/Info")
            if info is not None:
                for key in ("/CreationDate", "/ModDate"):
                    if key in info:
                        del info[key]
            pdf.save(tmp, deterministic_id=True)
        os.replace(tmp, output_path)
    finally:
        for leftover in (staged, tmp):
            if leftover.exists():
                leftover.unlink()


def fill_final(
    payload: dict,
    output_path: str | Path,
    *,
    as_of: date | None = None,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Draw flat black ink onto the approved blank. Never touches signature."""
    as_of = as_of or date.today()
    doc = pymupdf.open(str(BLANK))
    try:
        spec = assert_blank(doc, spec)
        page = doc[0]
        checkboxes = _marked_checkboxes(payload, spec)

        for name, field_spec in spec["fields"].items():
            if name in spec["protected"]:
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
        _save_deterministic(doc, output_path)
        return spec
    finally:
        doc.close()


# ═══════════════════════════════════════════════════════════════════════════
# 7. Proof receipt — machine-readable evidence of a filing
# ═══════════════════════════════════════════════════════════════════════════


def _canonical_payload_hash(app: Application) -> str:
    """Canonical hash of the applicant's typed input."""
    blob = json.dumps(
        app.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(blob).hexdigest()


def build_proof_receipt(
    *,
    spec: dict[str, Any],
    app: Application,
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
        "template_hash": spec["blank_sha256"],
        "form_spec_version": f"{spec['form_id']}@{spec['revision']}#{spec['version']}",
        "input_hash": _canonical_payload_hash(app),
        "output_hash": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "renderer": {
            "engine": "mupdf",
            "pymupdf": getattr(pymupdf, "VersionBind", "unknown"),
        },
        "verification_engines": ["mupdf", "pdfium"],
        "fee": {
            "total": fee.get("total"),
            "copy_fee": fee.get("copy_fee"),
            "search_fee": fee.get("search_fee"),
            "currency": fee.get("currency"),
            "requires_review": fee.get("requires_review", False),
            "breakdown": fee.get("breakdown"),
            "fee_status": fee.get("fee_status"),
            "fee_schedule_version": fee.get("fee_schedule_version"),
            "alternative_reading": fee.get("alternative_reading"),
        },
        "notes": list(validation.notes),
        "checks_passed": passed,
        "checks_failed": failed,
        "check_passed": bool(check_result.get("passed")),
        "as_of": as_of.isoformat(),
    }


def write_proof_receipt(receipt: dict[str, Any], pdf_path: Path) -> Path:
    """Write the proof receipt JSON next to the PDF."""
    proof_path = Path(str(pdf_path) + ".proof.json")
    _atomic_write_json(receipt, proof_path)
    return proof_path


def _atomic_write_json(obj: Any, path: Path) -> None:
    tmp = str(path) + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ═══════════════════════════════════════════════════════════════════════════
# 7b. Receipt signing (Ed25519)
#
# The hashes above prove a file hasn't been altered since it was written.
# They don't prove who wrote it — anyone can regenerate a receipt. A detached
# signature over the canonical receipt closes that gap: with the PDF, the
# receipt, the signature, and a trusted public key, a third party can verify
# the filing without this repo, and (because output is byte-deterministic)
# rebuild the PDF and get the same hash.
# ═══════════════════════════════════════════════════════════════════════════

SIGNING_KEY_ENV = "CC2002B_SIGNING_KEY"
PUBLIC_KEY_ENV = "CC2002B_PUBLIC_KEY"


def canonical_receipt_bytes(receipt: dict[str, Any]) -> bytes:
    """The exact bytes signer and verifier both agree to hash over."""
    return json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()


def _public_hex(public_key: ed25519.Ed25519PublicKey) -> str:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()


def generate_signing_key() -> tuple[str, str]:
    """A fresh Ed25519 keypair as (private_hex, public_hex)."""
    key = ed25519.Ed25519PrivateKey.generate()
    private_hex = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex()
    return private_hex, _public_hex(key.public_key())


def load_signing_key() -> ed25519.Ed25519PrivateKey | None:
    """Signing key from the environment, or None if filings go unsigned."""
    raw = os.environ.get(SIGNING_KEY_ENV, "").strip()
    if not raw:
        return None
    try:
        seed = bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError(f"{SIGNING_KEY_ENV} must be hex-encoded") from exc
    if len(seed) != 32:
        raise ValueError(
            f"{SIGNING_KEY_ENV} must be a 32-byte Ed25519 seed "
            f"(64 hex chars), got {len(seed)} bytes"
        )
    return ed25519.Ed25519PrivateKey.from_private_bytes(seed)


def sign_receipt(
    receipt: dict[str, Any], key: ed25519.Ed25519PrivateKey
) -> dict[str, str]:
    return {
        "algorithm": "ed25519",
        "public_key": _public_hex(key.public_key()),
        "signature": key.sign(canonical_receipt_bytes(receipt)).hex(),
    }


def write_signature(signature: dict[str, str], pdf_path: Path) -> Path:
    sig_path = Path(str(pdf_path) + ".proof.json.sig")
    _atomic_write_json(signature, sig_path)
    return sig_path


def verify_filing(
    pdf_path: str | Path,
    *,
    expected_public_key: str | None = None,
) -> dict[str, Any]:
    """Verify a filing from its artifacts alone — no blank, no payload, no repo.

    Answers three questions: are these bytes the ones the receipt describes,
    was the receipt signed by the holder of a private key, and is that key the
    one the verifier expected.
    """
    pdf_path = Path(pdf_path)
    proof_path = Path(str(pdf_path) + ".proof.json")
    sig_path = Path(str(pdf_path) + ".proof.json.sig")
    results: list[dict[str, Any]] = []

    if not pdf_path.exists():
        return {
            "passed": False,
            "checks": [{
                "check": "pdf_present",
                "passed": False,
                "detail": f"no such file: {pdf_path}",
            }],
        }
    if not proof_path.exists():
        return {
            "passed": False,
            "checks": [{
                "check": "receipt_present",
                "passed": False,
                "detail": f"no proof receipt at {proof_path}",
            }],
        }

    receipt = json.loads(proof_path.read_text())
    actual_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    claimed = receipt.get("output_hash", "")
    results.append({
        "check": "output_hash_matches_pdf",
        "passed": actual_hash == claimed,
        "detail": f"receipt claims {claimed[:16]}…, pdf is {actual_hash[:16]}…",
    })

    if not sig_path.exists():
        results.append({
            "check": "signature_present",
            "passed": False,
            "detail": (
                f"no detached signature at {sig_path}; the receipt proves "
                f"integrity but not authorship"
            ),
        })
        return {"passed": False, "checks": results}

    sig = json.loads(sig_path.read_text())
    algorithm = sig.get("algorithm")
    results.append({
        "check": "signature_algorithm",
        "passed": algorithm == "ed25519",
        "detail": f"got {algorithm!r}, want 'ed25519'",
    })

    signer = sig.get("public_key", "")
    try:
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(signer)
        )
        public_key.verify(
            bytes.fromhex(sig.get("signature", "")),
            canonical_receipt_bytes(receipt),
        )
        valid, detail = True, f"valid ed25519 signature by {signer[:16]}…"
    except (InvalidSignature, ValueError) as exc:
        valid = False
        detail = f"signature does not verify against the receipt: {exc}"
    results.append({
        "check": "signature_valid",
        "passed": valid,
        "detail": detail,
    })

    # A signature with an unpinned key proves only that *somebody* signed it.
    if expected_public_key:
        results.append({
            "check": "signer_is_expected_key",
            "passed": signer == expected_public_key.strip().lower(),
            "detail": f"signed by {signer[:16]}…, expected {expected_public_key[:16]}…",
        })
    else:
        results.append({
            "check": "signer_is_expected_key",
            "passed": False,
            "detail": (
                f"no trusted key given, so authorship is unverified — pass the "
                f"expected public key or set {PUBLIC_KEY_ENV} (signer claims "
                f"{signer[:16]}…)"
            ),
        })

    return {"passed": all(r["passed"] for r in results), "checks": results}


# ═══════════════════════════════════════════════════════════════════════════
# 8. Verification — semantic + raster; prove the visible artifact
#
# A model may propose; only a mechanical check may accept.
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
    spec: dict[str, Any],
) -> list[dict]:
    """Added words must equal expected. Checkboxes need two crossing strokes."""
    words = list(filled_page.get_text("words"))
    drawings = list(filled_page.get_drawings())
    marked = _marked_checkboxes(payload, spec)

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
    spec: dict[str, Any],
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

        marked = _marked_checkboxes(payload, spec)
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


def _pdfium_dark_counts(
    filled_path: Path, fm: dict
) -> tuple[dict[str, int], int]:
    """Per-field dark-pixel counts and the stray count, through pdfium.

    Shared with tools/calibrate_raster.py so the calibration baseline and the
    live check can never be measuring two different things.
    """
    b_bytes, bw, bh = _pdfium_render(BLANK)
    f_bytes, fw, fh = _pdfium_render(Path(filled_path))

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
    return dark, stray


def _check_layer_c_pdfium(
    filled_path: Path,
    payload: dict,
    fm: dict,
    *,
    as_of: date,
    spec: dict[str, Any],
) -> list[dict]:
    """Cross-check layer B with an independent renderer. If mupdf and pdfium
    disagree, it's a rendering artifact, not proof the form is correct.
    """
    try:
        dark, stray = _pdfium_dark_counts(Path(filled_path), fm)
    except ImportError:
        return [{
            "check": "pdfium_cross_check:available",
            "passed": False,
            "detail": "pypdfium2 not installed; second-renderer cross-check skipped",
        }]

    results: list[dict] = [{
        "check": "pdfium_cross_check:no_stray_ink",
        "passed": stray == 0,
        "detail": f"{stray} dirty pixels outside approved rects (pdfium render)",
    }]

    marked = _marked_checkboxes(payload, spec)
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

        # Protected/empty fields must stay untouched — same as layer B's no_ink
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
    try:
        app = load_application(payload_path)
    except ValidationError as exc:
        return {
            "passed": False,
            "checks": [{
                "check": "payload_schema",
                "passed": False,
                "detail": str(exc),
            }],
        }

    validation = validate(app, as_of=as_of)
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

    flat = form_values(app)
    fm = spec["fields"]
    blank = pymupdf.open(str(BLANK))
    filled = pymupdf.open(str(filled_path))
    try:
        assert_blank(blank, spec)
        bw = _blank_words_set(blank[0])
        results = _check_layer_a(
            filled[0], flat, fm, bw, as_of=as_of, spec=spec
        )
        results += _check_layer_b(
            filled[0], flat, fm, as_of=as_of, spec=spec
        )
    finally:
        blank.close()
        filled.close()
    results += _check_layer_c_pdfium(
        Path(filled_path), flat, fm, as_of=as_of, spec=spec
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
    """Extract one page from the packet into a blank form.

    deterministic_id=True pins /ID so the SHA-256 is reproducible — same reason
    _save_deterministic uses it on the way out.
    """
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
# 10. CLI
# ═══════════════════════════════════════════════════════════════════════════


def _print_check(result: dict[str, Any], label: str = "Check") -> int:
    checks = result.get("checks", [])
    passed = sum(1 for c in checks if c.get("passed"))
    total = len(checks)
    status = "PASSED" if result.get("passed") else "FAILED"
    print(f"{label + ':':9}{status}  ({passed}/{total})")
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
        if digest == SPEC["blank_sha256"]:
            version = f"{SPEC['form_id']}@{SPEC['revision']}#{SPEC['version']}"
            print(f"Spec:      {version} (approved)")
        else:
            print("Spec:      UNKNOWN — approve a new cc2002b.spec.json before filling")
        return 0

    # --keygen [key.hex]
    if argv[0] == "--keygen":
        private_hex, public_hex = generate_signing_key()
        if len(argv) > 1:
            key_path = Path(argv[1])
            key_path.write_text(private_hex + "\n")
            key_path.chmod(0o600)
            print(f"Private: {key_path} (mode 600)")
        else:
            print(private_hex)
        print(f"Public:  {public_hex}", file=sys.stderr)
        return 0

    # --pubkey — the key a verifier should pin, derived from the signing key
    if argv[0] == "--pubkey":
        try:
            key = load_signing_key()
        except ValueError as exc:
            print(f"SIGNING KEY REJECTED: {exc}", file=sys.stderr)
            return 1
        if key is None:
            print(f"{SIGNING_KEY_ENV} is not set", file=sys.stderr)
            return 1
        print(_public_hex(key.public_key()))
        return 0

    # --verify filled.pdf [expected_public_key_hex]
    if argv[0] == "--verify":
        if len(argv) < 2:
            print("usage: cc2002b.py --verify filled.pdf [public_key_hex]")
            return 1
        expected = (
            argv[2] if len(argv) > 2 else os.environ.get(PUBLIC_KEY_ENV) or None
        )
        return _print_check(
            verify_filing(argv[1], expected_public_key=expected), "Verify"
        )

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

    # fill: payload.json [output.pdf]
    payload_path = Path(argv[0])
    output_path = (
        Path(argv[1])
        if len(argv) > 1
        else ROOT / "outputs" / f"{payload_path.stem}_filled.pdf"
    )

    try:
        app = load_application(payload_path)
    except ValidationError as exc:
        print("PAYLOAD SCHEMA REJECTED:")
        print(f"  {exc}")
        return 1

    as_of = date.today()
    validation = validate(app, as_of=as_of)
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

    # Resolve the signing key before drawing: a bad key should cost nothing.
    try:
        key = load_signing_key()
    except ValueError as exc:
        print(f"SIGNING KEY REJECTED: {exc}", file=sys.stderr)
        return 1

    flat = form_values(app)
    fill_final(flat, output_path, as_of=as_of, spec=spec)

    check_result = check_correctness(output_path, payload_path, as_of=as_of)
    code = _print_check(check_result)

    if not check_result["passed"]:
        # never release a pdf the checker doesn't believe
        Path(output_path).unlink(missing_ok=True)
        failed_path = Path(str(output_path) + ".failed.json")
        with open(failed_path, "w") as f:
            json.dump(check_result, f, indent=2)
            f.write("\n")
        print(
            f"Verification failed; unverified PDF deleted. Report: {failed_path}",
            file=sys.stderr,
        )
        return 1

    print(f"Filled:  {output_path}")

    receipt = build_proof_receipt(
        spec=spec,
        app=app,
        output_path=Path(output_path),
        validation=validation,
        check_result=check_result,
        as_of=as_of,
    )
    proof_path = write_proof_receipt(receipt, Path(output_path))
    print(f"Proof:   {proof_path}")

    if key is None:
        print(
            f"Signed:  no — set {SIGNING_KEY_ENV} to sign this receipt "
            f"({SIGNING_KEY_ENV}=$(python cc2002b.py --keygen))",
            file=sys.stderr,
        )
    else:
        signature = sign_receipt(receipt, key)
        sig_path = write_signature(signature, Path(output_path))
        print(f"Signed:  {sig_path} (ed25519, key {signature['public_key'][:16]}…)")

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
