"""Demonstrate the determinism gap between raw MuPDF and pikepdf re-save.

Fills the same payload N times each way and prints the SHA-256 of every
output so the difference is visible: MuPDF stamps a fresh random /ID per
save, pikepdf derives /ID from content.

Run:  uv run python tools/demo_determinism.py
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pikepdf
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
BLANK = ROOT / "00_packet" / "cc2002b_blank.pdf"
PAYLOAD = ROOT / "samples" / "01_party_short.json"
RUNS = 5


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fill_raw_pymupdf(out: Path) -> None:
    """MuPDF only: save straight from pymupdf. Random /ID every run."""
    import json

    payload = json.loads(PAYLOAD.read_text())
    doc = pymupdf.open(str(BLANK))
    try:
        page = doc[0]
        # one ink mark so the page isn't byte-identical to the blank
        page.insert_text((72, 72), "DEMO", fontsize=10, color=(0, 0, 0))
        doc.save(str(out))
    finally:
        doc.close()


def fill_pikepdf_resave(out: Path) -> None:
    """pymupdf draws, then pikepdf re-saves with deterministic_id=True."""
    import json

    payload = json.loads(PAYLOAD.read_text())
    staged = out.with_suffix(".stage")
    doc = pymupdf.open(str(BLANK))
    try:
        page = doc[0]
        page.insert_text((72, 72), "DEMO", fontsize=10, color=(0, 0, 0))
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


def main() -> int:
    tmp = ROOT / "outputs" / "_demo"
    tmp.mkdir(parents=True, exist_ok=True)

    print(f"Same payload, {RUNS} runs each. Ink is identical across runs.\n")

    print("RAW PYMUPDF (mupdf stamps a fresh random /ID every save):")
    raw_hashes = []
    for i in range(RUNS):
        out = tmp / f"raw_{i}.pdf"
        fill_raw_pymupdf(out)
        h = _hash(out)
        raw_hashes.append(h)
        print(f"  run {i + 1}: {h}")
    print(f"  unique hashes: {len(set(raw_hashes))} / {RUNS}")

    print()
    print("PIKEPDF RE-SAVE (deterministic_id=True, dates stripped):")
    det_hashes = []
    for i in range(RUNS):
        out = tmp / f"det_{i}.pdf"
        fill_pikepdf_resave(out)
        h = _hash(out)
        det_hashes.append(h)
        print(f"  run {i + 1}: {h}")
    print(f"  unique hashes: {len(set(det_hashes))} / {RUNS}")

    print()
    if len(set(raw_hashes)) > 1 and len(set(det_hashes)) == 1:
        print("RESULT: MuPDF gave {} different hashes for the same input;".format(len(set(raw_hashes))))
        print("       pikepdf gave 1 hash every time. That's the gap the receipt relies on.")
    else:
        print("RESULT: unexpected — see hashes above.")

    # cleanup
    for f in tmp.glob("*.pdf"):
        f.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
