"""The Traveler's Clone button: run `pod2twc clone` from the app.

Why this exists (doc 46): the clone was a terminal step -- a venv that did not
exist, a ~100-character command with a product ID pasted in -- and it was
never once run. Every in-house twin since 2026-09-09 was hand-built in the
admin instead, where the defaults are wrong (weight 0, tracked, no shipping).

Shape mirrors onboarding/storefront.py:

    preflight(run)  -> {ok, blocking[], warnings[], plan{}}   the dry run. Writes
                       nothing to Shopify; records the preview on the run.
    commit(run)     -> {ok, error}  starts the real clone in the background.
    status(run)     -> {state, ...}  what the background clone is doing, and
                       when it finishes, records the result and ticks the step.

Decisions, answering doc 46's open questions:

* pod2twc is SUBPROCESSED, not imported. It is a CLI that prints and
  sys.exit()s; importing it would mean capturing stdout inside a gunicorn
  worker. Instead pod2twc grew `clone --json`: human lines to stderr, one JSON
  document to stdout, exit 0 only on success. No screen-scraping.
* It runs on THIS app's interpreter (sys.executable, i.e.
  ~/.venvs/steeple-onboarding-312). pod2twc's only dependency is `requests`,
  which this venv already has, so ~/.venvs/pod2twc never needs creating.
* One token. The app passes its own .env credentials through the environment
  (SHOPIFY_STORE -> SHOPIFY_STORE_DOMAIN). pod2twc also falls back to reading
  this app's .env when run by hand.
* Timeouts. A clone costs ~2 API calls per variant plus ~15; a 100-variant
  product is 200+ calls, which can outrun gunicorn's --timeout 300 once
  Shopify throttles. So the commit runs DETACHED (double-forked through
  /bin/sh, so the process is not a child of the worker and survives a worker
  restart), writing its JSON to product_runs/<run>.clone.out. The page polls.
  The preview is a handful of reads and runs inline.

Nothing here raises. A non-zero exit is always reported as a failure, never
rendered as success.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from onboarding import traveler
from onboarding.shopify_pull import _load_env, configured

POD2TWC_DIR = Path(os.environ.get("POD2TWC_DIR", Path.home() / "Dev" / "pod2twc"))
SCRIPT = POD2TWC_DIR / "pod2twc.py"
CONFIG = POD2TWC_DIR / "config.json"
PREVIEW_TIMEOUT = 120

# Scopes the clone's writes need. write_products covers duplicate, update,
# options, metafields and media; write_inventory covers activate/deactivate
# and cost; write_publications covers publishablePublish.
NEEDED_SCOPES = ["write_products", "write_inventory"]
WANTED_SCOPES = ["write_publications"]

CLONE_STEP = "clone"


# ------------------------------------------------------------------ helpers

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _job_paths(run_id: str) -> dict[str, Path]:
    base = traveler._path(run_id).with_suffix("")      # validates the id
    return {
        "out": base.with_name(base.name + ".clone.out"),
        "log": base.with_name(base.name + ".clone.log"),
        "lock": base.with_name(base.name + ".clone.lock"),
    }


def _config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def pod_org(run: dict) -> tuple[str, str]:
    """(pod2twc org key, problem) for the run's partner.

    The traveler keys orgs by collection handle ("haven-of-hope",
    "highway-of-holiness"); pod2twc keys them by its own short names ("hoh" is
    Highway of Holiness, NOT Haven of Hope). Matching on the key would clone
    to the wrong church. Match on the vendor string, which is the thing that
    has to agree anyway.
    """
    org = traveler.org_by_key(run.get("org_key", ""))
    if not org:
        return "", "No partner org chosen in Run details."
    orgs = _config().get("orgs") or {}
    if not orgs:
        return "", f"Could not read pod2twc's org registry at {CONFIG}."
    matches = [k for k, o in orgs.items() if o.get("vendor") == org["vendor"]]
    if not matches:
        return "", (f"“{org['vendor']}” is not in pod2twc's org registry "
                    f"({CONFIG.name}). Add it there — collection id, tag, pickup "
                    "locations — before cloning.")
    if len(matches) > 1:
        return "", f"Vendor “{org['vendor']}” appears more than once in {CONFIG.name}: {matches}."
    return matches[0], ""


def options(run: dict) -> dict:
    o = run.get("clone_opts") or {}
    return {
        "pod_cost": str(o.get("pod_cost") or "").strip(),
        "price": str(o.get("price") or "").strip(),
        # Default ON (Larry, 2026-09-30): the POD mockups are a render of the
        # real design and are better than an empty listing. Only a saved
        # choice -- the box unticked and previewed -- turns it off.
        "keep_images": bool(o.get("keep_images", True)),
        "no_flip": bool(o.get("no_flip")),
    }


def set_options(run: dict, form) -> None:
    run["clone_opts"] = {
        "pod_cost": (form.get("pod_cost") or "").strip(),
        "price": (form.get("price") or "").strip(),
        "keep_images": bool(form.get("keep_images")),
        "no_flip": bool(form.get("no_flip")),
    }


def _money(text: str) -> float | None:
    try:
        value = float(str(text).replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _inputs(run: dict) -> tuple[dict, list[str]]:
    """Validated clone inputs, and anything blocking."""
    blocking: list[str] = []
    pid = (run.get("product_id") or "").strip()
    if not pid:
        blocking.append("No POD product ID in Run details.")
    elif not pid.isdigit():
        blocking.append(f"POD product ID “{pid}” should be digits only — the last "
                        "segment of the admin URL.")
    org, problem = pod_org(run)
    if problem:
        blocking.append(problem)
    cost = _money(run.get("cost", ""))
    if (run.get("cost") or "").strip() and cost is None:
        blocking.append(f"Loaded in-house cost “{run.get('cost')}” is not a number.")
    elif cost is None:
        blocking.append("No loaded in-house cost in Run details. The clone would keep "
                        "the POD cost, and the margin workbook would read the wrong spread.")
    opts = options(run)
    for field, label in (("pod_cost", "POD cost override"), ("price", "Price override")):
        if opts[field] and _money(opts[field]) is None:
            blocking.append(f"{label} “{opts[field]}” is not a number.")
    return {"product_id": pid, "org": org, "cost": cost, **opts}, blocking


def _argv(inputs: dict, commit: bool) -> list[str]:
    args = [sys.executable, str(SCRIPT), "clone", inputs["product_id"],
            "--org", inputs["org"], "--cost", f"{inputs['cost']:.2f}", "--json"]
    if inputs["pod_cost"]:
        args += ["--pod-cost", f"{_money(inputs['pod_cost']):.2f}"]
    if inputs["price"]:
        args += ["--price", f"{_money(inputs['price']):.2f}"]
    if inputs["keep_images"]:
        args.append("--keep-images")
    if inputs["no_flip"]:
        args.append("--no-flip")
    if commit:
        args.append("--commit")
    return args


def _fingerprint(inputs: dict) -> str:
    return hashlib.sha1(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:12]


def _env() -> dict:
    _load_env()
    env = os.environ.copy()
    if env.get("SHOPIFY_STORE") and not env.get("SHOPIFY_STORE_DOMAIN"):
        env["SHOPIFY_STORE_DOMAIN"] = env["SHOPIFY_STORE"]
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def command_line(run: dict, commit: bool = False) -> str:
    """The exact command the button runs, for the page and for a terminal."""
    inputs, _ = _inputs(run)
    if not inputs["org"]:
        inputs["org"] = "<org>"
    if inputs["cost"] is None:
        inputs["cost"] = 0.0
    inputs["product_id"] = inputs["product_id"] or "<product-id>"
    return " ".join(shlex.quote(a) for a in _argv(inputs, commit))


# ------------------------------------------------------------------ transport

def _run(args: list[str], timeout: int) -> dict:
    """Run pod2twc and return {ok, code, doc, error, stderr}. Never raises."""
    if not SCRIPT.exists():
        return {"ok": False, "code": None, "doc": None, "stderr": "",
                "error": f"pod2twc not found at {SCRIPT}."}
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              env=_env(), cwd=str(POD2TWC_DIR))
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": None, "doc": None, "stderr": "",
                "error": f"pod2twc did not finish within {timeout}s."}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "code": None, "doc": None, "stderr": "",
                "error": f"Could not start pod2twc: {exc}"}
    return _interpret(proc.returncode, proc.stdout, proc.stderr)


def _interpret(code: int | None, stdout: str, stderr: str) -> dict:
    doc = None
    try:
        doc = json.loads(stdout) if stdout.strip() else None
    except ValueError:
        doc = None
    if doc is None:
        tail = "\n".join((stderr or stdout or "").strip().splitlines()[-12:])
        return {"ok": False, "code": code, "doc": None, "stderr": stderr,
                "error": f"pod2twc exited {code} without a result." +
                         (f"\n{tail}" if tail else "")}
    # Both must agree. A zero exit with ok:false, or ok:true with a non-zero
    # exit, is a failure -- never let an ambiguous run look like success.
    ok = code == 0 and bool(doc.get("ok"))
    error = doc.get("error") or ("" if ok else
                                 "; ".join(doc.get("blocking") or []) or
                                 f"pod2twc exited {code}.")
    return {"ok": ok, "code": code, "doc": doc, "stderr": stderr, "error": error}


def _scope_check() -> tuple[list[str], list[str]]:
    """(blocking, warnings) from the token's granted scopes."""
    from onboarding import storefront                     # its _gql never raises
    result = storefront._gql("query { currentAppInstallation { accessScopes { handle } } }")
    if not result["ok"]:
        return [], [f"Could not read the token's scopes ({result['error']}) — the "
                    "commit will find out the hard way if one is missing."]
    have = {s["handle"] for s in
            ((result["data"].get("currentAppInstallation") or {}).get("accessScopes") or [])}
    missing = [s for s in NEEDED_SCOPES if s not in have]
    blocking = ([f"The Shopify token lacks {', '.join(missing)}. Reconnect Shopify from "
                 "Settings with the write scopes before cloning."] if missing else [])
    soft = [s for s in WANTED_SCOPES if s not in have]
    warnings = ([f"The token lacks {', '.join(soft)} — the clone would be created but not "
                 "published to the sales channels."] if soft else [])
    return blocking, warnings


# ------------------------------------------------------------------ preflight

def preflight(run: dict) -> dict:
    """The dry run. Nothing is written to Shopify; the result is saved on the run.

    Returns {ok, blocking[], warnings[], plan{}, events[], command}.
    """
    inputs, blocking = _inputs(run)
    warnings: list[str] = []

    _load_env()
    if not configured():
        blocking.append("Shopify is not connected. Settings > Shopify.")
    if not SCRIPT.exists():
        blocking.append(f"pod2twc not found at {SCRIPT}.")

    plan, events, doc = {}, [], None
    if not blocking:
        scope_block, scope_warn = _scope_check()
        blocking += scope_block
        warnings += scope_warn
        result = _run(_argv(inputs, commit=False), PREVIEW_TIMEOUT)
        doc = result["doc"]
        if doc:
            plan = doc.get("plan") or {}
            events = doc.get("events") or []
            blocking += doc.get("blocking") or []
            warnings += doc.get("warnings") or []
        if not result["ok"] and not (doc and doc.get("blocking")):
            blocking.append(result["error"])

        # The CLI treats a missing or placeholder POD cost as a warning. Here it
        # blocks: pod_basis_cost is what the partner payout is computed from,
        # and a twin without it silently pays the partner nothing.
        cost = plan.get("cost") or {}
        if plan and not inputs["pod_cost"]:
            if cost.get("pod_basis_suspect"):
                blocking.append(
                    f"The POD unit cost ({cost.get('pod_basis')}) equals retail — a "
                    "placeholder the POD app pushed, not a real cost. Enter the true "
                    "POD cost in “POD cost override”.")
            elif not cost.get("pod_basis"):
                blocking.append("No POD unit cost on the source product. Enter it in "
                                "“POD cost override” — the payout basis depends on it.")

    ok = not blocking and bool(plan)
    preview = {
        "ok": ok, "at": _now(), "fingerprint": _fingerprint(inputs),
        "blocking": blocking, "warnings": warnings, "plan": plan, "events": events,
        "command": command_line(run, commit=False),
    }
    run["clone_preview"] = preview
    traveler.save(run)
    return preview


# ------------------------------------------------------------------ commit

def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def commit(run: dict) -> dict:
    """Start the real clone in the background. Returns {ok, error}."""
    if (run.get("clone") or {}).get("ok"):
        return {"ok": False, "error": "This run has already been cloned — "
                                      f"{run['clone'].get('new_id')}."}
    state = status(run)
    if state["state"] == "running":
        return {"ok": False, "error": "A clone is already running for this run."}

    inputs, blocking = _inputs(run)
    if blocking:
        return {"ok": False, "error": " ".join(blocking)}
    preview = run.get("clone_preview") or {}
    if not preview.get("ok"):
        return {"ok": False, "error": "Run a successful Preview first."}
    if preview.get("fingerprint") != _fingerprint(inputs):
        return {"ok": False, "error": "Run details or options changed since the preview. "
                                      "Preview again, then commit."}

    paths = _job_paths(run["id"])
    try:
        fd = os.open(paths["lock"], os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, _now().encode())
        os.close(fd)
    except FileExistsError:
        return {"ok": False, "error": "A clone is already starting for this run."}

    for key in ("out", "log"):
        paths[key].unlink(missing_ok=True)
    argv = _argv(inputs, commit=True)
    # /bin/sh backgrounds the clone and exits at once, so the clone is
    # re-parented away from this gunicorn worker: a worker timeout or restart
    # cannot kill it half-way through, and it never lingers as a zombie.
    shell = (" ".join(shlex.quote(a) for a in argv) +
             f" > {shlex.quote(str(paths['out']))} 2> {shlex.quote(str(paths['log']))}"
             " & echo $!")
    try:
        proc = subprocess.run(["/bin/sh", "-c", shell], capture_output=True, text=True,
                              timeout=15, env=_env(), cwd=str(POD2TWC_DIR))
        pid = int(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:                                  # noqa: BLE001
        paths["lock"].unlink(missing_ok=True)
        return {"ok": False, "error": f"Could not start the clone: {exc}"}

    run["clone_job"] = {"pid": pid, "started": _now(), "fingerprint": preview["fingerprint"],
                        "command": " ".join(shlex.quote(a) for a in argv)}
    traveler.save(run)
    return {"ok": True, "pid": pid}


def status(run: dict) -> dict:
    """{state: idle|running|done|failed, ...}. Records a finished job on the run."""
    if (run.get("clone") or {}).get("finished"):
        rec = run["clone"]
        return {"state": "done" if rec.get("ok") else "failed", **rec}
    job = run.get("clone_job")
    if not job:
        return {"state": "idle"}

    paths = _job_paths(run["id"])
    if _pid_alive(job.get("pid")):
        log = ""
        try:
            log = paths["log"].read_text(errors="replace")
        except OSError:
            pass
        lines = [l.strip() for l in log.splitlines() if l.strip()]
        return {"state": "running", "started": job.get("started"),
                "progress": lines[-6:]}

    try:
        stdout = paths["out"].read_text(errors="replace")
    except OSError:
        stdout = ""
    try:
        stderr = paths["log"].read_text(errors="replace")
    except OSError:
        stderr = ""
    # The exit code is gone with the detached process; pod2twc's JSON carries
    # ok and is only printed at the very end, so a missing or unparsable
    # document means it died mid-run. Treat that as failure.
    doc = None
    try:
        doc = json.loads(stdout) if stdout.strip() else None
    except ValueError:
        doc = None
    result = _interpret(0 if doc and doc.get("ok") else 1, stdout, stderr)
    doc = result["doc"] or {}
    record = {
        "finished": _now(), "started": job.get("started"),
        "ok": result["ok"], "error": "" if result["ok"] else result["error"],
        "new_id": doc.get("new_id"), "partial": bool(doc.get("partial")),
        "events": doc.get("events") or [], "api_calls": doc.get("api_calls"),
    }
    run["clone"] = record
    run.pop("clone_job", None)
    if record["ok"]:
        run.setdefault("steps", {})[CLONE_STEP] = True
        run["twc_product_id"] = (record["new_id"] or "").rsplit("/", 1)[-1]
    traveler.save(run)
    paths["lock"].unlink(missing_ok=True)
    return {"state": "done" if record["ok"] else "failed", **record}


def reset(run: dict) -> None:
    """Forget a FAILED result so the run can be previewed and committed again.

    Never clears a successful clone. A partial failure left a product in the
    store; the page says so, and it has to be dealt with in admin.
    """
    rec = run.get("clone") or {}
    if rec.get("ok"):
        return
    run.pop("clone", None)
    run.pop("clone_preview", None)
    traveler.save(run)
