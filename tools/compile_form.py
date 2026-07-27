"""Agentic form compiler: blank PDF -> candidate spec JSON + overlay.

Offline-only. Calls a vision model once to propose field coordinates from a
rendered blank government form, then lets the existing deterministic 3-layer
checker try to verify the proposal against a sample payload.

Usage
-----
    python tools/compile_form.py 00_packet/cc2002b_blank.pdf \
        --output /tmp/candidate_spec.json \
        --overlay /tmp/candidate_overlay.png \
        --payload samples/01_party_short.json
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import pymupdf
from openai import OpenAI

# Allow importing the sibling hot-path module from repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cc2002b as app
import prompt

OVERLAY_DPI = 150 / 72
COLORS = {"checkbox": (1, 0, 0), "text": (0, 0, 1), "line": (0.6, 0, 0.6)}


def _page_size(blank_path: Path) -> tuple[float, float]:
    doc = pymupdf.open(str(blank_path))
    try:
        page = doc[0]
        return (round(page.rect.width, 2), round(page.rect.height, 2))
    finally:
        doc.close()


def render_page_png(blank_path: Path, dpi: float = 150.0) -> bytes:
    """Render the first page of a blank PDF to a PNG byte string."""
    doc = pymupdf.open(str(blank_path))
    try:
        page = doc[0]
        scale = dpi / 72.0
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
        return pix.tobytes("png")
    finally:
        doc.close()


def extract_word_bboxes(blank_path: Path) -> list[dict[str, Any]]:
    """Return every word/glyph bounding box from page 1."""
    doc = pymupdf.open(str(blank_path))
    try:
        words: list[dict[str, Any]] = []
        for w in doc[0].get_text("words"):
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
            words.append({
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
                "text": str(text),
            })
        return words
    finally:
        doc.close()


def call_gpt4o(image_png: bytes, user_text: str, api_key: str) -> dict[str, Any]:
    """Send the rendered form + geometry to GPT-4o and return parsed JSON."""
    client = OpenAI(api_key=api_key)
    b64 = base64.b64encode(image_png).decode()
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt.SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64}",
                            "detail": "high",
                        },
                    },
                    {"type": "text", "text": user_text},
                ],
            },
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("GPT-4o returned empty content")
    return json.loads(content)


def normalize_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten/validate the model response into a field dict keyed by name."""
    fields_raw = raw.get("fields", raw)
    if isinstance(fields_raw, list):
        fields_raw = {f.get("name", f"field_{i}"): f for i, f in enumerate(fields_raw)}
    if not isinstance(fields_raw, dict):
        raise ValueError(f"expected 'fields' object, got {type(fields_raw).__name__}")

    normalized: dict[str, Any] = {}
    for name, spec in fields_raw.items():
        if not isinstance(spec, dict):
            continue
        name = str(name).strip()
        if not name:
            continue
        for key in ("x", "y", "w", "h"):
            if key not in spec:
                raise ValueError(f"field {name!r} is missing coordinate {key}")

        nf = dict(spec)
        nf["x"] = float(nf["x"])
        nf["y"] = float(nf["y"])
        nf["w"] = float(nf["w"])
        nf["h"] = float(nf["h"])
        nf.setdefault("type", "text")
        nf.setdefault("name", name)
        nf["x1"] = nf["x"] + nf["w"]
        nf["y1"] = nf["y"] + nf["h"]

        if nf.get("fill") is None:
            low = name.lower()
            if "signature" in low or (low.endswith("_date") and "signature" in low):
                nf["fill"] = False
            else:
                nf["fill"] = True

        if nf["type"] == "text" and "sizes" not in nf:
            nf["sizes"] = [10, 9, 8]

        normalized[name] = nf
    return normalized


def build_spec(blank_path: Path, fields: dict[str, Any]) -> dict[str, Any]:
    """Assemble a full candidate spec from the blank metadata + proposed fields."""
    doc = pymupdf.open(str(blank_path))
    try:
        page = doc[0]
        page_size = (round(page.rect.width, 2), round(page.rect.height, 2))
        page_count = doc.page_count
        is_form = bool(doc.is_form_pdf)
    finally:
        doc.close()

    sha = hashlib.sha256(blank_path.read_bytes()).hexdigest()
    protected = [n for n, f in fields.items() if not f.get("fill", True)]

    return {
        "form_id": "CANDIDATE",
        "revision": "candidate",
        "version": "0.0.0-candidate",
        "blank_sha256": sha,
        "page_count": page_count,
        "page_size": page_size,
        "acroform_expected": is_form,
        "anchors": [],
        "protected": protected,
        "form_checkboxes": {},
        "authorization_checkboxes": {},
        "fields": fields,
    }


def write_spec(spec: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(spec, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def draw_overlay(blank_path: Path, fields: dict[str, Any], out_png: Path) -> None:
    """Render the blank with proposed field boxes and names."""
    doc = pymupdf.open(str(blank_path))
    try:
        page = doc[0]
        for name, f in fields.items():
            rect = pymupdf.Rect(f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"])
            color = COLORS.get(f.get("type", "text"), (0, 0, 1))
            page.draw_rect(rect, color=color, width=1.2)
            page.insert_text((rect.x0, rect.y0 - 2), name, fontsize=5, color=color)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(OVERLAY_DPI, OVERLAY_DPI))
        pix.save(str(out_png))
    finally:
        doc.close()


def auto_verify(blank_path: Path, spec: dict[str, Any], payload_path: Path) -> dict[str, Any]:
    """Attempt a test fill + 3-layer check against the candidate spec.

    fill_final()/check_correctness() open cc2002b.py's module-level BLANK
    constant internally rather than an argument, so BLANK must be patched
    too -- otherwise compiling a *different* form would silently verify
    against the approved CC2002B blank instead of the one actually passed
    in. Both SPEC/FIELDS and BLANK are restored afterward.
    """
    original_spec, original_fields, original_blank = app.SPEC, app.FIELDS, app.BLANK
    app.SPEC = spec
    app.FIELDS = spec["fields"]
    app.BLANK = blank_path
    try:
        app_obj = app.load_application(payload_path)
        flat = app.form_values(app_obj)
        with tempfile.TemporaryDirectory() as td:
            filled = Path(td) / "filled.pdf"
            as_of = date.today()
            app.fill_final(flat, filled, as_of=as_of, spec=spec)
            return app.check_correctness(filled, payload_path, as_of=as_of)
    finally:
        app.SPEC, app.FIELDS, app.BLANK = original_spec, original_fields, original_blank


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agentic form compiler")
    parser.add_argument("blank_pdf", type=Path, help="blank PDF to analyze")
    parser.add_argument("--output", type=Path, help="candidate spec JSON path")
    parser.add_argument("--overlay", type=Path, help="overlay PNG output path")
    parser.add_argument("--payload", type=Path, help="sample payload JSON for auto-verify")
    parser.add_argument("--dpi", type=float, default=150.0)
    args = parser.parse_args(argv)

    if not args.blank_pdf.exists():
        print(f"blank PDF not found: {args.blank_pdf}", file=sys.stderr)
        return 1

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY environment variable is required", file=sys.stderr)
        return 1

    print("Rendering blank page to PNG...")
    png = render_page_png(args.blank_pdf, args.dpi)
    words = extract_word_bboxes(args.blank_pdf)
    print(f"Extracted {len(words)} word boxes")

    page_size = _page_size(args.blank_pdf)
    user_prompt = prompt.build_user_prompt(words, page_size)
    print("Calling GPT-4o...")
    raw = call_gpt4o(png, user_prompt, api_key)
    fields = normalize_fields(raw)
    spec = build_spec(args.blank_pdf, fields)

    if args.output:
        write_spec(spec, args.output)
        print(f"Wrote candidate spec: {args.output} ({len(fields)} fields)")
    else:
        print(f"Proposed {len(fields)} fields; no --output written")

    if args.overlay:
        draw_overlay(args.blank_pdf, fields, args.overlay)
        print(f"Wrote overlay: {args.overlay}")

    if args.payload:
        if not args.output:
            print("--payload requires --output so the spec can be verified", file=sys.stderr)
            return 1
        if not args.payload.exists():
            print(f"payload not found: {args.payload}", file=sys.stderr)
            return 1
        print(f"Auto-verifying with {args.payload}...")
        check = auto_verify(args.blank_pdf, spec, args.payload)
        checks = check.get("checks", [])
        passed = sum(1 for c in checks if c.get("passed"))
        status = "PASSED" if check.get("passed") else "FAILED"
        print(f"Auto-verify: {status} ({passed}/{len(checks)} checks)")
        for c in checks:
            if not c.get("passed"):
                print(f"  FAIL: {c['check']} — {c['detail']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
