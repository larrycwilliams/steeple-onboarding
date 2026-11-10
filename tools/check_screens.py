#!/usr/bin/env python3
"""Every Portal page at every screen we use (doc 70: the device standard).

    python tools/check_screens.py                 # report only
    python tools/check_screens.py --shots DIR     # also save a screenshot per page per device

Needs Playwright with Chromium (`pip install playwright && playwright install
chromium`). It is a build-time gate, run where the patches are built; the hub
does not need it.

Builds a throwaway copy of the app's CODE with made-up partners, orders, leads
and Purchasing data (never partners/, cache/ or .env), serves it on a free
loopback port, opens each page at each device size and fails when anything is
wider than the screen -- the one fault that makes a page unusable on a phone
and the one a desktop browser never shows you.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "tools"))
from devices import DEVICES  # noqa: E402

SKIP = {".env", "partners", "cache", "output", "assets", "leads", "product_runs", ".git",
        "__pycache__", "Claude outputs", "cost_changes"}

PAGES = ["/", "/orders", "/programs", "/programs?show=attention", "/partners", "/dashboard", "/pipeline", "/purchasing", "/statements",
         "/readiness", "/tools", "/settings", "/discovery", "/traveler", "/slips"]


def build_copy() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="screens-check-"))
    work = tmp / "app"
    shutil.copytree(APP_ROOT, work, ignore=lambda d, names: [n for n in names if n in SKIP
                                                            or n.endswith(".app")])
    return work


def seed(work: Path) -> list[str]:
    """Made-up data so every page has something real-shaped to lay out.
    Returns extra paths worth checking (a partner's own pages)."""
    from onboarding import agreement, store
    now = datetime.now(timezone.utc)

    def ago(days):
        return (now - timedelta(days=days)).isoformat(timespec="seconds")

    long_name = "Highway of Holiness Apostolic Fellowship & Christian Academy"
    recs = [store.save({"org_name": n, "short_name": s, "margin_pct": "30", "plan": "Growth",
                        "shopify_vendor": n, "monthly_fee": "49", "setup_fee": "399",
                        "poc_name": "Pat Example", "poc_email": f"pat@{s.lower()}.example",
                        "recurring_invoice_started": (now - timedelta(days=90)).date().isoformat(),
                        "recurring_paid_through": (now - timedelta(days=40)).date().isoformat()})
            for n, s in (("Germantown Christian School", "GCS"), ("Haven of Hope", "HOH"),
                         (long_name, "HOHAF"))]
    agreement.mark_sent(store.partner_id(recs[1]), when=(now - timedelta(days=9)).date().isoformat())

    def line(title, vendor, qty=1):
        return {"title": title, "qty": qty, "qty_ordered": qty, "vendor": vendor,
                "vendor_at_sale": vendor, "product_id": "gid://p/1", "variant_missing": False,
                "product_status": "ACTIVE", "unit_price": 24.0, "unit_cost": 9.0}

    names = [r["org_name"] for r in recs]
    orders = []
    for i in range(18):
        vendor = names[i % 3]
        orders.append({"name": f"#40{i:02d}", "id": str(4000 + i), "created_at": ago(i * 1.3),
                       "test": False, "cancelled": False, "status": "PAID",
                       "fulfillment": "FULFILLED" if i % 5 == 4 else "UNFULFILLED",
                       "delivery": "Shop Pickup – Dayton" if i % 2 else "Standard",
                       "lines": [line(f"{vendor} Logo Mascot Hoodie — Extended Title TWC", vendor, 2),
                                 line("Phone case", vendor)]})
    cache = work / "cache"
    (cache / "purchasing").mkdir(parents=True, exist_ok=True)
    (cache / "shopify_sales.json").write_text(json.dumps({
        "pulled_at": ago(0.01), "orders": orders, "collections": [],
        # products with the longest titles and widest price ranges a card can carry (doc 71)
        "catalog": [{"id": f"gid://shopify/Product/{900 + i}", "vendor": names[i % 3],
                     "title": f"{names[i % 3]} Logo Mascot Hoodie — Extended Title Garment-Dyed Heavyweight {i}" + (" TWC" if i < 10 else " Printify"),
                     "status": ("ACTIVE", "DRAFT", "ARCHIVED")[i % 3], "handle": f"item-{i}",
                     "type": "Hoodies & Sweatshirts", "variants": 48, "price_min": 24.0,
                     "price_max": 1249.0, "unit_cost": None if i % 4 == 0 else 19.5,
                     "unit_costs": [], "image": "", "updated_at": ago(i)} for i in range(12)],
        "locations": ["Shop Pickup – Dayton"]}))
    (cache / "shopify_leads.json").write_text(json.dumps({"pulled_at": ago(0.02), "leads": [
        {"id": f"gid://shopify/Company/{i}", "key": str(i), "org_name": n, "created_at": ago(i),
         "note": "submitted to the form", "email": f"office{i}@example.org", "name": "Office"}
        for i, n in enumerate(["Grace Fellowship of the Miami Valley", "New Hope", long_name])]}))
    (cache / "purchasing" / "snapshot.json").write_text(json.dumps({"at": ago(0.02), "ok": True, "proposal": {
        "lines": [], "other_suppliers": [],
        "tracked": [{"key": f"Steeple & Stitch|{i}", "store": "Steeple & Stitch", "order": f"#40{i:02d}",
                     "qty": 2, "status": "received" if i % 3 == 0 else "ordered", "ss_order": "77434382"}
                    for i in range(0, 18, 2)]}}))
    (cache / "studio_summary.json").write_text(json.dumps({
        "ok": True, "version": "1.9.0", "at": ago(0.05), "oldest_sent_days": 3,
        "total": {"sent": 2, "changes": 1, "approved": 4, "production": 1},
        "brands": {"ss": {"sent": 2}},
        # the longest Studio line a Programs card can carry (doc 69 phase 4)
        "partners": {store.partner_id(recs[0]): {"projects": 12, "sent": 14, "changes": 3, "approved": 27,
                                                 "production": 9, "oldest_sent_days": 8}}}))
    return [f"/partner/{store.partner_id(recs[2])}", f"/partner/{store.partner_id(recs[2])}/files",
            f"/partner/{store.partner_id(recs[2])}/products",
            f"/partner/{store.partner_id(recs[0])}/products?show=active"]


def serve(work: Path):
    sys.path.insert(0, str(work))
    import app as portal  # the copy
    from werkzeug.serving import make_server
    assert Path(portal.__file__).resolve().is_relative_to(work.resolve()), "imported the real app"
    extra = seed(work)
    server = make_server("127.0.0.1", 0, portal.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}", extra


OVERFLOW_JS = """() => {
  const w = document.documentElement.clientWidth;
  const wide = document.scrollingElement.scrollWidth;
  const out = [];
  if (wide > w + 1) {
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect();
      if (r.width && r.right > w + 1) {
        // Skip anything inside a box that is meant to scroll sideways.
        let p = el.parentElement, scrolls = false;
        while (p && p !== document.body) {
          const s = getComputedStyle(p).overflowX;
          if (s === 'auto' || s === 'scroll' || s === 'hidden') { scrolls = true; break; }
          p = p.parentElement;
        }
        if (!scrolls) out.push((el.id ? '#' + el.id : el.tagName.toLowerCase() +
          (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).join('.') : ''))
          + ' → ' + Math.round(r.right) + 'px');
      }
      if (out.length >= 4) break;
    }
  }
  return {w, wide, out};
}"""


def main(argv: list[str]) -> int:
    shots = Path(argv[argv.index("--shots") + 1]) if "--shots" in argv else None
    from playwright.sync_api import sync_playwright

    work = build_copy()
    server, base, extra = serve(work)
    pages = PAGES + extra
    problems, checked = [], 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for dev in DEVICES:
            ctx = browser.new_context(viewport={"width": dev["w"], "height": dev["h"]},
                                      device_scale_factor=dev.get("dpr", 1),
                                      is_mobile=dev.get("mobile", False),
                                      has_touch=dev.get("mobile", False))
            page = ctx.new_page()
            for path in pages:
                response = page.goto(base + path, wait_until="load")
                status = response.status if response else 0
                if status != 200:
                    problems.append(f"{dev['name']:<14} {path:<34} HTTP {status}")
                    continue
                checked += 1
                result = page.evaluate(OVERFLOW_JS)
                if result["out"]:
                    problems.append(f"{dev['name']:<14} {path:<34} {result['wide']}px wide on a "
                                    f"{result['w']}px screen: " + "; ".join(result["out"]))
                if shots:
                    shots.mkdir(parents=True, exist_ok=True)
                    slug = path.strip("/").replace("/", "_") or "home"
                    page.screenshot(path=str(shots / f"{dev['name']}__{slug}.png"), full_page=False)
            ctx.close()
        browser.close()
    server.shutdown()
    shutil.rmtree(work.parent, ignore_errors=True)

    for line in problems:
        print("  FAIL " + line)
    print(f"\n{checked} page views across {len(DEVICES)} screens, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
