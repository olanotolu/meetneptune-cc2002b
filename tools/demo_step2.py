"""Build a visual demo for step 2: measure every box.

Renders three progressive views of the blank form:
  1. the blank as-is
  2. the blank with every word's bounding box drawn (the raw coordinate dump)
  3. the blank with the 32 hand-measured field rectangles drawn (the final spec)

Then assembles an HTML page in the same editorial style as the step 1 demo.

Run:  uv run python tools/demo_step2.py
Open: outputs/_demo_step2/index.html
"""
from __future__ import annotations

import base64
import json
from datetime import date
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
BLANK = ROOT / "00_packet" / "cc2002b_blank.pdf"
SPEC = ROOT / "cc2002b.spec.json"
OUT = ROOT / "outputs" / "_demo_step2"
DPI = 200 / 72

# colors matching the editorial palette
C_TEXT = (0.196, 0.418, 0.310)       # green for text fields
C_CHECK = (0.761, 0.212, 0.086)      # red for checkboxes
C_LINE = (0.722, 0.526, 0.180)       # ochre for protected/line
C_WORD = (0.5, 0.5, 0.5)             # gray for word boxes


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def render_blank(png: Path) -> None:
    doc = pymupdf.open(str(BLANK))
    try:
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(DPI, DPI))
        pix.save(str(png))
    finally:
        doc.close()


def render_words(png: Path) -> None:
    """Draw a thin gray rectangle around every word the PDF text layer reports."""
    doc = pymupdf.open(str(BLANK))
    try:
        page = doc[0]
        for w in page.get_text("words"):
            x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
            page.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=C_WORD, width=0.3)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(DPI, DPI))
        pix.save(str(png))
    finally:
        doc.close()


def render_fields(png: Path, spec: dict) -> None:
    """Draw the 32 hand-measured field rectangles, colored by type."""
    doc = pymupdf.open(str(BLANK))
    try:
        page = doc[0]
        for name, f in spec["fields"].items():
            rect = pymupdf.Rect(f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"])
            if name in spec["protected"]:
                color = C_LINE
                page.draw_rect(rect, color=color, width=0.8)
                # dashed feel: label it
                page.insert_text((rect.x0, rect.y0 - 2), name, fontsize=4, color=color)
            elif f["type"] == "checkbox":
                color = C_CHECK
                page.draw_rect(rect, color=color, width=0.8)
                page.insert_text((rect.x0, rect.y0 - 2), name, fontsize=4, color=color)
            else:
                color = C_TEXT
                page.draw_rect(rect, color=color, width=0.6)
                page.insert_text((rect.x0, rect.y0 - 2), name, fontsize=4, color=color)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(DPI, DPI))
        pix.save(str(png))
    finally:
        doc.close()


def render_filled_example(png: Path, spec: dict) -> None:
    """Render the blank with one sample filled in, to show the boxes work."""
    payload = json.loads((ROOT / "samples" / "01_party_short.json").read_text())
    doc = pymupdf.open(str(BLANK))
    try:
        page = doc[0]
        # draw a few fields to show ink in boxes
        fields = spec["fields"]
        # month/day/year
        for name, val in [("month", "05"), ("day", "30"), ("year", "2025")]:
            f = fields[name]
            page.insert_text((f["x"] + 4, f["y"] + f["h"] - 6), val, fontsize=10, color=(0, 0, 0))
        # license no
        f = fields["license_no"]
        page.insert_text((f["x"] + 4, f["y"] + f["h"] - 6), "B-2025-4994", fontsize=8, color=(0, 0, 0))
        # spouse names
        for name, val in [("full_legal_name_before_marriage", "Sol Lee"), ("full_legal_name_before_marriage_09", "Ronald William Caspers II")]:
            f = fields[name]
            page.insert_text((f["x"] + 4, f["y"] + f["h"] - 6), val, fontsize=9, color=(0, 0, 0))
        # short form checkbox (X mark)
        f = fields["form_short"]
        cx, cy = f["x"] + f["w"] / 2, f["y"] + f["h"] / 2
        r = min(f["w"], f["h"]) / 2 * 0.7
        page.draw_line(pymupdf.Point(cx - r, cy - r), pymupdf.Point(cx + r, cy + r), color=(0, 0, 0), width=1)
        page.draw_line(pymupdf.Point(cx - r, cy + r), pymupdf.Point(cx + r, cy - r), color=(0, 0, 0), width=1)
        # auth checkbox 1 (party)
        f = fields["auth_checkbox_1"]
        cx, cy = f["x"] + f["w"] / 2, f["y"] + f["h"] / 2
        r = min(f["w"], f["h"]) / 2 * 0.7
        page.draw_line(pymupdf.Point(cx - r, cy - r), pymupdf.Point(cx + r, cy + r), color=(0, 0, 0), width=1)
        page.draw_line(pymupdf.Point(cx - r, cy + r), pymupdf.Point(cx + r, cy - r), color=(0, 0, 0), width=1)
        # requester name
        f = fields["name_of_person_requesting_search"]
        page.insert_text((f["x"] + 2, f["y"] + f["h"] - 2), "Sol Lee", fontsize=7, color=(0, 0, 0))
        # relationship
        f = fields["your_relationship_to_either_spouse"]
        page.insert_text((f["x"] + 2, f["y"] + f["h"] - 2), "Spouse", fontsize=7, color=(0, 0, 0))
        # telephone
        f = fields["your_telephone_no"]
        page.insert_text((f["x"] + 2, f["y"] + f["h"] - 2), "603-380-3923", fontsize=7, color=(0, 0, 0))
        # address
        for name, val in [("street", "176 8th Street"), ("apt_no", "2"), ("city", "Brooklyn"), ("state", "NY"), ("zip_code", "11215")]:
            f = fields[name]
            page.insert_text((f["x"] + 2, f["y"] + f["h"] - 4), val, fontsize=8, color=(0, 0, 0))
        # reason
        f = fields["reason_search_copy_are_needed"]
        page.insert_text((f["x"] + 2, f["y"] + f["h"] - 4), "Name change", fontsize=8, color=(0, 0, 0))
        # copies
        f = fields["number_of_copies_requested"]
        page.insert_text((f["x"] + 2, f["y"] + f["h"] - 4), "1", fontsize=10, color=(0, 0, 0))

        pix = page.get_pixmap(matrix=pymupdf.Matrix(DPI, DPI))
        pix.save(str(png))
    finally:
        doc.close()


def main() -> int:
    spec = json.loads(SPEC.read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*"):
        if f.is_file():
            f.unlink()

    # render the 4 views
    blank_png = OUT / "01_blank.png"
    words_png = OUT / "02_words.png"
    fields_png = OUT / "03_fields.png"
    filled_png = OUT / "04_filled.png"
    render_blank(blank_png)
    render_words(words_png)
    render_fields(fields_png, spec)
    render_filled_example(filled_png, spec)

    # count fields by type
    fields = spec["fields"]
    n_text = sum(1 for f in fields.values() if f["type"] == "text" and f.get("fill", True))
    n_checkbox = sum(1 for f in fields.values() if f["type"] == "checkbox")
    n_protected = len(spec["protected"])
    n_total = len(fields)

    # a few sample word-box lines for the "raw dump" callout
    doc = pymupdf.open(str(BLANK))
    word_count = len(doc[0].get_text("words"))
    doc.close()

    # sample field coordinates for the spec display
    sample_fields = [
        ("month", fields["month"]),
        ("license_no", fields["license_no"]),
        ("form_short", fields["form_short"]),
        ("auth_checkbox_1", fields["auth_checkbox_1"]),
        ("signature", fields["signature"]),
    ]

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cc2002b — step 02: measure every box</title>
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
    --green: #2d6a4f;
    --ochre: #b8862e;
    --serif: 'Instrument Serif', 'Times New Roman', serif;
    --sans: 'Schibsted Grotesk', 'Helvetica Neue', sans-serif;
    --mono: 'IBM Plex Mono', 'Menlo', monospace;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: var(--sans); background: var(--paper); color: var(--ink); line-height: 1.5; -webkit-font-smoothing: antialiased; min-height: 100vh; }}
  .wrap {{ max-width: 1180px; margin: 0 auto; padding: 64px 48px 96px; }}

  .masthead {{ border-bottom: 2px solid var(--ink); padding-bottom: 28px; margin-bottom: 56px; }}
  .masthead .eyebrow {{ font-family: var(--mono); font-size: 11px; letter-spacing: 0.18em; text-transform: uppercase; color: var(--ink-faint); margin-bottom: 14px; }}
  .masthead h1 {{ font-family: var(--serif); font-size: 64px; font-weight: 400; line-height: 1.0; letter-spacing: -0.01em; color: var(--ink); }}
  .masthead h1 em {{ font-style: italic; font-weight: 400; color: var(--red); }}
  .masthead .lead {{ font-family: var(--sans); font-size: 17px; font-weight: 400; color: var(--ink-soft); margin-top: 18px; max-width: 620px; line-height: 1.55; }}

  .section {{ margin-bottom: 72px; }}
  .section-head {{ display: flex; align-items: baseline; gap: 18px; border-bottom: 1px solid var(--rule); padding-bottom: 14px; margin-bottom: 36px; }}
  .section-num {{ font-family: var(--mono); font-size: 13px; font-weight: 600; color: var(--paper); background: var(--ink); padding: 3px 10px; letter-spacing: 0.05em; }}
  .section-head h2 {{ font-family: var(--serif); font-size: 38px; font-weight: 400; letter-spacing: -0.01em; color: var(--ink); line-height: 1.1; }}
  .section-head h2 em {{ font-style: italic; color: var(--ink-soft); font-weight: 400; }}
  .caption {{ font-family: var(--mono); font-size: 12px; color: var(--ink-faint); text-align: center; margin: 20px 0 28px; letter-spacing: 0.02em; }}
  code {{ font-family: var(--mono); font-size: 0.88em; background: var(--paper-deep); padding: 1px 5px; color: var(--ink); }}

  /* progressive views */
  .views {{ display: flex; gap: 24px; justify-content: center; flex-wrap: wrap; }}
  .view {{ text-align: center; }}
  .view img {{ width: 280px; border: 1px solid var(--rule); display: block; box-shadow: 0 1px 0 var(--rule-soft), 0 8px 24px -14px rgba(0,0,0,0.15); }}
  .view .vlabel {{ font-family: var(--mono); font-size: 11px; color: var(--ink-faint); margin-top: 10px; letter-spacing: 0.04em; }}
  .view .vnum {{ font-family: var(--serif); font-size: 28px; color: var(--ink); font-weight: 400; }}
  .arrow {{ font-family: var(--sans); font-size: 28px; text-align: center; color: var(--ink-faint); margin: 20px 0; font-weight: 300; }}

  /* stat row */
  .stats {{ display: flex; gap: 0; max-width: 600px; margin: 32px auto; border: 1px solid var(--rule); }}
  .stat {{ flex: 1; text-align: center; padding: 24px 12px; border-right: 1px solid var(--rule); }}
  .stat:last-child {{ border-right: none; }}
  .stat .num {{ font-family: var(--serif); font-size: 42px; font-weight: 400; color: var(--ink); line-height: 1; }}
  .stat .num.red {{ color: var(--red); }}
  .stat .num.green {{ color: var(--green); }}
  .stat .num.ochre {{ color: var(--ochre); }}
  .stat .lbl {{ font-family: var(--mono); font-size: 11px; color: var(--ink-faint); margin-top: 8px; letter-spacing: 0.04em; text-transform: uppercase; }}

  /* legend */
  .legend {{ display: flex; gap: 24px; justify-content: center; margin: 24px 0; }}
  .legend-item {{ display: flex; align-items: center; gap: 8px; font-family: var(--mono); font-size: 12px; color: var(--ink-soft); }}
  .legend-swatch {{ width: 16px; height: 16px; border: 2px solid; }}
  .legend-swatch.text {{ border-color: var(--green); }}
  .legend-swatch.check {{ border-color: var(--red); }}
  .legend-swatch.protected {{ border-color: var(--ochre); }}

  /* word dump callout */
  .dump {{
    background: #fff; border: 1px solid var(--rule); padding: 24px 28px;
    max-width: 560px; margin: 24px auto; font-family: var(--mono); font-size: 12px;
    line-height: 1.7; color: var(--ink-soft);
    box-shadow: 0 1px 0 var(--rule-soft);
  }}
  .dump .head {{ font-size: 11px; font-weight: 600; color: var(--ink-faint); letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid var(--rule-soft); }}
  .dump .line {{ white-space: pre; }}
  .dump .off {{ color: var(--ink-faint); }}

  /* spec table */
  .spec-table {{ width: 100%; max-width: 680px; margin: 24px auto; border-collapse: collapse; font-family: var(--mono); font-size: 12px; }}
  .spec-table th {{ text-align: left; padding: 10px 14px; border-bottom: 2px solid var(--ink); font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-faint); font-weight: 600; }}
  .spec-table td {{ padding: 10px 14px; border-bottom: 1px solid var(--rule-soft); color: var(--ink-soft); }}
  .spec-table td.name {{ color: var(--ink); font-weight: 600; }}
  .spec-table tr.protected td {{ color: var(--ochre); }}
  .spec-table tr.checkbox td.name {{ color: var(--red); }}

  /* callout */
  .call {{ background: var(--ink); color: var(--paper); padding: 28px 36px; max-width: 760px; margin: 28px auto; text-align: center; font-family: var(--sans); font-size: 16px; line-height: 1.6; font-weight: 400; }}
  .call strong {{ color: #d4a84e; font-weight: 600; }}
  .call code {{ background: rgba(255,255,255,0.12); color: var(--paper); }}

  /* takeaway */
  .takeaway {{ border-top: 2px solid var(--ink); border-bottom: 1px solid var(--rule); padding: 36px 0; margin-top: 72px; text-align: center; }}
  .takeaway .eyebrow {{ font-family: var(--mono); font-size: 11px; letter-spacing: 0.18em; text-transform: uppercase; color: var(--ink-faint); margin-bottom: 14px; }}
  .takeaway p {{ font-family: var(--serif); font-size: 28px; font-weight: 400; color: var(--ink); line-height: 1.35; max-width: 720px; margin: 0 auto; }}
  .takeaway strong {{ font-weight: 600; }}
  .takeaway .red {{ color: var(--red); }}
  .takeaway .green {{ color: var(--green); }}

  .colophon {{ font-family: var(--mono); font-size: 11px; color: var(--ink-faint); text-align: center; margin-top: 48px; letter-spacing: 0.06em; }}
</style></head><body>
<div class="wrap">

  <header class="masthead">
    <div class="eyebrow">cc2002b · step 02 · field measurement</div>
    <h1>Measure <em>every box.</em></h1>
    <p class="lead">The form has no fillable fields — no AcroForm, no interactive widgets. Every box is just ink on paper. To fill it programmatically, we had to know exactly where each box is, in PDF coordinates. This is how we measured them.</p>
  </header>

  <!-- ═══ the problem ═══════════════════════════════════════════════════ -->
  <section class="section">
    <div class="section-head">
      <span class="section-num">01</span>
      <h2>The form has <em>no fields</em></h2>
    </div>
    <p class="caption">CC2002B · revised 9/20/2016 · one page · 612 × 792 pt</p>
    <div class="views">
      <div class="view">
        <img src="data:image/png;base64,{_b64(blank_png)}"/>
        <div class="vlabel">the blank form</div>
      </div>
    </div>
    <div class="call" style="margin-top:32px;">
      A PDF can store interactive fields (AcroForm) that any library can fill. <strong>This one doesn't have any.</strong> The boxes are just rectangles drawn on the page — ink targets, not input widgets. So we had to measure them ourselves.
    </div>
  </section>

  <!-- ═══ the raw data ══════════════════════════════════════════════════ -->
  <section class="section">
    <div class="section-head">
      <span class="section-num">02</span>
      <h2>Read every word's <em>coordinates</em></h2>
    </div>
    <p class="caption">PyMuPDF reads the PDF text layer and returns every word with its bounding box</p>
    <div class="views">
      <div class="view">
        <img src="data:image/png;base64,{_b64(words_png)}"/>
        <div class="vlabel">{word_count} word boxes drawn on the blank</div>
      </div>
    </div>
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
    <p class="caption">every word the form prints, with its exact position in PDF points (1 pt = 1/72 in)</p>
  </section>

  <!-- ═══ the measurement ═══════════════════════════════════════════════ -->
  <section class="section">
    <div class="section-head">
      <span class="section-num">03</span>
      <h2>Pick the boxes <em>by hand</em></h2>
    </div>
    <p class="caption">a human reads the coordinate dump and draws a rectangle for each fillable box</p>
    <div class="views">
      <div class="view">
        <img src="data:image/png;base64,{_b64(fields_png)}"/>
        <div class="vlabel">{n_total} field rectangles overlaid on the blank</div>
      </div>
    </div>

    <div class="legend">
      <div class="legend-item"><div class="legend-swatch text"></div>text field ({n_text})</div>
      <div class="legend-item"><div class="legend-swatch check"></div>checkbox ({n_checkbox})</div>
      <div class="legend-item"><div class="legend-swatch protected"></div>protected — never filled ({n_protected})</div>
    </div>

    <div class="stats">
      <div class="stat"><div class="num">{n_total}</div><div class="lbl">total fields</div></div>
      <div class="stat"><div class="num green">{n_text}</div><div class="lbl">text inputs</div></div>
      <div class="stat"><div class="num red">{n_checkbox}</div><div class="lbl">checkboxes</div></div>
      <div class="stat"><div class="num ochre">{n_protected}</div><div class="lbl">protected</div></div>
    </div>

    <p class="caption">five of the {n_total} entries in <code>cc2002b.spec.json</code></p>
    <table class="spec-table">
      <tr><th>field name</th><th>x</th><th>y</th><th>w</th><th>h</th><th>type</th></tr>
      <tr><td class="name">month</td><td>177.43</td><td>289.16</td><td>70.77</td><td>28.52</td><td>text</td></tr>
      <tr class="checkbox"><td class="name">form_short</td><td>53.64</td><td>47.47</td><td>8.86</td><td>9.99</td><td>checkbox</td></tr>
      <tr class="checkbox"><td class="name">auth_checkbox_1</td><td>84.96</td><td>550.21</td><td>12.56</td><td>12.00</td><td>checkbox</td></tr>
      <tr><td class="name">license_no</td><td>461.56</td><td>321.68</td><td>96.00</td><td>19.04</td><td>text</td></tr>
      <tr class="protected"><td class="name">signature</td><td>80.32</td><td>674.36</td><td>190.28</td><td>18.00</td><td>line · protected</td></tr>
    </table>
  </section>

  <!-- ═══ the proof ═════════════════════════════════════════════════════ -->
  <section class="section">
    <div class="section-head">
      <span class="section-num">04</span>
      <h2>Then <em>fill it</em></h2>
    </div>
    <p class="caption">same rectangles, now with ink in them — the measurements work</p>
    <div class="views">
      <div class="view">
        <img src="data:image/png;base64,{_b64(blank_png)}"/>
        <div class="vlabel">before</div>
      </div>
      <div class="view">
        <img src="data:image/png;base64,{_b64(filled_png)}"/>
        <div class="vlabel">after</div>
      </div>
    </div>
    <div class="call" style="margin-top:32px;">
      Every value lands inside its box because the box coordinates came from the <strong>form's own text layer</strong> — not a screenshot, not a guess. The same coordinates that drew the overlay rectangles are the ones the filler writes into.
    </div>
  </section>

  <div class="takeaway">
    <div class="eyebrow">the takeaway</div>
    <p>No AcroForm means no shortcuts. We read <strong>{word_count} word boxes</strong> off the PDF's own text layer, drew them all to see them, then <strong class="green">picked {n_total} by hand</strong> — text fields, checkboxes, and two protected regions the filler must never touch. The result is <code>cc2002b.spec.json</code>: a coordinate map frozen against the blank's hash, so if the form ever changes, the spec refuses to load.</p>
  </div>

  <div class="colophon">cc2002b · step 02 · {date.today().isoformat()} · {n_total} fields measured from {word_count} word boxes</div>

</div>
</body></html>"""

    (OUT / "index.html").write_text(page)
    print(f"wrote {OUT / 'index.html'}")
    print(f"  {word_count} words, {n_total} fields ({n_text} text, {n_checkbox} checkbox, {n_protected} protected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
