#!/usr/bin/env python3
"""Offline check: who the Portal thinks you are behind `tailscale serve` (3.93).

    hub:  cd ~/Dev/steeple-onboarding
          ~/.venvs/steeple-onboarding-312/bin/python tools/check_https_identity.py

Behind serve every request arrives from 127.0.0.1. Without the 3.93 change that
read as the hub's own keyboard -- the owner -- for every phone. Copies the app's
CODE only (never .env*, partners/, cache/) into a temp folder.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
SKIP = {"partners", "cache", "output", "assets", "leads", "product_runs", ".git",
        "__pycache__", "Claude outputs", "cost_changes"}
tmp = Path(tempfile.mkdtemp(prefix="https-check-"))
work = tmp / "app"
shutil.copytree(APP_ROOT, work, ignore=lambda d, names: [n for n in names if n in SKIP
                                                        or n.startswith(".env") or n.endswith(".app")])
sys.path.insert(0, str(work))

from onboarding import access  # noqa: E402
assert Path(access.__file__).resolve().is_relative_to(work.resolve()), "imported the real app"

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}")


calls = []


def fake_whois(ip):
    calls.append(ip)
    table = {"100.112.168.99": {"login": "larry@thewilliamscollective.com", "name": "Larry",
                                "machine": "larry-iphone-duo", "error": ""},
             "100.64.0.9": {"login": "", "name": "", "machine": "tagged-tv", "error": ""}}
    return table.get(ip, {"login": "", "name": "", "machine": "", "error": "not on the tailnet"})


access.whois = fake_whois
access.PEOPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
access.PEOPLE_PATH.write_text(json.dumps({"enforce": True, "people": {
    "larry@thewilliamscollective.com": {"name": "Larry", "role": "owner"},
    "helper@example.com": {"name": "Helper", "role": "operator"}}}))

me = access.who("127.0.0.1", {})
check("plain loopback (the hub's keyboard) is still the owner", me["source"] == "local" and me["is_owner"])

me = access.who("127.0.0.1", {"Tailscale-User-Login": "Helper@Example.com",
                              "Tailscale-User-Name": "Helper", "X-Forwarded-For": "100.70.1.2"})
check("through serve, an operator is an operator (not the owner)",
      me["login"] == "helper@example.com" and me["role"] == "operator" and not me["is_owner"])

me = access.who("127.0.0.1", {"Tailscale-User-Login": "larry@thewilliamscollective.com",
                              "X-Forwarded-For": "100.112.168.99"})
check("through serve, Larry is the owner by his login", me["is_owner"] and me["source"] == "people")

me = access.who("127.0.0.1", {"Tailscale-User-Login": "stranger@example.com"})
check("through serve, a tailnet login not in the people file is an operator",
      me["role"] == "operator" and me["source"] == "tailnet")

calls.clear()
me = access.who("127.0.0.1", {"X-Forwarded-For": "100.64.0.9"})
check("through serve with no user header (a tagged device) asks Tailscale about the real address",
      calls == ["100.64.0.9"] and me["source"] == "unknown" and not me["is_owner"])

me = access.who("100.70.1.2", {"Tailscale-User-Login": "larry@thewilliamscollective.com"})
check("a header sent straight to port 5000 over the tailnet is ignored",
      me["source"] == "unknown" and not me["is_owner"])

check("direct tailnet visits still work as before",
      access.who("100.112.168.99", {})["is_owner"])

sys.path.insert(0, str(work))
import os  # noqa: E402
os.environ.setdefault("SHOPIFY_STORE", "check-store.myshopify.com")
import app as portal  # noqa: E402
client = portal.app.test_client()
host = "imac.tail8aaf17.ts.net"
r = client.post("/orders/tick", data={"key": "ss|#1", "step": "pressed"},
                headers={"Host": host, "Origin": f"https://{host}",
                         "Tailscale-User-Login": "larry@thewilliamscollective.com",
                         "X-Forwarded-For": "100.112.168.99", "X-Forwarded-Proto": "https"})
check("a form posted on the HTTPS address is accepted (same-site check)", r.status_code == 303)
r = client.get("/settings", headers={"Host": host, "Tailscale-User-Login": "helper@example.com",
                                     "X-Forwarded-For": "100.70.1.2"})
check("Settings is owner-only through serve too", r.status_code in (302, 303, 403))
check("version is 3.93 or later", tuple(map(int, portal.APP_VERSION.split("."))) >= (3, 93))

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
