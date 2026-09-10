# Steeple & Stitch — Partner Onboarding

Enter a partner once. Get the Service Agreement (editable .docx **and** a
signable PDF), Setup Checklist, Launch Week Kit, Promo Templates, a branded QR
code and print-ready postcards — all consistent, all named to convention.

## File naming

Everything the app produces lands in `output/<Customer-Name>/` and is named:

```
YYYY-MM-DD_Customer-Name_Descriptor_v01.ext
```

e.g. `2026-09-05_Germantown-Christian-Schools_Launch-Week-Kit_v01.docx`

The customer token is the full organization name, hyphenated, so a Finder
window full of partners is readable without opening anything. Rename the
organization in the app and the next generation follows the new name.

## Running it

**Double-click `Steeple & Stitch Onboarding.app`** in this folder, or drag it
to the Dock or into `/Applications`. It opens `run.command` in Terminal, which
starts the server and opens your browser. Close the Terminal window to stop it.

To use it from the iPad or the phone, double-click **`run-mobile.command`**
instead — same app, served where your other devices can reach it. See
[Using it from the iPad and the phone](#using-it-from-the-ipad-and-the-phone).

> **Why it goes through Terminal.** This folder lives in iCloud Drive, which
> macOS protects under Privacy & Security. Permission is granted per
> *application*, and an unsigned bundle has no stable identity for a grant to
> attach to — so running Python directly from the app failed with
> `PermissionError: [Errno 1] Operation not permitted` before a single module
> loaded. Terminal already holds that permission. Handing off inherits it
> instead of asking for a second grant, and keeps `run.command` as the one
> place that knows how to build and update the environment.
>
> If you would rather have the app run Python itself — its own Dock icon, no
> Terminal window — add it under **System Settings → Privacy & Security →
> Full Disk Access**. Be aware the grant is tied to the app's exact contents,
> so it has to be renewed each time the launcher changes.

> **iCloud strips the executable bit**, on the app bundle exactly as it does
> on `run.command`. If double-clicking the icon does nothing on a second Mac,
> run this once there:
>
> ```bash
> chmod +x "$HOME/Library/Mobile Documents/com~apple~CloudDocs/20-Steeple-Stitch/Business-Files/Onboarding Document Templates/Steeple & Stitch Onboarding.app/Contents/MacOS/steeple-stitch"
> ```
>
> If macOS says the developer cannot be verified, right-click the app and
> choose **Open** once. It is unsigned because it is yours.

`run.command` still works and remains the repair path: it is the thing that
builds and updates the Python environment, and it prints its errors where you
can read them.

### The old way

Double-click **`run.command`** in Finder. First run takes a minute while it builds
its environment; after that it opens <http://127.0.0.1:5000> in your browser.

If double-clicking does nothing, the file needs its executable bit (copying
through iCloud drops it). Once, in Terminal:

```bash
chmod +x "$HOME/Library/Mobile Documents/com~apple~CloudDocs/20-Steeple-Stitch/Business-Files/Onboarding Document Templates/run.command"
```

From Terminal instead:

```bash
python3 -m venv ~/.venvs/steeple-onboarding
~/.venvs/steeple-onboarding/bin/pip install -r requirements.txt
~/.venvs/steeple-onboarding/bin/python app.py
```

If the launcher hits a problem it now prints the reason and waits for a keypress
instead of closing silently. Dependencies are only reinstalled when
`requirements.txt` actually changes, and anything in `requirements-optional.txt`
is allowed to fail without stopping the app.

> The environment lives in `~/.venvs`, not in this folder. iCloud and Python
> virtual environments don't mix — thousands of small package files syncing
> causes slow starts and occasional broken installs. Same reason: **don't run
> `git init` in this folder.** If you want version control, clone it to `~/Dev`.

## Using it from the iPad and the phone

Three different things hide inside "use it on my iPad," and they have three
different answers.

**Seeing the finished files already works.** Everything the app produces lands
in this iCloud folder, so it is on the iPad and the phone within a minute of
being generated. Files → iCloud Drive → `20-Steeple-Stitch`. Nothing to set up,
and it works with the Mac asleep. If all you want is to show a partner their
postcard over coffee, stop here.

**Using the app's screens needs the Mac awake and serving.** The app is Python;
there is no version of it that runs on iOS. What you can do is reach the Mac
that is running it. Double-click **run-mobile.command** instead of
run.command — it serves on the Mac's Tailscale address and keeps the Mac from
idling to sleep — then open the address it prints in Safari on the iPad. It
looks like `http://100.x.y.z:5000`, and it works over cellular from anywhere,
not just at home.

Set up once, on all three devices:

1. Install Tailscale (Mac App Store, App Store) and sign in with the same
   account on the Mac, the iPad and the iPhone.
2. On the Mac, double-click `run-mobile.command`. It prints the address.
3. In Safari on the iPad, open that address and add it to the Home Screen.

**There is no login on this app.** Every partner record, every negotiated
margin and every contact is readable by anything that can reach the port.
Tailscale is what makes that safe: it is a private network between your own
devices, and nothing outside it can route to that address, so the Mac stays
invisible even on hotel or coffee-shop Wi-Fi. `SS_HOST=lan` exists for a home
network and prints a warning every time, because on any other network it is a
straightforward leak.

**Generating still happens on the Mac, and that is fine.** When you tap
Generate from the iPad, the Mac does the work — LibreOffice, the PDF, the QR —
and the files appear in iCloud, so they are on the iPad a moment later. The one
thing that behaves oddly at a distance is **Create draft in Mail**: the draft
opens in Mail *on the Mac*, since that is where the app and the attachments
are. Tap it from the iPad and the draft is waiting for you next time you sit
down. That is usually what you want anyway — the last look before a welcome
email goes out is worth doing on a real screen.

### Capturing a partner on the phone, with nothing running

The realistic phone moment is standing in a church office having just gotten a
yes, with the Mac at home and asleep. You do not need to generate anything
there; you need to not lose the details.

An Apple Shortcut handles this with no server at all. Have it ask for the
handful of fields below, assemble them into JSON with a Text action, and use
**Save File** to write it into

    iCloud Drive/20-Steeple-Stitch/Business-Files/Onboarding Document Templates/partners/

as `<slug>.json`. The app reads that folder on every page load, so the partner
is simply there the next time you open it on the Mac, ready to fill in the
rest.

The minimum worth capturing in a parking lot:

    {"id": "grace-fellowship", "org_name": "Grace Fellowship",
     "org_short": "Grace", "org_type": "church",
     "poc_name": "", "poc_email": "", "poc_phone": "",
     "plan": "Starter", "margin_pct": 0}

`margin_pct: 0` is deliberate — it is the seed value the app already treats as
*unset*, so a record captured this way cannot be generated and sent with a
made-up margin on it. It will show up in the list with an incomplete warning
until you finish it, which is exactly right.

### What this does not solve

The Mac has to be awake and running the app. `run-mobile.command` stops it
idling, but a closed lid still sleeps it. If you want the app reachable
whenever you happen to pick up the iPad, the answer is a Mac that stays
powered on and logged in, with the app started at login — not a change to
this app.

## The dashboard

**Dashboard** in the nav. Revenue, cost of goods, margin and **what each partner
is owed** under their negotiated rate — all time and by quarter, because payouts
are quarterly. Plus what is actually live in each store, and anything misfiled.

Two buttons:

- **Refresh from Shopify** — pulls fresh figures. Needs a token (below).
- **Save snapshot to iCloud** — writes
  `output/Dashboard/Steeple-Stitch-Dashboard.html`, a standalone page that needs
  no server. It syncs to your iPad and phone; add it to a Home Screen. The
  filename never changes so the bookmark keeps working, and the page stamps
  when it was taken so you always know how old it is.

### The numbers, precisely

Revenue is what was **actually collected** — after every discount including
store-wide ones, and excluding refunded units. Margin is that minus Shopify's
recorded cost of goods. Owed is margin × the partner's rate.

**A partner still on the 0% seed shows a dash, not $0.00.** Those are different
facts and the dashboard will not blur them.

**A missing cost is never treated as zero.** Some items have no cost in Shopify.
Counting a blank as $0 would make margin equal revenue and overstate what a
church is owed by exactly the cost of the shirt. Instead those lines contribute
revenue only, get named on screen under the partner, and every payout is
labelled a **floor**. Fill the costs in and the numbers correct themselves.

### The vendor field is what pays people

Your collections are smart collections keyed on vendor, so the vendor field
decides which partner a product — and its sales — belongs to. That is why it
matters that it gets hand-corrected after each POD creation.

The dashboard uses `shopify_vendor` on each partner record, set from the
collection's own rule:

    ~/.venvs/steeple-onboarding/bin/python tools/set_shopify_vendors.py
    ~/.venvs/steeple-onboarding/bin/python tools/set_shopify_vendors.py --write

Run it after adding a partner or renaming a collection. Without it the app has
to guess from the organisation name, and two partners can guess the same
vendor — GCS Athletics inherited Germantown's legal name and claimed $2,973.76
of its sales before this existed.

Anything the dashboard cannot attribute — a sale whose vendor matches no
partner, an active product in no collection, two partners claiming one vendor —
is listed under **Worth a look** rather than quietly absorbed.

### Connecting to Shopify

The dashboard ships with a seeded cache so it works immediately. To refresh it
yourself, connect the app once: **Settings → Shopify connection**.

**There is no token to paste.** Your store has no legacy custom apps — the kind
that showed you a `shpat_` value to copy. Shopify removed those. A Dev Dashboard
app mints its token during install and sends it to the app's redirect URL,
once. So this app *is* the redirect: you give it the app's Client ID and
secret, click **Connect to Shopify**, approve once, and the token is written to
`.env` and never shown again.

Setting the app up in Shopify, if it ever has to be done again:

1. Shopify admin → Settings → Apps → **Build apps in Dev Dashboard**.
2. Create an app, **Start from Dev Dashboard**.
3. Scopes: `read_orders`, `read_products`, `read_inventory`. Miss
   `read_inventory` and every cost comes back blank, which looks identical to a
   store where nobody entered costs.
4. Allowed redirection URLs:
   `http://127.0.0.1:5000/shopify/callback,http://localhost:5000/shopify/callback`
5. Untick **Embed app in Shopify admin** — this app is not an embedded admin
   app, and leaving it on makes the install try to render it in an iframe.
6. Release the version, then **Install app** on the Steeple & Stitch store.
7. Copy the Client ID and secret from **App settings → Credentials** into this
   app's Settings, and press Connect.

> **Port 5000 is not really yours on a Mac.** AirPlay Receiver listens on it and
> answers with HTTP 403. `localhost` resolves to IPv6 `::1`, where AirPlay is;
> Flask binds IPv4 `127.0.0.1`. A callback sent to the `localhost` spelling gets
> a 403 from AirPlay while the app is running perfectly one address over. The
> redirect URL is now derived from whichever host you are browsing, so it
> matches — but if port 5000 misbehaves, that is why. System Settings → General
> → AirDrop & Handoff turns AirPlay Receiver off.

If a refresh fails, the previous data stays on screen with the failure noted —
it never blanks.


## The discovery call

**Discovery** in the header, or the **Discovery call** button on any lead in
**Pipeline**. Twelve questions in the order the conversation goes —
understand them, build the line-up, make it real — each with a note box and a
tick. It is the "Church store discovery" checklist that used to live as a
standalone page on the iPad, moved in here so the notes stop living in one
browser.

- **Every note saves itself** a moment after you stop typing, one field at a
  time. Writing a note ticks its question. The bar at the bottom says
  *Saved* — or, if the connection drops in a church basement, that the edits
  are being kept on the device and will send when it is back. Closing the tab
  while offline loses nothing; the next open resends them.
- **One set of notes per lead.** Opening it again resumes. The pipeline button
  shows progress (*Discovery 7/12*).
- **Walk-ins** — someone who never filled the form — start from the Discovery
  page with just a name. They do not touch the pipeline.
- **Mark call held** moves the lead to *Call held*, never backwards.
- **Promote to partner** creates the record, and the *line-up* and *sizes*
  answers become its **Launch product lineup** and **Size range** — only where
  the record still has the blank or seed value. **Copy into partner** does the
  same for a lead that was promoted another way. Call notes never overwrite
  something typed on the partner form; tidy them into one sentence there.
- **Write-up** copies a plain-text summary for an email or the partner's file.

"What you can promise in the room" reads `discovery.json` (decoration,
delivery, price points, sizes — checked against the live store on the date it
carries) and the plan table reads `terms.json`, so the pitch in the room and
the agreement can't disagree. Change shipping rates or the price ladder in the
store → change `discovery.json` the same day.

Notes live in `leads/discovery/`, which is live data: gitignored and covered by
the nightly backup with the rest of `leads/`.

## The workflow, per partner

1. **New partner** → fill the form → **Save**
2. Upload their logo → **Sample palette from logo** → review the five colours
3. If Shopify is connected: **Pull lineup & sizes** to fill the store fields
4. **Save & generate package**
5. **Add the redirect in Shopify** (see below) — *before anything goes to print*
6. Download the zip, or pull individual files

Re-running generate overwrites that day's files. Change a margin, a contact, a
launch date — regenerate and every document updates together.

## The redirect step — do not skip this

QR codes point at `steepleandstitch.com/go/<slug>`, **not** at the collection URL.

Printed postcards are permanent; Shopify collection handles are not. If you ever
rename a collection, every printed card pointing at `/collections/<handle>` dies.
Pointing at a redirect you control means you re-target it in ten seconds instead.

Set it up in **Shopify Admin → Online Store → Navigation → URL Redirects**:

| Redirect from | Redirect to |
|---|---|
| `/go/gcs` | `/collections/gcs-cardinals` |

The app generates this CSV for you — per partner on the generate screen, or for
everyone at once via **Redirect CSV** in the nav.

> The GCS postcards already in the wild point at `/collections/gcs-cardinals`.
> Add a `/go/gcs` redirect now so the next print run can move over, and never
> rename that collection while the old cards are circulating.

## What each field drives

Most fields land in more than one document, which is the point — enter the margin
once and it appears in the agreement, the checklist and the kit's payout appendix.

**Organization type** is the big one. It swaps vocabulary across every document:

| | Church | School | Non-Profit |
|---|---|---|---|
| audience | congregation | families | supporters |
| gathering | service | chapel | event |
| newsletter | bulletin | newsletter | newsletter |
| office | church office | front office | main office |
| big event | Sunday | game day | event day |

One template set, three voices. Edit the map in `onboarding/schema.py` (`VOCAB`)
if a phrase doesn't sound right for a partner type.

**Mascot** is optional. Leave it blank and the copy falls back to the short name,
and the sign-off becomes the org-type default instead of "Go <Mascot>!".

## Shopify auto-fill (optional)

Copy `.env.example` to `.env` and fill in:

```
SHOPIFY_STORE=steepleandstitch.myshopify.com
SHOPIFY_ADMIN_TOKEN=shpat_...
```

Needs `read_products` scope. With it, **Pull lineup & sizes** reads the live
collection and proposes the product lineup sentence and size range. Without it,
everything still works — you just type those two fields.

`.env` is gitignored. Don't put the token anywhere else.

## Partner record history

Every save snapshots the previous version to `partners/_history/<partner>/`,
keeping the last 30. Records have no other version control, and on 2026-09-05
a regression script silently overwrote a live partner's contacts, address and
negotiated terms; it was recoverable only because the old values happened to
be quoted in a chat transcript. A few KB of JSON per save is cheap insurance.

## Editing the documents themselves

The templates in `docx_templates/` are generated — **don't edit them by hand.**

To change wording for everyone:

1. Edit the matching file in `source_docs/`
2. Run `~/.venvs/steeple-onboarding/bin/python tools/build_templates.py`
3. Regenerate any partner

`tools/build_templates.py` holds the replacement maps — the literal strings from
your documents on the left, merge tags on the right. If you add a new blank to a
source document, add a rule there. The build reports any rule that stopped
matching and refuses to leave customer-specific strings (GCS, Cardinal, 1981) in
a template, so a stale rule shows up immediately rather than in a partner's kit.

## Layout

```
app.py                   the web app
run.command              double-click launcher
onboarding/
  schema.py              fields, vocabulary map, all derived values
  store.py               partner JSON records
  merge.py               docx rendering + embedded image swapping
  settings.py            your own details (company.json)
  welcome_email.py       the welcome email, merged from the record
  mail_draft.py          opens the draft in Apple Mail
  agreement_pdf.py       signable PDF, built from the rendered agreement
  qr.py                  QR generation, redirect CSV
  palette.py             palette sampling from the logo
  postcard.py            print-ready postcard PDF/PNG
  shopify_pull.py        optional live collection lookup
  leads.py               discovery-call pipeline, from Shopify Forms
  discovery.py           the twelve-question discovery call and its notes
  docx_tools.py          run-aware docx text replacement
tools/
  build_templates.py     source_docs -> docx_templates
  seed_gcs.py            regression check against the hand-built GCS kit
source_docs/             your originals — the editable master copies
docx_templates/          generated merge templates
partners/                one JSON per partner (the real record)
leads/discovery/         one JSON per discovery call
discovery.json           what can be promised on a call (not pricing)
assets/<partner>/        their logo
output/<partner>/        everything generated for them
```

## Regression check

`tools/seed_gcs.py` rebuilds the GCS Cardinals package from the app and prints
the sampled palette. Compare it against the kit you built by hand — that's the
test that the templating didn't quietly break something.

Sampled vs. your hand-picked GCS palette:

| Role | Sampled | Yours |
|---|---|---|
| Primary | `A92932` | `CD3135` |
| Shadow | `76242B` | `8E2027` |
| Ink | `1B222C` | `1B222C` |
| Light | `F4F1EC` | `F4F1EC` |
| Muted | `87898C` | `8A9099` |

Three of five land on your exact values. The reds come out darker because
sampling averages the mark's antialiased edges — override them in the form when
you want the brighter red.

## Launch Week Kit theme

The kit is a Steeple & Stitch document, so its chrome uses the S&S brand
colours sampled from the logo:

| Role | Hex | Where |
|---|---|---|
| Navy | `102C48` | Section headings, table headers |
| Deep navy | `0D2742` | Dark panels and cover fills |
| Gold | `CB9C52` | Rules under headings, callout borders |

Navy carries the text because gold on chalk is too low-contrast to read at body
size; gold does the flourish work — the same division as the logo.

The **Launch Week Kit theme** field on the partner form switches this to
*Partner colours*, which uses their sampled palette instead. Either way the
partner's logo, their branded QR, the postcards and the "YOUR COLOR PALETTE"
table always show the partner's own colours — only the document chrome changes.

These colours live in XML attributes rather than run text, which is why the kit
stayed cardinal red through earlier merges. `tools/build_templates.py` rewrites
them as merge tags (`theme_accent`, `theme_rule`, `theme_ink`, …) when it builds
the template, and `onboarding/schema.py` resolves them at render.

## Partner logos

> Records store the logo as an absolute path. When that path goes stale — a
> different Mac, a moved folder — `qr.py` and `postcard.py` both simply test
> `Path(logo).exists()` and skip the branded artwork, so the package builds
> "successfully" with the partner's mark on none of it. `store.resolve_logo()`
> now falls back to the partner's own `assets/<id>/` folder before giving up.

Marks arrive in whatever state the partner has them, so two problems are handled
automatically before a logo is placed:

**White-boxed logos.** A mark flattened onto a white rectangle would paste that
rectangle onto the dark postcard, and a white square into the middle of the QR.
The background is flood-filled away from the four corners inward, so white
*inside* the mark — an eye, a highlight — survives. A blanket "make white
transparent" pass would punch holes in it.

**Dark logos on the dark card.** An all-black mark on the near-black panel is
invisible. Rather than recolouring someone's artwork, it goes on a rounded plate
that contrasts with it.

The test for "can this be seen" measures the *share* of the mark's pixels that
clear a contrast threshold, not the average colour. Averaging is wrong here: the
GCS cardinal is mostly near-black body with a red crest, so the mean says
"invisible" while the crest reads perfectly well. Measured, the cardinal puts
10% of its pixels above the threshold and an all-black silhouette puts 0%, so
the cut sits at 8% — the cardinal stays unplated, the silhouette gets a plate.

Both live in `onboarding/imaging.py`, shared by the postcards and the QR.

## The branded QR

Every partner's code carries their logo in the centre, on a white plate ringed in
their primary brand colour. The white plate is not decoration — it gives the
scanner a clean area instead of logo pixels bleeding into modules.

Error correction is fixed at H (30% recovery). The app starts with the logo at
26% of the code's width and, if the finished image doesn't decode, steps it down
(22%, 18%, 14%) until it does. The generate screen reports the size it settled on
and whether it decoded.

That automatic check needs OpenCV (`opencv-python-headless`, in requirements). If
it isn't installed the codes still generate at the default size — you just don't
get the machine-verified guarantee, so scan-test by hand.

Three files per partner:

| File | Use |
|---|---|
| `QR-Branded-Print` | The one to use. Logo centred, 1176px, 300 DPI. |
| `QR-Print-1176px` | Plain, no logo. For very small reproductions. |
| `QR-Vector` | SVG. For signage and large-format print. |

## The signable agreement PDF

Alongside the editable `.docx`, every package now carries
`..._Service-Agreement-SIGNABLE_v01.pdf`: ten fillable AcroForm fields and an
intent checkbox, ready to email.

It is built **from the rendered `.docx`**, not from a second copy of the clause
text. Change a term in `source_docs/`, rebuild the templates, regenerate — and
the two files cannot say different things. Built natively with ReportLab, so
there is no Word or LibreOffice anywhere on the launcher's path.

That applies to the **formatting** as well as the wording. Every paragraph is
rebuilt from the run-level formatting the `.docx` actually carries — font,
size, weight, colour, alignment, spacing, indents, paragraph borders — and the
centred logo and the watermark are read from the document's own drawing
extents, so they land at the size Word places them. Nothing in
`agreement_pdf.py` knows that the headings are Georgia navy or that the rule
above the signature block is gold; restyle `source_docs/`, rebuild, regenerate,
and the PDF follows on its own.

### Fonts

The `.docx` asks for Georgia and Calibri. The PDF looks for the real font
first, then a metric-compatible substitute (Gelasio or Tinos for Georgia,
Carlito for Calibri), then a base-14 face. A **substitution** changes the glyph
shapes slightly but not where a line breaks; a **fallback** does neither
faithfully. The generate screen reports what each family resolved to, and
`manifest.json` records it as `pdf_fonts`.

Georgia ships with macOS, so it resolves to the real font. Calibri only
arrives with Microsoft Office and was falling back to Helvetica — which is
metrically different, so the agreement PDF broke lines in different places
than the .docx. **Carlito is now bundled in `assets/fonts/`** (SIL Open Font
License, metric-compatible with Calibri), and that folder is searched before
the system font folders, so the result is pinned on any machine.

Run `tools/preflight.py` to see what each family resolved to.

| Prefilled from the record | Left blank for the partner |
|---|---|
| Printed name, title, agreement date (Company) | Both signature boxes |
| Printed name and title (Client) | Client's date |
| Billing contact email, target launch date | Intent checkbox |

> **It is a fillable PDF, not a signature service.** No audit trail, no
> tamper-evidence, no identity check. For anything that has to be enforceable,
> send the same document through Dropbox Sign or DocuSign.

## Nothing goes out half-filled

`default_record()` seeds fees at `0 / 0 / 10%`. Those are placeholders, but a
generated agreement prints them as real terms over a signature line and nothing
errors. So the generate screen no longer stays quiet about it: any partner with
a blank contact, a zero fee, a missing signer, no launch date or no logo gets a
red banner listing exactly what is unset, and the same list rides along in
`manifest.json` as `incomplete`.

Fill those in before the package leaves your machine.

## The welcome email

After generating a package, click **Welcome email**. The app merges the
partner record into the template, shows you the rendered result, and opens a
draft in Apple Mail with the recipient, subject, body and all four
customer-facing files already attached.

**It never sends.** A signed partner is a relationship moment; the last look
before it goes is the point.

### Where each field comes from

Five come off the partner record, four are yours, one is optional:

| From the partner record | From Settings |
|---|---|
| Organization name, store name, plan, client margin, contact first name | Your name, email, phone |

The mailing address in Settings is deliberately blank — the business runs out
of the house, and that address is not going on partner-facing email. The footer
line only appears if the field is filled, so a P.O. Box later needs no code
change.

`asset_upload_link` is optional. Leave it blank and the email asks partners to
reply with their files attached; fill it and the gold "Send your brand assets"
button comes back. No template edit either way.

> **Your details are not the partner's.** `poc_name` on a record is *their*
> contact. `point_of_contact_name` in the email is *you*, under "call or text
> directly during launch week". They are separate fields on purpose.

### Three voices, not one

The email uses the same `org_type` vocabulary the documents do. A church reads
"your congregation" and "your church"; a school reads "families" and "your
school"; a non-profit reads "supporters" and "your organization". One template.

### The guard

Nothing drafts while a merge field is empty, and the screen names what is
missing. A **0% giveback counts as missing** — it is the seed value, and it
would otherwise print as a real negotiated term above a signature line.

### The email describes its own attachments

A "What's Attached" section sits between the timeline and the homework list.
The Service Agreement gets a bordered panel of its own rather than a bullet,
because it is the one attachment that stops the clock: fillable PDF, type your
name in the signature box, tick the box underneath, save, reply with it
attached. The kit, postcards and QR code follow as bullets.

**That copy is built from what is actually attached.** A partner with no logo
gets no branded QR — and their email does not mention one. If nothing is
attached, the whole section disappears rather than leaving a heading over an
empty space. You never have to check whether the body matches the paperclips.

### Apple Mail, not Gmail

AppleScript needs no OAuth, no client secret and no token to expire. The cost
is one macOS Automation prompt the first time — approve it. If Mail is not
installed the button turns off and **Download .html** is the fallback.

## The Launch Week Kit PDF

The kit also comes out as `..._Launch-Week-Kit_v01.pdf` — the version to
email. It renders identically everywhere, opens on a phone, and cannot be
edited by accident on the way.

It is **converted**, not re-drawn. The kit is a designed document — ten
tables, a hundred-odd shaded cells, five images, gold rules, page breaks — and
hand-rendering that would drift from the .docx the first time the source
document is restyled. The Service Agreement PDF is hand-built only because it
has to carry fillable form fields, which no converter can add.

The converter is whatever the Mac already has:

| Engine | Notes |
|---|---|
| **LibreOffice** (preferred) | Headless, no dialogs, ~2s per kit. Runs against a throwaway profile so it cannot collide with a LibreOffice window you have open. |
| **Microsoft Word** | Driven by AppleScript. Perfect fidelity, but the first run raises a macOS Automation prompt, and a dialog open in Word will block it until the timeout. |

If neither is installed, no PDF is produced and the generate screen says so.
One command fixes it permanently:

```bash
brew install --cask libreoffice
```

Pages is deliberately **not** used. Its .docx import re-flows the layout, so it
would produce a PDF that quietly disagrees with the .docx beside it.

To give another document a PDF as well, add its label to the block in
`onboarding/package.py`. The Setup Checklist and Promo Templates are left as
.docx on purpose — one is meant to be filled in, the other copied from.

## The product photo (removed)

The kit used to carry a "PRODUCT PHOTO FOR YOUR POSTS" section. It was cut on
2026-09-05: it meant producing an extra asset for every partner that no
customer had asked for.

What that leaves behind, should the requests start coming in:

- `onboarding/product_shot.py` builds a sheet from a partner's own Shopify
  product photography, with a fallback order that can never reach another
  partner's merchandise.
- `tools/fetch_product_shots.py` downloads and caches those images.
- `product_shot_urls` is still on every partner record.

Putting it back is three steps: restore the section in
`source_docs/SRC_Launch_Week_Kit.docx` (a copy from before the removal is in
`source_docs/_backup/`), rebuild the templates, and call `product_shot.build`
again from the block in `onboarding/package.py` that says so.

> The reason it was there at all is worth remembering. The section originally
> showed the image baked into the template — the GCS Cardinals photo — in
> **every** partner's kit, captioned as their own launch lineup. If the
> section returns, it must never fall back to the template's image.

## Known limits

- **The Launch Week Kit gets to about 90%.** Merge fields, vocabulary and the
  palette are automatic. The mascot-voice lines — the ones with real personality
  — are generic by comparison. Read it once per partner before sending.
- **The agreement is a legal document.** Clause text is fixed in the template and
  stamped with a version (`AGREEMENT_TEMPLATE_VERSION` in `schema.py`). Bump it
  if counsel changes the terms, so you can tell which partners signed which
  version.
