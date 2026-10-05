#!/usr/bin/env python3
"""Offline check of the Purchasing tab, end to end. Touches nothing real.

    hub:  cd ~/Dev/steeple-onboarding
          ~/.venvs/steeple-onboarding-312/bin/python tools/check_purchasing.py

What it does: copies ~/Dev/ssorder into a temp folder, swaps ONLY its network
edges for fakes (Shopify's open orders and the S&S client come from ssorder's
own demo fixtures), and then drives every Purchasing route through Flask's
test client with the real templates. The real ssorder CLI runs underneath --
its guards, its ledger, its --json documents -- so this exercises the same
subprocess path the hub does.

It cannot reach Shopify or S&S: the fake S&S client writes every order it is
handed to a file in the temp folder, and the tests count those lines. The
app's own cache/purchasing/ and ssorder's own state/ are never opened.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
REAL_SSORDER = Path(os.environ.get("SSORDER_DIR", Path.home() / "Dev" / "ssorder"))

FAKE_CLI = '''\
"""Test double: the real ssorder with its two network edges replaced."""
import json, sys, time
from pathlib import Path
import ssorder_real as real

HERE = Path(__file__).resolve().parent
def _knob(name, default):
    p = HERE / name
    return p.read_text().strip() if p.is_file() else default
MODE, SCENARIO = _knob("fake_mode.txt", "ok"), _knob("fake_scenario.txt", "pickup")
SENT = HERE / "fake_sent.jsonl"

class FakeSS(real.DemoSS):
    def post_order(self, payload):
        with SENT.open("a") as f:
            f.write(json.dumps(payload) + "\\n")
        if MODE == "slow":
            time.sleep(2.5)
        nth = len(SENT.read_text().splitlines())
        if MODE == "fail" or (MODE == "fail2" and nth >= 2):
            raise real.SSError("S&S POST /orders/ -> HTTP 400: line 1 is out of stock", status=400)
        if MODE == "timeout":            # S&S has the request; no answer came back
            raise real.SSError("S&S POST /orders/ -> no complete answer (timeout: timed out)")
        if MODE == "die":                # the process is killed with the order already sent
            import os
            os._exit(137)
        if MODE == "nonumber":
            return []
        def order(n, wh):
            return {"orderNumber": str(n), "warehouseAbbr": wh,
                    "shippingMethod": payload["shippingMethod"],
                    "orderStatus": "Cancelled" if payload["testOrder"] else "InProgress",
                    "subtotal": 61.9, "shipping": 0, "tax": 4.64, "smallOrderFee": 0,
                    "total": 66.54, "expectedDeliveryDate": "2026-10-06"}
        if MODE == "two":                # S&S splits one PO across two warehouses
            return [order(7700001, "OH"), order(7700101, "IL")]
        return [order(7700000 + nth, "OH")]

    def cancel_order(self, number):
        with (HERE / "fake_cancelled.txt").open("a") as f:
            f.write(str(number) + "\\n")
        return [] if MODE == "nocancel" else [{"orderNumber": str(number)}]

real.real_client = lambda cfg: FakeSS(SCENARIO)
real.open_lines = lambda store: real.demo_lines(SCENARIO)
real.main()
'''


class PurchasingTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (REAL_SSORDER / "ssorder.py").is_file():
            raise unittest.SkipTest(f"ssorder not found at {REAL_SSORDER} (set SSORDER_DIR)")
        import app as app_module
        from onboarding import access, purchasing
        cls.app_module, cls.access, cls.purchasing = app_module, access, purchasing
        app_module.app.config["TESTING"] = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="purchasing-check-"))
        self.ss = self.tmp / "ssorder"
        shutil.copytree(REAL_SSORDER, self.ss,
                        ignore=shutil.ignore_patterns("state", ".env", "config.json",
                                                      "__pycache__", ".git"))
        (self.ss / "ssorder.py").rename(self.ss / "ssorder_real.py")
        (self.ss / "ssorder.py").write_text(FAKE_CLI)
        shutil.copy(self.ss / "fixtures" / "demo_styles.json", self.ss / "styles.json")
        (self.ss / ".env").write_text("SS_ACCOUNT_NUMBER=00000\nSS_API_KEY=not-a-real-key\n")
        self.config = {
            "ship_to": {"customer": "Check Co", "attn": "", "address": "1 Test St",
                        "city": "Dayton", "state": "OH", "zip": "45402", "residential": False},
            "stores": [{"name": "Demo Store", "env_file": "/nonexistent/.env"}],
        }
        self.write_config()

        p = self.purchasing
        self.state = self.tmp / "cache"
        self.patches = [
            mock.patch.object(p, "SSORDER_DIR", self.ss),
            mock.patch.object(p, "SCRIPT", self.ss / "ssorder.py"),
            mock.patch.object(p, "STATE", self.state),
        ]
        for patch in self.patches:
            patch.start()
        self.client = self.app_module.app.test_client()

    def tearDown(self):
        self.wait()
        for patch in self.patches:
            patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------ helpers
    def write_config(self):
        (self.ss / "config.json").write_text(json.dumps(self.config))

    def knob(self, name, value):
        (self.ss / f"fake_{name}.txt").write_text(value)

    def sent(self) -> list:
        path = self.ss / "fake_sent.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.is_file() else []

    def ledger(self) -> dict:
        path = self.ss / "state" / "ledger.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    def post(self, path, **form):
        return self.client.post(path, data=form, follow_redirects=True).get_data(as_text=True)

    def page(self) -> str:
        r = self.client.get("/purchasing")
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def wait(self, seconds=30):
        deadline = time.time() + seconds
        while time.time() < deadline:
            state = self.purchasing.status()
            if state["state"] != "running":
                return state
            time.sleep(0.2)
        self.fail("the background order never finished")

    def po(self) -> str:
        return self.purchasing.load()["proposal"]["po"]

    def state_file(self, name) -> dict:
        path = self.state / name
        return json.loads(path.read_text()) if path.is_file() else {}

    def put_state(self, name, data):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / name).write_text(json.dumps(data))

    def statuses(self) -> list:
        return sorted(e["status"] for e in self.ledger().values())

    def place(self, option="will_call"):
        """refresh -> review -> place -> wait. Returns the final status."""
        self.post("/purchasing/refresh")
        self.post("/purchasing/review", option=option)
        self.post("/purchasing/place", po=self.po(), option=option, confirm="yes")
        return self.wait()

    # ------------------------------------------------------------ the page
    def test_empty_page_renders_and_calls_nothing(self):
        html = self.page()
        self.assertIn("No proposal yet", html)
        self.assertIn(">Purchasing</a>", html)                 # the nav link
        self.assertFalse((self.ss / "state").exists())         # loading ran no ssorder

    def test_missing_install_is_said_plainly(self):
        with mock.patch.object(self.purchasing, "SCRIPT", self.tmp / "nope" / "ssorder.py"):
            self.assertIn("ssorder is not installed", self.page())
            self.assertIn("Could not build a proposal", self.post("/purchasing/refresh"))

    def test_missing_config_and_env_are_named(self):
        (self.ss / "config.json").unlink()
        (self.ss / ".env").unlink()
        html = self.page()
        self.assertIn("config.json is missing", html)
        self.assertIn(".env is missing", html)

    def test_refresh_shows_the_proposal_and_orders_nothing(self):
        html = self.post("/purchasing/refresh")
        self.assertIn("Proposal refreshed — 4 line(s) to buy.", html)
        self.assertIn("Will Call at OH", html)
        self.assertRegex(html, r'value="will_call"[^>]*\schecked')   # the recommendation, pre-ticked
        self.assertIn("Demo Logo Tee TWC", html)               # the Shopify line behind the sku
        self.assertIn("recommended", html)
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.ledger(), {})

    def test_hold_is_recommended_and_cannot_be_reviewed(self):
        self.knob("scenario", "hold")
        html = self.post("/purchasing/refresh")
        self.assertIn("Hold and batch with the next orders", html)
        # "No automatic pick": on a Hold no option arrives pre-ticked.
        self.assertNotRegex(html, r'name="option"[^>]*\schecked')
        self.assertIn("nothing is picked for you", html)
        self.assertIn("Choose Will Call, Ship or Split", self.post("/purchasing/review"))
        self.assertIn("This order cannot be placed", self.post("/purchasing/review", option="hold"))
        self.assertIn("This order cannot be placed", self.post("/purchasing/review", option="will_call"))
        self.assertEqual(self.sent(), [])

    # ------------------------------------------------------------ the money path
    def test_place_is_refused_without_a_review(self):
        self.post("/purchasing/refresh")
        html = self.post("/purchasing/place", po=self.po(), option="will_call", confirm="yes")
        self.assertIn("Review the order first", html)
        self.assertEqual(self.sent(), [])

    def test_review_sends_nothing_and_shows_the_payload(self):
        self.post("/purchasing/refresh")
        html = self.post("/purchasing/review", option="will_call")
        self.assertIn("Type yes to place it", html)
        self.assertIn("Will Call / pickup", html)
        self.assertIn("DEMO100001", html)
        self.assertIn("no payment profile is set", html)
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.ledger(), {})

    def test_review_says_when_a_saved_card_will_be_charged(self):
        self.config["ss"] = {"payment_profile": {"email": "a@b.c", "profileID": 7}}
        self.write_config()
        self.post("/purchasing/refresh")
        html = self.post("/purchasing/review", option="will_call")
        self.assertIn("the saved card in config.json (profile 7)", html)
        self.assertIn("Type yes to place it", html)
        self.assertNotIn('name="payment"', html)               # one card: nothing to pick

    def test_place_needs_yes_the_same_po_and_the_same_option(self):
        self.post("/purchasing/refresh")
        self.post("/purchasing/review", option="will_call")
        po = self.po()
        for form, expect in (
            (dict(po=po, option="will_call", confirm=""), "Type yes"),
            (dict(po=po, option="will_call", confirm="y"), "Type yes"),
            (dict(po="TWC-000000-0000", option="will_call", confirm="yes"), "changed since it was reviewed"),
            (dict(po=po, option="ship", confirm="yes"), "changed since it was reviewed"),
            (dict(po=po, option="hold", confirm="yes"), "Choose Will Call, Ship or Split"),
        ):
            self.assertIn(expect, self.post("/purchasing/place", **form), form)
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.ledger(), {})

    def test_a_refresh_voids_the_review(self):
        self.post("/purchasing/refresh")
        self.post("/purchasing/review", option="will_call")
        po = self.po()
        self.post("/purchasing/refresh")
        html = self.post("/purchasing/place", po=po, option="will_call", confirm="yes")
        self.assertIn("changed since it was reviewed", html)
        html = self.post("/purchasing/place", po=self.po(), option="will_call", confirm="yes")
        self.assertIn("changed since it was reviewed", html)   # the new PO was never reviewed
        self.assertNotIn("Type yes to place it", self.page())
        self.assertEqual(self.sent(), [])

    def test_stale_proposal_cannot_be_reviewed_or_placed(self):
        self.post("/purchasing/refresh")
        self.post("/purchasing/review", option="will_call")
        snap = self.purchasing.load()
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        snap["proposal"]["created"] = old
        self.put_state("snapshot.json", snap)
        self.put_state("review.json", {**self.state_file("review.json"), "created": old})
        self.assertIn("out of date", self.page())
        html = self.post("/purchasing/place", po=self.po(), option="will_call", confirm="yes")
        self.assertIn("minutes old", html)
        self.assertIn("minutes old", self.post("/purchasing/review", option="will_call"))
        self.assertEqual(self.sent(), [])

    def test_ssorder_refuses_a_stale_proposal_even_if_the_app_is_fooled(self):
        """The app's age check is a courtesy. ssorder's is the one that holds."""
        self.post("/purchasing/refresh")
        prop_file = self.ss / "state" / "proposals" / f"{self.po()}.json"
        prop = json.loads(prop_file.read_text())
        prop["created"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        prop_file.write_text(json.dumps(prop))
        html = self.post("/purchasing/review", option="will_call")
        self.assertIn("This order cannot be placed", html)
        self.assertEqual(self.sent(), [])

    def test_placeholder_address_blocks_the_review(self):
        self.config["ship_to"]["address"] = "REPLACE_ME"
        self.write_config()
        self.post("/purchasing/refresh")
        self.assertIn("placeholders", self.post("/purchasing/review", option="will_call"))
        self.assertEqual(self.sent(), [])

    def test_test_order_is_flagged_carries_no_payment_and_records_nothing(self):
        self.config["ss"] = {"payment_profile": {"email": "a@b.c", "profileID": 7}}
        self.write_config()
        self.post("/purchasing/refresh")
        html = self.post("/purchasing/test", option="will_call")
        self.assertIn("Nothing was placed", html)
        self.assertIn("66.54", html)                           # S&S's total, on the page
        sent = self.sent()
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0]["testOrder"])
        self.assertNotIn("paymentProfile", sent[0])
        self.assertEqual(self.ledger(), {})

    def test_placing_sends_one_real_order_and_moves_the_lines(self):
        state = self.place("will_call")
        self.assertEqual(state["state"], "done", state)
        sent = self.sent()
        self.assertEqual(len(sent), 1)
        self.assertFalse(sent[0]["testOrder"])
        self.assertEqual(sent[0]["shippingMethod"], "6")
        self.assertEqual(sent[0]["shippingAddress"]["address"], "1 Test St")
        ledger = self.ledger()
        self.assertEqual(len(ledger), 4)
        self.assertEqual({e["status"] for e in ledger.values()}, {"ordered"})
        self.assertEqual({e["ss_order"] for e in ledger.values()}, {"7700001"})

        html = self.page()
        self.assertIn("Placed with S&amp;S", html)
        self.assertIn("7700001", html)
        self.assertIn("Nothing to buy", html)                  # re-planned by itself
        self.assertIn("Mark ticked as received", html)
        self.assertIn("Cancel this order at S&amp;S", html)    # inside the ten minutes
        for leftover in ("place.lock", "place.claim", "place.job"):
            self.assertFalse((self.state / leftover).exists(), leftover)
        self.assertEqual(len(self.purchasing.history()), 1)
        self.purchasing.status(); self.page()                  # looking again records nothing new
        self.assertEqual(len(self.purchasing.history()), 1)
        self.assertTrue(sent[0]["poNumber"])

        # The same order cannot go twice: there is nothing left to review.
        self.assertIn("cannot be placed", self.post("/purchasing/review", option="will_call"))
        self.assertEqual(len(self.sent()), 1)

    def test_split_places_two_orders(self):
        self.knob("scenario", "hold")
        state = self.place("split")
        self.assertEqual(state["state"], "done", state)
        sent = self.sent()
        self.assertEqual([p["shippingMethod"] for p in sent], ["6", "1"])
        self.assertEqual([p["poNumber"][-2:] for p in sent], ["-P", "-S"])
        html = self.page()
        self.assertIn("7700001", html)
        self.assertIn("7700002", html)

    def test_second_half_of_a_split_failing_says_the_first_was_placed(self):
        self.knob("scenario", "hold")
        self.knob("mode", "fail2")                             # S&S takes part one, rejects part two
        state = self.place("split")
        self.assertEqual(state["state"], "failed")
        self.assertTrue(state["partial"])
        self.assertEqual(len(self.sent()), 2)
        ledger = self.ledger()
        self.assertEqual(len(ledger), 3)                       # the pickup half only
        self.assertEqual({e["ss_order"] for e in ledger.values()}, {"7700001"})
        html = self.page()
        self.assertIn("did not go through cleanly", html)
        self.assertRegex(html, r"PO TWC-[\d-]+-P WAS placed</b> as S&amp;S order 7700001")
        # Re-planned by itself: the three placed lines are awaiting, one is still to buy.
        self.assertEqual(len(self.purchasing.load()["proposal"]["lines"]), 1)
        self.assertIn("<b>1</b> to buy", html)

    def test_a_second_place_while_one_runs_is_refused(self):
        self.knob("mode", "slow")
        self.post("/purchasing/refresh")
        self.post("/purchasing/review", option="will_call")
        po = self.po()
        self.post("/purchasing/place", po=po, option="will_call", confirm="yes")
        html = self.post("/purchasing/place", po=po, option="will_call", confirm="yes")
        self.assertIn("already being placed", html)
        self.assertIn("Placing", self.page())                  # the running box
        self.assertEqual(self.client.get("/purchasing/status").get_json()["state"], "running")
        self.assertEqual(self.wait()["state"], "done")
        self.assertEqual(len(self.sent()), 1)

    def test_ss_rejecting_the_order_is_red_and_records_nothing(self):
        self.knob("mode", "fail")
        state = self.place("will_call")
        self.assertEqual(state["state"], "failed")
        self.assertFalse(state["partial"])
        self.assertEqual(self.ledger(), {})
        html = self.page()
        self.assertIn("did not go through cleanly", html)
        self.assertIn("out of stock", html)
        self.assertNotIn("Placed with S&amp;S", html)
        self.assertIn("Will Call at OH", html)                 # the lines are still to buy
        self.assertNotIn("did not go through", self.post("/purchasing/dismiss"))

    def test_an_answer_without_an_order_number_is_red_and_holds_the_lines(self):
        self.knob("mode", "nonumber")
        state = self.place("will_call")
        self.assertEqual(state["state"], "failed")
        self.assertTrue(state["partial"])
        self.assertEqual(len(self.ledger()), 4)                # held: cannot be ordered twice
        html = self.page()
        self.assertIn("with no order number", html)
        self.assertNotIn("WAS placed", html)                   # it is not known that it was
        self.assertIn("no S&amp;S order number for this PO", html)
        self.assertIn("--not-placed", html)
        self.assertEqual(self.statuses(), ["ordering"] * 4)

    def test_no_clear_answer_from_ss_holds_the_lines_and_says_so(self):
        """A timeout after the order left: S&S may have it. Never 'nothing was placed'."""
        self.knob("mode", "timeout")
        state = self.place("will_call")
        self.assertEqual(state["state"], "failed")
        self.assertTrue(state["unknown"])
        self.assertEqual(self.statuses(), ["ordering"] * 4)    # held: cannot be ordered twice
        html = self.page()
        self.assertIn("MAY have been placed", html)
        self.assertNotIn("Nothing was placed", html)
        self.assertNotIn("Placed with S&amp;S", html)
        self.assertIn("Nothing to buy", html)                  # not re-offered
        self.assertIn("--ss-order &lt;number&gt; --commit", html)   # how to settle it, both ways
        self.assertIn("--not-placed --commit", html)
        self.assertNotIn("unmark", html)                       # by PO, never by Shopify order
        self.assertIn("cannot be placed", self.post("/purchasing/review", option="will_call"))
        self.assertEqual(len(self.sent()), 1)

    def test_the_order_process_being_killed_after_sending_holds_the_lines(self):
        self.knob("mode", "die")
        state = self.place("will_call")
        self.assertEqual(state["state"], "failed")
        self.assertTrue(state["died"])
        self.assertEqual(len(self.sent()), 1)                  # it had left
        self.assertEqual(self.statuses(), ["ordering"] * 4)    # ...and the lines are held
        html = self.page()
        self.assertIn("not known whether S&amp;S received the order", html)
        self.assertIn("Nothing to buy", html)

    def test_a_job_whose_process_vanished_is_reported_not_left_spinning(self):
        self.post("/purchasing/refresh")
        self.put_state("place.job", {"pid": 999999999, "started": "2026-10-05T12:00:00+00:00",
                                     "po": self.po(), "option": "will_call"})
        html = self.page()
        self.assertIn("not known whether S&amp;S received the order", html)
        self.assertEqual(self.purchasing.status()["state"], "failed")
        self.assertFalse((self.state / "place.job").exists())

    def test_a_finished_order_is_seen_as_finished_even_if_its_pid_is_alive(self):
        """Pids get reused. A complete document is what says the order is over."""
        self.place("will_call")
        out = (self.state / "place.out").read_text()
        self.purchasing.dismiss_last()
        (self.state / "placed.json").unlink()
        (self.state / "place.out").write_text(out)
        self.put_state("place.job", {"pid": os.getpid(), "started": self.purchasing._stamp(),
                                     "po": self.po() if self.purchasing.load()["proposal"].get("po") else "X",
                                     "option": "will_call"})
        self.assertEqual(self.purchasing.status()["state"], "done")

    def test_a_refresh_that_overlaps_an_order_cannot_lose_or_duplicate_it(self):
        """The running job is not in the snapshot, so no refresh can overwrite it."""
        self.knob("mode", "slow")
        self.post("/purchasing/refresh")
        self.post("/purchasing/review", option="will_call")
        self.post("/purchasing/place", po=self.po(), option="will_call", confirm="yes")
        # The button is refused while an order is out...
        self.assertIn("An order is being placed", self.post("/purchasing/refresh"))
        # ...and a snapshot write that lands anyway (the other worker, a ledger
        # edit finishing) still cannot touch the job, which is not in that file.
        self.put_state("snapshot.json", {**self.purchasing.load(), "at": "overwritten"})
        self.assertEqual(self.purchasing.status()["state"], "running")
        self.assertEqual(self.wait()["state"], "done")
        self.post("/purchasing/refresh")                       # and one after
        self.assertEqual(self.purchasing.status()["state"], "done")
        self.assertEqual(len(self.purchasing.history()), 1)
        self.assertEqual(len(self.sent()), 1)
        self.assertFalse((self.state / "place.lock").exists())
        self.assertIn("Placed with S&amp;S", self.page())

    def test_the_order_runs_outside_the_apps_process_group(self):
        """launchd restarting the app signals its process group. The order must not be in it."""
        self.knob("mode", "slow")
        self.post("/purchasing/refresh")
        self.post("/purchasing/review", option="will_call")
        self.post("/purchasing/place", po=self.po(), option="will_call", confirm="yes")
        pid = self.state_file("place.job")["pid"]
        self.assertNotEqual(os.getpgid(pid), os.getpgid(0))
        self.wait()

    # ------------------------------------------------------------ a review is of ONE proposal
    def test_a_review_of_one_proposal_cannot_be_spent_on_another(self):
        """Same PO number, different contents: what a same-minute re-plan produces."""
        self.post("/purchasing/refresh")
        self.post("/purchasing/review", option="will_call")
        snap = self.purchasing.load()
        snap["proposal"]["created"] = datetime.now(timezone.utc).isoformat()   # rebuilt
        self.put_state("snapshot.json", snap)
        html = self.post("/purchasing/place", po=self.po(), option="will_call", confirm="yes")
        self.assertIn("changed since it was reviewed", html)
        self.assertNotIn("Type yes to place it", self.page())  # the stale review is not shown
        self.assertEqual(self.sent(), [])

    def test_ssorder_checks_the_build_time_again_at_the_last_moment(self):
        """The app agrees with itself, but the proposal FILE was rebuilt underneath it."""
        self.post("/purchasing/refresh")
        self.post("/purchasing/review", option="will_call")
        prop_file = self.ss / "state" / "proposals" / f"{self.po()}.json"
        prop = json.loads(prop_file.read_text())
        prop["created"] = datetime.now(timezone.utc).isoformat()
        for line in prop["options"]["will_call"]["payloads"][0]["lines"]:
            line["qty"] = 6                                    # a different order entirely
        prop_file.write_text(json.dumps(prop))
        self.post("/purchasing/place", po=self.po(), option="will_call", confirm="yes")
        state = self.wait()
        self.assertEqual(state["state"], "failed")
        self.assertIn("rebuilt since it was reviewed", state["error"])
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.ledger(), {})

    # ------------------------------------------------------------ which card
    TWO_CARDS = {"payment_profile": {"email": "larry@example.com", "profileID": None},
                 "payment_profiles": [{"label": "Visa ...1111 (Shop)", "profileID": 111},
                                      {"label": "Amex ...2222 (Larry)", "profileID": 222}]}

    def two_cards(self):
        self.config["ss"] = json.loads(json.dumps(self.TWO_CARDS))
        self.write_config()
        return self.post("/purchasing/refresh")

    def test_both_cards_are_offered_and_neither_is_pre_ticked(self):
        html = self.two_cards()
        self.assertIn("Visa ...1111 (Shop)", html)
        self.assertIn("Amex ...2222 (Larry)", html)
        self.assertNotRegex(html, r'name="payment"[^>]*\schecked')
        self.assertRegex(html, r'name="payment"[^>]*\srequired')

    def test_no_review_without_a_card_and_none_with_a_made_up_one(self):
        self.two_cards()
        for payment in ("", "999", "111 --commit"):
            html = self.post("/purchasing/review", option="will_call", payment=payment)
            self.assertIn("Choose which card pays for this order", html, payment)
            self.assertNotIn("Type yes to place it", html)
        html = self.post("/purchasing/place", po=self.po(), option="will_call", confirm="yes")
        self.assertIn("Review the order first", html)
        self.assertEqual(self.sent(), [])

    def test_the_card_that_was_picked_is_the_card_that_pays(self):
        self.two_cards()
        html = self.post("/purchasing/review", option="will_call", payment="222")
        self.assertIn("<b>Amex ...2222 (Larry)</b>", html)          # in the review box
        self.assertRegex(html, r'value="222"[^>]*\schecked')
        self.post("/purchasing/place", po=self.po(), option="will_call", confirm="yes")
        self.assertEqual(self.wait()["state"], "done")
        sent = self.sent()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["paymentProfile"], {"email": "larry@example.com", "profileID": 222})
        self.assertIn("paid with Amex ...2222 (Larry)", self.page())

    def test_swapping_the_card_after_the_review_stops_the_order(self):
        """The card is inside the fingerprint, so ssorder refuses rather than charges."""
        self.two_cards()
        self.post("/purchasing/review", option="will_call", payment="111")
        self.put_state("review.json", {**self.state_file("review.json"), "payment": "222"})
        self.post("/purchasing/place", po=self.po(), option="will_call", confirm="yes")
        state = self.wait()
        self.assertEqual(state["state"], "failed")
        self.assertIn("no longer what was reviewed", state["error"])
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.ledger(), {})

    def test_a_refresh_clears_the_card_choice(self):
        self.two_cards()
        self.post("/purchasing/review", option="will_call", payment="111")
        html = self.post("/purchasing/refresh")
        self.assertNotRegex(html, r'name="payment"[^>]*\schecked')

    def test_totals_need_no_card_and_send_none(self):
        self.two_cards()
        html = self.post("/purchasing/test", option="will_call")
        self.assertIn("Nothing was placed", html)
        self.assertNotIn("paymentProfile", self.sent()[0])

    def test_cards_added_since_the_last_refresh_are_asked_for_not_skipped(self):
        self.post("/purchasing/refresh")                       # the page knows of no cards
        self.config["ss"] = json.loads(json.dumps(self.TWO_CARDS))
        self.write_config()
        html = self.post("/purchasing/review", option="will_call")
        self.assertIn("choose which card", html)
        self.assertIn("Refresh the proposal", html)
        self.assertNotIn("Type yes to place it", html)
        self.assertEqual(self.sent(), [])

    # ------------------------------------------------------------ after the order
    def test_receive_and_put_back(self):
        self.place("will_call")
        keys = list(self.ledger())
        html = self.post("/purchasing/receive", key=keys[:2])
        self.assertIn("2 line(s) received.", html)
        self.assertIn("Received, not yet fulfilled in Shopify (2)", html)
        self.assertIn("<b>2</b> awaiting product", html)        # the counts move with it
        self.assertIn("<b>2</b> received", html)
        statuses = sorted(e["status"] for e in self.ledger().values())
        self.assertEqual(statuses, ["ordered", "ordered", "received", "received"])
        html = self.post("/purchasing/receive", key=keys[:1], undo="1")
        self.assertIn("moved back to Awaiting product", html)
        self.assertEqual(self.ledger()[keys[0]]["status"], "ordered")
        self.assertIn("Tick at least one line", self.post("/purchasing/receive"))
        self.assertEqual(len(self.sent()), 1)

    def test_cancelling_one_half_of_a_split_po_keeps_the_lines_held(self):
        self.knob("mode", "two")
        self.place("will_call")
        html = self.page()
        self.assertIn("7700001,7700101", html)
        self.assertIn("S&amp;S split this PO", html)
        self.assertNotIn("Cancel this order at S&amp;S", html)  # not offered for a split
        html = self.post("/purchasing/cancel", ss_order="7700001")   # ...and safe if sent anyway
        self.assertIn("4 line(s) are still held", html)
        self.assertEqual({e["ss_order"] for e in self.ledger().values()}, {"7700101"})
        self.assertIn("Nothing to buy", html)

    def test_cancel_releases_the_lines(self):
        self.place("will_call")
        html = self.post("/purchasing/cancel", ss_order="7700001")
        self.assertIn("S&amp;S order 7700001 cancelled. 4 line(s) are back in Sourcing.", html)
        self.assertEqual(self.ledger(), {})
        self.assertIn("Will Call at OH", html)

    def test_a_cancel_ss_does_not_confirm_changes_nothing(self):
        self.place("will_call")
        self.knob("mode", "nocancel")
        html = self.post("/purchasing/cancel", ss_order="7700001")
        self.assertIn("was NOT cancelled", html)
        self.assertEqual(len(self.ledger()), 4)

    def test_mark_as_bought_by_hand_and_undo(self):
        self.post("/purchasing/refresh")
        keys = [s["key"] for l in self.purchasing.load()["proposal"]["lines"] for s in l["sources"]]
        html = self.post("/purchasing/mark", key=keys[:1])
        self.assertIn("1 line(s) marked as already bought.", html)
        self.assertIn("Bought by hand", html)
        self.assertEqual(self.ledger()[keys[0]]["status"], "sourced")
        self.assertEqual(len(self.purchasing.load()["proposal"]["lines"]), 3)
        html = self.post("/purchasing/mark", key=keys[:1], undo="1")
        self.assertIn("put back in Sourcing", html)
        self.assertEqual(self.ledger(), {})
        self.assertEqual(self.sent(), [])

    def test_a_placed_line_cannot_be_put_back_from_the_page(self):
        self.place("will_call")
        keys = list(self.ledger())
        html = self.post("/purchasing/mark", key=keys[:1], undo="1")
        self.assertIn("Not changed", html)
        self.assertEqual(len(self.ledger()), 4)

    # ------------------------------------------------------------ who may do it
    def test_operators_cannot_send_anything_to_ss(self):
        self.post("/purchasing/refresh")
        self.post("/purchasing/review", option="will_call")
        po = self.po()
        with mock.patch.object(self.access, "may", return_value=False):
            self.post("/purchasing/test", option="will_call")
            self.post("/purchasing/place", po=po, option="will_call", confirm="yes")
            self.post("/purchasing/cancel", ss_order="7700001")
        self.assertEqual(self.sent(), [])
        self.assertFalse((self.ss / "fake_cancelled.txt").exists())
        self.assertFalse((self.state / "place.job").exists())

    def test_a_form_posted_from_another_site_is_refused(self):
        self.post("/purchasing/refresh")
        self.post("/purchasing/review", option="will_call")
        form = dict(po=self.po(), option="will_call", confirm="yes")
        for headers in ({"Origin": "http://evil.example"}, {"Origin": "null"},
                        {"Referer": "http://evil.example/page"}):
            r = self.client.post("/purchasing/place", data=form, headers=headers)
            self.assertEqual(r.status_code, 403, headers)
        self.assertEqual(self.sent(), [])
        # A hostile domain pointed at the hub's address is "same origin" to the
        # browser. It still is not one of this app's own names.
        rebound = {"Host": "evil.example:5000", "Origin": "http://evil.example:5000"}
        r = self.client.post("/purchasing/place", data=form, headers=rebound)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.sent(), [])
        own = self.app_module._own_address
        for host in ("100.119.56.54:5000", "127.0.0.1:5000", "localhost:5000", "twc-imac:5000",
                     "twc-imac.tail8aaf17.ts.net:5000", "twc-imac.local:5000", "[::1]:5000"):
            self.assertTrue(own(host), host)
        for host in ("evil.example:5000", "100.119.56.54.evil.example", "", "ts.net.evil.com"):
            self.assertFalse(own(host), host)
        ours = {"Host": "100.119.56.54:5000", "Origin": "http://100.119.56.54:5000"}
        r = self.client.post("/purchasing/place", data=form, headers=ours)
        self.assertEqual(r.status_code, 302)                   # what the page itself sends
        self.assertEqual(self.wait()["state"], "done")
        self.assertEqual(len(self.sent()), 1)

    def test_the_address_or_card_changing_after_the_review_stops_the_order(self):
        self.post("/purchasing/refresh")
        self.post("/purchasing/review", option="will_call")
        self.config["ship_to"]["address"] = "99 Somewhere Else"
        self.config["ss"] = {"payment_profile": {"email": "a@b.c", "profileID": 7}}
        self.write_config()                                    # config.json edited since
        self.post("/purchasing/place", po=self.po(), option="will_call", confirm="yes")
        state = self.wait()
        self.assertEqual(state["state"], "failed")
        self.assertIn("no longer what was reviewed", state["error"])
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.ledger(), {})

    def test_a_lock_left_with_no_job_behind_it_does_not_block_ordering_forever(self):
        self.post("/purchasing/refresh")
        self.post("/purchasing/review", option="will_call")
        lock = self.state / "place.lock"
        lock.write_text("orphan")
        form = dict(po=self.po(), option="will_call", confirm="yes")
        self.assertIn("already starting", self.post("/purchasing/place", **form))   # fresh: respected
        self.assertEqual(self.sent(), [])
        old = time.time() - 120
        os.utime(lock, (old, old))
        self.post("/purchasing/place", **form)
        self.assertEqual(self.wait()["state"], "done")

    def test_a_job_without_a_pid_is_not_called_dead_while_it_is_running(self):
        self.put_state("place.job", {"pid": None, "started": self.purchasing._stamp(),
                                     "po": "TWC-X", "option": "will_call"})
        self.assertEqual(self.purchasing.status()["state"], "running")     # inside the grace
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        self.put_state("place.job", {"pid": None, "started": old, "po": "TWC-X",
                                     "option": "will_call"})
        (self.state / "place.out").write_text("")              # the shell got as far as opening it
        self.assertEqual(self.purchasing.status()["state"], "running")
        (self.state / "place.out").unlink()
        self.assertEqual(self.purchasing.status()["state"], "failed")      # it never started


if __name__ == "__main__":
    unittest.main(verbosity=2)
