"""Read and write the Shopify token in .env, from the Settings screen.

`.env` is a dotfile, so Finder hides it and pasting a token into it means
either fighting Cmd-Shift-period or opening Terminal — where the token then
sits in shell history. Neither is a reasonable ask for a one-time paste, and
both are the kind of friction that leaves the dashboard running on a stale
cache for a month.

So the token goes in through a form field in the app instead. It is written to
`.env` (already in .gitignore, never syncs anywhere), and it is never rendered
back: once set, the screen shows only the last four characters, so a shoulder
or a screen-share cannot read it off the page. To replace it you paste a new
one; there is no "reveal".
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

# Shopify Admin API access tokens for custom apps. Checked before saving so a
# mis-paste (the API *key*, a truncated copy, a whole curl command) is caught
# here rather than surfacing later as an auth failure that reads like a wrong
# store domain.
TOKEN_PATTERN = re.compile(r"^shpat_[0-9a-fA-F]{32}$")

HEADER = """# Shopify credentials. This file is in .gitignore and never leaves the Mac.
#
# SHOPIFY_STORE is the .myshopify.com domain — the generated handle, not your
# custom domain. Guessing it is the most common way this fails: you get a 401
# that reads exactly like a bad token.
#
# SHOPIFY_ADMIN_TOKEN is written by the app after an OAuth install — it is not
# something you paste. Newer stores have no legacy custom apps, so Shopify
# never shows you a static token; it mints one during install and posts it to
# the app's redirect URL. Reconnect from Settings if it needs replacing.
#
# Scopes: read_orders, read_products, read_inventory
# Miss read_inventory and every cost comes back blank, which looks identical
# to a store where nobody entered costs.
"""


# The Williams Collective's own Shopify store (doc 73): the same keys in a
# file of its own, so the S&S token and the TWC token can never be confused,
# and so ssorder can point a second `stores[]` entry straight at it.
TWC_ENV_PATH = ROOT / ".env.twc"


def read_env(path: Path | None = None) -> dict:
    path = path or ENV_PATH
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(values: dict, path: Path | None = None) -> None:
    """Rewrite .env from scratch, keeping any keys already present.

    Deliberately not an in-place edit: a half-written .env is an app that will
    not start, and the file is four lines long.
    """
    path = path or ENV_PATH
    lines = [HEADER]
    for key in sorted(values):
        lines.append(f"{key}={values[key]}")
    path.write_text("\n".join(lines) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def masked(path: Path | None = None) -> str:
    """'shpat_…a1b2', or '' when unset. Never the whole token."""
    token = read_env(path).get("SHOPIFY_ADMIN_TOKEN", "")
    if not token:
        return ""
    return f"shpat_…{token[-4:]}"


def store_domain(path: Path | None = None) -> str:
    return read_env(path).get("SHOPIFY_STORE", "")


def save_token(token: str, store: str = "", path: Path | None = None) -> tuple[bool, str]:
    """(ok, message). Validates before writing."""
    token = (token or "").strip().strip('"').strip("'")
    store = (store or "").strip().lower()

    values = read_env(path)
    if store:
        store = store.replace("https://", "").replace("http://", "").strip("/")
        if not store.endswith(".myshopify.com"):
            return False, (
                f"{store!r} is not a .myshopify.com domain. Use the generated "
                "handle from your admin URL, not your custom domain.")
        values["SHOPIFY_STORE"] = store

    if token:
        if token.startswith("shpat_") and not TOKEN_PATTERN.match(token):
            return False, ("That looks like a token but is the wrong length — "
                           "check the whole value was copied.")
        if not token.startswith("shpat_"):
            return False, ("An Admin API access token starts with 'shpat_'. "
                           "The API key and secret shown next to it are a "
                           "different thing and will not work here.")
        values["SHOPIFY_ADMIN_TOKEN"] = token

    values.setdefault("SHOPIFY_API_VERSION", "2025-07")
    values.setdefault("SHOPIFY_STORE", "")
    write_env(values, path)
    return True, "Shopify credentials saved."


def clear_token() -> None:
    values = read_env()
    values["SHOPIFY_ADMIN_TOKEN"] = ""
    write_env(values)


# ---------------------------------------------------------------- OAuth app --
#
# Shopify removed legacy custom apps (the ones that showed you a static
# `shpat_` token) from newer stores. A Dev Dashboard app is an OAuth app: the
# access token is minted during install and POSTed to the app's redirect URL,
# once. If that URL is the `https://example.com` placeholder — which is the
# default — the token is delivered to example.com and is simply gone. The
# install still succeeds, which is what makes it confusing.
#
# So the app performs the OAuth exchange itself, against a redirect it owns.
# The client ID and secret identify the app; the access token they buy is
# written straight to .env and never displayed.

def app_credentials(path: Path | None = None) -> dict:
    values = read_env(path)
    return {
        "client_id": values.get("SHOPIFY_CLIENT_ID", ""),
        "client_secret": values.get("SHOPIFY_CLIENT_SECRET", ""),
    }


def masked_secret(path: Path | None = None) -> str:
    secret = read_env(path).get("SHOPIFY_CLIENT_SECRET", "")
    return f"…{secret[-4:]}" if secret else ""


def save_app_credentials(client_id: str, client_secret: str, path: Path | None = None,
                         store: str = "", extra: dict | None = None) -> tuple[bool, str]:
    client_id = (client_id or "").strip()
    client_secret = (client_secret or "").strip()
    values = read_env(path)
    store = (store or "").strip().lower().replace("https://", "").replace("http://", "").strip("/")
    if store:
        if not store.endswith(".myshopify.com"):
            return False, (f"{store!r} is not a .myshopify.com domain. It is the generated handle "
                           "(Settings › Domains in that store's admin), not its custom domain.")
        if store != values.get("SHOPIFY_STORE") and values.get("SHOPIFY_ADMIN_TOKEN"):
            values["SHOPIFY_ADMIN_TOKEN"] = ""          # a token belongs to one store
        values["SHOPIFY_STORE"] = store
    for key, value in (extra or {}).items():
        values[key] = (value or "").strip()
    if client_id:
        if not re.fullmatch(r"[0-9a-f]{32}", client_id):
            return False, ("A Client ID is 32 hex characters. Copy it from "
                           "Dev Dashboard → your app → App settings.")
        values["SHOPIFY_CLIENT_ID"] = client_id
    if client_secret:
        if not client_secret.startswith("shpss_"):
            return False, ("A Client secret starts with 'shpss_'. The value "
                           "above it is the Client ID, which is different.")
        values["SHOPIFY_CLIENT_SECRET"] = client_secret
    values.setdefault("SHOPIFY_ADMIN_TOKEN", "")
    values.setdefault("SHOPIFY_API_VERSION", "2025-07")
    values.setdefault("SHOPIFY_STORE", "")
    write_env(values, path)
    return True, "App credentials saved."
