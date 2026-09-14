"""The quarterly donation statement as a PDF, for emailing with the check.

The 30% is **a charitable donation from Steeple & Stitch back to the partner**,
not the partner's cut of a joint venture. Larry's wording, and the document has
to carry it: "your share" frames the partner as a party to the trade, which is
neither what the agreement says nor how he wants the relationship read. Every
partner-facing string here says *donation*, and the arithmetic is shown only to
make the figure checkable.

Hand-built in ReportLab rather than converted from a .docx, and the reason is
narrower than it looks. The Launch Week Kit is converted because it has a
source document a person designed and will restyle; re-drawing it here would
drift from that source the first time it changed. This document has no source.
Every row on it is computed -- the item table is as long as the quarter was
busy -- so there is nothing to drift from, and a converter would add
LibreOffice to the path of the one document that has to come out identically
on any machine at quarter end.

It does deliberately borrow the agreement's typography (Georgia navy headings,
Calibri body, the gold rule) through `agreement_pdf._register_family`, so the
two documents a partner receives look like they came from the same company.
Font resolution is reported, not assumed -- see `font_report()` there.

Nothing in here decides anything. Every figure arrives from
`statement.build()`; this file only lays it out. A number that is wrong on the
page is wrong in the statement, which is the only place to fix it.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame,
                                KeepTogether, NextPageTemplate, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

from .agreement_pdf import _register_family, font_report  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

PAGE_W, PAGE_H = LETTER
MARGIN = 0.8 * inch
CONTENT_W = PAGE_W - 2 * MARGIN
BAND_H = 0.78 * inch

# The Service Agreement's own palette, read out of its source document and
# recorded in the ops notes. Shared on purpose: these two arrive in the same
# relationship, months apart, and should not look like two different firms.
NAVY = colors.HexColor("#1B2A4A")
GOLD = colors.HexColor("#B8912F")
INK = colors.HexColor("#333333")
MUTED = colors.HexColor("#5B6472")
RULE = colors.HexColor("#DCD6C8")
PANEL = colors.HexColor("#FBF3DF")
PAPER = colors.HexColor("#F5F2EC")
WARN = colors.HexColor("#8C6A1F")

BASIS_MARK = {"estimated": "*", "uncosted": "†", "partial": "†"}


def money(value) -> str:
    """'—' for a figure that is genuinely absent.

    $0.00 and "we could not work this out" are different statements, and this
    document is the one place a partner reads them. Never collapse them.
    """
    if value is None:
        return "—"
    return f"${value:,.2f}"


def _styles() -> dict:
    serif = _register_family("Georgia")
    sans = _register_family("Calibri")
    base = dict(fontName=sans, fontSize=9.5, leading=13, textColor=INK)
    return {
        "serif": serif,
        "sans": sans,
        "title": ParagraphStyle("title", fontName=serif, fontSize=17,
                                leading=21, textColor=NAVY, spaceAfter=3),
        "subtitle": ParagraphStyle("subtitle", fontName=sans, fontSize=10,
                                   leading=14, textColor=MUTED, spaceAfter=16),
        "h2": ParagraphStyle("h2", fontName=serif, fontSize=11, leading=15,
                             textColor=NAVY, spaceBefore=16, spaceAfter=7),
        "body": ParagraphStyle("body", **base),
        "small": ParagraphStyle("small", fontName=sans, fontSize=8.3,
                                leading=11.5, textColor=MUTED),
        "label": ParagraphStyle("label", fontName=sans, fontSize=7.6,
                                leading=10, textColor=MUTED),
        "cell": ParagraphStyle("cell", fontName=sans, fontSize=9,
                               leading=11.5, textColor=INK),
        "num": ParagraphStyle("num", fontName=sans, fontSize=9, leading=11.5,
                              textColor=INK, alignment=TA_RIGHT),
        "big": ParagraphStyle("big", fontName=serif, fontSize=26, leading=30,
                              textColor=NAVY, alignment=TA_CENTER),
        "bigLabel": ParagraphStyle("bigLabel", fontName=sans, fontSize=8,
                                   leading=11, textColor=WARN,
                                   alignment=TA_CENTER),
    }


# ------------------------------------------------------------ page furniture

def _band(canvas, doc, statement: dict, styles: dict, first: bool) -> None:
    """The navy letterhead band, drawn rather than placed.

    No image dependency on purpose. `assets/` is gitignored and resolved by
    absolute path elsewhere in this app, and a logo that silently fails to
    resolve is a documented failure mode here -- the QR and postcard builders
    have both shipped a partner's package with the mark missing and reported
    success. A statement that goes out unbranded at quarter end would be the
    same mistake on a document about money.
    """
    height = BAND_H if first else 0.42 * inch
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, PAGE_H - height, PAGE_W, height, stroke=0, fill=1)
    canvas.setFillColor(GOLD)
    canvas.rect(0, PAGE_H - height - 2.4, PAGE_W, 2.4, stroke=0, fill=1)

    canvas.setFillColor(PAPER)
    if first:
        canvas.setFont(styles["serif"], 15)
        canvas.drawString(MARGIN, PAGE_H - 0.44 * inch, "STEEPLE")
        width = canvas.stringWidth("STEEPLE ", styles["serif"], 15)
        canvas.setFillColor(GOLD)
        canvas.drawString(MARGIN + width, PAGE_H - 0.44 * inch, "&")
        canvas.setFillColor(PAPER)
        canvas.drawString(MARGIN + width + canvas.stringWidth("& ", styles["serif"], 15),
                          PAGE_H - 0.44 * inch, "STITCH CO.")
        canvas.setFont(styles["sans"], 8)
        canvas.setFillColor(colors.HexColor("#9AA3B2"))
        canvas.drawString(MARGIN, PAGE_H - 0.62 * inch,
                          "Branded merchandise for churches, schools and non-profits")
        canvas.setFont(styles["sans"], 8.5)
        canvas.setFillColor(GOLD)
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 0.44 * inch,
                               "QUARTERLY DONATION STATEMENT")
        canvas.setFillColor(colors.HexColor("#9AA3B2"))
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 0.62 * inch,
                               statement["number"])
    else:
        canvas.setFont(styles["sans"], 8.5)
        canvas.drawString(MARGIN, PAGE_H - 0.26 * inch,
                          f"{statement['org_name']} — {statement['quarter']}")
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 0.26 * inch,
                               statement["number"])

    canvas.setFont(styles["sans"], 7.6)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, 0.52 * inch,
                      f"{statement['number']} · issued "
                      f"{statement['issued'].strftime('%-d %B %Y')}")
    canvas.drawRightString(PAGE_W - MARGIN, 0.52 * inch,
                           f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


# ----------------------------------------------------------------- sections

def _facts(statement: dict, styles: dict) -> Table:
    def block(label, value):
        return [Paragraph(label.upper(), styles["label"]),
                Paragraph(value or "—", styles["body"])]

    left = [
        Paragraph("PREPARED FOR", styles["label"]),
        Paragraph(f"<b>{statement['org_name']}</b>", styles["body"]),
    ]
    if statement["poc_name"]:
        left.append(Paragraph(statement["poc_name"], styles["body"]))
    if statement["poc_email"]:
        left.append(Paragraph(statement["poc_email"], styles["small"]))

    right = [
        Paragraph("PERIOD", styles["label"]),
        Paragraph(f"<b>{statement['quarter']}</b>", styles["body"]),
        Paragraph(statement["period"], styles["small"]),
        Spacer(1, 7),
    ]
    right += block("Plan and rate", (
        f"{statement['plan']} · {_rate(statement)} of margin"
        if statement["plan"] else f"{_rate(statement)} of margin"))

    table = Table([[left, right]], colWidths=[CONTENT_W * 0.55, CONTENT_W * 0.45])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (-1, 0), (-1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _rate(statement: dict) -> str:
    pct = statement["margin_pct"]
    if pct is None:
        return "—"
    return f"{pct:g}%"


def _headline(statement: dict, styles: dict) -> Table:
    """The number the envelope is about, and the sentence that explains it."""
    payout = money(statement["payout"])
    sentence = (
        f"Steeple &amp; Stitch Co. donates {_rate(statement)} of the margin "
        f"earned on your merchandise. This quarter that is "
        f"{_rate(statement)} of {money(statement['margin'])} in margin, on "
        f"{statement['units']} item{'s' if statement['units'] != 1 else ''} "
        f"across {statement['orders']} order"
        f"{'s' if statement['orders'] != 1 else ''}."
    )
    inner = [
        Paragraph("THIS QUARTER&rsquo;S DONATION", styles["bigLabel"]),
        Spacer(1, 4),
        Paragraph(f"<b>{payout}</b>", styles["big"]),
        Spacer(1, 3),
        Paragraph(sentence, ParagraphStyle(
            "c", parent=styles["small"], alignment=TA_CENTER)),
    ]
    table = Table([[inner]], colWidths=[CONTENT_W])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("BOX", (0, 0), (-1, -1), 0.9, GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
    ]))
    return table


def _workings(statement: dict, styles: dict) -> Table:
    rows = [
        ("Gross sales", money(statement["revenue"]),
         "after every discount, excluding refunded items"),
        ("Cost of goods", f"({money(statement['cost'])})",
         "blanks, garments, decoration and shipping paid to produce them"),
        ("Margin", money(statement["margin"]), "gross sales less cost of goods"),
        (f"Donation at {_rate(statement)}", money(statement["payout"]),
         "the amount given back to you this quarter"),
    ]
    data = [[Paragraph(f"<b>{label}</b>" if index == len(rows) - 1 else label,
                       styles["cell"]),
             Paragraph(note, styles["small"]),
             Paragraph(f"<b>{value}</b>" if index == len(rows) - 1 else value,
                       styles["num"])]
            for index, (label, value, note) in enumerate(rows)]

    table = Table(data, colWidths=[CONTENT_W * 0.26, CONTENT_W * 0.54,
                                   CONTENT_W * 0.20])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
        ("LINEABOVE", (0, -1), (-1, -1), 0.9, NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
    ]))
    return table


def _items(statement: dict, styles: dict) -> Table:
    header = ["Item", "Qty", "Orders", "Sales", "Cost of goods", "Margin"]
    data = [[Paragraph(f"<b>{text}</b>", styles["label"] if index == 0
                       else ParagraphStyle("h", parent=styles["label"],
                                           alignment=TA_RIGHT))
             for index, text in enumerate(header)]]

    for row in statement["lines"]:
        mark = BASIS_MARK.get(row["basis"], "")
        title = row["title"] + (f' <font color="#8C6A1F">{mark}</font>' if mark else "")
        data.append([
            Paragraph(title, styles["cell"]),
            Paragraph(str(row["units"]), styles["num"]),
            Paragraph(str(row["orders"]), styles["num"]),
            Paragraph(money(row["revenue"]), styles["num"]),
            Paragraph(money(row["cost"]), styles["num"]),
            Paragraph(money(row["margin"]), styles["num"]),
        ])

    data.append([
        Paragraph("<b>Total</b>", styles["cell"]),
        Paragraph(f"<b>{statement['units']}</b>", styles["num"]),
        Paragraph(f"<b>{statement['orders']}</b>", styles["num"]),
        Paragraph(f"<b>{money(statement['revenue'])}</b>", styles["num"]),
        Paragraph(f"<b>{money(statement['cost'])}</b>", styles["num"]),
        Paragraph(f"<b>{money(statement['margin'])}</b>", styles["num"]),
    ])

    widths = [CONTENT_W * w for w in (0.36, 0.08, 0.10, 0.15, 0.16, 0.15)]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY),
        ("LINEBELOW", (0, 1), (-1, -2), 0.35, RULE),
        ("LINEABOVE", (0, -1), (-1, -1), 0.9, NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 5.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
    ]))
    return table


def _orders(statement: dict, styles: dict) -> Table:
    header = ["Order", "Date", "Items", "Sales"]
    data = [[Paragraph(f"<b>{text}</b>", styles["label"] if index == 0
                       else ParagraphStyle("h", parent=styles["label"],
                                           alignment=TA_RIGHT))
             for index, text in enumerate(header)]]
    for row in statement["order_rows"]:
        data.append([
            Paragraph(row["name"], styles["cell"]),
            Paragraph(row["date"].strftime("%-d %b %Y"), styles["num"]),
            Paragraph(str(row["units"]), styles["num"]),
            Paragraph(money(round(row["revenue"], 2)), styles["num"]),
        ])
    widths = [CONTENT_W * w for w in (0.34, 0.26, 0.18, 0.22)]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY),
        ("LINEBELOW", (0, 1), (-1, -1), 0.35, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
    ]))
    return table


def _footnotes(statement: dict, styles: dict) -> list:
    """The two ways a line can fail to be a fact, said plainly.

    A partner who is told only the total has no way to ask a useful question
    about it. A partner who is told which items could not be costed, and why,
    can look at their own order history and see the same thing.
    """
    notes = []
    if statement["estimated_revenue"]:
        notes.append(Paragraph(
            f'<font color="#8C6A1F">*</font> {money(statement["estimated_revenue"])} '
            f'of these sales were made on product options that have since been '
            f'removed from the store, so their exact cost can no longer be read '
            f'back. They are <b>not</b> included in the donation above. Priced '
            f'from the middle cost of that product&rsquo;s remaining options, they '
            f'would add {money(round((statement["payout_with_estimates"] or 0) - (statement["payout"] or 0), 2))} '
            f'to this quarter. Say the word and we will include them.',
            styles["small"]))
    if statement["uncosted_revenue"]:
        notes.append(Paragraph(
            f'<font color="#8C6A1F">†</font> {money(statement["uncosted_revenue"])} '
            f'of these sales have no production cost recorded against them, so no '
            f'margin can be stated and they add nothing to this donation. They '
            f'are listed because they are real sales and they appear in your store '
            f'history; leaving them out would make this page disagree with it.',
            styles["small"]))
    return notes


def _closing(statement: dict, company: dict, styles: dict) -> list:
    name = company.get("point_of_contact_name") or "Steeple & Stitch Co."
    email = company.get("point_of_contact_email", "")
    phone = company.get("point_of_contact_phone", "")
    contact = " · ".join(part for part in (email, phone) if part)
    return [
        Paragraph(
            "Steeple &amp; Stitch Co. gives back a share of the margin on "
            "everything your store sells, as a donation to your organization, "
            "issued quarterly by check. Margin is what remains after the cost of "
            "producing each item. The percentage is set by your agreement and "
            "does not change with volume or with the plan you are on. Every "
            "figure above comes from the store&rsquo;s own order records for "
            "this period, and is shown so that the donation can be checked "
            "line by line.", styles["small"]),
        Spacer(1, 8),
        Paragraph(
            f"Questions about any line on this statement are welcome — "
            f"{name}{', ' + contact if contact else ''}.", styles["small"]),
    ]


# ------------------------------------------------------------------- build

def build_statement_pdf(statement: dict, out_path: str | Path,
                        company: dict | None = None,
                        allow_incomplete: bool = False) -> Path:
    """Render one statement. Returns the path written.

    Refuses to write a file at all when the statement carries blockers. The
    guard lives here rather than only in the route because the failure this
    prevents is a *file* -- a PDF with a dash where the rate should be, sitting
    in the partner's output folder, one drag away from an email six weeks
    later. Nothing in the folder should be unsendable.
    """
    company = company or {}
    if statement.get("blockers") and not allow_incomplete:
        raise ValueError("; ".join(statement["blockers"]))
    styles = _styles()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = BaseDocTemplate(
        str(out_path), pagesize=LETTER,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=BAND_H + 0.34 * inch, bottomMargin=0.8 * inch,
        title=f"{statement['org_name']} — {statement['quarter']} donation statement",
        author="Steeple & Stitch Co.", subject=statement["number"],
    )
    first_frame = Frame(MARGIN, doc.bottomMargin, CONTENT_W,
                        PAGE_H - BAND_H - 0.34 * inch - doc.bottomMargin,
                        id="first", leftPadding=0, rightPadding=0,
                        topPadding=0, bottomPadding=0)
    later_frame = Frame(MARGIN, doc.bottomMargin, CONTENT_W,
                        PAGE_H - 0.42 * inch - 0.3 * inch - doc.bottomMargin,
                        id="later", leftPadding=0, rightPadding=0,
                        topPadding=0, bottomPadding=0)
    doc.addPageTemplates([
        PageTemplate(id="first", frames=[first_frame],
                     onPage=lambda c, d: _band(c, d, statement, styles, True)),
        PageTemplate(id="later", frames=[later_frame],
                     onPage=lambda c, d: _band(c, d, statement, styles, False)),
    ])

    story = [
        # Page 1 carries the full letterhead band; everything after it gets the
        # slim running head. Without this the first template repeats and every
        # page arrives with a three-quarter-inch banner on it.
        NextPageTemplate("later"),
        Paragraph("Quarterly Donation Statement", styles["title"]),
        Paragraph(f"{statement['quarter']} &nbsp;·&nbsp; {statement['period']}",
                  styles["subtitle"]),
        _facts(statement, styles),
        Spacer(1, 18),
        _headline(statement, styles),
        Spacer(1, 6),
        KeepTogether([Paragraph("How this figure was reached", styles["h2"]),
                      _workings(statement, styles)]),
        # Platypus will happily leave a heading alone at the foot of a page
        # with its table on the next one -- it did exactly that with "Orders in
        # this period" the first time this ran. A KeepTogether cannot be used
        # here because these tables are as long as the quarter was busy and
        # must stay splittable, so each heading instead demands enough room
        # below it to be worth printing.
        CondPageBreak(1.6 * inch),
        Paragraph("What sold this quarter", styles["h2"]),
    ]

    if statement["lines"]:
        story.append(_items(statement, styles))
        for note in _footnotes(statement, styles):
            story += [Spacer(1, 8), note]
    else:
        story.append(Paragraph(
            "No orders were placed for your store in this period.",
            styles["body"]))

    if statement["order_rows"]:
        story += [CondPageBreak(1.6 * inch),
                  Paragraph("Orders in this period", styles["h2"]),
                  _orders(statement, styles)]

    story += [Spacer(1, 20), CondPageBreak(1.0 * inch)] + _closing(
        statement, company, styles)
    doc.build(story)
    return out_path
