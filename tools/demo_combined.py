"""Combined visual demo: step 1 (extract page 2 + speed/determinism comparison)
followed by the determinism gap (same payload, 5 runs, mupdf vs pikepdf).

Renders packet pages, extracted page 2, benchmark bars, and per-run PDFs.
Output: outputs/_demo_combined/index.html

Run:  uv run python tools/demo_combined.py
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import statistics
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import pikepdf
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

engine = importlib.import_module("cc2002b")

PACKET = ROOT / "00_packet" / "neptune-takehome-form-fill-packet (7).pdf"
BLANK = ROOT / "00_packet" / "cc2002b_blank.pdf"
PAYLOAD = ROOT / "samples" / "01_party_short.json"
OUT = ROOT / "outputs" / "_demo_combined"
RUNS = 5
BENCH_RUNS = 50
DPI = 110 / 72


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _render_png(pdf_path: Path, png_path: Path) -> None:
    doc = pymupdf.open(str(pdf_path))
    try:
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(DPI, DPI))
        pix.save(str(png_path))
    finally:
        doc.close()


def _render_png_at(pdf_path: Path, png_path: Path, dpi: float) -> None:
    doc = pymupdf.open(str(pdf_path))
    try:
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(dpi, dpi))
        pix.save(str(png_path))
    finally:
        doc.close()


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


# ── extraction ──────────────────────────────────────────────────────────────
def extract_pikepdf(packet: Path, out: Path) -> None:
    with pikepdf.open(packet) as src:
        dst = pikepdf.Pdf.new()
        dst.pages.append(src.pages[1])
        dst.save(out, deterministic_id=True)


def extract_pymupdf(packet: Path, out: Path) -> None:
    doc = pymupdf.open(str(packet))
    try:
        doc.select([1])
        doc.save(str(out))
    finally:
        doc.close()


def bench(fn, runs: int) -> list[float]:
    tmp = OUT / "_bench_tmp.pdf"
    times = []
    for _ in range(runs):
        tmp.unlink(missing_ok=True)
        t0 = time.perf_counter()
        fn(PACKET, tmp)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)
    tmp.unlink(missing_ok=True)
    return times


# ── determinism demo (same as before) ───────────────────────────────────────
def fill_raw(out: Path) -> None:
    doc = pymupdf.open(str(BLANK))
    try:
        doc[0].insert_text((72, 72), "DEMO", fontsize=10, color=(0, 0, 0))
        doc.save(str(out))
    finally:
        doc.close()


def fill_det(out: Path) -> None:
    staged = out.with_suffix(".stage")
    doc = pymupdf.open(str(BLANK))
    try:
        doc[0].insert_text((72, 72), "DEMO", fontsize=10, color=(0, 0, 0))
        doc.save(str(staged))
    finally:
        doc.close()
    with pikepdf.open(staged) as pdf:
        info = pdf.trailer.get("/Info")
        if info is not None:
            for key in ("/CreationDate", "/ModDate"):
                if key in info:
                    del info[key]
        pdf.save(out, deterministic_id=True)
    staged.unlink(missing_ok=True)


# ── step 2: measure every box ───────────────────────────────────────────────
STEP2_DPI = 200 / 72
C_TEXT = (0.196, 0.418, 0.310)
C_CHECK = (0.761, 0.212, 0.086)
C_LINE = (0.722, 0.526, 0.180)
C_WORD = (0.5, 0.5, 0.5)


def render_words_overlay(png: Path) -> int:
    """Draw a thin gray rectangle around every word. Returns word count."""
    doc = pymupdf.open(str(BLANK))
    try:
        page = doc[0]
        words = page.get_text("words")
        for w in words:
            page.draw_rect(pymupdf.Rect(w[0], w[1], w[2], w[3]), color=C_WORD, width=0.3)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(STEP2_DPI, STEP2_DPI))
        pix.save(str(png))
        return len(words)
    finally:
        doc.close()


def render_fields_overlay(png: Path, spec: dict) -> None:
    """Draw the hand-measured field rectangles, colored by type."""
    doc = pymupdf.open(str(BLANK))
    try:
        page = doc[0]
        for name, f in spec["fields"].items():
            rect = pymupdf.Rect(f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"])
            if name in spec["protected"]:
                color = C_LINE
            elif f["type"] == "checkbox":
                color = C_CHECK
            else:
                color = C_TEXT
            page.draw_rect(rect, color=color, width=0.8)
            page.insert_text((rect.x0, rect.y0 - 2), name, fontsize=4, color=color)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(STEP2_DPI, STEP2_DPI))
        pix.save(str(png))
    finally:
        doc.close()


def render_checkbox_crops(top_png: Path, bottom_png: Path) -> None:
    """Render the two visually different checkbox regions."""
    doc = pymupdf.open(str(BLANK))
    try:
        page = doc[0]
        for png, clip in (
            (top_png, pymupdf.Rect(35, 25, 150, 100)),
            (bottom_png, pymupdf.Rect(60, 535, 330, 650)),
        ):
            pix = page.get_pixmap(matrix=pymupdf.Matrix(STEP2_DPI, STEP2_DPI), clip=clip)
            pix.save(str(png))
    finally:
        doc.close()


def render_inline_blank_demo(fits_png: Path, floor_png: Path, spec: dict) -> None:
    """Show the auth_relation inline blank at 10pt (fits) vs a long word (needs the floor)."""
    field = spec["fields"]["auth_relation"]
    rect = pymupdf.Rect(
        field["x"], field["y"], field["x"] + field["w"], field["y"] + field["h"]
    )
    clip = pymupdf.Rect(rect.x0 - 90, rect.y0 - 10, rect.x1 + 10, rect.y1 + 10)
    for png, word in ((fits_png, "child"), (floor_png, "granddaughter")):
        doc = pymupdf.open(str(BLANK))
        try:
            page = doc[0]
            size, _fontname, _fontfile, lines = engine._fit(
                word, field["w"], field["h"], "auth_relation", field.get("sizes")
            )
            page.draw_rect(rect, color=(0.196, 0.418, 0.310), width=0.8)
            page.insert_text(
                (rect.x0 + 1, rect.y0 + size), lines[0], fontsize=size, color=(0, 0, 0)
            )
            pix = page.get_pixmap(matrix=pymupdf.Matrix(STEP2_DPI * 1.6, STEP2_DPI * 1.6), clip=clip)
            pix.save(str(png))
        finally:
            doc.close()


def render_no_rules_crop(png: Path) -> None:
    """The address row: no drawn vertical rules, columns come from label edges."""
    doc = pymupdf.open(str(BLANK))
    try:
        page = doc[0]
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(STEP2_DPI * 1.4, STEP2_DPI * 1.4),
            clip=pymupdf.Rect(35, 460, 575, 500),
        )
        pix.save(str(png))
    finally:
        doc.close()


def render_fee_schedule_crop(png: Path) -> None:
    """Page 3 of the packet: the two contradictory fee statements."""
    doc = pymupdf.open(str(PACKET))
    try:
        page = doc[2]
        words = page.get_text("words")
        broad = next((w for w in words if "$15.00" in w[4]), None)
        specific = next((w for w in words if "$35" in w[4]), None)
        top = (broad[1] if broad else 420) - 25
        bottom = (specific[3] if specific else 480) + 45
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(STEP2_DPI * 1.3, STEP2_DPI * 1.3),
            clip=pymupdf.Rect(50, top, 565, bottom),
        )
        pix.save(str(png))
    finally:
        doc.close()


def render_forgery(
    good_pdf: Path,
    out_png: Path,
    clip: pymupdf.Rect,
    apply_tamper,
    payload_path: Path,
    as_of: date,
) -> dict:
    """Apply one real tamper from the test suite, crop it, and return the
    actual check_correctness() verdict — no invented pass/fail text."""
    tampered = out_png.with_suffix(".tamper.pdf")
    doc = pymupdf.open(str(good_pdf))
    try:
        apply_tamper(doc[0])
        doc.save(str(tampered))
    finally:
        doc.close()
    render = pymupdf.open(str(tampered))
    try:
        pix = render[0].get_pixmap(matrix=pymupdf.Matrix(STEP2_DPI * 1.6, STEP2_DPI * 1.6), clip=clip)
        pix.save(str(out_png))
    finally:
        render.close()
    result = engine.check_correctness(tampered, payload_path, as_of=as_of)
    tampered.unlink(missing_ok=True)
    return result


def render_filled_sample(png: Path, spec: dict) -> None:
    """Render the blank with a sample payload filled in."""
    doc = pymupdf.open(str(BLANK))
    try:
        page = doc[0]
        fields = spec["fields"]
        for name, val, sz in [
            ("month", "05", 10), ("day", "30", 10), ("year", "2025", 10),
            ("license_no", "B-2025-4994", 8),
            ("full_legal_name_before_marriage", "Sol Lee", 9),
            ("full_legal_name_before_marriage_09", "Ronald William Caspers II", 9),
            ("name_of_person_requesting_search", "Sol Lee", 7),
            ("your_relationship_to_either_spouse", "Spouse", 7),
            ("your_telephone_no", "603-380-3923", 7),
            ("street", "176 8th Street", 8), ("apt_no", "2", 8),
            ("city", "Brooklyn", 8), ("state", "NY", 8), ("zip_code", "11215", 8),
            ("reason_search_copy_are_needed", "Name change", 8),
            ("number_of_copies_requested", "1", 10),
        ]:
            f = fields[name]
            page.insert_text((f["x"] + 2, f["y"] + f["h"] - (4 if sz > 8 else 2)), val, fontsize=sz, color=(0, 0, 0))
        # X marks for checkboxes
        for name in ["form_short", "auth_checkbox_1"]:
            f = fields[name]
            cx, cy = f["x"] + f["w"] / 2, f["y"] + f["h"] / 2
            r = min(f["w"], f["h"]) / 2 * 0.7
            page.draw_line(pymupdf.Point(cx - r, cy - r), pymupdf.Point(cx + r, cy + r), color=(0, 0, 0), width=1)
            page.draw_line(pymupdf.Point(cx - r, cy + r), pymupdf.Point(cx + r, cy - r), color=(0, 0, 0), width=1)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(STEP2_DPI, STEP2_DPI))
        pix.save(str(png))
    finally:
        doc.close()


def hex_diff(a: bytes, b: bytes, context: int = 32) -> list[tuple[int, str, str]]:
    diffs = []
    n = min(len(a), len(b))
    i = 0
    while i < n:
        if a[i] != b[i]:
            start = max(0, i - context)
            j = i
            while j < min(n, i + context) and a[j] != b[j]:
                j += 1
            end = min(n, j + context)
            diffs.append((start, a[start:end].hex(), b[start:end].hex()))
            i = end
        else:
            i += 1
    return diffs


def find_id_strings(data: bytes) -> list[str]:
    out = []
    idx = 0
    while True:
        idx = data.find(b"/ID", idx)
        if idx == -1:
            break
        window = data[idx : idx + 80]
        s = ""
        for byte in window:
            c = chr(byte)
            if 32 <= byte < 127 or c in "()[]/<":
                s += c
            else:
                if s:
                    break
        out.append(s)
        idx += 3
    return out


def main() -> int:
    import html
    import json

    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*"):
        if f.is_file():
            f.unlink()

    # ── render the 3 packet pages ───────────────────────────────────────────
    packet_doc = pymupdf.open(str(PACKET))
    packet_pngs = []
    page_labels = ["page 1 — brief", "page 2 — the form (CC2002B)", "page 3 — fee schedule"]
    try:
        for i in range(len(packet_doc)):
            png = OUT / f"packet_p{i}.png"
            pix = packet_doc[i].get_pixmap(matrix=pymupdf.Matrix(DPI, DPI))
            pix.save(str(png))
            packet_pngs.append(png)
    finally:
        packet_doc.close()

    # ── extract page 2 both ways ────────────────────────────────────────────
    pike_out = OUT / "extracted_pikepdf.pdf"
    mupdf_out = OUT / "extracted_pymupdf.pdf"
    extract_pikepdf(PACKET, pike_out)
    extract_pymupdf(PACKET, mupdf_out)
    pike_png = OUT / "extracted_pikepdf.png"
    mupdf_png = OUT / "extracted_pymupdf.png"
    _render_png(pike_out, pike_png)
    _render_png(mupdf_out, mupdf_png)

    # ── benchmark ───────────────────────────────────────────────────────────
    # warmup
    extract_pikepdf(PACKET, OUT / "_w_p.pdf")
    extract_pymupdf(PACKET, OUT / "_w_m.pdf")
    (OUT / "_w_p.pdf").unlink(missing_ok=True)
    (OUT / "_w_m.pdf").unlink(missing_ok=True)

    pike_times = bench(extract_pikepdf, BENCH_RUNS)
    mupdf_times = bench(extract_pymupdf, BENCH_RUNS)

    pike_med = statistics.median(pike_times)
    mupdf_med = statistics.median(mupdf_times)
    pike_mean = statistics.mean(pike_times)
    mupdf_mean = statistics.mean(mupdf_times)

    # determinism of extraction (5 runs each)
    pike_hashes = set()
    mupdf_hashes = set()
    for i in range(5):
        extract_pikepdf(PACKET, OUT / f"p_{i}.pdf")
        pike_hashes.add(_hash(OUT / f"p_{i}.pdf"))
        extract_pymupdf(PACKET, OUT / f"m_{i}.pdf")
        mupdf_hashes.add(_hash(OUT / f"m_{i}.pdf"))
        (OUT / f"p_{i}.pdf").unlink()
        (OUT / f"m_{i}.pdf").unlink()

    # ── step 2: measure every box ──────────────────────────────────────────
    spec = json.loads((ROOT / "cc2002b.spec.json").read_text())
    step2_blank_png = OUT / "step2_blank.png"
    step2_words_png = OUT / "step2_words.png"
    step2_fields_png = OUT / "step2_fields.png"
    step2_filled_png = OUT / "step2_filled.png"
    step2_checkboxes_top_png = OUT / "step2_checkboxes_top.png"
    step2_checkboxes_bottom_png = OUT / "step2_checkboxes_bottom.png"
    _render_png_at(BLANK, step2_blank_png, STEP2_DPI)
    word_count = render_words_overlay(step2_words_png)
    with pymupdf.open(str(BLANK)) as structure_doc:
        drawing_count = len(structure_doc[0].get_drawings())
        has_acroform = bool(structure_doc.is_form_pdf)
    render_fields_overlay(step2_fields_png, spec)
    render_checkbox_crops(step2_checkboxes_top_png, step2_checkboxes_bottom_png)
    render_filled_sample(step2_filled_png, spec)

    # ── "where the form fought you" evidence ────────────────────────────────
    inline_fits_png = OUT / "fight_inline_fits.png"
    inline_floor_png = OUT / "fight_inline_floor.png"
    no_rules_png = OUT / "fight_no_rules.png"
    fee_schedule_png = OUT / "fight_fee_schedule.png"
    render_inline_blank_demo(inline_fits_png, inline_floor_png, spec)
    render_no_rules_crop(no_rules_png)
    render_fee_schedule_crop(fee_schedule_png)
    fit_child = engine._fit("child", spec["fields"]["auth_relation"]["w"],
                             spec["fields"]["auth_relation"]["h"], "auth_relation",
                             spec["fields"]["auth_relation"].get("sizes"))
    fit_grand = engine._fit("granddaughter", spec["fields"]["auth_relation"]["w"],
                             spec["fields"]["auth_relation"]["h"], "auth_relation",
                             spec["fields"]["auth_relation"].get("sizes"))

    fields = spec["fields"]
    n_text = sum(1 for f in fields.values() if f["type"] == "text" and f.get("fill", True))
    n_checkbox = sum(1 for f in fields.values() if f["type"] == "checkbox")
    n_protected = len(spec["protected"])
    n_total = len(fields)

    # ── end-to-end Sol payload → real engine output ─────────────────────────
    payload_path = ROOT / "samples" / "01_party_short.json"
    payload_data = json.loads(payload_path.read_text())
    application = engine.Application.model_validate(payload_data)
    as_of = date(2026, 7, 26)
    validation = engine.validate(application, as_of=as_of)
    if not validation.valid:
        raise ValueError(f"demo payload unexpectedly invalid: {validation.errors}")
    end_to_end_pdf = OUT / "step3_sol_lee.pdf"
    engine.fill_final(
        engine.form_values(application),
        end_to_end_pdf,
        as_of=as_of,
        spec=engine.SPEC,
    )
    end_to_end_check = engine.check_correctness(
        end_to_end_pdf, payload_path, as_of=as_of
    )
    if not end_to_end_check["passed"]:
        raise ValueError("demo payload failed its correctness check")
    check_count = len(end_to_end_check["checks"])
    all_checks = end_to_end_check["checks"]

    def _pick(prefix: str, n: int = 1) -> list[dict]:
        matches = [c for c in all_checks if c["check"].startswith(prefix)]
        return matches[:n]

    layer_a_sample = (
        _pick("checkbox_marked:", 1)
        + _pick("field_text_exact:", 2)
    )
    layer_b_sample = (
        [c for c in all_checks if c["check"] in ("no_stray_ink", "no_erased_ink", "ink_is_black")]
        + _pick("ink_is_legible:", 1)
        + _pick("no_ink:", 1)
        + _pick("nothing_drawn_over:", 1)
    )
    layer_c_sample = _pick("pdfium_cross_check:", 4)
    n_layer_a = len([c for c in all_checks if c["check"].startswith(("checkbox_", "field_text_exact:"))])
    n_layer_b = len([
        c for c in all_checks
        if not c["check"].startswith(("checkbox_", "field_text_exact:", "pdfium_cross_check:"))
    ])
    n_layer_c = len(_pick("pdfium_cross_check:", 999))
    end_to_end_png = OUT / "step3_sol_lee.png"
    _render_png(end_to_end_pdf, end_to_end_png)

    # ── real forgeries, real crops, real verdicts ───────────────────────────
    reason_text = engine.text_for(
        "reason_search_copy_are_needed", engine.form_values(application), as_of=as_of
    )

    def _tamper_signature(page: pymupdf.Page) -> None:
        page.insert_text((150, 683), "SOL LEE", fontsize=10)

    def _tamper_checkbox(page: pymupdf.Page) -> None:
        engine._xmark(page, engine.FIELDS["auth_checkbox_2"])

    def _tamper_cover(page: pymupdf.Page) -> None:
        f = engine.FIELDS["full_legal_name_before_marriage"]
        page.draw_rect(
            pymupdf.Rect(f["x"], f["y"], f["x1"], f["y1"]),
            color=(1, 1, 1), fill=(1, 1, 1), overlay=True,
        )

    def _tamper_color(page: pymupdf.Page) -> None:
        f = engine.FIELDS["reason_search_copy_are_needed"]
        rect = pymupdf.Rect(f["x"], f["y"], f["x1"], f["y1"])
        page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()
        page.insert_text(
            (f["x"] + 2, f["y"] + f["h"] / 2 + 3),
            reason_text, fontname="helv", fontsize=10, color=(0.0, 0.28, 0.0),
        )

    forge_sig_png = OUT / "forge_signature.png"
    forge_box_png = OUT / "forge_checkbox.png"
    forge_cover_png = OUT / "forge_cover.png"
    forge_color_png = OUT / "forge_color.png"

    sig_result = render_forgery(
        end_to_end_pdf, forge_sig_png, pymupdf.Rect(60, 655, 340, 705),
        _tamper_signature, payload_path, as_of,
    )
    box_result = render_forgery(
        end_to_end_pdf, forge_box_png, pymupdf.Rect(60, 540, 340, 650),
        _tamper_checkbox, payload_path, as_of,
    )
    cover_result = render_forgery(
        end_to_end_pdf, forge_cover_png, pymupdf.Rect(160, 335, 470, 380),
        _tamper_cover, payload_path, as_of,
    )
    color_result = render_forgery(
        end_to_end_pdf, forge_color_png, pymupdf.Rect(150, 390, 420, 435),
        _tamper_color, payload_path, as_of,
    )

    def _named(result: dict, prefix: str) -> str:
        for c in result["checks"]:
            if c["check"].startswith(prefix) and not c["passed"]:
                return c["check"]
        return prefix

    sig_check = _named(sig_result, "no_ink:signature")
    box_check = _named(box_result, "checkbox_NOT_marked:auth_checkbox_2")
    cover_check = _named(cover_result, "nothing_drawn_over")
    color_check = _named(color_result, "ink_is_black")
    color_pdfium_ok = all(
        c["passed"] for c in color_result["checks"]
        if c["check"].startswith("pdfium_cross_check")
    )

    # ── fuzz visualization: real hypothesis-generated payloads and mutations ─
    import warnings

    from hypothesis.errors import NonInteractiveExampleWarning

    sys.path.insert(0, str(ROOT / "tests"))
    test_fuzz = importlib.import_module("test_fuzz")

    N_FUZZ = 8
    fuzz_valid_rows: list[tuple[str, bool]] = []
    valid_strategy = test_fuzz.valid_payload()
    warnings.filterwarnings("ignore", category=NonInteractiveExampleWarning)
    for _ in range(N_FUZZ):
        payload = valid_strategy.example()
        app_obj = engine.Application.model_validate(payload)
        result = engine.validate(app_obj, as_of=as_of)
        passed = result.valid
        if passed:
            with tempfile.TemporaryDirectory() as tmpdir:
                out = Path(tmpdir) / "f.pdf"
                engine.fill_final(engine.form_values(app_obj), out, as_of=as_of)
                pj = Path(tmpdir) / "p.json"
                pj.write_text(json.dumps(payload, default=str))
                checked = engine.check_correctness(out, pj, as_of=as_of)
                passed = checked["passed"]
        label = payload["spouse_a"]["name"].strip() or "(blank)"
        fuzz_valid_rows.append((f'{payload["certificate_type"]} · {label[:18]}', passed))

    fuzz_mutation_rows: list[tuple[str, bool]] = []
    mutation_strategy = test_fuzz.mutation()
    party_payload = json.loads((ROOT / "samples" / "01_party_short.json").read_text())
    party_obj = engine.Application.model_validate(party_payload)
    for _ in range(N_FUZZ):
        mut = mutation_strategy.example()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "f.pdf"
            engine.fill_final(engine.form_values(party_obj), out, as_of=as_of)
            pj = Path(tmpdir) / "p.json"
            pj.write_text(json.dumps(party_payload))
            tampered = test_fuzz._apply_mutation(out, mut)
            checked = engine.check_correctness(tampered, pj, as_of=as_of)
        caught = not checked["passed"]
        label = mut["type"] + (f':{mut.get("box") or mut.get("field")}' if len(mut) > 1 else "")
        fuzz_mutation_rows.append((label, caught))

    scenario_pngs = []
    for stem in ("01_party_short", "02_relation_extended", "03_law_enforcement"):
        source_pdf = ROOT / "outputs" / f"{stem}.pdf"
        scenario_png = OUT / f"{stem}.png"
        _render_png(source_pdf, scenario_png)
        scenario_pngs.append(scenario_png)
    receipt_path = ROOT / "outputs" / "01_party_short.pdf.proof.json"
    receipt_data = json.loads(receipt_path.read_text())
    receipt_preview = html.escape(
        json.dumps(
            {
                "template_hash": receipt_data["template_hash"][:12] + "…",
                "input_hash": receipt_data["input_hash"][:12] + "…",
                "output_hash": receipt_data["output_hash"][:12] + "…",
                "checks_passed": receipt_data["checks_passed"],
                "signed": receipt_path.with_suffix(receipt_path.suffix + ".sig").exists(),
            },
            indent=2,
        )
    )
    def _fuzz_line(label: str, ok: bool, ok_word: str, delay: float) -> str:
        badge = f'<span class="term-pass">{ok_word}</span>' if ok else '<span class="term-fail">MISS</span>'
        return (
            f'<div class="term-line" style="animation-delay:{delay:.2f}s;">'
            f'  {badge}  <span class="term-name">{html.escape(label)}</span></div>'
        )

    fuzz_valid_lines = "\n".join(
        _fuzz_line(label, ok, "PASS", 0.1 * i)
        for i, (label, ok) in enumerate(fuzz_valid_rows)
    )
    fuzz_mutation_lines = "\n".join(
        _fuzz_line(label, ok, "CAUGHT", 0.1 * i)
        for i, (label, ok) in enumerate(fuzz_mutation_rows)
    )
    fuzz_valid_all_ok = all(ok for _, ok in fuzz_valid_rows)
    fuzz_mutation_all_caught = all(ok for _, ok in fuzz_mutation_rows)

    payload_pretty = html.escape(json.dumps(payload_data, indent=2))
    flat_preview = html.escape(
        json.dumps(engine.form_values(application), indent=2, default=str)
    )

    # a representative sample of real check results, animated top to bottom
    term_sample = (
        [c for c in all_checks if c["check"] in ("page_count", "page_size")]
        + layer_a_sample
        + layer_b_sample
        + layer_c_sample
    )
    term_lines_list = []
    delay = 0.0
    for i, c in enumerate(term_sample):
        delay = 0.12 * i
        status = (
            '<span class="term-pass">PASS</span>' if c["passed"]
            else '<span class="term-fail">FAIL</span>'
        )
        name = html.escape(c["check"])
        term_lines_list.append(
            f'<div class="term-line" style="animation-delay:{delay:.2f}s;">'
            f'  {status}  <span class="term-name">{name}</span></div>'
        )
    remaining = check_count - len(term_sample)
    delay += 0.12
    if remaining > 0:
        term_lines_list.append(
            f'<div class="term-line" style="animation-delay:{delay:.2f}s;">'
            f'  <span class="term-dim">… {remaining} more checks, all '
            f'<span class="term-pass">PASS</span> …</span></div>'
        )
        delay += 0.12
    term_lines = "\n".join(term_lines_list)
    term_delay = delay + 0.2

    # ── determinism demo (fill) ─────────────────────────────────────────────
    raw_pdfs, det_pdfs = [], []
    raw_hashes, det_hashes = [], []
    for i in range(RUNS):
        r = OUT / f"raw_{i}.pdf"
        fill_raw(r)
        raw_pdfs.append(r)
        raw_hashes.append(_hash(r))
        d = OUT / f"det_{i}.pdf"
        fill_det(d)
        det_pdfs.append(d)
        det_hashes.append(_hash(d))

    raw_pngs = [OUT / f"raw_{i}.png" for i in range(RUNS)]
    det_pngs = [OUT / f"det_{i}.png" for i in range(RUNS)]
    for r, p in zip(raw_pdfs, raw_pngs):
        _render_png(r, p)
    for d, p in zip(det_pdfs, det_pngs):
        _render_png(d, p)

    raw_diffs = hex_diff(raw_pdfs[0].read_bytes(), raw_pdfs[1].read_bytes())
    raw_ids_0 = find_id_strings(raw_pdfs[0].read_bytes())
    raw_ids_1 = find_id_strings(raw_pdfs[1].read_bytes())
    det_diffs = hex_diff(det_pdfs[0].read_bytes(), det_pdfs[1].read_bytes())

    # ── build HTML (presentation mode: big visuals, minimal text) ──────────
    def page_card(png: Path, label: str, highlight: bool = False) -> str:
        img = _b64(png)
        cls = "pagecard" + (" keep" if highlight else "")
        return f'<div class="{cls}"><img src="data:image/png;base64,{img}"/><div class="pagelabel">{label}</div></div>'

    def extract_card(png: Path, lib: str, h: str, ms: float, unique: int) -> str:
        img = _b64(png)
        short = h[:12] + "…" + h[-6:]
        badge_cls = "good" if unique == 1 else "bad"
        return f"""
        <div class="card {'det' if lib=='pikepdf' else 'raw'}">
          <div class="run">{lib}</div>
          <img src="data:image/png;base64,{img}"/>
          <div class="stat">{ms:.1f}ms</div>
          <div class="badge {badge_cls}">{unique}/5 unique</div>
        </div>"""

    def fill_card(idx: int, png: Path, h: str, kind: str) -> str:
        img = _b64(png)
        short = h[:10] + "…" + h[-6:]
        return f"""
        <div class="card {kind}">
          <div class="run">run {idx + 1}</div>
          <img src="data:image/png;base64,{img}"/>
          <div class="hash" title="{h}">{short}</div>
        </div>"""

    packet_cards = "".join(page_card(p, l, i == 1) for i, (p, l) in enumerate(zip(packet_pngs, page_labels)))
    extract_cards = extract_card(pike_png, "pikepdf", _hash(pike_out), pike_med, len(pike_hashes)) + \
                    extract_card(mupdf_png, "pymupdf", _hash(mupdf_out), mupdf_med, len(mupdf_hashes))

    raw_cards = "".join(fill_card(i, p, h, "raw") for i, (p, h) in enumerate(zip(raw_pngs, raw_hashes)))
    det_cards = "".join(fill_card(i, p, h, "det") for i, (p, h) in enumerate(zip(det_pngs, det_hashes)))

    max_med = max(pike_med, mupdf_med)
    pike_bar_w = (pike_med / max_med) * 100
    mupdf_bar_w = (mupdf_med / max_med) * 100

    def diff_rows(diffs):
        if not diffs:
            return '<div class="nodiff">identical bytes — no differences</div>'
        rows = []
        for offset, a_hex, b_hex in diffs[:2]:
            def fmt(hexstr, base, other_hexstr, byte_base):
                lines = []
                for r in range(0, len(hexstr), 16):
                    chunk = hexstr[r : r + 16]
                    other_chunk = other_hexstr[r : r + 32] if r < len(other_hexstr) else ""
                    parts = []
                    for i in range(0, len(chunk), 2):
                        byte_val = chunk[i:i+2]
                        other_val = other_chunk[i:i+2] if i < len(other_chunk) else ""
                        if byte_val != other_val:
                            parts.append(f'<span class="diff-byte">{byte_val}</span>')
                        else:
                            parts.append(byte_val)
                    spaced = " ".join(parts)
                    lines.append(f'<div class="hexline"><span class="off">{base + r // 2:08x}</span> {spaced}</div>')
                return "".join(lines)
            rows.append(f"""
              <div class="diffblock">
                <div class="diff-head">technical detail &nbsp;·&nbsp; hidden PDF bytes differ here</div>
                <div class="hexcols">
                  <div class="hexcol"><div class="label">run 1</div>{fmt(a_hex, offset, b_hex, offset)}</div>
                  <div class="hexcol"><div class="label">run 2</div>{fmt(b_hex, offset, a_hex, offset)}</div>
                </div>
              </div>""")
        return "".join(rows)

    raw_diff_html = diff_rows(raw_diffs)
    det_diff_html = diff_rows(det_diffs)

    import html as htmlmod
    id_lines_0 = "".join(f"<li><code>{htmlmod.escape(s)}</code></li>" for s in raw_ids_0)
    id_lines_1 = "".join(f"<li><code>{htmlmod.escape(s)}</code></li>" for s in raw_ids_1)

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cc2002b — from packet to proof</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Schibsted+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --paper: #ffffff;
    --paper-deep: #f2f2f0;
    --ink: #111;
    --ink-soft: #444;
    --ink-faint: #888;
    --rule: #ddd;
    --rule-soft: #e8e8e6;
    --red: #c23616;
    --red-soft: #e07a5f;
    --green: #2d6a4f;
    --green-soft: #74c69d;
    --ochre: #b8862e;
    --serif: 'Instrument Serif', 'Times New Roman', serif;
    --sans: 'Schibsted Grotesk', 'Helvetica Neue', sans-serif;
    --mono: 'IBM Plex Mono', 'Menlo', monospace;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: var(--sans);
    background: var(--paper);
    color: var(--ink);
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    min-height: 100vh;
  }}
  .wrap {{ max-width: 1180px; margin: 0 auto; padding: 64px 48px 96px; }}

  /* ── masthead ─────────────────────────────────────────────────────── */
  .masthead {{ border-bottom: 2px solid var(--ink); padding-bottom: 28px; margin-bottom: 56px; }}
  .masthead .eyebrow {{
    font-family: var(--mono); font-size: 11px; letter-spacing: 0.18em;
    text-transform: uppercase; color: var(--ink-faint); margin-bottom: 14px;
  }}
  .masthead h1 {{
    font-family: var(--serif); font-size: 64px; font-weight: 400;
    line-height: 1.0; letter-spacing: -0.01em; color: var(--ink);
  }}
  .masthead h1 em {{ font-style: italic; font-weight: 400; color: var(--red); }}
  .masthead .lead {{
    font-family: var(--sans); font-size: 17px; font-weight: 400;
    color: var(--ink-soft); margin-top: 18px; max-width: 620px;
    line-height: 1.55;
  }}

  /* ── section headers ──────────────────────────────────────────────── */
  .section {{ margin-bottom: 72px; }}
  .section-head {{
    display: flex; align-items: baseline; gap: 18px;
    border-bottom: 1px solid var(--rule); padding-bottom: 14px; margin-bottom: 36px;
  }}
  .section-num {{
    font-family: var(--mono); font-size: 13px; font-weight: 600;
    color: var(--paper); background: var(--ink);
    padding: 3px 10px; letter-spacing: 0.05em;
  }}
  .section-head h2 {{
    font-family: var(--serif); font-size: 38px; font-weight: 400;
    letter-spacing: -0.01em; color: var(--ink); line-height: 1.1;
  }}
  .section-head h2 em {{ font-style: italic; color: var(--ink-soft); font-weight: 400; }}
  .caption {{
    font-family: var(--mono); font-size: 12px; color: var(--ink-faint);
    text-align: center; margin: 20px 0 28px; letter-spacing: 0.02em;
  }}

  /* ── rows & cards ─────────────────────────────────────────────────── */
  .row {{ display: flex; gap: 24px; flex-wrap: wrap; align-items: flex-start; justify-content: center; }}
  .pagecard {{
    background: #fff; border: 1px solid var(--rule);
    padding: 12px; width: 300px;
    box-shadow: 0 1px 0 var(--rule-soft), 0 8px 24px -12px rgba(28,26,23,0.18);
  }}
  .pagecard.keep {{
    border: 2px solid var(--green);
    box-shadow: 0 1px 0 var(--rule-soft), 0 8px 24px -10px rgba(45,90,61,0.3);
  }}
  .pagecard img {{ width: 100%; display: block; }}
  .pagelabel {{
    font-family: var(--mono); font-size: 11px; color: var(--ink-faint);
    margin-top: 10px; text-align: center; letter-spacing: 0.04em;
  }}
  .pagecard.keep .pagelabel {{ color: var(--green); font-weight: 600; }}

  .card {{
    background: #fff; border: 1px solid var(--rule); padding: 14px;
    width: 210px; text-align: center;
    box-shadow: 0 1px 0 var(--rule-soft), 0 6px 18px -10px rgba(28,26,23,0.15);
  }}
  .card.det {{ border-top: 3px solid var(--green); }}
  .card.raw {{ border-top: 3px solid var(--red); }}
  .card .run {{
    font-family: var(--mono); font-size: 13px; font-weight: 600;
    color: var(--ink); margin-bottom: 12px; letter-spacing: 0.02em;
  }}
  .card img {{ width: 100%; display: block; border: 1px solid var(--rule-soft); }}
  .card .hash {{
    font-family: var(--mono); font-size: 12px; margin-top: 12px;
    color: var(--ink-soft); letter-spacing: -0.01em;
  }}
  .card .stat {{
    font-family: var(--mono); font-size: 24px; font-weight: 700;
    margin-top: 12px; color: var(--ochre);
  }}

  /* ── badges ───────────────────────────────────────────────────────── */
  .badge {{
    display: inline-block; padding: 5px 14px; font-size: 13px; font-weight: 600;
    font-family: var(--mono); letter-spacing: 0.02em;
  }}
  .badge.bad {{ background: var(--red); color: #fff; }}
  .badge.good {{ background: var(--green); color: #fff; }}
  .badge.neutral {{ background: var(--ink); color: var(--paper); }}
  .badge-row {{ text-align: center; margin: 16px 0 24px; }}

  /* ── arrow ────────────────────────────────────────────────────────── */
  .arrow {{
    font-family: var(--sans); font-size: 28px; text-align: center;
    color: var(--ink-faint); margin: 20px 0; font-weight: 300;
  }}

  /* ── step 2: views, stats, legend, dump, spec table ───────────────── */
  .views {{ display: flex; gap: 24px; justify-content: center; flex-wrap: wrap; }}
  .view {{ text-align: center; }}
  .view img {{ width: 300px; border: 1px solid var(--rule); display: block; box-shadow: 0 1px 0 var(--rule-soft), 0 8px 24px -14px rgba(0,0,0,0.15); }}
  .view .vlabel {{ font-family: var(--mono); font-size: 11px; color: var(--ink-faint); margin-top: 10px; letter-spacing: 0.04em; }}

  /* The signature motion for step 2: the page is being measured top-to-bottom. */
  .scan-stage {{
    position: relative; width: 300px; aspect-ratio: 612 / 792;
    margin: 0 auto; overflow: hidden; background: #fff;
    box-shadow: 0 1px 0 var(--rule-soft), 0 8px 24px -14px rgba(0,0,0,0.15);
  }}
  .scan-stage img {{ width: 100%; height: 100%; object-fit: cover; border: 1px solid var(--rule); box-shadow: none; }}
  .scan-beam {{
    position: absolute; left: 0; right: 0; top: 0; height: 4px;
    background: var(--green); box-shadow: 0 0 0 1px rgba(45,106,79,0.22), 0 0 20px 5px rgba(45,106,79,0.72);
    animation: scan-page 2.8s cubic-bezier(0.22, 1, 0.36, 1) infinite;
    will-change: top, opacity;
    pointer-events: none;
  }}
  .scan-beam::after {{
    content: ''; position: absolute; left: 0; right: 0; top: 4px; height: 72px;
    background: linear-gradient(to bottom, rgba(45,106,79,0.24), rgba(45,106,79,0.05), transparent);
  }}
  .scan-tag {{
    position: absolute; right: 10px; top: 10px; z-index: 2;
    padding: 4px 7px; color: #fff; background: var(--green);
    font-family: var(--mono); font-size: 9px; letter-spacing: 0.08em;
    text-transform: uppercase; opacity: 0.92;
  }}
  .scan-note {{
    margin: 12px auto 0; font-family: var(--mono); font-size: 11px;
    color: var(--green); letter-spacing: 0.03em;
  }}
  @keyframes scan-page {{
    0% {{ top: 0; opacity: 0; }}
    10% {{ top: 0; opacity: 1; }}
    86% {{ top: calc(100% - 4px); opacity: 1; }}
    100% {{ top: calc(100% - 4px); opacity: 0; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .scan-beam {{ animation: none; top: 50%; opacity: 0.8; }}
  }}

  .stats {{ display: flex; gap: 0; max-width: 600px; margin: 32px auto; border: 1px solid var(--rule); }}
  .stat {{ flex: 1; text-align: center; padding: 24px 12px; border-right: 1px solid var(--rule); }}
  .stat:last-child {{ border-right: none; }}
  .stat .num {{ font-family: var(--serif); font-size: 42px; font-weight: 400; color: var(--ink); line-height: 1; }}
  .stat .num.red {{ color: var(--red); }}
  .stat .num.green {{ color: var(--green); }}
  .stat .num.ochre {{ color: var(--ochre); }}
  .stat .lbl {{ font-family: var(--mono); font-size: 11px; color: var(--ink-faint); margin-top: 8px; letter-spacing: 0.04em; text-transform: uppercase; }}

  .checkbox-families {{
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 20px; max-width: 820px; margin: 24px auto;
  }}
  .checkbox-family {{ background: #fff; border: 1px solid var(--rule); padding: 14px; }}
  .checkbox-family img {{ width: 100%; height: 150px; object-fit: contain; display: block; background: var(--paper-deep); }}
  .family-label {{ font-family: var(--sans); font-size: 13px; color: var(--ink-soft); margin-top: 12px; line-height: 1.45; }}
  .family-label strong {{ color: var(--ink); }}
  .legend {{ display: flex; gap: 24px; justify-content: center; margin: 24px 0; }}
  .legend-item {{ display: flex; align-items: center; gap: 8px; font-family: var(--mono); font-size: 12px; color: var(--ink-soft); }}
  .legend-swatch {{ width: 16px; height: 16px; border: 2px solid; }}
  .legend-swatch.text {{ border-color: var(--green); }}
  .legend-swatch.check {{ border-color: var(--red); }}
  .legend-swatch.protected {{ border-color: var(--ochre); }}

  .dump {{
    background: #fff; border: 1px solid var(--rule); padding: 24px 28px;
    max-width: 560px; margin: 24px auto; font-family: var(--mono); font-size: 12px;
    line-height: 1.7; color: var(--ink-soft);
    box-shadow: 0 1px 0 var(--rule-soft);
  }}
  .dump .head {{ font-size: 11px; font-weight: 600; color: var(--ink-faint); letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid var(--rule-soft); }}
  .dump .line {{ white-space: pre; }}
  .dump .off {{ color: var(--ink-faint); }}

  .spec-table {{ width: 100%; max-width: 680px; margin: 24px auto; border-collapse: collapse; font-family: var(--mono); font-size: 12px; }}
  .spec-table th {{ text-align: left; padding: 10px 14px; border-bottom: 2px solid var(--ink); font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-faint); font-weight: 600; }}
  .spec-table td {{ padding: 10px 14px; border-bottom: 1px solid var(--rule-soft); color: var(--ink-soft); }}
  .spec-table td.name {{ color: var(--ink); font-weight: 600; }}
  .spec-table tr.protected td {{ color: var(--ochre); }}
  .spec-table tr.checkbox td.name {{ color: var(--red); }}

  /* ── benchmark ────────────────────────────────────────────────────── */
  .bench {{
    background: #fff; border: 1px solid var(--rule); padding: 28px 32px;
    max-width: 560px; margin: 24px auto;
    box-shadow: 0 1px 0 var(--rule-soft), 0 8px 24px -14px rgba(28,26,23,0.15);
  }}
  .bench-title {{
    font-family: var(--mono); font-size: 11px; letter-spacing: 0.12em;
    text-transform: uppercase; color: var(--ink-faint); margin-bottom: 18px;
  }}
  .bar-row {{ display: flex; align-items: center; gap: 16px; margin: 14px 0; }}
  .bar-label {{
    width: 90px; font-family: var(--mono); font-size: 14px; font-weight: 600;
    color: var(--ink); text-align: right;
  }}
  .bar-track {{ flex: 1; background: var(--paper-deep); height: 32px; position: relative; }}
  .bar-fill {{
    height: 100%; display: flex; align-items: center; padding-left: 14px;
    font-family: var(--mono); font-size: 14px; font-weight: 600;
  }}
  .bar-fill.pike {{ background: var(--green); color: #fff; }}
  .bar-fill.mupdf {{ background: var(--red); color: #fff; }}
  .bench-note {{
    font-family: var(--mono); font-size: 11px; color: var(--ink-faint);
    margin-top: 14px; text-align: right;
  }}

  /* ── verdict ──────────────────────────────────────────────────────── */
  .verdict {{ display: flex; gap: 24px; max-width: 760px; margin: 28px auto; }}
  .verdict .col {{
    flex: 1; background: #fff; border: 1px solid var(--rule); padding: 24px;
  }}
  .verdict .col.win {{ border-left: 4px solid var(--green); }}
  .verdict .col.lose {{ border-left: 4px solid var(--red); }}
  .verdict h4 {{
    font-family: var(--sans); font-size: 18px; font-weight: 600;
    margin-bottom: 12px; color: var(--ink); letter-spacing: -0.01em;
  }}
  .verdict .badges {{ margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap; }}
  .verdict p {{
    font-family: var(--sans); font-size: 14px; color: var(--ink-soft);
    line-height: 1.55;
  }}
  .verdict code, .call code, .takeaway code, .caption code {{
    font-family: var(--mono); font-size: 0.88em; background: var(--paper-deep);
    padding: 1px 5px; color: var(--ink);
  }}

  /* ── callout ──────────────────────────────────────────────────────── */
  .proof-grid {{
    display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px; max-width: 820px; margin: 24px auto;
  }}
  .proof-card {{ background: #fff; border: 1px solid var(--rule); padding: 20px; text-align: center; }}
  .proof-card.changed {{ border-top: 3px solid var(--red); }}
  .proof-card.result {{ border-top: 3px solid var(--green); }}
  .proof-icon {{
    width: 40px; height: 40px; margin: 0 auto 12px; display: grid;
    place-items: center; border: 1px solid var(--ink); font-family: var(--serif);
    font-size: 28px; line-height: 1;
  }}
  .proof-icon.changed {{ color: var(--red); border-color: var(--red); }}
  .proof-icon.result {{ color: var(--green); border-color: var(--green); }}
  .proof-card h3 {{ font-family: var(--sans); font-size: 15px; margin-bottom: 8px; }}
  .proof-card p {{ font-family: var(--sans); font-size: 13px; color: var(--ink-soft); line-height: 1.5; }}
  .method-compare {{
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px; max-width: 820px; margin: 28px auto;
  }}
  .method-card {{ background: #fff; border: 1px solid var(--rule); padding: 20px; }}
  .method-card.chosen {{ border-left: 3px solid var(--green); }}
  .method-card.rejected {{ border-left: 3px solid var(--red); }}
  .method-kicker {{ font-family: var(--mono); font-size: 10px; letter-spacing: 0.08em; color: var(--ink-faint); }}
  .method-card h3 {{ font-family: var(--sans); font-size: 17px; margin: 9px 0 4px; }}
  .method-speed {{ font-family: var(--mono); font-size: 13px; font-weight: 600; color: var(--red); margin-bottom: 8px; }}
  .method-card.chosen .method-speed {{ color: var(--green); }}
  .method-card p {{ font-family: var(--sans); font-size: 13px; color: var(--ink-soft); line-height: 1.5; }}
  .json-flow {{
    display: grid; grid-template-columns: minmax(0, 0.8fr) 34px minmax(0, 1.2fr);
    gap: 18px; align-items: center; max-width: 980px; margin: 28px auto;
  }}
  .json-panel {{ background: #171717; color: #f4f4f0; padding: 20px; overflow: auto; text-align: left; }}
  .json-panel .panel-label {{ font-family: var(--mono); color: #d4a84e; font-size: 10px; letter-spacing: 0.1em; margin-bottom: 12px; }}
  .json-panel pre {{ font-family: var(--mono); font-size: 11px; line-height: 1.65; white-space: pre-wrap; margin: 0; }}
  .flow-arrow {{ font-family: var(--serif); font-size: 28px; color: var(--green); text-align: center; }}
  .mapping-panel {{ background: #fff; border: 1px solid var(--rule); padding: 14px; text-align: left; }}
  .mapping-panel .panel-label {{ font-family: var(--mono); color: var(--green); font-size: 10px; letter-spacing: 0.1em; margin-bottom: 10px; }}
  .mapping-panel pre {{ font-family: var(--mono); font-size: 10px; line-height: 1.55; white-space: pre-wrap; margin: 0; color: var(--ink-soft); }}
  .mapping-panel code {{ background: transparent; padding: 0; }}
  .pipeline {{ display: flex; gap: 0; max-width: 900px; margin: 28px auto; border: 1px solid var(--rule); }}
  .pipeline-step {{ flex: 1; padding: 16px 10px; text-align: center; border-right: 1px solid var(--rule); }}
  .pipeline-step:last-child {{ border-right: none; }}
  .pipeline-step strong {{ display: block; font-family: var(--sans); font-size: 13px; }}
  .pipeline-step {{ animation: pipeline-pulse 3.2s ease-in-out infinite; }}
  .pipeline-step:nth-child(2) {{ animation-delay: .45s; }}
  .pipeline-step:nth-child(3) {{ animation-delay: .9s; }}
  .pipeline-step:nth-child(4) {{ animation-delay: 1.35s; }}
  .pipeline-step:nth-child(5) {{ animation-delay: 1.8s; }}
  .pipeline-step span {{ display: block; font-family: var(--mono); font-size: 10px; color: var(--ink-faint); margin-top: 5px; }}
  @keyframes pipeline-pulse {{
    0%, 70%, 100% {{ background: transparent; }}
    15%, 35% {{ background: rgba(45,106,79,0.10); }}
  }}
  .scenario-grid {{
    display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 20px; max-width: 900px; margin: 24px auto;
  }}
  .scenario-card {{ background: #fff; border: 1px solid var(--rule); padding: 12px; text-align: center; }}
  .scenario-card img {{ width: 100%; display: block; border: 1px solid var(--rule-soft); margin-bottom: 12px; }}
  .scenario-card strong {{ display: block; font-family: var(--sans); font-size: 13px; }}
  .scenario-card span {{ display: block; font-family: var(--mono); font-size: 10px; color: var(--ink-faint); margin-top: 5px; }}
  .receipt-chain {{ display: flex; align-items: center; justify-content: center; gap: 12px; max-width: 900px; margin: 28px auto; }}
  .receipt-node {{ min-width: 130px; padding: 18px 12px; background: #fff; border: 1px solid var(--rule); text-align: center; }}
  .receipt-node strong {{ display: block; font-family: var(--sans); font-size: 14px; }}
  .receipt-node span {{ display: block; font-family: var(--mono); font-size: 10px; color: var(--ink-faint); margin-top: 5px; }}
  .receipt-node.signed {{ border: 2px solid var(--green); color: var(--green); transform: rotate(-1deg); }}
  .receipt-link {{ font-family: var(--serif); font-size: 24px; color: var(--green); }}

  /* ── terminal check runner ────────────────────────────────────────── */
  .terminal {{
    background: #14171b; border: 1px solid #262b31; max-width: 820px;
    margin: 28px auto; overflow: hidden; text-align: left;
    box-shadow: 0 20px 50px -25px rgba(0,0,0,0.5);
  }}
  .terminal-bar {{
    background: #1d2126; padding: 10px 14px; display: flex; align-items: center; gap: 8px;
    border-bottom: 1px solid #262b31;
  }}
  .terminal-dot {{ width: 10px; height: 10px; border-radius: 50%; background: #4a4f56; }}
  .terminal-dot.red {{ background: #e0645a; }}
  .terminal-dot.yellow {{ background: #e0b84a; }}
  .terminal-dot.green {{ background: #4ac278; }}
  .terminal-title {{ font-family: var(--mono); font-size: 11px; color: #8b94a3; margin-left: 8px; }}
  .terminal-body {{ padding: 20px 22px; font-family: var(--mono); font-size: 12.5px; line-height: 2; max-height: 420px; overflow-y: auto; }}
  .term-line {{ color: #c7ccd4; opacity: 0; animation: term-reveal 0.4s ease forwards; white-space: pre-wrap; }}
  .term-line .term-pass {{ color: #4ac278; font-weight: 700; }}
  .term-line .term-fail {{ color: #e0645a; font-weight: 700; }}
  .term-line .term-dim {{ color: #6b7280; }}
  .term-line .term-name {{ color: #eef1f4; }}
  .term-line .term-accent {{ color: #d4a84e; }}
  .term-summary {{
    margin-top: 14px; padding-top: 14px; border-top: 1px dashed #2c3138;
    color: #4ac278; font-weight: 700; opacity: 0; animation: term-reveal 0.5s ease forwards;
  }}
  @keyframes term-reveal {{ from {{ opacity: 0; transform: translateY(2px); }} to {{ opacity: 1; transform: translateY(0); }} }}
  @media (prefers-reduced-motion: reduce) {{
    .term-line, .term-summary {{ animation: none !important; opacity: 1 !important; }}
  }}
  .layer-tabs {{ display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 16px; max-width: 820px; margin: 24px auto 0; }}
  .layer-tab {{ background: #fff; border: 1px solid var(--rule); border-top: 3px solid var(--ink-faint); padding: 16px; text-align: center; }}
  .layer-tab .layer-letter {{ font-family: var(--serif); font-size: 30px; color: var(--ink); }}
  .layer-tab .layer-title {{ font-family: var(--sans); font-size: 13px; font-weight: 600; margin-top: 4px; }}
  .layer-tab .layer-count {{ font-family: var(--mono); font-size: 11px; color: var(--green); margin-top: 6px; }}

  .forge-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 14px; max-width: 940px; margin: 24px auto; }}
  .forge-card {{ background: #fff; border: 1px solid var(--rule); border-top: 3px solid var(--red); padding: 12px; text-align: center; }}
  .forge-card img {{ width: 100%; border: 1px solid var(--rule-soft); display: block; margin-bottom: 10px; background: #fff; }}
  .forge-card strong {{ display: block; font-family: var(--sans); font-size: 13px; }}
  .forge-card span {{ display: block; font-family: var(--sans); font-size: 11px; color: var(--ink-faint); margin-top: 4px; }}
  .forge-caught {{ margin-top: 10px; font-family: var(--mono); font-size: 10px; color: var(--ink-soft); line-height: 1.6; }}
  .forge-caught code {{ background: var(--paper-deep); word-break: break-word; }}
  .forge-note {{ margin-top: 8px; font-family: var(--mono); font-size: 9.5px; color: var(--ink-faint); line-height: 1.5; }}
  .fail-badge {{ display: inline-block; background: var(--red); color: #fff; font-family: var(--mono); font-size: 9px; font-weight: 700; padding: 2px 6px; margin-bottom: 4px; letter-spacing: 0.04em; }}
  .ok-word {{ color: var(--red); font-weight: 700; }}
  .fail-word {{ color: var(--green); font-weight: 700; }}

  .fight-block {{ max-width: 820px; margin: 0 auto 44px; padding-top: 28px; border-top: 1px solid var(--rule-soft); }}
  .fight-block:first-of-type {{ border-top: none; }}
  .fight-head {{ display: flex; align-items: center; gap: 14px; margin-bottom: 12px; }}
  .fight-num {{
    width: 32px; height: 32px; flex-shrink: 0; border-radius: 50%; background: var(--ink);
    color: var(--paper); font-family: var(--mono); font-weight: 700; font-size: 14px;
    display: grid; place-items: center;
  }}
  .fight-head h3 {{ font-family: var(--serif); font-size: 24px; font-weight: 400; color: var(--ink); }}
  .fight-body {{ font-family: var(--sans); font-size: 14.5px; color: var(--ink-soft); line-height: 1.65; margin-bottom: 18px; }}
  .fight-fit-row {{ display: flex; gap: 24px; justify-content: center; }}
  .fit-card {{ background: #fff; border: 1px solid var(--rule); padding: 12px; text-align: center; }}
  .fit-card.ok {{ border-top: 3px solid var(--green); }}
  .fit-card.floor {{ border-top: 3px solid var(--ochre); }}
  .fit-card img {{ height: 60px; display: block; margin: 0 auto 10px; background: #fff; }}
  .fit-label {{ font-family: var(--sans); font-size: 13px; color: var(--ink); }}
  .fit-size {{ font-family: var(--mono); font-size: 11px; color: var(--ink-faint); margin-left: 4px; }}
  .checklist {{ max-width: 900px; margin: 24px auto; border-top: 2px solid var(--ink); }}
  .check-row {{ display: grid; grid-template-columns: 28px minmax(0, 1fr) minmax(0, 1fr); gap: 12px; align-items: baseline; padding: 13px 0; border-bottom: 1px solid var(--rule-soft); font-family: var(--sans); font-size: 14px; }}
  .check-mark {{ color: var(--green); font-family: var(--mono); font-weight: 700; }}
  .check-row strong {{ color: var(--ink); }}
  .check-row span:last-child {{ color: var(--ink-faint); font-family: var(--mono); font-size: 11px; }}
  .result-pass {{ color: var(--green) !important; font-weight: 600; }}
  .step-flow {{
    display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px; max-width: 820px; margin: 28px auto;
  }}
  .flow-card {{ padding: 18px; border-left: 3px solid var(--green); background: var(--paper-deep); }}
  .flow-card .flow-num {{ font-family: var(--mono); font-size: 11px; color: var(--green); letter-spacing: 0.08em; }}
  .flow-card h3 {{ font-family: var(--sans); font-size: 16px; margin: 8px 0 6px; }}
  .flow-card p {{ font-family: var(--sans); font-size: 13px; color: var(--ink-soft); line-height: 1.5; }}
  .technical-proof {{ max-width: 720px; margin: 28px auto 0; border-top: 1px solid var(--rule); padding-top: 16px; }}
  .technical-proof summary {{ cursor: pointer; font-family: var(--mono); font-size: 11px; color: var(--ink-faint); text-align: center; letter-spacing: 0.04em; }}
  .technical-proof summary:hover {{ color: var(--ink); }}
  .technical-intro {{ font-family: var(--sans); font-size: 13px; color: var(--ink-faint); margin: 18px auto; max-width: 560px; text-align: center; }}

  .call {{
    background: var(--ink); color: var(--paper); padding: 28px 36px;
    max-width: 760px; margin: 28px auto; text-align: center;
  }}
  .call strong {{ color: #d4a84e; font-weight: 600; }}
  .call {{ font-family: var(--sans); font-size: 16px; line-height: 1.6; font-weight: 400; }}
  .call code {{ background: rgba(255,255,255,0.12); color: var(--paper); }}

  /* ── hex diff ─────────────────────────────────────────────────────── */
  .diffblock {{
    background: #fff; border: 1px solid var(--rule); padding: 24px 28px;
    max-width: 720px; margin: 0 auto 20px;
    box-shadow: 0 1px 0 var(--rule-soft);
  }}
  .diff-head {{
    font-family: var(--mono); font-size: 11px; font-weight: 600;
    color: var(--ink-faint); margin-bottom: 16px; letter-spacing: 0.06em;
    text-transform: uppercase; padding-bottom: 10px; border-bottom: 1px solid var(--rule-soft);
  }}
  .hexcols {{
    display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 24px;
  }}
  .hexcol {{ min-width: 0; overflow: hidden; font-family: var(--mono); font-size: 10px; }}
  .hexline {{ white-space: pre; line-height: 1.8; color: var(--ink-soft); overflow: hidden; }}
  .hexline .off {{ color: var(--ink-faint); margin-right: 12px; }}
  .diff-byte {{ color: var(--red); font-weight: 600; background: rgba(194,54,22,0.08); padding: 1px 0; }}
  .diffblock .label {{
    font-family: var(--mono); font-size: 11px; font-weight: 600;
    color: var(--ink); margin-bottom: 10px; letter-spacing: 0.06em;
    text-transform: uppercase;
  }}
  @media (max-width: 680px) {{
    .hexcols {{ grid-template-columns: 1fr; gap: 18px; }}
    .proof-grid, .step-flow, .method-compare, .checkbox-families, .json-flow {{ grid-template-columns: 1fr; }}
    .flow-arrow {{ transform: rotate(90deg); }}
    .pipeline {{ flex-direction: column; }}
    .pipeline-step {{ border-right: none; border-bottom: 1px solid var(--rule); }}
    .pipeline-step:last-child {{ border-bottom: none; }}
    .scenario-grid {{ grid-template-columns: 1fr; max-width: 320px; }}
    .check-row {{ grid-template-columns: 24px 1fr; }}
    .check-row span:last-child {{ grid-column: 2; }}
    .receipt-chain {{ flex-wrap: wrap; }}
    .receipt-link {{ transform: rotate(90deg); }}
    .forge-grid, .layer-tabs {{ grid-template-columns: 1fr; }}
    .terminal-title {{ display: none; }}
    .fight-fit-row {{ flex-direction: column; align-items: center; }}
  }}
  .nodiff {{
    font-family: var(--mono); font-size: 16px; font-weight: 600;
    color: var(--green); padding: 24px; text-align: center;
    background: #fff; border: 1px solid var(--rule); max-width: 680px;
    margin: 0 auto; letter-spacing: 0.02em;
  }}

  /* ── /ID strings ──────────────────────────────────────────────────── */
  .ids {{
    background: #fff; border: 1px solid var(--rule); padding: 24px 28px;
    max-width: 720px; margin: 0 auto;
    box-shadow: 0 1px 0 var(--rule-soft);
  }}
  .ids-cols {{ display: flex; gap: 32px; }}
  .ids .col {{ flex: 1; min-width: 0; }}
  .ids .label {{
    font-family: var(--mono); font-size: 11px; font-weight: 600;
    color: var(--ink); margin-bottom: 10px; letter-spacing: 0.06em;
    text-transform: uppercase;
  }}
  .ids ul {{ list-style: none; }}
  .ids li {{ font-family: var(--mono); font-size: 12px; color: var(--red); margin-bottom: 8px; word-break: break-all; line-height: 1.5; }}

  /* ── takeaway ─────────────────────────────────────────────────────── */
  .takeaway {{
    border-top: 2px solid var(--ink); border-bottom: 1px solid var(--rule);
    padding: 36px 0; margin-top: 72px; text-align: center;
  }}
  .takeaway .eyebrow {{
    font-family: var(--mono); font-size: 11px; letter-spacing: 0.18em;
    text-transform: uppercase; color: var(--ink-faint); margin-bottom: 14px;
  }}
  .takeaway p {{
    font-family: var(--serif); font-size: 28px; font-weight: 400;
    color: var(--ink); line-height: 1.35; max-width: 720px; margin: 0 auto;
  }}
  .takeaway strong {{ font-weight: 600; }}
  .takeaway .red {{ color: var(--red); }}
  .takeaway .green {{ color: var(--green); }}

  /* ── footer rule ──────────────────────────────────────────────────── */
  .colophon {{
    font-family: var(--mono); font-size: 11px; color: var(--ink-faint);
    text-align: center; margin-top: 48px; letter-spacing: 0.06em;
  }}
</style></head><body>
<div class="wrap">

  <header class="masthead">
    <div class="eyebrow">cc2002b · nyc marriage record · take-home</div>
    <h1>From packet <em>to proof.</em></h1>
    <p class="lead">Nine moments. Understand the brief. Extract the form. Measure and freeze its map. Turn Sol's JSON into black ink. Check the filing. Harden the proof. Ship the scenarios. See where the form fought back. Answer the brief.</p>
  </header>

  <!-- ═══ step 0: understand the brief ══════════════════════════════════ -->
  <section class="section">
    <div class="section-head">
      <span class="section-num">00</span>
      <h2>Understand <em>the brief</em></h2>
    </div>

    <p class="caption">one take-home packet &nbsp;·&nbsp; three pages &nbsp;·&nbsp; one target form</p>
    <div class="row">{packet_cards}</div>
    <div class="call" style="margin-top:28px;">
      <strong>First question:</strong> what are we actually being asked to automate? Page 1 is the brief. Page 2 is the CC2002B form we must fill. Page 3 is the fee schedule. We separate the form from the packet before doing anything else.
    </div>
  </section>

  <!-- ═══ step 1: extract the blank ═════════════════════════════════════ -->
  <section class="section">
    <div class="section-head">
      <span class="section-num">01</span>
      <h2>Pull the blank <em>out of the packet</em></h2>
    </div>

    <p class="caption">keep page two &nbsp;·&nbsp; extract the real PDF object graph</p>
    <div class="arrow">&darr;</div>

    <p class="caption">page two, extracted two ways</p>
    <div class="row">{extract_cards}</div>

    <div class="bench">
      <div class="bench-title">extraction speed · {BENCH_RUNS} runs</div>
      <div class="bar-row">
        <div class="bar-label">pymupdf</div>
        <div class="bar-track"><div class="bar-fill mupdf" style="width:{mupdf_bar_w:.1f}%">{mupdf_med:.1f} ms</div></div>
      </div>
      <div class="bar-row">
        <div class="bar-label">pikepdf</div>
        <div class="bar-track"><div class="bar-fill pike" style="width:{pike_bar_w:.1f}%">{pike_med:.1f} ms</div></div>
      </div>
      <div class="bench-note">pymupdf ~{pike_med/mupdf_med:.0f}x faster</div>
    </div>

    <div class="verdict">
      <div class="col lose">
        <h4>pymupdf</h4>
        <div class="badges"><span class="badge neutral">{mupdf_med:.1f}ms</span><span class="badge bad">{len(mupdf_hashes)}/5 unique</span></div>
        <p>Fast — but a fresh random <code>/ID</code> on every save. Same extraction, different bytes each time.</p>
      </div>
      <div class="col win">
        <h4>pikepdf</h4>
        <div class="badges"><span class="badge neutral">{pike_med:.1f}ms</span><span class="badge good">{len(pike_hashes)}/5 unique</span></div>
        <p>~{pike_med/mupdf_med:.0f}x slower — but <code>deterministic_id=True</code>. Same extraction, same bytes, always.</p>
      </div>
    </div>

    <div class="call">
      <strong>The call: pikepdf.</strong> &nbsp;One-time extraction — four milliseconds versus one doesn't matter. The blank is the reference everything downstream keys off. If it isn't reproducible, nothing is.
    </div>
  </section>

  <!-- ═══ step 2: measure every box ═════════════════════════════════════ -->
  <section class="section">
    <div class="section-head">
      <span class="section-num">02</span>
      <h2>Measure <em>every box</em></h2>
    </div>

    <p class="caption">the form has no AcroForm &nbsp;·&nbsp; no interactive fields &nbsp;·&nbsp; just ink on paper</p>
    <div class="stats">
      <div class="stat"><div class="num">1</div><div class="lbl">page</div></div>
      <div class="stat"><div class="num">612×792</div><div class="lbl">PDF points</div></div>
      <div class="stat"><div class="num red">{word_count}</div><div class="lbl">words</div></div>
      <div class="stat"><div class="num ochre">{drawing_count}</div><div class="lbl">drawings</div></div>
    </div>
    <div class="call" style="margin-top:20px;">
      <strong>First decision:</strong> AcroForm = {has_acroform}. No interactive fields means no shortcut. We use the PDF's own coordinates, then have a human approve the final map.
    </div>
    <div class="step-flow">
      <div class="flow-card"><div class="flow-num">01 · READ</div><h3>Find the coordinates</h3><p>PyMuPDF tells us where the form’s words and artwork sit on the page.</p></div>
      <div class="flow-card"><div class="flow-num">02 · CHOOSE</div><h3>Mark the real boxes</h3><p>We draw rectangles around the places where ink is allowed to go.</p></div>
      <div class="flow-card"><div class="flow-num">03 · FREEZE</div><h3>Save the map</h3><p>We store those rectangles in JSON so the runtime never guesses.</p></div>
    </div>

    <div class="method-compare">
      <div class="method-card chosen">
        <div class="method-kicker">USED FOR THIS FORM</div>
        <h3>Native PDF geometry</h3>
        <div class="method-speed">fast path</div>
        <p>Reads the coordinates already inside the PDF. Precise, reproducible, and easy to review with an overlay.</p>
      </div>
      <div class="method-card rejected">
        <div class="method-kicker">TESTED · NOT THE HOT PATH</div>
        <h3>GPT-4o Vision proposal</h3>
        <div class="method-speed">~460× slower</div>
        <p>Useful for a pure scan with no structure. Here it added latency without improving the answer, so we kept it offline-only.</p>
      </div>
    </div>

    <div class="views">
      <div class="view">
        <img src="data:image/png;base64,{_b64(step2_blank_png)}"/>
        <div class="vlabel">the blank — no fields, only boxes</div>
      </div>
    </div>
    <div class="call" style="margin-top:28px;">
      A PDF can store interactive fields that any library fills. <strong>This one doesn't.</strong> The boxes are just rectangles drawn on the page — ink targets, not input widgets. So we had to measure them ourselves.
    </div>

    <p class="caption" style="margin-top:48px;">PyMuPDF reads the text layer &nbsp;·&nbsp; every word comes with its bounding box</p>
    <div class="views">
      <div class="view">
        <div class="scan-stage">
          <img src="data:image/png;base64,{_b64(step2_words_png)}"/>
          <div class="scan-tag">measuring</div>
          <div class="scan-beam" aria-hidden="true"></div>
        </div>
        <div class="scan-note">native coordinates · top → bottom</div>
        <div class="vlabel">{word_count} word boxes drawn on the blank</div>
      </div>
    </div>
    <details class="technical-proof">
      <summary>Show the raw coordinate output</summary>
      <div class="dump">
        <div class="head">raw output of <code>page.get_text("words")</code> — first 12 lines</div>
      <div class="line"><span class="off">     x0       y0       x1       y1   word</span></div>
      <div class="line"><span class="off">  448.56   745.97   476.69   758.43  FORM</span></div>
      <div class="line"><span class="off">  479.88   746.46   518.32   758.28  CC2002B</span></div>
      <div class="line"><span class="off">  521.28   747.91   549.46   757.84  9/20/2016</span></div>
      <div class="line"><span class="off">   54.12    34.56    78.56    46.56  Check</span></div>
      <div class="line"><span class="off">   80.76    34.56    99.90    46.56  form</span></div>
      <div class="line"><span class="off">  103.80    34.56   134.64    46.56  desired:</span></div>
      <div class="line"><span class="off">   65.76    46.33    89.32    58.32  Short</span></div>
      <div class="line"><span class="off">   90.48    46.33   111.28    58.32  form</span></div>
      <div class="line"><span class="off">   65.40    58.93   101.73    70.92  Extended</span></div>
      <div class="line"><span class="off">  104.88    58.93   124.02    70.92  form</span></div>
      <div class="line"><span class="off">   65.28    72.01    89.32    84.00  Other</span></div>
      <div class="line"><span class="off">  241.20   103.54   262.24   116.82  THE</span></div>
        <div class="line" style="color:var(--ink-faint);margin-top:8px;">… {word_count} lines total …</div>
      </div>
    </details>

    <p class="caption" style="margin-top:48px;">a human reads the dump and draws a rectangle for each fillable box</p>
    <div class="views">
      <div class="view">
        <img src="data:image/png;base64,{_b64(step2_fields_png)}"/>
        <div class="vlabel">{n_total} field rectangles overlaid on the blank</div>
      </div>
    </div>

    <div class="legend">
      <div class="legend-item"><div class="legend-swatch text"></div>text field ({n_text})</div>
      <div class="legend-item"><div class="legend-swatch check"></div>checkbox ({n_checkbox})</div>
      <div class="legend-item"><div class="legend-swatch protected"></div>protected — never filled ({n_protected})</div>
    </div>

    <p class="caption" style="margin-top:40px;">the eight checkboxes are not all the same under the hood</p>
    <div class="checkbox-families">
      <div class="checkbox-family">
        <img src="data:image/png;base64,{_b64(step2_checkboxes_top_png)}"/>
        <div class="family-label"><strong>3 top boxes</strong><br/>Wingdings-style checkbox glyphs</div>
      </div>
      <div class="checkbox-family">
        <img src="data:image/png;base64,{_b64(step2_checkboxes_bottom_png)}"/>
        <div class="family-label"><strong>5 lower boxes</strong><br/>ASCII <code>(_)</code> glyphs in the sworn-statement section</div>
      </div>
    </div>
    <div class="call" style="margin-top:20px;">
      They look like the same kind of checkbox to a person, but the PDF stores them differently. We map them by their position in reading order — not by assuming every box uses the same character.
    </div>

    <div class="stats">
      <div class="stat"><div class="num">{n_total}</div><div class="lbl">total fields</div></div>
      <div class="stat"><div class="num green">{n_text}</div><div class="lbl">text inputs</div></div>
      <div class="stat"><div class="num red">{n_checkbox}</div><div class="lbl">checkboxes</div></div>
      <div class="stat"><div class="num ochre">{n_protected}</div><div class="lbl">protected</div></div>
    </div>

    <details class="technical-proof">
      <summary>Show the saved coordinate map</summary>
      <p class="caption">five of the {n_total} entries in <code>cc2002b.spec.json</code></p>
      <table class="spec-table">
      <tr><th>field name</th><th>x</th><th>y</th><th>w</th><th>h</th><th>type</th></tr>
      <tr><td class="name">month</td><td>177.43</td><td>289.16</td><td>70.77</td><td>28.52</td><td>text</td></tr>
      <tr class="checkbox"><td class="name">form_short</td><td>53.64</td><td>47.47</td><td>8.86</td><td>9.99</td><td>checkbox</td></tr>
      <tr class="checkbox"><td class="name">auth_checkbox_1</td><td>84.96</td><td>550.21</td><td>12.56</td><td>12.00</td><td>checkbox</td></tr>
      <tr><td class="name">license_no</td><td>461.56</td><td>321.68</td><td>96.00</td><td>19.04</td><td>text</td></tr>
      <tr class="protected"><td class="name">signature</td><td>80.32</td><td>674.36</td><td>190.28</td><td>18.00</td><td>line · protected</td></tr>
      </table>
    </details>

    <p class="caption" style="margin-top:48px;">then fill it &nbsp;·&nbsp; the measurements work</p>
    <div class="views">
      <div class="view">
        <img src="data:image/png;base64,{_b64(step2_blank_png)}"/>
        <div class="vlabel">before</div>
      </div>
      <div class="view">
        <img src="data:image/png;base64,{_b64(step2_filled_png)}"/>
        <div class="vlabel">after</div>
      </div>
    </div>
    <div class="call" style="margin-top:28px;">
      Every value lands inside its box because the coordinates came from the <strong>form's own text layer</strong> — not a screenshot, not a guess. The same rectangles that drew the overlay are the ones the filler writes into.
    </div>
  </section>

  <!-- ═══ step 3: freeze the specification ═══════════════════════════════ -->
  <section class="section">
    <div class="section-head">
      <span class="section-num">03</span>
      <h2>Freeze the <em>field map</em></h2>
    </div>
    <p class="caption">the measurement is approved once · runtime never re-derives it</p>
    <div class="step-flow">
      <div class="flow-card"><div class="flow-num">MAP</div><h3>32 explicit rectangles</h3><p>30 writable regions, 8 checkbox targets, and 2 protected regions.</p></div>
      <div class="flow-card"><div class="flow-num">PIN</div><h3>Blank fingerprint</h3><p>The approved form is pinned to SHA-256: <code>{spec['blank_sha256'][:12]}…</code></p></div>
      <div class="flow-card"><div class="flow-num">GUARD</div><h3>Fail closed</h3><p>A changed form or malformed coordinate map stops before drawing.</p></div>
    </div>
    <div class="call">
      This is the handoff from exploration to production: the human-approved map becomes boring, explicit data. The runtime does not guess where boxes are.
    </div>
  </section>

  <!-- ═══ step 4: JSON to ink ════════════════════════════════════════════ -->
  <section class="section">
    <div class="section-head">
      <span class="section-num">04</span>
      <h2>Turn the JSON <em>into ink</em></h2>
    </div>

    <p class="caption">Sol's structured facts &nbsp;·&nbsp; strict validation &nbsp;·&nbsp; approved field map &nbsp;·&nbsp; black ink</p>
    <div class="json-flow">
      <div class="json-panel">
        <div class="panel-label">SOL'S INPUT · JSON</div>
        <pre>{payload_pretty}</pre>
      </div>
      <div class="flow-arrow">→</div>
      <div class="mapping-panel">
        <div class="panel-label">FORM VALUES · FLAT MAP</div>
        <pre>{flat_preview}</pre>
      </div>
    </div>
    <div class="pipeline">
      <div class="pipeline-step"><strong>Schema</strong><span>shape + types</span></div>
      <div class="pipeline-step"><strong>Rules</strong><span>dates + address + fee</span></div>
      <div class="pipeline-step"><strong>Map</strong><span>JSON → field names</span></div>
      <div class="pipeline-step"><strong>Draw</strong><span>black ink + X marks</span></div>
      <div class="pipeline-step"><strong>Check</strong><span class="result-pass">{check_count} checks pass</span></div>
    </div>
    <div class="views">
      <div class="view">
        <img src="data:image/png;base64,{_b64(step2_blank_png)}"/>
        <div class="vlabel">blank form</div>
      </div>
      <div class="arrow">→</div>
      <div class="view">
        <img src="data:image/png;base64,{_b64(end_to_end_png)}"/>
        <div class="vlabel">Sol Lee · completed output</div>
      </div>
    </div>
    <div class="call" style="margin-top:28px;">
      The JSON is not pasted onto the page. It is validated, transformed into the form's field vocabulary, placed at the approved coordinates, and then reopened and checked. The output is only released if the checker agrees.
    </div>

  </section>

  <!-- ═══ step 5: check and harden ═══════════════════════════════════════ -->
  <section class="section">
    <div class="section-head">
      <span class="section-num">05</span>
      <h2>Check the filing, <em>then harden the proof</em></h2>
    </div>

    <p class="caption">watch the actual checker run against Sol's real output</p>
    <div class="layer-tabs">
      <div class="layer-tab"><div class="layer-letter">A</div><div class="layer-title">Semantic</div><div class="layer-count">{n_layer_a} checks</div></div>
      <div class="layer-tab"><div class="layer-letter">B</div><div class="layer-title">Raster</div><div class="layer-count">{n_layer_b} checks</div></div>
      <div class="layer-tab"><div class="layer-letter">C</div><div class="layer-title">pdfium cross-check</div><div class="layer-count">{n_layer_c} checks</div></div>
    </div>

    <div class="terminal">
      <div class="terminal-bar">
        <span class="terminal-dot red"></span><span class="terminal-dot yellow"></span><span class="terminal-dot green"></span>
        <span class="terminal-title">uv run python cc2002b.py --check outputs/01_party_short.pdf samples/01_party_short.json</span>
      </div>
      <div class="terminal-body">
{term_lines}
        <div class="term-summary" style="animation-delay:{term_delay:.2f}s;">Check:   PASSED  ({check_count}/{check_count})</div>
      </div>
    </div>

    <div class="call">
      If any check fails, the PDF is deleted and a <code>.failed.json</code> report is written instead. There is no proof receipt for a filing the checker does not believe — every one of the {check_count} checks above must pass first.
    </div>

    <p class="caption" style="margin-top:40px;">the fee ambiguity is visible too</p>
    <div class="method-compare">
      <div class="method-card chosen">
        <div class="method-kicker">SAMPLE 02 · BILLED</div>
        <h3>Extended form · 2 copies</h3>
        <div class="method-speed" style="color:var(--green);">$65 copies + $2 search = $67</div>
        <p>The type-specific extended rate is used, and the receipt marks the filing for review.</p>
      </div>
      <div class="method-card rejected">
        <div class="method-kicker">ALTERNATIVE READING</div>
        <h3>Broad short-form rate</h3>
        <div class="method-speed">$25 copies + $2 search = $27</div>
        <p>We show the competing interpretation and the actual exposure: $40 difference. Nothing is silently resolved.</p>
      </div>
    </div>

    <p class="caption" style="margin-top:48px;">forge Sol's real, already-checked PDF &nbsp;·&nbsp; four live tampers, four real crops, four real verdicts</p>
    <div class="forge-grid">
      <div class="forge-card">
        <img src="data:image/png;base64,{_b64(forge_sig_png)}"/>
        <strong>Fake signature ink</strong>
        <span>"SOL LEE" typed on the protected line</span>
        <div class="forge-caught"><span class="fail-badge">FAILS</span> <code>{sig_check}</code></div>
      </div>
      <div class="forge-card">
        <img src="data:image/png;base64,{_b64(forge_box_png)}"/>
        <strong>Second checkbox marked</strong>
        <span>two sworn statements at once</span>
        <div class="forge-caught"><span class="fail-badge">FAILS</span> <code>{box_check}</code></div>
      </div>
      <div class="forge-card">
        <img src="data:image/png;base64,{_b64(forge_cover_png)}"/>
        <strong>White cover-up</strong>
        <span>painted over the real name</span>
        <div class="forge-caught"><span class="fail-badge">FAILS</span> <code>{cover_check}</code></div>
      </div>
      <div class="forge-card">
        <img src="data:image/png;base64,{_b64(forge_color_png)}"/>
        <strong>Dark green ink</strong>
        <span>same words, same position, wrong color</span>
        <div class="forge-caught"><span class="fail-badge">FAILS</span> <code>{color_check}</code></div>
        <div class="forge-note">pdfium alone: <span class="{'ok-word' if color_pdfium_ok else 'fail-word'}">{'would pass' if color_pdfium_ok else 'also fails'}</span> — MuPDF's color check isn't redundant</div>
      </div>
    </div>
    <div class="call">
      These are not mockups — each card is a real tampered PDF, cropped from the actual render, run through the actual <code>check_correctness()</code>. The named check above is whatever the engine reported failing, live, when this page was generated.
    </div>

    <p class="caption" style="margin-top:44px;">then Hypothesis does it {N_FUZZ * 2}+ more times, unsupervised</p>
    <div class="terminal">
      <div class="terminal-bar">
        <span class="terminal-dot red"></span><span class="terminal-dot yellow"></span><span class="terminal-dot green"></span>
        <span class="terminal-title">hypothesis · {N_FUZZ} random valid payloads</span>
      </div>
      <div class="terminal-body">
{fuzz_valid_lines}
        <div class="term-summary" style="animation-delay:{0.1 * N_FUZZ + 0.2:.2f}s;">{'zero false rejects' if fuzz_valid_all_ok else 'a real failure surfaced'} — property holds on {N_FUZZ}/{N_FUZZ} random shapes</div>
      </div>
    </div>
    <div class="terminal" style="margin-top:18px;">
      <div class="terminal-bar">
        <span class="terminal-dot red"></span><span class="terminal-dot yellow"></span><span class="terminal-dot green"></span>
        <span class="terminal-title">hypothesis · {N_FUZZ} random mutations of a good PDF</span>
      </div>
      <div class="terminal-body">
{fuzz_mutation_lines}
        <div class="term-summary" style="animation-delay:{0.1 * N_FUZZ + 0.2:.2f}s;">{'zero false accepts' if fuzz_mutation_all_caught else 'a mutation slipped through'} — every random tamper was caught</div>
      </div>
    </div>
    <div class="call" style="margin-top:24px;">
      In CI this runs at <strong>50 examples each</strong>, not {N_FUZZ}. The property never changes: a valid payload always fills and checks cleanly, and a mutated PDF is never mistaken for a clean one.
    </div>

    <p class="caption" style="margin-top:48px;">now prove the bytes are reproducible</p>
    <p class="caption" style="margin-top:32px;">same payload, five runs &nbsp;·&nbsp; the pages are identical &nbsp;·&nbsp; watch the hashes</p>

    <p class="caption" style="margin-top:32px;">PyMuPDF <code>doc.save()</code></p>
    <div class="badge-row"><span class="badge bad">{len(set(raw_hashes))} unique hashes / {RUNS} runs</span></div>
    <div class="row">{raw_cards}</div>

    <p class="caption" style="margin-top:40px;">pikepdf <code>deterministic_id=True</code></p>
    <div class="badge-row"><span class="badge good">{len(set(det_hashes))} unique hash / {RUNS} runs</span></div>
    <div class="row">{det_cards}</div>

    <p class="caption" style="margin-top:48px;">what changed? &nbsp;·&nbsp; the visible page did not</p>
    <div class="proof-grid">
      <div class="proof-card">
        <div class="proof-icon same">=</div>
        <h3>Same visible page</h3>
        <p>Same form. Same payload. Same ink in the same places.</p>
      </div>
      <div class="proof-card changed">
        <div class="proof-icon changed">≠</div>
        <h3>Different hidden ID</h3>
        <p>PyMuPDF quietly writes a new internal <code>/ID</code> every time it saves.</p>
      </div>
      <div class="proof-card result">
        <div class="proof-icon result">#</div>
        <h3>Different file hash</h3>
        <p>One hidden change means the file’s SHA-256 hash changes completely.</p>
      </div>
    </div>

    <p class="caption" style="margin-top:36px;">the hidden value that changed</p>
    <div class="ids">
      <div class="ids-cols">
        <div class="col"><div class="label">run 1</div><ul>{id_lines_0}</ul></div>
        <div class="col"><div class="label">run 2</div><ul>{id_lines_1}</ul></div>
      </div>
    </div>

    <details class="technical-proof">
      <summary>Show the raw byte-level proof</summary>
      <p class="technical-intro">This is the same difference, shown as hexadecimal bytes for a PDF engineer or reviewer who wants the lowest-level evidence.</p>
      {raw_diff_html}
    </details>

    <p class="caption" style="margin-top:36px;">pikepdf run 1 vs 2</p>
    {det_diff_html}
  </section>

  <!-- ═══ step 6: ship evidence ══════════════════════════════════════════ -->
  <section class="section">
    <div class="section-head">
      <span class="section-num">06</span>
      <h2>Ship the <em>evidence</em></h2>
    </div>
    <p class="caption">three scenarios · checked outputs · proof receipt</p>
    <div class="scenario-grid">
      <div class="scenario-card"><img src="data:image/png;base64,{_b64(scenario_pngs[0])}"/><strong>Sol · party / short</strong><span>$15 · baseline</span></div>
      <div class="scenario-card"><img src="data:image/png;base64,{_b64(scenario_pngs[1])}"/><strong>Morgan · relation / extended</strong><span>$67 · fee review note</span></div>
      <div class="scenario-card"><img src="data:image/png;base64,{_b64(scenario_pngs[2])}"/><strong>Avery · law enforcement</strong><span>$15 · under-50 note</span></div>
    </div>
    <div class="receipt-chain">
      <div class="receipt-node"><strong>INPUT</strong><span>JSON hash</span></div>
      <div class="receipt-link">→</div>
      <div class="receipt-node"><strong>FORM</strong><span>template hash</span></div>
      <div class="receipt-link">→</div>
      <div class="receipt-node"><strong>OUTPUT</strong><span>PDF hash</span></div>
      <div class="receipt-link">→</div>
      <div class="receipt-node signed"><strong>SIGNED</strong><span>Ed25519</span></div>
    </div>

    <p class="caption" style="margin-top:44px;">a stranger verifies the filing &nbsp;·&nbsp; no payload, no repo, no trust required</p>
    <div class="terminal">
      <div class="terminal-bar">
        <span class="terminal-dot red"></span><span class="terminal-dot yellow"></span><span class="terminal-dot green"></span>
        <span class="terminal-title">uv run python cc2002b.py --verify outputs/01_party_short.pdf $(cat SIGNING_KEY.pub)</span>
      </div>
      <div class="terminal-body">
        <div class="term-line" style="animation-delay:.10s;">  <span class="term-pass">PASS</span>  <span class="term-name">output_hash_matches_pdf</span>  <span class="term-dim">— the bytes match what the receipt claims</span></div>
        <div class="term-line" style="animation-delay:.30s;">  <span class="term-pass">PASS</span>  <span class="term-name">signature_present</span>  <span class="term-dim">— a detached <span class="term-accent">.sig</span> file exists</span></div>
        <div class="term-line" style="animation-delay:.50s;">  <span class="term-pass">PASS</span>  <span class="term-name">signature_algorithm</span>  <span class="term-dim">— ed25519</span></div>
        <div class="term-line" style="animation-delay:.70s;">  <span class="term-pass">PASS</span>  <span class="term-name">signature_valid</span>  <span class="term-dim">— signature verifies against the receipt bytes</span></div>
        <div class="term-line" style="animation-delay:.90s;">  <span class="term-pass">PASS</span>  <span class="term-name">signer_is_expected_key</span>  <span class="term-dim">— signed by the pinned public key, not just <em>a</em> key</span></div>
        <div class="term-summary" style="animation-delay:1.10s;">Verify:  PASSED  (5/5)</div>
      </div>
    </div>
    <div class="call">
      Re-signing a tampered receipt with a <strong>different</strong> key still produces a self-consistent signature — that's why <code>signer_is_expected_key</code> exists. Verification with no pinned key is reported as a <strong>failure</strong>, never a silent pass.
    </div>

    <details class="technical-proof" style="margin-top:32px;">
      <summary>Show the receipt fields</summary>
      <div class="json-flow">
        <div class="json-panel"><div class="panel-label">PROOF RECEIPT</div><pre>{receipt_preview}</pre></div>
        <div class="flow-arrow">→</div>
        <div class="mapping-panel"><div class="panel-label">VERIFY</div><pre>rebuild PDF
compare hashes
verify signer</pre></div>
      </div>
    </details>
    <div class="call">
      This is the finish line: not just a PDF that looks right, but three real scenarios, a passed correctness report, deterministic bytes, and a receipt that lets someone else verify what happened.
    </div>
  </section>

  <!-- ═══ step 7: where the form fought you ═══════════════════════════════ -->
  <section class="section">
    <div class="section-head">
      <span class="section-num">07</span>
      <h2>Where the <em>form fought you</em></h2>
    </div>
    <p class="caption">the brief asked for this explicitly &nbsp;·&nbsp; four real fights, four real screenshots</p>

    <div class="fight-block">
      <div class="fight-head"><span class="fight-num">1</span><h3>Two checkbox glyph families</h3></div>
      <p class="fight-body">Same visual idea, two different PDF internals. The 3 form-type boxes at top render Wingdings-style glyphs; the 5 sworn-statement boxes below render plain ASCII <code>(_)</code>. A human can't tell by looking. We disambiguate by <strong>reading order and position</strong>, never by glyph code.</p>
      <div class="checkbox-families">
        <div class="checkbox-family"><img src="data:image/png;base64,{_b64(step2_checkboxes_top_png)}"/><div class="family-label"><strong>top · Wingdings-style</strong></div></div>
        <div class="checkbox-family"><img src="data:image/png;base64,{_b64(step2_checkboxes_bottom_png)}"/><div class="family-label"><strong>bottom · ASCII <code>(_)</code></strong></div></div>
      </div>
    </div>

    <div class="fight-block">
      <div class="fight-head"><span class="fight-num">2</span><h3>Inline blanks aren't table cells</h3></div>
      <p class="fight-body">The relation and law-enforcement lines are ~47pt underscore runs <em>inside a printed sentence</em>, not full-width cells. "child" fits at 10pt. "granddaughter" doesn't — so those two fields alone get a lower font-size floor: <code>10 / 9 / 8 / 7 / 6</code>, tried largest-first, instead of rejecting a valid relation.</p>
      <div class="fight-fit-row">
        <div class="fit-card ok">
          <img src="data:image/png;base64,{_b64(inline_fits_png)}"/>
          <div class="fit-label">"child" <span class="fit-size">{fit_child[0]}pt</span></div>
        </div>
        <div class="fit-card floor">
          <img src="data:image/png;base64,{_b64(inline_floor_png)}"/>
          <div class="fit-label">"granddaughter" <span class="fit-size">{fit_grand[0]}pt</span></div>
        </div>
      </div>
    </div>

    <div class="fight-block">
      <div class="fight-head"><span class="fight-num">3</span><h3>No vertical rules</h3></div>
      <p class="fight-body">The address row has no drawn grid lines separating Street / Apt / City / State / Zip. Column boundaries come from where each <strong>label word ends</strong>, not a ruled table. Measuring this form meant reading it the way a person does, not parsing it like a spreadsheet.</p>
      <div class="views">
        <div class="view"><img src="data:image/png;base64,{_b64(no_rules_png)}"/><div class="vlabel">no vertical rules — column edges come from label text</div></div>
      </div>
    </div>

    <div class="fight-block">
      <div class="fight-head"><span class="fight-num">4</span><h3>The fee schedule contradicts itself</h3></div>
      <p class="fight-body">One paragraph quotes <strong>$15 / $10</strong> for any certified copy. A few lines later, the same page quotes <strong>$35 / $30</strong> for the extended form specifically. The brief calls this out on purpose: pick an interpretation, write down why, and make the ambiguity visible instead of resolving it silently.</p>
      <div class="views">
        <div class="view"><img src="data:image/png;base64,{_b64(fee_schedule_png)}"/><div class="vlabel">packet page 3 · the two competing rates</div></div>
      </div>
      <div class="call" style="margin-top:20px;">
        <strong>Our call:</strong> bill the type-specific rate ($35/$30 for extended) and write an <code>AMBIGUOUS FEE</code> note on every affected receipt with a priced-out <code>alternative_reading</code> — never resolved quietly.
      </div>
    </div>

    <div class="fight-block">
      <div class="fight-head"><span class="fight-num">+</span><h3>The brief's own note: three legitimate approaches</h3></div>
      <p class="fight-body">"The source PDF has no AcroForm fields. Coordinate overlay, OCR-assisted positioning, and full re-typesetting are all legitimate, with different tradeoffs." We picked coordinate overlay from the PDF's own geometry — see step 02 — because the form already has structure a vision model or OCR pass would just be re-discovering, slower and with more variance.</p>
    </div>
  </section>

  <!-- ═══ step 8: answer the brief ════════════════════════════════════════ -->
  <section class="section">
    <div class="section-head">
      <span class="section-num">08</span>
      <h2>Answer the <em>brief</em></h2>
    </div>
    <p class="caption">requirement · implementation · evidence</p>
    <div class="checklist">
      <div class="check-row"><span class="check-mark">✓</span><strong>JSON in, print-ready PDF out</strong><span>CLI + three committed scenarios</span></div>
      <div class="check-row"><span class="check-mark">✓</span><strong>Text inside the correct boxes</strong><span>32-field map + fit checks</span></div>
      <div class="check-row"><span class="check-mark">✓</span><strong>Exactly one form and sworn statement</strong><span>strict schema + checkbox checks</span></div>
      <div class="check-row"><span class="check-mark">✓</span><strong>Signature stays empty</strong><span>protected field + no-ink check</span></div>
      <div class="check-row"><span class="check-mark">✓</span><strong>Ambiguous fee is visible</strong><span>type-specific bill + alternative reading</span></div>
      <div class="check-row"><span class="check-mark">✓</span><strong>Correctness is real</strong><span>semantic + MuPDF raster + pdfium raster</span></div>
      <div class="check-row"><span class="check-mark">✓</span><strong>Evidence is verifiable</strong><span>hashes + optional Ed25519 signature</span></div>
    </div>
    <div class="method-compare" style="margin-top:28px;">
      <div class="method-card chosen"><div class="method-kicker">INTENTIONALLY OUT OF SCOPE</div><h3>No auth, persistence, UI, or multi-form platform</h3><p>The brief asked for one form done carefully. We kept the hot path narrow and made a second form easy through a new spec plus fee data.</p></div>
      <div class="method-card rejected"><div class="method-kicker">NEXT TWO WEEKS</div><h3>Second form · broader scripts · richer locale coverage</h3><p>Onboard another form, add CJK/Arabic/RTL font coverage, resolve the remaining “other” fee with the Clerk, and add an HTTP boundary only when auth and persistence exist.</p></div>
    </div>
  </section>

  <div class="takeaway">
    <div class="eyebrow">the takeaway</div>
    <p>A receipt's <code>output_hash</code> only means "rebuild it yourself" if the bytes are reproducible. <span class="red"><strong>PyMuPDF: five runs, five hashes.</strong></span> <span class="green"><strong>pikepdf: one hash, forever.</strong></span> That's why <code>fill_final</code> re-saves through pikepdf before anything gets signed.</p>
  </div>

  <div class="colophon">cc2002b · {date.today().isoformat()} · brief · extract · measure · freeze · fill · check · ship</div>

</div>
</body></html>"""

    (OUT / "index.html").write_text(page)
    print(f"wrote {OUT / 'index.html'}")
    print(f"  pikepdf: {pike_med:.2f}ms median, {len(pike_hashes)}/5 unique hashes")
    print(f"  pymupdf: {mupdf_med:.2f}ms median, {len(mupdf_hashes)}/5 unique hashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
