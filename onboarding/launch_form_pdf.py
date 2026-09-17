"""The one page of the Launch Week Kit a partner actually fills in.

The welcome email tells them to "fill in the names and send it back with the
agreement". The Kit goes out as a LibreOffice-converted PDF, which is flat --
`tools/formfield_probe.py` confirmed on the hub that the conversion drops Word
form fields entirely, so the instruction could not be honoured by the attached
file. Doc 04.

Stamping fields onto the finished Kit at fixed coordinates was the obvious bad
idea: the Kit reflows per partner -- a three-line launch line-up moves
everything under it -- and the coordinates would be wrong silently.

So this builds a separate one-pager with `agreement_pdf.FormRow`, which is
already the answer to that problem. Its fields are reportlab FLOWABLES that
call `canv.absolutePosition()` at draw time, so a field lands wherever the
layout puts it. That is how the agreement's ten fields survive an address that
runs to three lines, and it works here for the same reason.

THE ROWS COME FROM THE KIT, not from a list kept here. `plan_rows()` reads the
rendered Launch Week Kit and pulls the WHEN / DO THIS columns out of its
launch-week plan table, emitting one OWNER field per row. A second hand-kept
copy of that plan would drift from the real one the first time the plan
changed -- the mistake doc 16 records about the traveler's steps and doc 18
about the org table. If the table cannot be found the page still builds, with
the three standing decisions and a line saying the plan could not be read,
because a form that silently loses half its rows is worse than one that says
so.
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
                                Spacer)

from .agreement_pdf import MARGIN, FormRow, _register_family

PAGE_W, PAGE_H = LETTER
CONTENT_W = PAGE_W - 2 * MARGIN

INK = HexColor("#1B2A4A")
MUTED = HexColor("#5B6472")
GOLD = HexColor("#C8A44D")
FILL = HexColor("#F4F1EC")

# Headings the launch-week plan table can carry. Matched case-insensitively on
# the first row, so a template that renames "WHEN" to "TIMING" still works.
WHEN_HEADS = {"when", "timing", "week"}
DO_HEADS = {"do this", "what", "action", "task"}
OWNER_HEADS = {"owner", "who", "who owns it"}


def _cells(row) -> list[str]:
    return [" ".join(c.text.split()) for c in row.cells]


def plan_rows(kit_docx: Path | str) -> tuple[list[tuple[str, str]], str]:
    """[(when, do this)] from the Kit's launch-week table, and a note if not."""
    try:
        document = Document(str(kit_docx))
    except Exception as exc:
        return [], f"the Launch Week Kit could not be read ({exc})"

    for table in document.tables:
        if not table.rows:
            continue
        head = [c.lower() for c in _cells(table.rows[0])]
        when_at = next((i for i, c in enumerate(head) if c in WHEN_HEADS), None)
        do_at = next((i for i, c in enumerate(head) if c in DO_HEADS), None)
        owner_at = next((i for i, c in enumerate(head) if c in OWNER_HEADS), None)
        if when_at is None or do_at is None or owner_at is None:
            continue
        rows = []
        for row in table.rows[1:]:
            cells = _cells(row)
            if max(when_at, do_at) >= len(cells):
                continue
            when, what = cells[when_at], cells[do_at]
            if when or what:
                rows.append((when, what))
        if rows:
            return rows, ""
    return [], "the launch-week plan table was not found in the Kit"


def _styles(body_font: str, heading_font: str) -> dict:
    return {
        "h1": ParagraphStyle("h1", fontName=heading_font, fontSize=19,
                             leading=23, textColor=INK, spaceAfter=2),
        "sub": ParagraphStyle("sub", fontName=body_font, fontSize=10.5,
                              leading=15, textColor=MUTED, spaceAfter=16),
        "h2": ParagraphStyle("h2", fontName=heading_font, fontSize=10,
                             leading=13, textColor=GOLD, spaceBefore=16,
                             spaceAfter=8, alignment=TA_LEFT),
        "note": ParagraphStyle("note", fontName=body_font, fontSize=9,
                               leading=13, textColor=MUTED, spaceBefore=10),
        "row": ParagraphStyle("row", fontName=body_font, fontSize=9.5,
                              leading=13, textColor=INK),
    }


def build(record: dict, ctx: dict, kit_docx: Path | str,
          out_path: Path | str) -> dict:
    """Write the fillable one-pager. Returns {ok, path, rows, note, error}."""
    out_path = Path(out_path)
    try:
        body_font = _register_family("Calibri")
        heading_font = _register_family("Georgia")
        rows, note = plan_rows(kit_docx)
        style = _styles(body_font, heading_font)

        org = (record.get("org_name") or "").strip()
        story = [
            Paragraph("Launch week — who does what", style["h1"]),
            Paragraph(
                f"{org}. Fill this in, save it, and send it back with the signed "
                "agreement. It is the only page of the kit we need returned — "
                "everything else is yours to read and print.", style["sub"]),
            Paragraph("The decisions", style["h2"]),
            FormRow("launch_date", "Launch date", CONTENT_W,
                    value=(ctx.get("launch_date_fmt") or "").strip(),
                    label_font=body_font, body_font=body_font,
                    ink=INK, muted=MUTED, fill=FILL,
                    tooltip="The day the store goes live"),
            FormRow("social_owner", "Who runs Instagram and Facebook that week",
                    CONTENT_W, value=(record.get("social_owner") or "").strip(),
                    label_font=body_font, body_font=body_font,
                    ink=INK, muted=MUTED, fill=FILL),
            FormRow("postcards_owner", "Who orders the postcards", CONTENT_W,
                    label_font=body_font, body_font=body_font,
                    ink=INK, muted=MUTED, fill=FILL),
        ]

        if rows:
            story += [Spacer(1, 4), Paragraph("The plan — who owns each piece",
                                              style["h2"])]
            for index, (when, what) in enumerate(rows):
                line = " · ".join(part for part in (when, what) if part)
                story.append(Paragraph(line, style["row"]))
                story.append(FormRow(f"owner_{index}", "Owner", CONTENT_W,
                                     height=19, label_font=body_font,
                                     body_font=body_font, ink=INK, muted=MUTED,
                                     fill=FILL, tooltip=line[:120]))
        else:
            story.append(Paragraph(
                f"The plan's rows are not listed here because {note}. "
                "Write the owners onto the plan in the kit instead.",
                style["note"]))

        story.append(Paragraph(
            "Nothing here is binding — it is how we know who to talk to during "
            "launch week. The agreement is the document that matters.",
            style["note"]))

        doc = BaseDocTemplate(
            str(out_path), pagesize=LETTER,
            leftMargin=MARGIN, rightMargin=MARGIN,
            topMargin=MARGIN, bottomMargin=MARGIN,
            title=f"{org} — launch week, who does what",
            author="Steeple & Stitch Co.")
        frame = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, id="body",
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        doc.addPageTemplates([PageTemplate(id="page", frames=[frame])])
        doc.build(story)
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "path": "", "rows": 0, "note": "", "error": str(exc)}

    return {"ok": True, "path": str(out_path), "rows": len(rows),
            "note": note, "error": ""}
