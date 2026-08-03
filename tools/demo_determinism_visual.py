"""Build a visual HTML demo of the MuPDF vs pikepdf determinism gap.

Renders 5 raw-pymupdf and 5 pikepdf-resave outputs to PNG, hex-diffs two raw
runs to find the differing bytes, and assembles an HTML page so a visual
learner can see: identical pages, different hashes (mupdf) vs identical
pages, identical hashes (pikepdf), plus exactly where the bytes diverge.

Run:  uv run python tools/demo_determinism_visual.py
Open: outputs/_demo_determinism/index.html
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
from pathlib import Path

import pikepdf
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
BLANK = ROOT / "00_packet" / "cc2002b_blank.pdf"
PAYLOAD = ROOT / "samples" / "01_party_short.json"
OUT = ROOT / "outputs" / "_demo_determinism"
RUNS = 5
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


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


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


def hex_diff(a: bytes, b: bytes, context: int = 32) -> list[tuple[int, str, str]]:
    """Find differing byte ranges and return (offset, a_hex, b_hex) per region."""
    diffs = []
    n = min(len(a), len(b))
    i = 0
    while i < n:
        if a[i] != b[i]:
            start = max(0, i - context)
            end = min(n, i + context)
            # extend to cover the whole differing run
            j = i
            while j < end and a[j] != b[j]:
                j += 1
            end = min(n, j + context)
            a_chunk = a[start:end]
            b_chunk = b[start:end]
            diffs.append((start, a_chunk.hex(), b_chunk.hex()))
            i = end
        else:
            i += 1
    return diffs


def find_id_strings(data: bytes) -> list[str]:
    """Pull printable strings around '/ID' markers."""
    out = []
    idx = 0
    while True:
        idx = data.find(b"/ID", idx)
        if idx == -1:
            break
        # grab a window of printable bytes after /ID
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
    OUT.mkdir(parents=True, exist_ok=True)
    # clear old
    for f in OUT.glob("*"):
        f.unlink()

    raw_pdfs = []
    det_pdfs = []
    raw_hashes = []
    det_hashes = []

    for i in range(RUNS):
        r = OUT / f"raw_{i}.pdf"
        fill_raw(r)
        raw_pdfs.append(r)
        raw_hashes.append(_hash(r))

        d = OUT / f"det_{i}.pdf"
        fill_det(d)
        det_pdfs.append(d)
        det_hashes.append(_hash(d))

    # render PNGs
    raw_pngs = [OUT / f"raw_{i}.png" for i in range(RUNS)]
    det_pngs = [OUT / f"det_{i}.png" for i in range(RUNS)]
    for r, p in zip(raw_pdfs, raw_pngs):
        _render_png(r, p)
    for d, p in zip(det_pdfs, det_pngs):
        _render_png(d, p)

    # hex diff between raw_0 and raw_1
    raw_diffs = hex_diff(raw_pdfs[0].read_bytes(), raw_pdfs[1].read_bytes())
    raw_ids_0 = find_id_strings(raw_pdfs[0].read_bytes())
    raw_ids_1 = find_id_strings(raw_pdfs[1].read_bytes())

    # det diff (should be empty)
    det_diffs = hex_diff(det_pdfs[0].read_bytes(), det_pdfs[1].read_bytes())

    # build HTML
    def card(idx: int, png: Path, h: str, kind: str) -> str:
        img = _b64(png)
        short = h[:16] + "…" + h[-8:]
        return f"""
        <div class="card {kind}">
          <div class="run">run {idx + 1}</div>
          <img src="data:image/png;base64,{img}" />
          <div class="hash" title="{h}">{short}</div>
          <div class="full">{h}</div>
        </div>"""

    raw_cards = "".join(card(i, p, h, "raw") for i, (p, h) in enumerate(zip(raw_pngs, raw_hashes)))
    det_cards = "".join(card(i, p, h, "det") for i, (p, h) in enumerate(zip(det_pngs, det_hashes)))

    raw_unique = len(set(raw_hashes))
    det_unique = len(set(det_hashes))

    # hex diff rows
    def diff_rows(diffs, a_bytes, b_bytes):
        if not diffs:
            return '<div class="nodiff">No byte differences. Files are identical.</div>'
        rows = []
        for offset, a_hex, b_hex in diffs[:3]:
            # format into 16-byte rows with offsets
            def fmt(hexstr, base):
                lines = []
                for r in range(0, len(hexstr), 32):
                    chunk = hexstr[r : r + 32]
                    spaced = " ".join(chunk[i : i + 2] for i in range(0, len(chunk), 2))
                    lines.append(f'<div class="hexline"><span class="off">{base + r // 2:08x}</span> {spaced}</div>')
                return "".join(lines)
            rows.append(f"""
              <div class="diffblock">
                <div class="difftitle">differing region @ offset {offset:#x}</div>
                <div class="hexcol"><div class="label">run 1</div>{fmt(a_hex, offset)}</div>
                <div class="hexcol"><div class="label">run 2</div>{fmt(b_hex, offset)}</div>
              </div>""")
        return "".join(rows)

    raw_diff_html = diff_rows(raw_diffs, raw_pdfs[0].read_bytes(), raw_pdfs[1].read_bytes())
    det_diff_html = diff_rows(det_diffs, det_pdfs[0].read_bytes(), det_pdfs[1].read_bytes())

    id_lines_0 = "".join(f"<li><code>{html.escape(s)}</code></li>" for s in raw_ids_0)
    id_lines_1 = "".join(f"<li><code>{html.escape(s)}</code></li>" for s in raw_ids_1)

    page = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>MuPDF vs pikepdf — determinism demo</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0; background: #0f1115; color: #e6e6e6; padding: 24px; }}
  h1 {{ font-size: 20px; margin: 0 0 6px; }}
  h2 {{ font-size: 16px; margin: 28px 0 10px; color: #9ec5fe; }}
  .sub {{ color: #888; font-size: 13px; margin-bottom: 18px; max-width: 760px; line-height: 1.5; }}
  .row {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  .card {{ background: #1a1d24; border: 1px solid #2a2f3a; border-radius: 8px; padding: 10px; width: 180px; }}
  .card.det {{ border-color: #2e6b3a; }}
  .card.raw {{ border-color: #6b2e2e; }}
  .run {{ font-size: 11px; color: #888; margin-bottom: 6px; }}
  img {{ width: 100%; border: 1px solid #333; border-radius: 4px; display: block; }}
  .hash {{ font-family: ui-monospace, SFMono-Regular, monospace; font-size: 11px; margin-top: 6px; color: #9ec5fe; }}
  .full {{ font-family: ui-monospace, monospace; font-size: 9px; color: #555; word-break: break-all; margin-top: 2px; }}
  .summary {{ background: #1a1d24; border-radius: 8px; padding: 14px 18px; margin: 12px 0 20px; max-width: 760px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }}
  .badge.bad {{ background: #6b2e2e; color: #ffb4b4; }}
  .badge.good {{ background: #2e6b3a; color: #b4ffb4; }}
  .diffblock {{ background: #14171d; border: 1px solid #2a2f3a; border-radius: 6px; padding: 12px; margin-bottom: 14px; max-width: 760px; }}
  .difftitle {{ font-size: 12px; color: #888; margin-bottom: 8px; }}
  .hexcol {{ display: inline-block; width: 48%; vertical-align: top; font-family: ui-monospace, monospace; font-size: 11px; }}
  .hexline {{ white-space: pre; line-height: 1.5; }}
  .off {{ color: #888; margin-right: 8px; }}
  .label {{ color: #9ec5fe; font-size: 11px; margin-bottom: 4px; }}
  .nodiff {{ color: #b4ffb4; font-family: ui-monospace, monospace; font-size: 13px; padding: 8px; }}
  .ids {{ background: #14171d; border: 1px solid #2a2f3a; border-radius: 6px; padding: 12px; max-width: 760px; }}
  .ids ul {{ margin: 6px 0; padding-left: 20px; }}
  .ids code {{ font-family: ui-monospace, monospace; color: #ffb4b4; font-size: 11px; word-break: break-all; }}
  .ids .col {{ display: inline-block; width: 48%; vertical-align: top; }}
  .takeaway {{ background: #1a241d; border: 1px solid #2e6b3a; border-radius: 8px; padding: 16px 20px; margin-top: 24px; max-width: 760px; line-height: 1.6; }}
</style></head><body>
<h1>Same input, five runs: MuPDF vs pikepdf</h1>
<div class="sub">
  Same blank, same payload, same ink — "DEMO" at (72,72). The only difference
  between the two columns is how the PDF is saved. Look at the pages: they're
  identical. Look at the hashes. That's the gap the proof receipt closes.
</div>

<h2>① Raw PyMuPDF — <code>doc.save()</code></h2>
<div class="summary">
  <span class="badge bad">{raw_unique} unique hashes / {RUNS} runs</span>
  &nbsp; MuPDF stamps a fresh random <code>/ID</code> into the PDF trailer on every save.
</div>
<div class="row">{raw_cards}</div>

<h2>② pikepdf re-save — <code>deterministic_id=True</code>, dates stripped</h2>
<div class="summary">
  <span class="badge good">{det_unique} unique hash / {RUNS} runs</span>
  &nbsp; <code>/ID</code> is derived from document content; <code>/CreationDate</code>/<code>/ModDate</code> removed.
</div>
<div class="row">{det_cards}</div>

<h2>③ Where the bytes actually differ (raw run 1 vs run 2)</h2>
<div class="sub">A hex dump around the first differing bytes. Same page, different <code>/ID</code>.</div>
{raw_diff_html}

<h2>④ The <code>/ID</code> strings MuPDF wrote</h2>
<div class="ids">
  <div class="col"><strong>run 1</strong><ul>{id_lines_0}</ul></div>
  <div class="col"><strong>run 2</strong><ul>{id_lines_1}</ul></div>
</div>

<h2>⑤ pikepdf run 1 vs run 2</h2>
{det_diff_html}

<div class="takeaway">
  <strong>The takeaway.</strong> A receipt's <code>output_hash</code> only means
  "rebuild it yourself and check" if the bytes are reproducible. MuPDF makes
  every save a snowflake — 5 runs, 5 hashes, same ink. pikepdf's
  <code>deterministic_id=True</code> re-save collapses that to 1 hash forever.
  That's why <code>fill_final</code> re-saves through pikepdf before anything
  gets signed.
</div>
</body></html>"""

    (OUT / "index.html").write_text(page)
    print(f"wrote {OUT / 'index.html'}")
    print(f"  raw unique hashes: {raw_unique}/{RUNS}")
    print(f"  det unique hashes: {det_unique}/{RUNS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
