"""Steeple & Stitch — partner onboarding app.

    python app.py        -> http://127.0.0.1:5000

Enter a partner once; generate the Service Agreement, Setup Checklist,
Launch Week Kit, Promo Templates, branded QR and print-ready postcards.
"""
from __future__ import annotations

import io
import os
from datetime import datetime
import webbrowser
from pathlib import Path
from threading import Thread

import hashlib
import hmac
import secrets as secrets_module
from urllib.parse import urlencode

import requests

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_file, session, url_for)

from onboarding import dashboard, discovery_reply, leads, mail_draft, package
from onboarding import secrets as env_secrets
from onboarding import settings as company_settings, shopify_sales, store
from onboarding import storefront, traveler
from onboarding import welcome_email
from onboarding.palette import extract_palette
from onboarding.schema import (DEFAULT_PALETTE, FIELDS, GROUPS, ORG_TYPES,
                               PALETTE_ROLES, derive, normalize, validate)
from onboarding.shopify_pull import configured as shopify_configured
from onboarding.shopify_pull import fetch_collection

ROOT = Path(__file__).resolve().parent

APP_VERSION = "3.7"   # shown in the header so you can tell a stale process at a glance

app = Flask(__name__)
app.secret_key = "steeple-stitch-local-only"
app.jinja_env.globals["APP_VERSION"] = APP_VERSION
app.jinja_env.globals.update(
    FIELDS=FIELDS, GROUPS=GROUPS, ORG_TYPES=ORG_TYPES, PALETTE_ROLES=PALETTE_ROLES
)


def _form_to_record(form, files, existing: dict | None = None) -> dict:
    record = dict(existing or store.new_record())
    normalized_notes: list[str] = []
    for field in FIELDS:
        if field.kind == "file":
            continue
        if field.key in form:
            record[field.key] = form.get(field.key, "").strip()

    palette = []
    for index in range(5):
        palette.append({
            "name": form.get(f"palette_name_{index}", "").strip() or DEFAULT_PALETTE[index]["name"],
            "hex": form.get(f"palette_hex_{index}", "").strip().lstrip("#").upper()
                   or DEFAULT_PALETTE[index]["hex"],
            "role": PALETTE_ROLES[index][1],
        })
    record["palette"] = palette

    normalized_notes.extend(normalize(record))
    record["_normalized"] = normalized_notes

    pid = store.partner_id(record)
    upload = files.get("logo_file") if files else None
    if upload and upload.filename:
        dest = store.asset_dir(pid) / Path(upload.filename).name
        upload.save(dest)
        record["logo_path"] = str(dest)
    return record


@app.route("/")
def index():
    partners = store.list_partners()
    return render_template("index.html", partners=partners,
                           shopify_ready=shopify_configured())


@app.route("/partner/new", methods=["GET", "POST"])
def new_partner():
    if request.method == "POST":
        record = _form_to_record(request.form, request.files)
        problems = validate(record)
        if problems:
            for problem in problems:
                flash(problem, "error")
            return render_template("form.html", record=record, ctx=derive(record),
                                   is_new=True, shopify_ready=shopify_configured())
        for note in record.get("_normalized") or []:
            flash(f"Tidied for the URL — {note}", "ok")
        record.pop("_normalized", None)
        record = store.save(record)
        flash(f"Saved {record['org_name']}.", "ok")
        return redirect(url_for("edit_partner", pid=record["id"]))
    record = store.new_record()
    return render_template("form.html", record=record, ctx=derive(record),
                           is_new=True, shopify_ready=shopify_configured())


@app.route("/partner/<pid>", methods=["GET", "POST"])
def edit_partner(pid):
    existing = store.load(pid)
    if existing is None:
        abort(404)
    if request.method == "POST":
        record = _form_to_record(request.form, request.files, existing)
        problems = validate(record)
        if problems:
            for problem in problems:
                flash(problem, "error")
        else:
            action = request.form.get("action")
            if action == "save_as_new":
                record = store.save_as_new(record)
                flash(
                    f"Created {record['org_name']} as a separate partner. "
                    f"{existing['org_name']} is unchanged.", "ok"
                )
                return redirect(url_for("edit_partner", pid=record["id"]))

            for note in record.get("_normalized") or []:
                flash(f"Tidied for the URL — {note}", "ok")
            record.pop("_normalized", None)
            renamed = store.partner_id(record) != pid
            record = store.save(record)
            if renamed:
                flash(
                    f"Renamed to {record['org_name']}. Its record, logo and "
                    f"output folder moved with it.", "ok"
                )
            else:
                flash("Saved.", "ok")
            if action == "generate":
                return redirect(url_for("generate", pid=record["id"]))
        return render_template("form.html", record=record, ctx=derive(record),
                               is_new=False, shopify_ready=shopify_configured())
    return render_template("form.html", record=existing, ctx=derive(existing),
                           is_new=False, shopify_ready=shopify_configured())


@app.route("/partner/<pid>/generate")
def generate(pid):
    record = store.load(pid)
    if record is None:
        abort(404)
    manifest = package.build(record)
    return render_template("generated.html", manifest=manifest, record=record)


@app.route("/partner/<pid>/palette", methods=["POST"])
def sample_palette(pid):
    record = store.load(pid) or store.new_record()
    logo = store.resolve_logo(record)
    if not logo:
        return jsonify({"ok": False, "error": "Upload and save a logo first."})
    palette = extract_palette(logo)
    record["palette"] = palette
    if record.get("id"):
        store.save(record)
    return jsonify({"ok": True, "palette": palette})


@app.route("/shopify/collection")
def shopify_collection():
    handle = request.args.get("handle", "").strip()
    if not handle:
        return jsonify({"ok": False, "error": "No handle supplied"})
    return jsonify(fetch_collection(handle))


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    """Your own details — the half of the welcome email that never changes."""
    if request.method == "POST":
        company_settings.save(request.form.to_dict())
        flash("Settings saved.", "ok")
        return redirect(url_for("settings_page"))
    return render_template(
        "settings.html",
        values=company_settings.load(),
        fields=company_settings.FIELDS,
        optional=company_settings.OPTIONAL,
        missing=company_settings.missing(),
        shopify_store=env_secrets.store_domain(),
        shopify_token=env_secrets.masked(),
        client_id=env_secrets.app_credentials()["client_id"],
        client_secret=env_secrets.masked_secret(),
    )


@app.route("/partner/<pid>/email")
def partner_email(pid):
    record = store.load(pid)
    if record is None:
        abort(404)
    return render_template(
        "email.html",
        record=record,
        email=welcome_email.render(record),
        mail_available=mail_draft.available(),
    )


@app.route("/partner/<pid>/email/preview")
def partner_email_preview(pid):
    """The rendered HTML on its own, for the preview frame and for download."""
    record = store.load(pid)
    if record is None:
        abort(404)
    html = welcome_email.render(record)["html"]
    if request.args.get("download"):
        name = f"{store.name_token(record)}_Welcome-Email.html"
        buffer = io.BytesIO(html.encode("utf8"))
        return send_file(buffer, as_attachment=True, download_name=name,
                         mimetype="text/html")
    return html


@app.route("/partner/<pid>/email/draft", methods=["POST"])
def partner_email_draft(pid):
    record = store.load(pid)
    if record is None:
        abort(404)
    email = welcome_email.render(record)

    # The guard is the whole point: never hand Mail a draft with a merge field
    # still empty, because at that stage the next click sends it.
    if email["missing"]:
        flash("Not drafted — still unset: " + ", ".join(email["missing"]), "warn")
        return redirect(url_for("partner_email", pid=pid))

    result = mail_draft.create_draft(
        subject=email["subject"],
        recipient=email["to"],
        html=email["html"],
        attachments=[a["path"] for a in email["attachments"]],
    )
    if result["ok"]:
        flash(f"Draft open in Mail with {len(email['attachments'])} attachment(s). "
              "Read it, then send.", "ok")
    else:
        flash("Could not open a Mail draft: " + result["error"], "warn")
    return redirect(url_for("partner_email", pid=pid))


@app.route("/download")
def download():
    path = Path(request.args.get("path", ""))
    if not path.exists() or not path.is_file():
        abort(404)
    if store.OUTPUT.resolve() not in path.resolve().parents:
        abort(403)
    return send_file(path, as_attachment=True)


@app.route("/redirects")
def redirects():
    return send_file(package.build_all_redirects(), as_attachment=True)


@app.route("/partner/<pid>/delete", methods=["POST"])
def delete_partner(pid):
    store.delete(pid)
    flash("Partner removed. Generated files were left in place.", "ok")
    return redirect(url_for("index"))




@app.route("/settings/shopify", methods=["POST"])
def save_shopify():
    ok, message = env_secrets.save_token(
        request.form.get("shopify_token", ""),
        request.form.get("shopify_store", ""),
    )
    flash(message, "ok" if ok else "error")
    # The token is read out of os.environ, which _load_env() only ever fills
    # with setdefault — so a value cached from an earlier, empty .env would
    # survive this save and the very next refresh would still fail. Clear the
    # two keys so the new file is what gets read.
    for key in ("SHOPIFY_STORE", "SHOPIFY_ADMIN_TOKEN"):
        os.environ.pop(key, None)
    return redirect(url_for("settings_page"))

# ------------------------------------------------------------------ pipeline

def _pipeline_context(refresh: bool = False) -> dict:
    rows, source = leads.list_leads(refresh=refresh)
    snap = leads.load_cache() or {}
    return {
        "rows": rows,
        "stats": leads.summary(rows),
        "stages": leads.STAGES,
        "source": source,
        "pulled_at": (snap.get("pulled_at") or "").replace("T", " ").replace("+00:00", " UTC"),
        "error": "" if rows or source in ("live", "cache") else source,
    }


@app.route("/pipeline")
def pipeline_page():
    refresh = request.args.get("refresh") == "1"
    context = _pipeline_context(refresh)
    if refresh:
        flash(f"Leads: {context['source']}.",
              "ok" if context["source"] == "live" else "error")
    return render_template("pipeline.html", **context)


@app.route("/pipeline/<key>/stage", methods=["POST"])
def pipeline_stage(key):
    lead = leads.find_lead(key)
    if not lead:
        flash("That lead is no longer in the pipeline.", "error")
        return redirect(url_for("pipeline_page"))
    stage = request.form.get("stage", "")
    if leads.set_stage(lead["id"], stage):
        flash(f"{lead.get('org_name') or lead['name']} → {stage}.", "ok")
    else:
        flash(f"{stage!r} is not a stage.", "error")
    return redirect(url_for("pipeline_page"))


@app.route("/pipeline/<key>/details", methods=["POST"])
def pipeline_details(key):
    lead = leads.find_lead(key)
    if not lead:
        flash("That lead is no longer in the pipeline.", "error")
        return redirect(url_for("pipeline_page"))
    leads.set_fields(lead["id"],
                     request.form.get("org_name", ""),
                     request.form.get("org_size", ""),
                     request.form.get("org_type", ""))
    flash("Saved.", "ok")
    return redirect(url_for("pipeline_page"))


@app.route("/pipeline/<key>/snooze", methods=["POST"])
def pipeline_snooze(key):
    lead = leads.find_lead(key)
    if not lead:
        flash("That lead is no longer in the pipeline.", "error")
        return redirect(url_for("pipeline_page"))
    ok, message = leads.set_snooze(lead["id"],
                                   request.form.get("snoozed_until", ""),
                                   request.form.get("snooze_reason", ""))
    flash(message, "ok" if ok else "error")
    return redirect(url_for("pipeline_page"))


@app.route("/pipeline/<key>/reply", methods=["POST"])
def pipeline_reply(key):
    """Open the discovery-call reply as a Mail draft, then mark it Replied.

    A draft and never a send, same as the welcome email: the last look before
    it goes out is the point. The stage only moves once Mail has actually
    accepted the draft -- marking a lead Replied because a button was pressed,
    when Automation permission was denied and no window opened, would be the
    dashboard lying in the one direction that matters.
    """
    lead = leads.find_lead(key)
    if not lead:
        flash("That lead is no longer in the pipeline.", "error")
        return redirect(url_for("pipeline_page"))

    if not lead.get("email"):
        flash("That lead has no email address on its contact, so there is "
              "nobody to write to.", "error")
        return redirect(url_for("pipeline_page"))

    draft = discovery_reply.render(lead)
    if draft["missing"]:
        flash("Not drafted — these are still empty: "
              + ", ".join(draft["missing"]) + ".", "error")
        return redirect(url_for("pipeline_page"))

    result = mail_draft.create_draft(draft["subject"], draft["to"], draft["html"])
    if not result["ok"]:
        flash(result["error"], "error")
        return redirect(url_for("pipeline_page"))

    leads.set_stage(lead["id"], "Replied")
    flash(f"Draft open in Mail for {draft['to_name'] or draft['to']}. "
          f"{lead.get('org_name') or 'Lead'} → Replied.", "ok")
    return redirect(url_for("pipeline_page"))


@app.route("/pipeline/<key>/reply/preview")
def pipeline_reply_preview(key):
    """The rendered email in the browser, for reading it before Mail opens."""
    lead = leads.find_lead(key)
    if not lead:
        flash("That lead is no longer in the pipeline.", "error")
        return redirect(url_for("pipeline_page"))
    draft = discovery_reply.render(lead)
    if draft["missing"]:
        flash("Cannot preview — these are still empty: "
              + ", ".join(draft["missing"]) + ".", "error")
        return redirect(url_for("pipeline_page"))
    return draft["html"]


@app.route("/pipeline/<key>/promote", methods=["POST"])
def pipeline_promote(key):
    lead = leads.find_lead(key)
    if not lead:
        flash("That lead is no longer in the pipeline.", "error")
        return redirect(url_for("pipeline_page"))
    record, message = leads.promote(lead)
    if record is None:
        flash(message, "error")
        return redirect(url_for("pipeline_page"))
    flash(message, "ok")
    # Straight to the partner form, because the record is deliberately
    # incomplete: fees, margin and signers are blank until the call fills them.
    return redirect(url_for("edit_partner", pid=store.partner_id(record)))

# ------------------------------------------------------------------ dashboard

DASHBOARD_DIR = Path(__file__).resolve().parent / "output" / "Dashboard"
DASHBOARD_FILE = DASHBOARD_DIR / "Steeple-Stitch-Dashboard.html"


def _dashboard_context(refresh: bool = False) -> dict:
    snap, source = shopify_sales.snapshot(refresh=refresh)
    records = store.list_partners()
    if snap is None:
        return {"data": None, "source": source, "pulled_at": "",
                "configured": shopify_sales.configured(),
                "scopes": shopify_sales.SCOPES,
                "ready_to_connect": False}
    return {
        "data": dashboard.rollup(snap, records),
        "source": source,
        "pulled_at": (snap.get("pulled_at") or "").replace("T", " ").replace("+00:00", " UTC"),
        "configured": shopify_sales.configured(),
        "scopes": shopify_sales.SCOPES,
        "ready_to_connect": all(env_secrets.app_credentials().values())
                            and bool(env_secrets.store_domain()),
    }


@app.route("/dashboard")
def dashboard_page():
    refresh = request.args.get("refresh") == "1"
    context = _dashboard_context(refresh)
    if context["data"] is None:
        flash(context["source"], "error")
        return redirect(url_for("index"))
    if refresh:
        flash(f"Shopify data: {context['source']}.",
              "ok" if context["source"] == "live" else "error")
    context["snapshot_path"] = (
        DASHBOARD_FILE.name if DASHBOARD_FILE.exists() else "")
    return render_template("dashboard.html", **context)


@app.route("/dashboard/snapshot")
def dashboard_snapshot():
    """Write a standalone copy into iCloud, for the iPad and the phone.

    Deliberately a stable filename rather than the dated convention the
    generated documents use. This one gets bookmarked on a Home Screen, and a
    new name every day would break the bookmark every day. The date it was
    taken is stamped inside the page instead, where it cannot drift from the
    figures it describes.
    """
    context = _dashboard_context(refresh=False)
    if context["data"] is None:
        flash(context["source"], "error")
        return redirect(url_for("index"))

    taken = datetime.now().strftime("%A %d %B %Y, %-I:%M %p")
    html = render_template("dashboard_snapshot.html", taken=taken, **context)
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    DASHBOARD_FILE.write_text(html, encoding="utf8")
    flash(f"Snapshot saved to output/Dashboard/{DASHBOARD_FILE.name} — "
          "it syncs to your iPad and phone through iCloud.", "ok")
    return redirect(url_for("dashboard_page"))

PORT = 5000
URL = f"http://127.0.0.1:{PORT}"


# ------------------------------------------------------------ Shopify OAuth
#
# Newer Shopify stores have no legacy custom apps, so there is no static
# `shpat_` token to copy: a Dev Dashboard app mints its token during install
# and POSTs it to the app's redirect URL, once. The default redirect is the
# `https://example.com` placeholder, so the token is delivered to example.com
# and lost — while the install itself succeeds, which is why it looks like it
# worked. These two routes make the app its own redirect, so the token lands
# in .env instead.

# write_products covers collections; write_publications the sales channels;
# write_url_redirects the /go/ slugs. Adding a scope requires RECONNECTING from
# Settings -- an existing token keeps the scopes it was minted with, and the
# refusal arrives as a 403 that reads like a bad store domain.
SHOPIFY_SCOPES = ("read_orders,read_products,read_inventory,read_customers,"
                  "read_companies,write_products,read_publications,"
                  "write_publications,write_content,"
                  "write_online_store_navigation")


def _redirect_uri() -> str:
    """Built from the host actually being browsed, not a hardcoded name.

    macOS AirPlay Receiver listens on port 5000 and answers with HTTP 403.
    `localhost` resolves to ::1 first, where AirPlay is; Flask binds 127.0.0.1.
    So a hardcoded localhost redirect handed Shopify's callback -- code, hmac
    and all -- to AirPlay, which refused it. The app was running fine the whole
    time, one address over.

    Deriving it from request.host_url means the callback returns to whichever
    address the browser is already on, and it keeps working over Tailscale.
    Both spellings are registered in the app's Allowed redirection URLs.
    """
    host = request.host_url.rstrip("/")
    return f"{host}/shopify/callback"


def _valid_hmac(params: dict, secret: str) -> bool:
    """Shopify signs the callback. Unverified, anyone who can reach this port
    could hand us a code and have us exchange it — so this is not optional."""
    received = params.get("hmac", "")
    message = "&".join(
        f"{key}={params[key]}" for key in sorted(params) if key != "hmac")
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, received)


@app.route("/shopify/connect")
def shopify_connect():
    creds = env_secrets.app_credentials()
    store = env_secrets.store_domain()
    if not creds["client_id"] or not creds["client_secret"]:
        flash("Add the Client ID and Client secret first.", "error")
        return redirect(url_for("settings_page"))
    if not store:
        flash("Set the store domain first.", "error")
        return redirect(url_for("settings_page"))

    state = secrets_module.token_urlsafe(24)
    session["shopify_state"] = state
    query = urlencode({
        "client_id": creds["client_id"],
        "scope": SHOPIFY_SCOPES,
        "redirect_uri": _redirect_uri(),
        "state": state,
    })
    return redirect(f"https://{store}/admin/oauth/authorize?{query}")


@app.route("/shopify/callback")
def shopify_callback():
    creds = env_secrets.app_credentials()
    params = request.args.to_dict()

    expected = session.pop("shopify_state", None)
    if not expected or params.get("state") != expected:
        flash("That Shopify response did not match the request this app "
              "started. Nothing was saved — start again from Settings.", "error")
        return redirect(url_for("settings_page"))

    if not _valid_hmac(params, creds["client_secret"]):
        flash("Shopify's signature on that response did not verify. Nothing "
              "was saved.", "error")
        return redirect(url_for("settings_page"))

    shop = params.get("shop", "")
    if shop != env_secrets.store_domain():
        flash(f"That response came from {shop}, not the store configured here. "
              "Nothing was saved.", "error")
        return redirect(url_for("settings_page"))

    try:
        response = requests.post(
            f"https://{shop}/admin/oauth/access_token",
            json={
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "code": params.get("code", ""),
            },
            timeout=20,
        )
        response.raise_for_status()
        token = response.json().get("access_token", "")
    except Exception as exc:
        flash(f"Could not exchange the code for a token: {exc}", "error")
        return redirect(url_for("settings_page"))

    if not token:
        flash("Shopify returned no access token.", "error")
        return redirect(url_for("settings_page"))

    ok, message = env_secrets.save_token(token)
    if not ok:
        flash(message, "error")
        return redirect(url_for("settings_page"))

    # _load_env() fills os.environ with setdefault, so a value read from the
    # previously-empty .env would survive and the next refresh would still
    # fail with the old (missing) token.
    for key in ("SHOPIFY_STORE", "SHOPIFY_ADMIN_TOKEN"):
        os.environ.pop(key, None)

    flash("Connected to Shopify. Refreshing the dashboard now.", "ok")
    return redirect(url_for("dashboard_page", refresh=1))


@app.route("/settings/shopify-app", methods=["POST"])
def save_shopify_app():
    ok, message = env_secrets.save_app_credentials(
        request.form.get("client_id", ""),
        request.form.get("client_secret", ""),
    )
    flash(message, "ok" if ok else "error")
    return redirect(url_for("settings_page"))


# ----------------------------------------------------------------- storefront
# Stage 10 of the lead-to-live-store path: the smart collection, its vendor
# rule, the sales channels and the /go/ redirect, in one button. See
# onboarding/storefront.py for why the rule must go on at creation.


@app.route("/partner/<pid>/storefront")
def partner_storefront(pid):
    record = store.load(pid)
    if record is None:
        abort(404)
    check = storefront.preflight(record)
    return render_template("storefront.html", pid=pid, record=record,
                           plan=check["plan"], blocking=check["blocking"],
                           warnings=check["warnings"])


@app.route("/partner/<pid>/storefront/create", methods=["POST"])
def partner_storefront_create(pid):
    record = store.load(pid)
    if record is None:
        abort(404)
    # Unchecked means create it hidden. A partner storefront published at
    # stage 10 is live two weeks before launch day, which is not what the
    # welcome email tells the partner to expect.
    result = storefront.create(record, publish=bool(request.form.get("publish")))
    if not result["ok"]:
        flash(f"Nothing was created — {result['error']}", "error")
    else:
        for step in result["steps"]:
            flash(f"{step['name']}: {step['detail']}", "ok" if step["ok"] else "error")
    return redirect(url_for("partner_storefront", pid=pid))


# ------------------------------------------------------------------ traveler
# The shop checklist for adding a POD/in-house product pair. Runs are stored
# server-side rather than in the browser: this app gets opened from an iPad
# over Tailscale as often as from the Mac, and a half-finished traveler that
# only exists on one device is worse than no traveler at all.


def _traveler_context(run: dict) -> dict:
    done, total = traveler.progress(run)
    numbers = {key: i + 1 for i, key in enumerate(traveler.STEP_KEYS)}
    return {
        "run": run,
        "phases": traveler.PHASES,
        "loop_index_map": numbers,
        "orgs": traveler.orgs(),
        "org": traveler.org_by_key(run.get("org_key", "")),
        "platforms": traveler.POD_PLATFORMS,
        "cmds": traveler.commands(run),
        "flags": traveler.CLONE_FLAGS,
        "ladder": traveler.PRICE_LADDER,
        "metafields": traveler.PINNED_METAFIELDS,
        "shots": traveler.shots_present(),
        "done": done,
        "total": total,
        "pct": round(done / total * 100) if total else 0,
    }


@app.route("/traveler")
def traveler_index():
    rows = []
    for run in traveler.list_runs():
        done, total = traveler.progress(run)
        org = traveler.org_by_key(run.get("org_key", ""))
        rows.append({
            "run": run, "done": done, "total": total,
            "pct": round(done / total * 100) if total else 0,
            "vendor": org["vendor"] if org else "",
        })
    shot_hints = [
        (step["shot"], step.get("shot_hint", ""))
        for phase in traveler.PHASES for step in phase["steps"] if step.get("shot")
    ]
    return render_template("traveler.html", runs=rows, orgs=traveler.orgs(),
                           platforms=traveler.POD_PLATFORMS,
                           shots=traveler.shots_present(), shot_hints=shot_hints)


@app.route("/traveler/new", methods=["POST"])
def traveler_new():
    run = traveler.new_run(
        title=request.form.get("title", ""),
        org_key=request.form.get("org_key", ""),
        platform=request.form.get("platform", ""),
    )
    traveler.save(run)
    return redirect(url_for("traveler_run", run_id=run["id"]))


@app.route("/traveler/<run_id>")
def traveler_run(run_id):
    run = traveler.load(run_id)
    if run is None:
        abort(404)
    return render_template("traveler_run.html", **_traveler_context(run))


@app.route("/traveler/<run_id>/save", methods=["POST"])
def traveler_save(run_id):
    run = traveler.load(run_id)
    if run is None:
        abort(404)
    for field in ("title", "org_key", "platform", "product_id", "cost", "notes"):
        run[field] = request.form.get(field, "").strip()
    traveler.save(run)
    flash("Run details saved.", "ok")
    return redirect(url_for("traveler_run", run_id=run_id))


@app.route("/traveler/<run_id>/step", methods=["POST"])
def traveler_step(run_id):
    run = traveler.load(run_id)
    if run is None:
        abort(404)
    payload = request.get_json(silent=True) or {}
    step = payload.get("step")
    if step not in traveler.STEP_KEYS:
        abort(400)
    run.setdefault("steps", {})[step] = bool(payload.get("done"))
    traveler.save(run)
    done, total = traveler.progress(run)
    return jsonify({"ok": True, "done": done, "total": total})


@app.route("/traveler/<run_id>/shot/<key>", methods=["POST"])
def traveler_shot_upload(run_id, key):
    run = traveler.load(run_id)
    if run is None:
        abort(404)
    upload = request.files.get("shot")
    if not upload or not upload.filename:
        flash("No image chosen.", "error")
    elif traveler.save_shot(key, upload.filename, upload.read()) is None:
        flash("That file type isn't accepted — use PNG, JPG, WEBP or GIF.", "error")
    else:
        flash(f"Screenshot saved for “{key.replace('-', ' ')}”. It shows on every run.", "ok")
    return redirect(url_for("traveler_run", run_id=run_id) + f"#step-{key}")


@app.route("/traveler/shot/<key>")
def traveler_shot(key):
    path = traveler.shot_path(key)
    if path is None:
        abort(404)
    return send_file(path)


@app.route("/traveler/<run_id>/delete", methods=["POST"])
def traveler_delete(run_id):
    traveler.delete(run_id)
    flash("Run deleted. The products themselves were not touched.", "ok")
    return redirect(url_for("traveler_index"))


# ---------------------------------------------------------------- reachability
#
# By default Flask binds to 127.0.0.1, so the app is reachable only from this
# Mac. That is the right default and it stays the default, because there is no
# login on this app: anything that can reach the port can read every partner
# record and every negotiated margin on file.
#
# To use it from the iPad or the phone, set SS_HOST:
#
#   SS_HOST=tailscale   bind to this Mac's Tailscale address only. Reachable
#                       from Larry's own devices anywhere in the world, and
#                       invisible to whatever coffee-shop network the Mac is
#                       sitting on. This is the one to use.
#   SS_HOST=lan         bind to every interface. Anyone on the same Wi-Fi can
#                       open it, no password. Home network only, and even then
#                       prefer tailscale.
#   SS_HOST=<address>   bind to a specific address.
#
# Tailscale hands every device a stable 100.64.0.0/10 address (CGNAT range) and
# will not route anything from outside the tailnet to it, so binding there is
# the whole access-control story -- no port forwarding, no firewall rule, and
# nothing exposed if the Mac joins an untrusted network.
TAILSCALE_NET = "100."


def _tailscale_address() -> str | None:
    """This Mac's Tailscale IPv4, or None if Tailscale is not up."""
    import shutil
    import subprocess

    for binary in ("tailscale",
                   "/usr/local/bin/tailscale",
                   "/opt/homebrew/bin/tailscale",
                   "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        found = shutil.which(binary) if "/" not in binary else (
            binary if Path(binary).exists() else None)
        if not found:
            continue
        try:
            out = subprocess.run([found, "ip", "-4"], capture_output=True,
                                 text=True, timeout=10).stdout.strip()
        except Exception:
            continue
        first = out.splitlines()[0].strip() if out else ""
        if first.startswith(TAILSCALE_NET):
            return first

    # Tailscale.app installed from the Mac App Store does not put a CLI on the
    # PATH, so fall back to reading the interface list directly.
    try:
        import socket
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            address = info[4][0]
            if address.startswith(TAILSCALE_NET):
                return address
    except Exception:
        pass
    return None


def resolve_host() -> tuple[str, list[str], str]:
    """(bind address, URLs to show, warning). Never raises."""
    choice = (os.environ.get("SS_HOST") or "").strip().lower()

    if choice in ("", "local", "localhost", "127.0.0.1"):
        return "127.0.0.1", [URL], ""

    if choice in ("tailscale", "ts"):
        address = _tailscale_address()
        if address:
            return address, [f"http://{address}:{PORT}"], ""
        return "127.0.0.1", [URL], (
            "SS_HOST=tailscale, but no Tailscale address was found on this "
            "Mac. Staying on localhost. Open the Tailscale app and make sure "
            "it is connected, then start this again.")

    if choice in ("lan", "all", "0.0.0.0"):
        urls = [URL]
        address = _lan_address()
        if address:
            urls.append(f"http://{address}:{PORT}")
        return "0.0.0.0", urls, (
            "SS_HOST=lan — every device on this network can open the app, and "
            "there is no password on it. Partner records and margins are "
            "readable by anyone here. Use SS_HOST=tailscale instead unless "
            "this is your own network.")

    # An explicit address. Trust it, but say what happened.
    return choice, [f"http://{choice}:{PORT}"], ""


def _lan_address() -> str | None:
    """This Mac's address on the local network, without needing a route."""
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Never actually sends a packet; it just asks the OS which interface
        # would be used, which is the only reliable way to get the address
        # that other devices on this network can reach.
        probe.connect(("192.0.2.1", 9))   # TEST-NET-1, guaranteed unroutable
        return probe.getsockname()[0]
    except Exception:
        return None
    finally:
        probe.close()


def _open_when_ready(host: str = "127.0.0.1", url: str = URL,
                     timeout: float = 60.0) -> None:
    """Open the browser once the server is actually accepting connections.

    A fixed delay was unreliable: with the reloader the server runs in a child
    process, and importing numpy, Pillow and docx can easily take longer than
    the delay -- so the browser opened on a refused connection, or not at all.
    """
    import socket
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            probe = "127.0.0.1" if host == "0.0.0.0" else host
            with socket.create_connection((probe, PORT), timeout=0.5):
                _launch_browser(url)
                return
        except OSError:
            time.sleep(0.25)
    print(f"  Server did not come up in {timeout:.0f}s — open {url} yourself.")


def _launch_browser(url: str = URL) -> None:
    """Open the default browser, falling back to the macOS `open` command.

    Python's webbrowser module can silently do nothing from inside a venv
    depending on how the process was started. macOS `open` always honours the
    user's default browser, so it is tried as well rather than assuming the
    first attempt worked.
    """
    import shutil
    import subprocess
    import sys

    opened = False
    try:
        opened = bool(webbrowser.open(url))
    except Exception:
        opened = False

    if not opened and sys.platform == "darwin" and shutil.which("open"):
        try:
            subprocess.run(["open", url], check=True, timeout=10)
            opened = True
        except Exception:
            pass

    if not opened:
        print(f"  Could not open a browser automatically — go to {url}")


if __name__ == "__main__":
    # The reloader restarts the server whenever a .py file changes. Without it
    # Python keeps the modules it imported at startup, so an updated app keeps
    # producing the old output until you quit and relaunch.
    is_reload = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    host, urls, warning = resolve_host()
    if not is_reload:
        # Only the launching process opens a window, so a reload does not keep
        # spawning new browser tabs while you work.
        Thread(target=_open_when_ready,
               args=(host, urls[-1]), daemon=True).start()
        print(f"\n  Steeple & Stitch onboarding v{APP_VERSION}")
        for url in urls:
            label = "on this Mac" if "127.0.0.1" in url else "from your other devices"
            print(f"    {url}   {label}")
        if warning:
            print(f"\n  !  {warning}")
        print()
    app.run(host=host, port=PORT, debug=False, use_reloader=True)
