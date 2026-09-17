"""Does this Mac's PDF pipeline turn Word form fields into fillable PDF fields?

    ~/.venvs/steeple-onboarding-312/bin/python tools/formfield_probe.py

Changes nothing. Writes two throwaway files to a temp folder and reports.

Why this exists before any template work. The Launch Week Kit's PDF is a flat
LibreOffice conversion, while the welcome email tells the partner to "fill in
the names and send it back" -- an instruction the attached file cannot honour.
Making it fillable has two possible routes:

  1. Put real Word form fields in docx_templates/launch_week_kit.docx and let
     the existing conversion carry them through. The fields then live in the
     document, reflow with the content, and survive a partner whose line-up
     runs to three lines instead of one.

  2. Stamp AcroForm fields onto the finished PDF at fixed coordinates. Every
     kit reflows per partner, so those coordinates are wrong the moment a
     field moves. This is the fragile route.

Route 1 is only available if LibreOffice's PDF export actually emits form
fields on THIS machine. `writer_pdf_Export` has an `ExportFormFields` option
that defaults on, but the legacy-FORMTEXT path is exactly the sort of thing
that quietly differs by version -- and this project has spent a week paying
for assumptions that were never measured.

So: measure first. If this prints EXPORTED, the template work is worth doing
and will hold. If it prints FLATTENED, say so and we take a different route
rather than building on a guess.
"""
from __future__ import annotations

import re
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onboarding import docx_pdf                                  # noqa: E402

# A minimal .docx containing one legacy Word text form field (FORMTEXT), which
# is what Word writes for a fill-in blank and what LibreOffice knows how to
# export. Built by hand because python-docx has no API for form fields.
FORM_RUN = """<w:p><w:r><w:t xml:space="preserve">Postcards ordered by: </w:t></w:r>
<w:r><w:fldChar w:fldCharType="begin"><w:ffData>
<w:name w:val="postcards_owner"/><w:enabled/><w:calcOnExit w:val="0"/>
<w:textInput><w:default w:val=" "/></w:textInput></w:ffData></w:fldChar></w:r>
<w:r><w:instrText xml:space="preserve"> FORMTEXT </w:instrText></w:r>
<w:r><w:fldChar w:fldCharType="separate"/></w:r>
<w:r><w:t>______________________</w:t></w:r>
<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>"""

DOCUMENT = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>Form field probe</w:t></w:r></w:p>
{FORM_RUN}
<w:sectPr/></w:body></w:document>"""

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def build_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", DOCUMENT)


def has_acroform(pdf: Path) -> tuple[bool, list[str]]:
    """True if the PDF carries an AcroForm with at least one widget."""
    raw = pdf.read_bytes()
    names = [m.decode("latin-1") for m in re.findall(rb"/T\s*\(([^)]*)\)", raw)]
    return (b"/AcroForm" in raw and b"/Widget" in raw), names


def main() -> int:
    engine = docx_pdf.available_engine()
    print(f"PDF engine: {engine or 'NONE — install LibreOffice'}")
    if engine is None:
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        docx, pdf = tmp / "probe.docx", tmp / "probe.pdf"
        build_docx(docx)
        result = docx_pdf.convert(docx, pdf)
        if not result["ok"]:
            print(f"conversion failed: {result['error']}")
            return 2
        found, names = has_acroform(pdf)
        print(f"PDF written: {pdf.stat().st_size} bytes")
        print(f"field names in the PDF: {names or '(none)'}")
        print()
        if found:
            print("EXPORTED — Word form fields survive the conversion on this Mac.")
            print("Route 1 is available: put the fields in the .docx template and")
            print("the existing pipeline carries them, reflow and all.")
            return 0
        print("FLATTENED — the conversion drops form fields on this Mac.")
        print("Route 1 is out. Do not stamp coordinates onto a document that")
        print("reflows per partner; pick a different approach instead.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
