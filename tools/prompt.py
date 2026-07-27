"""Structured prompt template for the agentic form compiler.

Kept separate from tools/compile_form.py so the system instructions and
geometry context can be tuned without touching the orchestration code.
"""

from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """You are a form-analysis agent. Your job is to read a single
rendered government-form page and its extracted word bounding boxes, then emit a
JSON field map for every fillable region.

For each field, output:
  - "name": snake_case identifier describing what the field is
  - "type": one of "text", "checkbox", or "line"
  - "x", "y", "w", "h": coordinates in PDF points (origin top-left, y grows down)

Rules:
  - Identify every blank box, line, and checkbox on the page.
  - Do not guess values; only mark where a human or machine should write.
  - Mark signature fields and the signature date field as "fill": false.
  - For checkboxes, use a small bounding box tightly around the printed box.
  - For text fields, use the full extent of the blank area that should receive ink.
  - For "line" type, use a horizontal strip where a signature should be written.
  - Output ONLY a JSON object with a single top-level key "fields" mapping field
    names to their specs.
"""


def build_user_prompt(word_bboxes: list[dict[str, Any]], page_size: tuple[float, float]) -> str:
    """Assemble the geometry context sent alongside the rendered page image."""
    width, height = page_size
    lines = [
        f"Page size: {width} x {height} points",
        "",
        "Extracted word bounding boxes (x0 y0 x1 y1 text):",
    ]
    for w in word_bboxes:
        text = str(w.get("text", "")).replace("\n", " ")
        lines.append(
            f"{w['x0']:8.2f} {w['y0']:8.2f} {w['x1']:8.2f} {w['y1']:8.2f}  {text}"
        )
    lines.append("")
    lines.append("Output JSON only, with the structure:")
    lines.append('{"fields": {"field_name": {"name": "field_name", "type": "text|checkbox|line", "x": ..., "y": ..., "w": ..., "h": ..., "fill": true|false}, ...}}')
    return "\n".join(lines)
