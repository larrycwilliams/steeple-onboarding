"""Signable Service Agreement PDF.

Built from the *rendered* .docx, never from a second copy of the clause text,
so the PDF a partner signs and the .docx in the same folder cannot drift apart.

That principle now covers formatting as well as wording. Every paragraph is
rebuilt from the run-level formatting the .docx actually carries -- font,
size, weight, colour, alignment, spacing, indents, borders -- rather than
from a parallel set of styles defined here. Restyle the source document,
rebuild the templates, regenerate, and the PDF follows on its own. Nothing in
this file needs to know that the headings are Georgia navy or that the rule
above the signature block is gold.

Native ReportLab. No Word, no LibreOffice, no headless Office anywhere on the
path -- the launcher has to survive on a Mac with nothing but Python.

This produces a *fillable* PDF: ten AcroForm fields and an intent checkbox.
It is not a signature service. There is no audit trail, no tamper-evidence and
no identity check. For anything that has to hold up, route the same document
through Dropbox Sign or DocuSign.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    Image as RLImage,
    KeepTogether,
    PageTemplate,
    Paragraph as RLParagraph,
    Spacer,
)

ROOT = Path(__file__).resolve().parents[1]

PAGE_W, PAGE_H = LETTER
MARGIN = 0.85 * inch
CONTENT_W = PAGE_W - 2 * MARGIN

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
WP_NS = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"

# Ten fields plus the intent checkbox. Anything added here has to stay unique:
# a repeated AcroForm name makes both widgets share one value, which on a
# signature block means typing the client's name into the company's box too.
FIELDS = (
    "company_signature",
    "company_printed_name",
    "company_title",
    "company_date",
    "client_signature",
    "client_printed_name",
    "client_title",
    "client_date",
    "client_email",
    "launch_date",
)
INTENT_FIELD = "esign_intent"

INTENT_TEXT = (
    "By checking this box I agree that typing my name above is my electronic "
    "signature, and that it binds me to this Agreement exactly as an ink "
    "signature would."
)

ALIGNMENTS = {"center": TA_CENTER, "right": TA_RIGHT, "both": TA_JUSTIFY,
              "left": TA_LEFT}


# ------------------------------------------------------------------ fonts ----

# Where each family might live, best match first. The real font is preferred
# because it is what the .docx specifies; the substitutes that follow are
# metric-compatible, so a fallback changes the glyph shapes slightly but not
# where a single line break lands.
FONT_SUBSTITUTES = {
    "georgia": ["Georgia", "Gelasio", "Tinos", "Liberation Serif", "DejaVu Serif"],
    "calibri": ["Calibri", "Carlito", "Liberation Sans", "DejaVu Sans"],
    "cambria": ["Cambria", "Caladea", "Liberation Serif", "DejaVu Serif"],
    "times new roman": ["Times New Roman", "Tinos", "Liberation Serif"],
    "arial": ["Arial", "Liberation Sans", "DejaVu Sans"],
}

SERIF_HINTS = ("georgia", "cambria", "times", "garamond", "book", "serif",
               "gelasio", "tinos", "caladea")

FONT_DIRS = [
    ROOT / "assets" / "fonts",
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path.home() / "Library" / "Fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
]

# filename fragments that mark each style
STYLE_TOKENS = {
    "boldItalic": (("bolditalic", "boldoblique", "bi", "zi"),),
    "bold": (("bold", "bd", "b"),),
    "italic": (("italic", "oblique", "it", "i"),),
}

_font_cache: dict[str, str] = {}
_font_report: dict[str, str] = {}


def _font_files() -> list[Path]:
    files: list[Path] = []
    for directory in FONT_DIRS:
        try:
            if not directory.is_dir():
                continue
            files.extend(
                path for path in directory.rglob("*")
                if path.suffix.lower() in (".ttf", ".otf")
            )
        except (OSError, PermissionError):
            continue
    return files


def _style_of(stem: str, family: str) -> str:
    """Which style a font filename represents, given its family name."""
    tail = stem.lower().replace(family.lower().replace(" ", ""), "")
    tail = re.sub(r"[^a-z]", "", tail.replace(family.lower(), ""))
    if not tail or tail in ("regular", "roman", "book", "mt", "ms"):
        return "regular"
    if ("bold" in tail or tail.startswith("bd") or tail == "b") and (
        "italic" in tail or "oblique" in tail or tail.endswith("i")
    ):
        return "boldItalic"
    if "bold" in tail or tail in ("bd", "b"):
        return "bold"
    if "italic" in tail or "oblique" in tail or tail == "i":
        return "italic"
    return "regular"


def _register_family(requested: str) -> str:
    """Register the closest available match for a .docx font name.

    Returns the ReportLab family name to use. Falls back to a base-14 face,
    which always exists, so a missing font degrades the look and never the
    build.
    """
    key = (requested or "").strip().lower()
    if key in _font_cache:
        return _font_cache[key]

    serif = any(hint in key for hint in SERIF_HINTS)
    fallback = "Times-Roman" if serif else "Helvetica"

    candidates = FONT_SUBSTITUTES.get(key, [requested] if requested else [])
    available = _font_files()

    for candidate in candidates:
        if not candidate:
            continue
        compact = candidate.lower().replace(" ", "")
        found: dict[str, Path] = {}
        for path in available:
            stem = path.stem.lower().replace(" ", "").replace("_", "").replace("-", "")
            if not stem.startswith(compact):
                continue
            found.setdefault(_style_of(path.stem, candidate), path)
        if "regular" not in found:
            continue

        base = candidate.replace(" ", "")
        try:
            pdfmetrics.registerFont(TTFont(base, str(found["regular"])))
            names = {"normal": base, "bold": base, "italic": base,
                     "boldItalic": base}
            for style, suffix in (("bold", "-Bold"), ("italic", "-Italic"),
                                  ("boldItalic", "-BoldItalic")):
                if style in found:
                    alias = base + suffix
                    pdfmetrics.registerFont(TTFont(alias, str(found[style])))
                    names[style] = alias
            pdfmetrics.registerFontFamily(
                base, normal=names["normal"], bold=names["bold"],
                italic=names["italic"], boldItalic=names["boldItalic"],
            )
        except Exception:
            continue

        _font_cache[key] = base
        _font_report[requested or "(default)"] = (
            candidate if candidate.lower() == key else f"{candidate} (substituted)"
        )
        return base

    _font_cache[key] = fallback
    _font_report[requested or "(default)"] = f"{fallback} (fallback)"
    return fallback


def font_report() -> dict[str, str]:
    """What each requested family actually resolved to, for the manifest."""
    return dict(_font_report)


# ------------------------------------------------------------- docx model ----

def _twips(value, default: float = 0.0) -> float:
    try:
        return float(value) / 20.0
    except (TypeError, ValueError):
        return default


def _run_format(run, para_props: dict) -> dict:
    font = run.font
    colour = None
    try:
        if font.color is not None and font.color.rgb is not None:
            colour = str(font.color.rgb)
    except (AttributeError, ValueError):
        colour = None
    return {
        "text": run.text,
        "font": font.name or para_props.get("font") or "Calibri",
        "size": font.size.pt if font.size is not None else para_props.get("size", 10.5),
        "bold": bool(run.bold),
        "italic": bool(run.italic),
        "color": colour or para_props.get("color") or "333333",
    }


def _paragraph_props(para: Paragraph) -> dict:
    pPr = para._p.find(f"{W_NS}pPr")
    props: dict = {
        "align": None,
        "space_before": None,
        "space_after": None,
        "indent": 0.0,
        "border_top": None,
    }
    if pPr is None:
        return props

    jc = pPr.find(f"{W_NS}jc")
    if jc is not None:
        props["align"] = jc.get(f"{W_NS}val")

    spacing = pPr.find(f"{W_NS}spacing")
    if spacing is not None:
        before = spacing.get(f"{W_NS}before")
        after = spacing.get(f"{W_NS}after")
        if before is not None:
            props["space_before"] = _twips(before)
        if after is not None:
            props["space_after"] = _twips(after)

    ind = pPr.find(f"{W_NS}ind")
    if ind is not None:
        props["indent"] = _twips(ind.get(f"{W_NS}left"), 0.0)

    border = pPr.find(f"{W_NS}pBdr")
    if border is not None:
        top = border.find(f"{W_NS}top")
        if top is not None and top.get(f"{W_NS}val") not in (None, "none", "nil"):
            props["border_top"] = {
                "color": top.get(f"{W_NS}color") or "000000",
                "width": float(top.get(f"{W_NS}sz") or 8) / 8.0,
                "space": float(top.get(f"{W_NS}space") or 0),
            }
    return props


def _iter_body(document: Document):
    """Walk paragraphs and tables in true document order.

    ``document.paragraphs`` skips tables entirely, so the signature block at
    the end would vanish without trace -- and that block is the whole reason
    this file exists.
    """
    for child in document.element.body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            yield Paragraph(child, document)
        elif tag == "tbl":
            yield Table(child, document)


def read_blocks(docx_path: Path) -> list[dict]:
    """Every body paragraph, with the formatting it actually carries."""
    document = Document(str(docx_path))
    blocks: list[dict] = []

    for item in _iter_body(document):
        if isinstance(item, Table):
            # The signature table is rebuilt as form fields, not copied.
            continue

        text = item.text.strip()
        if not text:
            continue
        if text.lower() == "agreed and accepted":
            # The signature block re-creates this heading with the form fields
            # under it. Keeping the document's copy too prints it twice, once
            # stranded at the foot of the previous page.
            continue

        style = item.style.name if item.style is not None else ""
        props = _paragraph_props(item)
        runs = [
            _run_format(run, props) for run in item.runs if run.text
        ]
        if not runs:
            continue

        blocks.append({
            "style": style,
            "bullet": style == "List Paragraph",
            "runs": runs,
            "text": text,
            **props,
        })

    return blocks


def _images(docx_path: Path) -> tuple[dict | None, dict | None]:
    """The body logo and the header watermark, with their real dimensions.

    Both are read from the document's own structure rather than guessed: the
    logo is the first drawing in the body, the watermark is the image the
    page header anchors behind the text. Sizes come from the drawing extents,
    so the PDF places them exactly where Word does.
    """
    with zipfile.ZipFile(docx_path) as archive:
        names = set(archive.namelist())

        def drawing_from(part: str, rels_part: str) -> dict | None:
            if part not in names or rels_part not in names:
                return None
            xml = archive.read(part).decode("utf8")
            extent = re.search(r'<wp:extent cx="(\d+)" cy="(\d+)"', xml)
            embed = re.search(r'<a:blip[^>]*r:embed="([^"]+)"', xml)
            if not extent or not embed:
                return None
            rels = archive.read(rels_part).decode("utf8")
            target = re.search(
                r'Id="%s"[^>]*Target="([^"]+)"' % re.escape(embed.group(1)), rels
            )
            if not target:
                return None
            media = "word/" + target.group(1).lstrip("/")
            if media not in names:
                return None
            return {
                "data": archive.read(media),
                "width": int(extent.group(1)) / 914400.0 * inch,
                "height": int(extent.group(2)) / 914400.0 * inch,
            }

        logo = drawing_from("word/document.xml", "word/_rels/document.xml.rels")
        watermark = None
        for index in ("", "1", "2", "3"):
            watermark = drawing_from(
                f"word/header{index}.xml", f"word/_rels/header{index}.xml.rels"
            )
            if watermark:
                break

    return logo, watermark


# ------------------------------------------------------------- flowables ----

class FormRow(Flowable):
    """One labelled AcroForm widget, positioned in absolute page space.

    ReportLab's AcroForm draws straight onto the page and ignores the
    translation platypus has applied to the canvas for the current frame. A
    field drawn at the flowable's local (0, 0) therefore lands at the bottom
    of the page -- every field in the block stacked on one line, which is
    exactly what the first version did. ``canv.absolutePosition`` converts
    local coordinates back to page coordinates, which is what the form
    machinery actually wants.
    """

    def __init__(self, name: str, label: str, width: float, value: str = "",
                 height: float = 22, label_font: str = "Helvetica",
                 body_font: str = "Helvetica", ink: Color | None = None,
                 muted: Color | None = None, fill: Color | None = None,
                 tooltip: str = "") -> None:
        super().__init__()
        self.name = name
        self.label = label
        self.value = value or ""
        self.width = width
        self.field_height = height
        self.label_font = label_font
        self.body_font = body_font
        self.ink = ink or HexColor("#1B2A4A")
        self.muted = muted or HexColor("#5B6472")
        self.fill = fill or HexColor("#F4F1EC")
        self.tooltip = tooltip or label
        self.height = height + 13

    def draw(self) -> None:
        canv = self.canv
        canv.setFont(self.label_font, 7.5)
        canv.setFillColor(self.muted)
        canv.drawString(1, self.field_height + 4, self.label.upper())

        x, y = canv.absolutePosition(0, 0)
        canv.acroForm.textfield(
            name=self.name,
            tooltip=self.tooltip,
            value=self.value,
            x=x,
            y=y,
            width=self.width,
            height=self.field_height,
            borderWidth=0,
            borderColor=None,
            fillColor=self.fill,
            textColor=self.ink,
            fontSize=10,
            forceBorder=False,
        )
        canv.setStrokeColor(self.muted)
        canv.setLineWidth(0.6)
        canv.line(0, 0, self.width, 0)


class IntentCheckbox(Flowable):
    """The intent checkbox and its sentence, on one line."""

    def __init__(self, width: float, ink: Color, muted: Color,
                 font: str = "Helvetica", fill: Color | None = None) -> None:
        super().__init__()
        self.width = width
        self.ink = ink
        self.muted = muted
        self.fill = fill or HexColor("#F4F1EC")
        self.style = ParagraphStyle(
            "intent", fontName=font, fontSize=8, leading=10.5, textColor=muted,
        )
        self.para = RLParagraph(INTENT_TEXT, self.style)
        self.para_w = width - 26
        _, self.para_h = self.para.wrap(self.para_w, 200)
        self.height = max(self.para_h, 16)

    def draw(self) -> None:
        canv = self.canv
        x, y = canv.absolutePosition(0, self.height - 14)
        canv.acroForm.checkbox(
            name=INTENT_FIELD,
            tooltip="I intend this to be my electronic signature",
            x=x, y=y, size=12, checked=False, buttonStyle="check",
            borderWidth=0.8, borderColor=self.muted, fillColor=self.fill,
            textColor=self.ink, forceBorder=True,
        )
        self.para.drawOn(canv, 26, 0)


class TopBorder(Flowable):
    """A paragraph's top border, as Word draws it: full content width."""

    def __init__(self, width: float, color: Color, thickness: float) -> None:
        super().__init__()
        self.width = width
        self.color = color
        self.thickness = thickness
        self.height = thickness

    def draw(self) -> None:
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 0, self.width, 0)


class TwoColumn(Flowable):
    """Two independent stacks of flowables, side by side.

    A platypus Table would be the obvious choice, but a Table re-wraps and
    re-draws its cell contents through its own canvas state, which breaks the
    absolute positioning the form widgets depend on. Laying the two columns
    out by hand keeps every field's ``absolutePosition`` honest.
    """

    def __init__(self, left: list, right: list, col_width: float, gap: float) -> None:
        super().__init__()
        self.left = left
        self.right = right
        self.col_width = col_width
        self.gap = gap
        self.width = col_width * 2 + gap
        self.height = 0.0

    def wrap(self, avail_w: float, avail_h: float) -> tuple[float, float]:
        heights = []
        for stack in (self.left, self.right):
            total = 0.0
            for item in stack:
                _, h = item.wrap(self.col_width, avail_h)
                total += h + getattr(getattr(item, "style", None), "spaceAfter", 0)
            heights.append(total)
        self.height = max(heights)
        return self.width, self.height

    def draw(self) -> None:
        for stack, x in ((self.left, 0.0), (self.right, self.col_width + self.gap)):
            y = self.height
            for item in stack:
                _, h = item.wrap(self.col_width, self.height)
                y -= h
                item.drawOn(self.canv, x, y)
                y -= getattr(getattr(item, "style", None), "spaceAfter", 0)


# ------------------------------------------------------------------ build ----

def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _markup(runs: list[dict]) -> str:
    """Rebuild a paragraph as ReportLab markup, run by run."""
    parts = []
    for run in runs:
        body = _escape(run["text"])
        if run["italic"]:
            body = f"<i>{body}</i>"
        if run["bold"]:
            body = f"<b>{body}</b>"
        parts.append(
            '<font name="%s" size="%s" color="#%s">%s</font>'
            % (_register_family(run["font"]), run["size"], run["color"], body)
        )
    return "".join(parts)


def _dominant_run(blocks: list[dict]) -> dict:
    """The formatting the bulk of the document is set in.

    Not simply the first body paragraph -- that is the centred title, which
    is Georgia navy and nothing like the running text. Taking the formatting
    used by the most characters gets the actual body face, which is what the
    form labels, the footer and the field text should match.
    """
    weights: dict[tuple, list] = {}
    for block in blocks:
        if block["style"].startswith("Heading"):
            continue
        for run in block["runs"]:
            key = (run["font"], run["size"], run["color"])
            entry = weights.setdefault(key, [0, run])
            entry[0] += len(run["text"])
    if not weights:
        return {}
    return max(weights.values(), key=lambda item: item[0])[1]


def build_agreement_pdf(docx_path: str | Path, ctx: dict,
                        out_path: str | Path) -> str:
    """Render a signable PDF of the agreement already rendered at docx_path."""
    docx_path = Path(docx_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    blocks = read_blocks(docx_path)
    logo, watermark = _images(docx_path)

    # The document's own palette, taken from the block that uses it, so the
    # signature furniture matches whatever the source document is styled in.
    heading = next(
        (b for b in blocks if b["style"].startswith("Heading")), None
    )
    heading_run = heading["runs"][0] if heading else {}
    body_run = _dominant_run(blocks)

    heading_font = _register_family(heading_run.get("font", "Georgia"))
    body_font = _register_family(body_run.get("font", "Calibri"))
    accent = HexColor("#" + heading_run.get("color", "1B2A4A"))
    ink = HexColor("#" + body_run.get("color", "333333"))
    muted = HexColor("#5B6472")
    rule_color = HexColor("#B8912F")
    field_fill = HexColor("#F4F1EC")

    story: list = []

    if logo:
        try:
            image = RLImage(io.BytesIO(logo["data"]))
            image.drawWidth = logo["width"]
            image.drawHeight = logo["height"]
            image.hAlign = "CENTER"
            story.append(image)
            story.append(Spacer(1, 4))
        except Exception:
            pass

    def style_for(block: dict, index: int) -> ParagraphStyle:
        first = block["runs"][0]
        size = float(first["size"])
        return ParagraphStyle(
            f"b{index}",
            fontName=_register_family(first["font"]),
            fontSize=size,
            leading=size * 1.21,
            textColor=HexColor("#" + first["color"]),
            alignment=ALIGNMENTS.get(block["align"] or "left", TA_LEFT),
            spaceBefore=block["space_before"] if block["space_before"] is not None else 0,
            spaceAfter=block["space_after"] if block["space_after"] is not None else 4,
            leftIndent=block["indent"] + (13 if block["bullet"] else 0),
            bulletIndent=block["indent"] + 3,
        )

    def flowable_for(block: dict, index: int):
        style = style_for(block, index)
        if block["bullet"]:
            return RLParagraph(_markup(block["runs"]), style, bulletText="•")
        return RLParagraph(_markup(block["runs"]), style)

    index = 0
    while index < len(blocks):
        block = blocks[index]
        pieces = []
        if block["border_top"]:
            border = block["border_top"]
            pieces.append(Spacer(1, block["space_before"] or 0))
            pieces.append(TopBorder(CONTENT_W, HexColor("#" + border["color"]),
                                    border["width"]))
            pieces.append(Spacer(1, border["space"]))
        pieces.append(flowable_for(block, index))

        # A heading and the first line beneath it travel together, so a
        # section title is never left stranded at the foot of a page.
        if block["style"].startswith("Heading") and index + 1 < len(blocks):
            pieces.append(flowable_for(blocks[index + 1], index + 1))
            index += 1
        story.append(KeepTogether(pieces) if len(pieces) > 1 else pieces[0])
        index += 1

    # ---- signature block -------------------------------------------------
    col_w = (CONTENT_W - 0.35 * inch) / 2
    sign_head = ParagraphStyle(
        "signhead", fontName=heading_font, fontSize=12, leading=15,
        textColor=accent, spaceAfter=5,
    )
    party_head = ParagraphStyle(
        "party", fontName=body_font, fontSize=9.5, leading=12,
        textColor=accent, spaceAfter=5,
    )

    def row(name: str, label: str, value: str = "", height: float = 22,
            tooltip: str = "") -> FormRow:
        return FormRow(name, label, col_w, value=value, height=height,
                       label_font=body_font, body_font=body_font,
                       ink=ink, muted=muted, fill=field_fill, tooltip=tooltip)

    def column(prefix: str, heading_text: str, name: str, title: str,
               date: str) -> list:
        return [
            RLParagraph(f"<b>{_escape(heading_text)}</b>", party_head),
            row(f"{prefix}_signature", "Signature", height=26,
                tooltip="Type your full name to sign"),
            Spacer(1, 7),
            row(f"{prefix}_printed_name", "Printed name", name),
            Spacer(1, 7),
            row(f"{prefix}_title", "Title", title),
            Spacer(1, 7),
            row(f"{prefix}_date", "Date", date),
        ]

    story.append(KeepTogether([
        Spacer(1, 17),
        TopBorder(CONTENT_W, rule_color, 1.0),
        Spacer(1, 6),
        RLParagraph("<b>Agreed and Accepted</b>", sign_head),
        Spacer(1, 4),
        TwoColumn(
            column("company", ctx.get("company_name", "Steeple & Stitch Co."),
                   ctx.get("company_signer", ""),
                   ctx.get("company_signer_title", ""),
                   ctx.get("agreement_date_fmt", "")),
            column("client", "Client", ctx.get("client_signer", ""),
                   ctx.get("client_signer_title", ""), ""),
            col_w, 0.35 * inch,
        ),
        Spacer(1, 12),
        row("client_email", "Billing contact email", ctx.get("finance_email", "")),
        Spacer(1, 9),
        row("launch_date", "Target launch date", _date_value(ctx)),
        Spacer(1, 12),
        IntentCheckbox(CONTENT_W, ink, muted, font=body_font, fill=field_fill),
    ]))

    # ---- page furniture --------------------------------------------------
    footer = "%s  •  %s  •  Template v%s" % (
        ctx.get("company_name", "Steeple & Stitch Co."),
        ctx.get("org_legal_name") or ctx.get("org_name", ""),
        ctx.get("template_version", ""),
    )

    def on_page(canv, doc) -> None:
        canv.saveState()
        if watermark:
            try:
                from reportlab.lib.utils import ImageReader

                canv.drawImage(
                    ImageReader(io.BytesIO(watermark["data"])),
                    (PAGE_W - watermark["width"]) / 2,
                    (PAGE_H - watermark["height"]) / 2,
                    width=watermark["width"],
                    height=watermark["height"],
                    mask="auto",
                )
            except Exception:
                pass
        canv.setFillColor(muted)
        canv.setFont(body_font, 7)
        canv.drawCentredString(PAGE_W / 2, MARGIN - 26, footer)
        canv.drawRightString(PAGE_W - MARGIN, MARGIN - 26, f"Page {doc.page}")
        canv.restoreState()

    frame = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
                  id="body")
    doc = BaseDocTemplate(
        str(out_path), pagesize=LETTER,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"Merch Store Services Agreement — {ctx.get('org_name', '')}",
        author=ctx.get("company_name", "Steeple & Stitch Co."),
        subject=f"Template v{ctx.get('template_version', '')}",
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=on_page)])
    doc.build(story)
    return str(out_path)


def _date_value(ctx: dict) -> str:
    """Pre-fill the launch date only when there is a real one.

    ``launch_date_fmt`` is padded with underscores when the date is blank --
    that reads as a rule in the .docx, but inside a form field it would look
    like someone had typed underscores into the box.
    """
    value = str(ctx.get("launch_date_fmt", "") or "")
    return "" if "_" in value else value.strip()
