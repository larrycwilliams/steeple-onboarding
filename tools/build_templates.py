"""Convert Larry's hand-built source documents into docxtpl merge templates.

Run once (or again whenever a source document changes):

    python tools/build_templates.py

Reads source_docs/SRC_*.docx, writes docx_templates/*.docx with jinja tags in
place of the customer-specific strings. All formatting, images, tables and
styling are preserved -- only run text is touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import docx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from onboarding.docx_tools import apply_map, find_unreplaced  # noqa: E402

SRC = ROOT / "source_docs"
OUT = ROOT / "docx_templates"


# ---------------------------------------------------------------------------
# 1. SERVICE AGREEMENT  — generic template, blanks become tags
# ---------------------------------------------------------------------------
AGREEMENT_MAP = {
    "[Church Legal Name]": "{{ org_legal_name }}",
    'and:  {{ org_legal_name }} ("Client" or "Church")  ______________________________________________':
        'and:  {{ org_legal_name }} ("Client")',
    "Church Address:  ______________________________________________":
        "{{ org_word_title }} Address:  {{ address_full }}",
    "Primary Contact Name & Title:  ______________________________________________":
        "Primary Contact Name & Title:  {{ poc_line }}",
    "Contact Email / Phone:  ______________________________________________":
        "Contact Email / Phone:  {{ poc_contact }}",
    "Service Tier Selected (Starter / Growth / Multi-Campus):  ______________________________________________":
        "Service Tier Selected:  {{ plan }}",
    "Store Name / URL:  ______________________________________________":
        "Store Name / URL:  {{ store_name }} — {{ store_url_display }}",
    "One-time Setup Fee: $__________, due upon signing, prior to store build.":
        "One-time Setup Fee: ${{ setup_fee_fmt }}, due upon signing, prior to store build.",
    "Monthly Platform Fee: $__________ per month, billed automatically starting on the Store's launch date.":
        "Monthly Platform Fee: ${{ monthly_fee_fmt }} per month, billed automatically starting on the Store's launch date.",
    "Church Margin: __________% of net product revenue, paid to Client according to the payout schedule in Section 3.":
        "Client Margin: {{ margin_pct_fmt }}% of net product revenue, paid to Client according to the payout schedule in Section 3.",
    "Payout Method (Check / Direct Deposit):  ______________________________________________":
        "Payout Method:  {{ payout_method }}",
    "Payout Account / Mailing Info:  ______________________________________________":
        "Payout Account / Mailing Info:  {{ payout_account }}",
    "Company will remit Client's earned margin on a quarterly basis":
        "Company will remit Client's earned margin on a {{ payout_frequency_lower }} basis",
    "This Agreement is governed by the laws of the State of Ohio":
        "This Agreement is governed by the laws of the State of {{ governing_state }}",
    "CHURCH MERCH STORE SERVICES AGREEMENT": "MERCH STORE SERVICES AGREEMENT",
    "Template — review with legal counsel before use":
        "Template v{{ template_version }} — reviewed with legal counsel. Terms fixed; only the fields above vary.",
    # signature block
    "Steeple & Stitch Co.\nSignature:": "Steeple & Stitch Co.\nSignature:",
    "Client / Church": "Client",
}

AGREEMENT_LINES = {
    'Steeple & Stitch Co., ("Company")  ______________________________________________':
        'Steeple & Stitch Co. ("Company")',
    '{{ org_legal_name }} ("Client" or "Church")  ______________________________________________':
        '{{ org_legal_name }} ("Client")',
}


def post_process_agreement(doc) -> None:
    """Fill the two-column signature table: left = company, right = client."""
    from onboarding.docx_tools import replace_all

    if not doc.tables:
        return
    table = doc.tables[-1]
    columns = [
        {  # Steeple & Stitch
            "Printed Name:  ______________________________________________":
                "Printed Name:  {{ company_signer }}",
            "Title:  ______________________________________________":
                "Title:  {{ company_signer_title }}",
            "Date:  ______________________________________________":
                "Date:  {{ agreement_date_fmt }}",
        },
        {  # Client
            "Printed Name:  ______________________________________________":
                "Printed Name:  {{ client_signer or poc_name }}",
            "Title:  ______________________________________________":
                "Title:  {{ client_signer_title or poc_title }}",
            "Date:  ______________________________________________":
                "Date:  {{ agreement_date_fmt }}",
        },
    ]
    cells = table.rows[0].cells
    for cell, mapping in zip(cells, columns):
        for para in cell.paragraphs:
            for old, new in mapping.items():
                replace_all(para, old, new)


def post_process_kit(doc) -> None:
    """Prefill the appendix confirmation blanks and the row-specific contacts."""
    from onboarding.docx_tools import replace_all, all_paragraphs

    appendix = {
        "Service tier (Starter / Growth / Multi-Campus):  _______________":
            "Service tier:  {{ plan }}",
        "Payout method (check / direct deposit / PayPal):  ______________":
            "Payout method:  {{ payout_method }}",
        "Payout account or mailing info:  _______________________________":
            "Payout account or mailing info:  {{ payout_account or '_______________________' }}",
        "Payout frequency (quarterly / monthly):  _______________________":
            "Payout frequency:  {{ payout_frequency }}",
        "Finance contact:  ______________________________________________":
            "Finance contact:  {{ finance_line }}",
        "Designation for funds (general fund, athletics, tuition assistance):  __________":
            "Designation for funds:  {{ fund_designation }}",
        "opens to {{ audience }} on ______________.":
            "opens to {{ audience }} on {{ launch_date_fmt }}.",
    }
    for para in all_paragraphs(doc):
        for old, new in appendix.items():
            replace_all(para, old, new)

    contact_rows = {
        "Store changes and brand questions": "{{ poc_contact_line }}",
        "Payout and billing questions": "{{ finance_contact_line }}",
    }
    blank = "______________________   ·   ______________________"
    for table in doc.tables:
        for row in table.rows:
            cells = row.cells
            if len(cells) < 2:
                continue
            label = cells[0].text.strip()
            if label in contact_rows:
                for para in cells[1].paragraphs:
                    replace_all(para, blank, contact_rows[label])


# ---------------------------------------------------------------------------
# 2. SETUP CHECKLIST — GCS-specific header, church-flavoured body
# ---------------------------------------------------------------------------
CHECKLIST_MAP = {
    "CHURCH MERCH STORE — SETUP CHECKLIST": "MERCH STORE — SETUP CHECKLIST",
    "Germantown Christian School - GCS": "{{ org_name }}",
    "Germantown Christian School": "{{ org_name }}",
    "For church staff / ministry leads — hand this to your point-of-contact at kickoff.":
        "For {{ org_word }} staff and leadership — hand this to your point of contact at kickoff.",
    "Tier selected (Starter / Growth / Multi-Campus)": "Tier selected — {{ plan }}",
    "Church payout account (bank info or designated fund) confirmed for margin payments":
        "Payout account confirmed for margin payments — {{ payout_method }}, {{ payout_frequency_lower }}, to {{ fund_designation }}",
    "Primary point of contact named on the church side":
        "Primary point of contact named — {{ poc_name }}",
    "High-resolution church logo provided (vector .ai/.eps preferred, or high-res .png)":
        "High-resolution {{ org_word }} logo provided (vector .ai/.eps preferred, or high-res .png)",
    "Ministry logos provided, if applicable (youth, men's/women's, worship, campuses)":
        "Sub-logos provided, if applicable ({{ subgroup_examples }})",
    "Store name and preferred URL/subdomain confirmed":
        "Store name and URL confirmed — {{ store_name }}, {{ store_url_display }}",
    "Church margin percentage confirmed and documented":
        "Margin percentage confirmed and documented — {{ margin_pct_fmt }}%",
    "Any bulk/event items identified (VBS, camp, staff shirts) with target order-by dates":
        "Any bulk/event items identified with target order-by dates",
    "Sample mockups reviewed and approved by church contact":
        "Sample mockups reviewed and approved by {{ poc_name }}",
    "Store reviewed end-to-end by church contact prior to launch":
        "Store reviewed end-to-end by {{ poc_name }} prior to launch",
    "Store link finalized and QR code generated for bulletins/signage":
        "Store link finalized and QR code generated for {{ newsletter }} and signage — {{ redirect_url_display }}",
    "Launch announced in service and/or church communications":
        "Launch announced in {{ gathering_plural }} and {{ org_word }} communications",
    "Monthly platform fee invoiced and paid":
        "Monthly platform fee invoiced and paid — ${{ monthly_fee_fmt }}/mo",
    "Sales summary shared with church contact":
        "Sales summary shared with {{ poc_name }}",
    "Quarterly margin payout issued to church account":
        "{{ payout_frequency }} margin payout issued",
    "Check-in call scheduled to review performance and upcoming ministry events":
        "Check-in call scheduled to review performance and upcoming {{ org_word }} events",
    "Questions at any stage? Contact Larry directly — Steeple & Stitch Co.":
        "Questions at any stage? Contact {{ company_signer }} directly — {{ company_name }}",
}


# ---------------------------------------------------------------------------
# 3. PROMO TEMPLATES ONE-PAGER
# ---------------------------------------------------------------------------
PROMO_MAP = {
    "Fill in: [Church Name], [store URL]": "Ready to use — {{ org_name }}",
    "Fill in: [Church Name], [store URL]  ": "Ready to use — {{ org_name }}",
    "Fill in: [store URL]": "Ready to use — {{ org_name }}",
    "Our church store is officially open! 🎉 Shop [Church Name] gear — tees, hoodies, hats, and more — and every purchase supports our ministry. Link in bio, or shop now at [store URL].":
        "Our {{ org_word }} store is officially open! 🎉 Shop {{ org_name }} gear — tees, hoodies, hats, and more — and every purchase {{ supports }}. Link in bio, or shop now at {{ store_url_display }}.",
    "Subject: Our Church Store Is Live!": "Subject: The {{ store_name }} is live",
    "Hey [Church Name] family,": "Hey {{ org_name }} family,",
    "We're excited to share that our official church merch store is now open! Grab a tee, hoodie, or cap and represent [Church Name] wherever you go. Every purchase helps support our ministry directly.":
        "We're excited to share that the official {{ org_name }} merch store is now open. Grab a tee, hoodie, or cap and represent {{ org_short }} wherever you go — and every purchase {{ supports }} directly.",
    "Shop now: [store URL]": "Shop now: {{ store_url_display }}",
    "[Church Name] Merch Is Here!": "{{ org_name }} Merch Is Here!",
    "Visit [store URL] or scan the QR code in the lobby to shop tees, hoodies, hats, and more — every order supports our ministry.":
        "Visit {{ store_url_display }} or scan the QR code in the lobby to shop tees, hoodies, hats, and more — every order {{ supports }}.",
    "Hey everyone — our church merch store just went live! Check it out here: [store URL] 👕":
        "Hey everyone — the {{ org_name }} merch store just went live! Check it out here: {{ store_url_display }} 👕",
    "SUNDAY BULLETIN / ANNOUNCEMENT SLIDE": "{{ announcement_slot|upper }} / SLIDE",
    "Steeple & Stitch Co.  •  Questions? Contact your point of contact from the Launch Kit.":
        "Steeple & Stitch Co.  •  Questions? Contact {{ company_signer }}.",
    "[Church Name]": "{{ org_name }}",
    "[store URL]": "{{ store_url_display }}",
}


# ---------------------------------------------------------------------------
# 4. LAUNCH WEEK KIT — the big one. GCS-specific throughout.
# ---------------------------------------------------------------------------
KIT_MAP = {
    # -- hashtags and compound names first (longest wins, but be explicit) --
    "#GCSCardinals #GermantownChristian": "#{{ org_short }}{{ mascot_plural }}",
    "#GCSCardinals": "#{{ org_short }}{{ mascot_plural }}",
    'The arched “Germantown Christian Cardinals · Est. 1981” lockup as vector art':
        "The full {{ org_name }} lockup as vector art",
    "The GCS shield lockup as vector art": "The {{ org_short }} primary lockup as vector art",
    "Germantown Christian Schools": "{{ org_name }}",
    "Germantown Christian School": "{{ org_name }}",
    "Est. 1981": "{{ est_line }}",

    # -- store identity --
    "steepleandstitch.com/collections/gcs-cardinals": "{{ store_url_display }}",
    "GCS Cardinals Merch Store": "{{ store_name }}",
    "The GCS Cardinals Merch store is finished.": "The {{ store_name }} is finished.",
    "GCS Cardinals Merch store": "{{ store_name }}",
    "The GCS Merch store opens to families on":
        "The {{ store_name }} opens to {{ audience }} on",
    "the official Germantown Christian Schools Merch store": "the {{ store_name }}",
    "The official GCS Merch store is open.": "The {{ store_name }} is open.",
    "the GCS Merch store": "the {{ store_name }}",
    "The GCS Merch store": "The {{ store_name }}",
    "the official GCS Merch store is now open": "the {{ store_name }} is now open",
    "The official GCS Merch store is open": "The {{ store_name }} is open",
    "the GCS Cardinals": "the {{ org_short }} {{ mascot_plural }}",
    "GCS Cardinal gear is here.": "{{ org_name }} gear is here.",
    "Hello GCS Leadership,": "Hello {{ org_short }} {{ leaders }},",
    "GCS Leadership": "{{ org_short }} {{ leaders }}",
    "Back to GCS": "Back to {{ org_short }}",
    "pointing at the collection page": "pointing at {{ redirect_url_display }}",
    "back to GCS": "back to {{ org_short }}",
    "supports the school": "{{ supports_short }}",
    "comes straight back to GCS": "comes straight back to {{ org_short }}",
    "comes right back to GCS": "comes right back to {{ org_short }}",
    "comes back to Germantown Christian Schools": "comes back to {{ org_name }}",
    "sends money back to the school": "{{ supports_short }}",
    "sends a share straight back to GCS": "sends a share straight back to {{ org_short }}",

    # -- mascot voice --
    "Cardinal families": "{{ audience_address }}",
    "Cardinal fans — want to wear what the team wears?":
        "{{ audience_address }} — want to represent {{ org_short }}?",
    "Cardinals, the official GCS Merch store is now open.":
        "{{ audience_address }}, the {{ store_name }} is now open.",
    "Cardinals — the GCS Merch store is live:":
        "{{ audience_address }} — the {{ store_name }} is live:",
    "Cardinals, the official": "{{ audience_address }}, the",
    "Cardinals — the": "{{ audience_address }} — the",
    "all with the cardinal on them": "all with the {{ mark_word }} on them",
    "Go Cards, ": "{{ cheer }} ",
    "Go Cards!": "{{ cheer }}",
    "Go Cards,": "{{ cheer }}",
    "cardinal red in the hallway": "{{ org_short }} colours in the hallway",
    "Spotted: ": "Spotted: ",
    "the cardinal mark": "the {{ mark_word }}",
    "your cardinal mark": "your {{ mark_word }}",
    "the cardinal.": "the {{ mark_word }}.",
    "all carrying the cardinal": "all carrying the {{ mark_word }}",
    "Vector version of the cardinal mark (.ai, .eps or .svg) — needed for embroidery and large-format print":
        "Vector version of the {{ mark_word }} (.ai, .eps or .svg) — needed for embroidery and large-format print",
    "the red and black": "your colours",
    "Class of ’81 through the class of today": "Every class, every year",
    "Athletic team or program sub-logos you would like added":
        "{{ subgroups|sentence }} sub-logos you would like added",

    # -- store facts --
    "Crew tee, pullover hoodie, performance long sleeve and embroidered cap.": "{{ product_lineup }}",
    "Youth through adult 3XL. (4X & 5X available in some sizes)": "{{ size_range }}",
    "youth through adult 3XL": "{{ size_range_inline }}",
    "Youth through adult 3XL": "{{ size_range_inline|sentence }}",
    "five to seven days": "{{ fulfillment_days }}",
    "A share of every order, paid out quarterly by default or monthly on request.":
        "{{ margin_pct_fmt }}% of every order, paid out {{ payout_frequency_lower }}.",
    "A share of every order comes right back to GCS.":
        "A share of every order comes right back to {{ org_short }}.",
    "Tees, hoodies, performance long sleeves and caps": "{{ product_lineup_short }}",
    "Tees, hoodies, caps and more, shipped to your door.": "{{ product_lineup_short }}, shipped to your door.",
    "tees, hoodies, performance long sleeves and embroidered caps": "{{ product_lineup_lower }}",
    "classic tees, pullover hoodies, performance long sleeves and embroidered caps":
        "{{ product_lineup_lower }}",
    "tees, hoodies and caps": "{{ product_lineup_lower }}",
    "Tees, hoodies, long sleeves and caps": "{{ product_lineup_short }}",

    # -- school vocabulary -> org-type vocabulary --
    "MORNING ANNOUNCEMENT OR CHAPEL READ": "{{ announcement_slot|upper }}",
    "Morning announcement or chapel read.": "{{ announcement_slot }}.",
    "Morning announcements or chapel": "{{ announcement_slot }}",
    "staff and faculty preview email": "staff preview email",
    "Staff and faculty preview email": "Staff preview email",
    "STAFF AND FACULTY PREVIEW EMAIL": "STAFF PREVIEW EMAIL",
    "Staff and faculty collection": "Staff collection",
    "EMAIL TO FAMILIES": "EMAIL TO {{ audience|upper }}",
    "Email to all families, first thing in the morning.":
        "Email to {{ audience_all }}, first thing in the morning.",
    "Postcards stacked where families wait at pickup.":
        "Postcards stacked where {{ audience_gather }}.",
    "Thank-you post to families.": "Thank-you post to the {{ audience }}.",
    "two to four days ahead of families": "two to four days ahead of {{ ahead_of }}",
    "before families sees it": "before everyone else sees it",
    "families": "{{ audience }}",
    "Families": "{{ audience|sentence }}",
    "the front office": "the {{ office }}",
    "Front office questions": "{{ office|sentence }} questions",
    "Front office counter": "{{ office|sentence }} counter",
    "front office": "{{ office }}",
    "GAME-DAY PA READ": "{{ big_event_title|upper }} ANNOUNCEMENT",
    "Game day": "{{ big_event_title }}",
    "Game-day": "{{ big_event_title }}",
    "game day": "{{ big_event }}",
    "read between the first and second quarter, or between sets":
        "{{ pa_context }}",
    "PA read between quarters or sets.": "{{ pa_context|sentence }}.",
    "Athletics PA read and the game table": "{{ big_event_title }} announcement and table",
    "Gym entrance and concession table": "Main entrance and welcome table",
    "Athletics and school website footer, linked rather than scanned":
        "Website footer, linked rather than scanned",
    "Email signatures for office and athletics staff": "Email signatures for staff",
    "Back of the school newsletter, every issue": "Back of the {{ newsletter }}, every issue",
    "NEWSLETTER OR BULLETIN BLURB": "{{ newsletter_title|upper }}",
    "sized for a newsletter column or printed bulletin":
        "sized for a {{ newsletter }} column",
    "Group text or Remind message to team and class lists.":
        "Group text to {{ roster_word }}.",
    "GROUP TEXT, REMIND OR TEAM APP": "GROUP TEXT OR MESSAGING APP",
    "Send to team, class and parent lists.": "Send to {{ roster_word }}.",
    "Athletics sub-collection, by team": "{{ extra_collection_1 }}",
    "Fine arts and music sub-collection": "{{ extra_collection_2 }}",
    "Alumni collection with a class-year treatment": "{{ alumni_word|sentence }} collection",
    "ALUMNI POST": "{{ alumni_word|upper }} POST",
    "Alumni post and forward the link to your alumni email list.":
        "{{ alumni_word|sentence }} post and forward the link to that email list.",
    "Alumni list": "{{ alumni_word|sentence }} list",
    "your alumni email list": "your {{ alumni_word }} email list",
    "Separate storefronts by division, elementary and secondary":
        "Separate storefronts by campus or {{ subgroups }}",
    "Bulk event runs — camps, tournaments, graduation, spirit week":
        "Bulk event runs — camps, tournaments and seasonal events",
    "students noticing teachers": "people noticing leadership",
    "Students noticing teachers in the gear does more than any post we can write.":
        "People noticing leadership in the gear does more than any post we can write.",
    "a student, teacher or team": "someone wearing it",
    "a photo of a student in the gear": "a photo of someone in the gear",
    "The performance long sleeve has been the staff favorite at other schools.":
        "The performance long sleeve has been the staff favourite elsewhere.",
    "at other schools": "elsewhere",
    "Send it home in folders, stack it at the front desk, and put a pile on the entrance table at games.":
        "Hand them out, stack them at the front desk, and put a pile on the welcome table at {{ event_plural }}.",
    "Postcards home in folders or backpacks.": "Postcards handed out.",
    "Postcards on the entrance table.": "Postcards on the welcome table.",
    "Order roughly one and a half postcards per family so there are extras for the front desk and games":
        "Order roughly one and a half postcards per household so there are extras for the front desk and events",
    "gloss makes codes harder to scan under gym lights":
        "gloss makes codes harder to scan under bright lights",
    "so the whole family can match on game day": "so everyone can match",
    "Lobby or welcome area, at eye height": "Lobby or welcome area, at eye height",
    "Two weeks out": "Two weeks out",

    # -- dates and owners --
    "Launch date: ______________________": "Launch date: {{ launch_date_fmt }}",
    "Who owns Instagram and Facebook that week: ______________________":
        "Who owns Instagram and Facebook that week: {{ social_owner_line }}",
    "Live and tested. Waiting on your launch date.":
        "Live and tested. Launching {{ launch_date_fmt }}.",

    # -- contacts --
    "Store changes and brand questions": "Store changes and brand questions",
    "Payout and billing questions": "Payout and billing questions",
    "support@steepleandstitch.com": "{{ support_email }}",

    # -- palette --
    "Cardinal Red": "{{ color1_name }}",
    "Deep Cardinal": "{{ color2_name }}",
    "Cardinal Ink": "{{ color3_name }}",
    "CD3135": "{{ color1_hex }}",
    "8E2027": "{{ color2_hex }}",
    "1B222C": "{{ color3_hex }}",
    "F4F1EC": "{{ color4_hex }}",
    "8A9099": "{{ color5_hex }}",
    "Primary accent. Headlines, the postcard ticket band, buttons on the store.":
        "{{ color1_role }}",
    "Crest shadow. Secondary accent and link text on light backgrounds.": "{{ color2_role }}",
    "The mark’s body. Backgrounds, garment color, body copy.": "{{ color3_role }}",
    "Light backgrounds and printed cardstock.": "{{ color4_role }}",
    "Captions and secondary text only.": "{{ color5_role }}",

    # -- remaining bare short name last --
    "GCS": "{{ org_short }}",
    "Cardinals": "{{ mascot_plural }}",
    "Cardinal": "{{ mascot }}",
    "cardinal": "{{ mascot_lower }}",
}

# Colours baked into the Launch Week Kit's own formatting. These live in XML
# attributes (w:color / w:fill), not in run text, so the text-replacement pass
# never sees them -- which is why the kit stayed cardinal red no matter what
# palette a partner had. Swapped for jinja tags here and resolved at render.
KIT_THEME_COLORS = {
    "CD3135": "{{ theme_accent }}",        # section headings, numbers, rules
    "8E2027": "{{ theme_accent_deep }}",   # secondary accent, link text
    "1B222C": "{{ theme_ink }}",           # dark panel and cover fills
    "F4F1EC": "{{ theme_light }}",         # light panel fills
    "7A8089": "{{ theme_muted }}",         # captions and secondary text
}


# Word writes the accent twice in different attribute shapes: `w:color w:val`
# for text and a bare `w:color` inside border definitions. Splitting them lets
# the rules take the gold while the headings take the navy -- the same pairing
# as the Steeple & Stitch logo.
KIT_BORDER_COLORS = {
    "CD3135": "{{ theme_rule }}",
}


def theme_kit_colors(path: Path) -> dict[str, int]:
    """Rewrite hardcoded colour attributes in a saved .docx as merge tags."""
    import shutil
    import zipfile

    hits: dict[str, int] = {k: 0 for k in KIT_THEME_COLORS}
    hits["borders"] = 0
    source = zipfile.ZipFile(path)
    tmp = path.with_suffix(".themed.docx")

    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.endswith(".xml") and item.filename.startswith("word/"):
                xml = data.decode("utf8")
                # borders first: their bare `w:color="X"` is a substring of
                # nothing else, but doing them before the text pass keeps the
                # two mappings from ever competing for the same hex.
                for old, new in KIT_BORDER_COLORS.items():
                    needle = f'w:color="{old}"'
                    count = xml.count(needle)
                    if count:
                        xml = xml.replace(needle, f'w:color="{new}"')
                        hits["borders"] += count
                for old, new in KIT_THEME_COLORS.items():
                    for attr in ('w:color w:val', 'w:fill', 'w:themeFill'):
                        needle = f'{attr}="{old}"'
                        count = xml.count(needle)
                        if count:
                            xml = xml.replace(needle, f'{attr}="{new}"')
                            hits[old] += count
                data = xml.encode("utf8")
            out.writestr(item, data)
    source.close()
    shutil.move(str(tmp), str(path))
    return hits


# Strings that must not survive into the templates.
LEAK_CHECKS = [
    "Germantown", "GCS", "Cardinal", "cardinal", "Go Cards",
    "gcs-cardinals", "CD3135", "8E2027", "1981",
]

JOBS = [
    ("SRC_Service_Agreement.docx", "service_agreement.docx", AGREEMENT_MAP, ["[Church Legal Name]"]),
    ("SRC_Setup_Checklist.docx", "setup_checklist.docx", CHECKLIST_MAP, ["Germantown", "GCS"]),
    ("SRC_Promo_Templates.docx", "promo_templates.docx", PROMO_MAP, ["[Church Name]", "[store URL]"]),
    ("SRC_Launch_Week_Kit.docx", "launch_week_kit.docx", KIT_MAP, LEAK_CHECKS),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    failures = 0

    for src_name, out_name, mapping, leaks in JOBS:
        src_path = SRC / src_name
        if not src_path.exists():
            print(f"  !! missing source: {src_name}")
            failures += 1
            continue

        doc = docx.Document(str(src_path))
        hits = apply_map(doc, mapping)
        if out_name == "service_agreement.docx":
            apply_map(doc, AGREEMENT_LINES)
            post_process_agreement(doc)
        if out_name == "launch_week_kit.docx":
            post_process_kit(doc)
        out_path = OUT / out_name
        doc.save(str(out_path))
        if out_name == "launch_week_kit.docx":
            themed = theme_kit_colors(out_path)
            total = sum(themed.values())
            print(f"  themed {total} colour attributes "
                  + ", ".join(f"{k}×{v}" for k, v in themed.items() if v))

        applied = sum(1 for v in hits.values() if v)
        unused = [k for k, v in hits.items() if not v]
        print(f"\n{out_name}")
        print(f"  {applied}/{len(mapping)} replacement rules matched")
        if unused:
            print(f"  unmatched rules ({len(unused)}):")
            for key in unused[:12]:
                print(f"    · {key[:70]}")

        check = docx.Document(str(out_path))
        leaked = find_unreplaced(check, leaks)
        if leaked:
            print(f"  LEAKED source-specific strings: {leaked}")
            failures += 1
        else:
            print("  clean — no source-specific strings remain")

    print("\nTemplates written to", OUT)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
