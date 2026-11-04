import copy
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT

ticket = load("trader-broker", "ticket")

NOW = "2026-10-19 10:15"          # IST
TODAY = "2026-10-19"

# The binding order shape (INDIA_SPEC section 5). The fingerprint must be computed over exactly these.
SPEC_ORDER_FIELDS = ("symbol", "exchange", "side", "qty", "type", "product", "time_in_force", "limit_price",
                     "stop_price", "client_order_id", "order_class", "take_profit_limit_price",
                     "stop_loss_stop_price", "stop_loss_limit_price")

# The canonical scenario: BUY 52 RELIANCE MIS, stop-loss 1406.40, target 1426.20, max loss 52 x 9.90 = 514.80.
RELIANCE = {"symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "qty": "52", "type": "market",
            "product": "MIS", "time_in_force": "day", "order_class": "bracket",
            "take_profit_limit_price": "1426.20", "stop_loss_stop_price": "1406.40",
            "why": "opening-range breakout on RELIANCE: 52 shares, bound by your 75% position cap",
            "plan": {"entry": 1416.30, "stop": 1406.40, "target": 1426.20, "max_loss": 514.8}}
RELIANCE_LIMIT = dict(RELIANCE, type="limit", limit_price="1416.30")
RELIANCE_PLAIN = ("BUY 52 RELIANCE (NSE, MIS), market, good for today / stop-loss 1406.40 / target 1426.20 / "
                  "max loss about ₹514.80 if the stop-loss fills at 1406.40 (it can fill worse in a fast market)")
SIMPLE = {"symbol": "TCS", "exchange": "NSE", "side": "buy", "qty": "10", "type": "limit", "product": "MIS",
          "time_in_force": "day", "limit_price": "3500"}
CFG = {"budget": 100000, "flat_by": "15:00"}
# words that belong to the old approval step or to a "paper" mode: none may come back
GONE = re.compile(r"(?i)paper|\byes\b|approve|decline|dry.run")


def order(**over):
    o = copy.deepcopy(RELIANCE)
    o.update(over)
    return o


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


def jdump(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def jload(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class Money(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(ticket.inr(5000), "₹5,000")
        self.assertEqual(ticket.inr(123456.78), "₹1,23,456.78")
        self.assertEqual(ticket.inr(12345678), "₹1,23,45,678")
        self.assertEqual(ticket.inr(100000), "₹1,00,000")
        self.assertEqual(ticket.inr(514.8), "₹514.80")
        self.assertEqual(ticket.inr(999.5), "₹999.50")
        self.assertEqual(ticket.inr(5000, 2), "₹5,000.00")
        self.assertEqual(ticket.inr(-1500), "-₹1,500")
        self.assertEqual(ticket.inr(0), "₹0")


class TimeIST(unittest.TestCase):
    def test_ist_clock(self):
        now = ticket.parse_now("2026-10-19 10:15")                     # naive = IST
        self.assertEqual(now.isoformat(), "2026-10-19T04:45:00")        # UTC
        self.assertEqual(ticket.ist_iso(now), "2026-10-19T10:15:00+05:30")
        self.assertEqual(ticket.parse_now("2026-10-19T04:45:00Z"), now)
        self.assertEqual(ticket.parse_now("2026-10-19T10:15:00+05:30"), now)
        self.assertEqual(ticket.to_ist(now).hour, 10)
        self.assertEqual(ticket.ist_to_utc(ticket.to_ist(now)), now)

    def test_no_daylight_saving(self):
        for ts in ("2026-01-05 09:15", "2026-07-06 09:15"):
            self.assertEqual(ticket.ist_iso(ticket.parse_now(ts))[-6:], "+05:30")
        # IST date can differ from the UTC date
        self.assertEqual(ticket.to_ist(ticket.parse_now("2026-10-19T20:00:00Z")).date().isoformat(), "2026-10-20")

    def test_bad_now(self):
        with self.assertRaises(ticket.TicketError):
            ticket.parse_now("half past ten")


class Fingerprint(unittest.TestCase):
    def test_constants_match_the_spec(self):
        self.assertEqual(ticket.ORDER_FIELDS, SPEC_ORDER_FIELDS)
        self.assertEqual(ticket.STRING_FIELDS, ("qty", "limit_price", "stop_price", "take_profit_limit_price",
                                                "stop_loss_stop_price", "stop_loss_limit_price"))
        self.assertEqual(ticket.PRODUCTS, {"MIS", "CNC", "NRML"})
        self.assertEqual(ticket.EXCHANGES, {"NSE", "BSE"})
        self.assertEqual(ticket.TYPES, {"market", "limit", "stop", "stop_limit"})
        self.assertEqual(ticket.TIFS, {"day", "ioc"})

    def test_recomputed_with_the_spec_fields(self):
        for o in (order(), RELIANCE_LIMIT, SIMPLE):
            core = {k: o[k] for k in SPEC_ORDER_FIELDS if k in o and o[k] is not None}
            blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
            self.assertEqual(ticket.fingerprint(o), hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16])

    def test_why_and_plan_are_not_part_of_it_but_the_product_is(self):
        a = order()
        b = order(why="something else", plan={})
        self.assertEqual(ticket.fingerprint(a), ticket.fingerprint(b))
        self.assertNotEqual(ticket.fingerprint(a), ticket.fingerprint(order(product="CNC")))
        self.assertNotEqual(ticket.fingerprint(a), ticket.fingerprint(order(qty="53")))
        self.assertNotEqual(ticket.fingerprint(a), ticket.fingerprint(order(exchange="BSE")))

    def test_same_function_as_trader_risk(self):
        try:
            check = load("trader-risk", "check")
        except Exception as e:                                          # trader-risk may be mid-port
            self.skipTest("trader-risk/check.py does not load: %s" % e)
        if not hasattr(check, "ORDER_FIELDS"):
            self.skipTest("trader-risk/check.py is not ported to ORDER_FIELDS yet")
        self.assertEqual(check.ORDER_FIELDS, ticket.ORDER_FIELDS)
        for o in (order(), RELIANCE_LIMIT, SIMPLE):
            self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))


class TicketShape(unittest.TestCase):
    def test_no_broker_call_and_no_approval_step_is_built_any_more(self):
        # nothing here maps an order to a broker's API, and a PASS from trader-risk is the only gate
        for gone in ("tool_input", "KITE_ORDER_TYPES", "_is_zerodha", "_broker_name", "_tool_name",
                     "parse_yes", "cmd_approve", "cmd_decline", "cmd_result", "paper_only", "DEFAULT_PAPER_BUDGET"):
            self.assertFalse(hasattr(ticket, gone), gone)

    def test_bracket_exit_legs(self):
        legs = ticket.exit_legs(order())
        self.assertEqual(len(legs), 2)
        stop, target = legs
        self.assertEqual((stop["side"], stop["type"], stop["stop_price"], stop["qty"], stop["product"],
                          stop["exchange"], stop["time_in_force"]),
                         ("sell", "stop", "1406.40", "52", "MIS", "NSE", "day"))
        self.assertEqual((target["side"], target["type"], target["limit_price"]), ("sell", "limit", "1426.20"))
        both = ticket.exit_legs(order(stop_loss_limit_price="1405.50"), qty=40)
        self.assertEqual((both[0]["type"], both[0]["limit_price"], both[0]["qty"]), ("stop_limit", "1405.50", "40"))
        self.assertEqual(ticket.exit_legs(SIMPLE), [])
        # the two legs are alternatives for trader-risk; the marker is not part of the fingerprint
        self.assertEqual({l["exit_group"] for l in legs}, {"RELIANCE bracket exit"})
        self.assertEqual(ticket.fingerprint(stop),
                         ticket.fingerprint({k: v for k, v in stop.items() if k != "exit_group"}))

    def test_plain_english(self):
        self.assertEqual(ticket.plain(order()), RELIANCE_PLAIN)
        self.assertEqual(ticket.plain(SIMPLE), "BUY 10 shares of TCS (NSE, MIS), limit order at ₹3,500, good for today")
        self.assertEqual(ticket.plain({"symbol": "ITC", "side": "sell", "qty": "1", "type": "limit",
                                       "limit_price": "475.5", "time_in_force": "ioc", "product": "CNC"}),
                         "SELL 1 share of ITC (NSE, CNC), limit order at ₹475.50, immediate or cancel")
        lim = order(type="limit", limit_price="1416.30")
        self.assertEqual(ticket.plain(lim).split(" / ")[0],
                         "BUY 52 RELIANCE (NSE, MIS), limit 1416.30, good for today")
        self.assertEqual(ticket.max_loss(lim), (514.8, 1406.40))
        self.assertEqual(ticket.max_loss(order(type="limit", limit_price="1415.00")), (447.2, 1406.40))   # 52 x 8.60
        no_plan = {k: v for k, v in order().items() if k != "plan"}
        self.assertEqual(ticket.max_loss(no_plan), (None, 1406.40))

    def test_a_notional_order_renders_but_says_it_is_not_valid(self):
        n = {"symbol": "TCS", "side": "buy", "notional": "5000", "type": "market", "time_in_force": "day"}
        text = ticket.plain(n)
        self.assertIn("₹5,000", text)
        self.assertIn("whole shares only", text)
        self.assertIn("trader-risk blocks", text)


class Account(unittest.TestCase):
    def test_the_kite_files_and_keys_are_gone_and_the_account_has_one_lakh(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "trader-broker", "claude_desktop_config.example.json")))
        cfg = jload(os.path.join(ROOT, "trader-budget", "config.example.json"))
        self.assertNotIn("broker", cfg)
        self.assertNotIn("paper", cfg)
        self.assertEqual(cfg["budget"], 100000)
        self.assertEqual(ticket.DEFAULT_BUDGET, 100000)


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        self.cfg = os.path.join(self.d, "config.json")
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.none = os.path.join(self.d, "none")
        jdump(self.orders, {"orders": [order()]})
        jdump(self.cfg, CFG)

    def tearDown(self):
        shutil.rmtree(self.d)

    def set_cfg(self, **over):
        jdump(self.cfg, dict(CFG, **over))

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", NOW)]

    def place(self, index, *extra, **kw):
        return run(self.args("place", "--index", str(index), *extra, **kw) + ["--portfolio", self.pf])

    def write_risk(self, verdicts=None, date=TODAY, kinds=None):
        """What trader-risk would have written: one result per order in the orders file."""
        orders = jload(self.orders)["orders"]
        results = []
        for i, o in enumerate(orders):
            v = (verdicts or {}).get(i, "PASS")
            results.append({"fingerprint": ticket.fingerprint(o), "verdict": v,
                            "kind": (kinds or {}).get(i, "entry" if o["side"] == "buy" else "exit"),
                            "reasons": [] if v == "PASS" else ["window: no new entries before 09:30"],
                            "warnings": []})
        jdump(self.risk, {"date": date, "results": results})

    def state_doc(self):
        return jload(self.state)

    # ---- not ready / blocked / stale

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", text)
        self.assertNotIn("Place it", text)
        code, _, err = self.place(1, "--price", "1416.30")
        self.assertEqual(code, 1)
        self.assertIn("not ready", err)
        self.assertFalse(os.path.exists(self.pf))
        self.assertFalse(os.path.exists(self.state))

    def test_an_edited_order_is_not_ready(self):
        self.write_risk()
        doc = jload(self.orders)
        doc["orders"][0]["qty"] = "53"
        jdump(self.orders, doc)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("was not in the last risk check", text)
        self.assertEqual(self.place(1, "--price", "1416.30")[0], 1)
        self.assertFalse(os.path.exists(self.pf))

    def test_a_changed_product_is_not_ready(self):
        self.write_risk()
        doc = jload(self.orders)
        doc["orders"][0]["product"] = "CNC"
        jdump(self.orders, doc)
        self.assertIn("was not in the last risk check", run(self.args("show", "--index", "1"))[1])

    def test_yesterdays_check_does_not_count(self):
        self.write_risk(date="2026-10-16")
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("not today", text)
        self.assertEqual(self.place(1, "--price", "1416.30")[0], 1)

    def test_blocked_orders_are_never_placed(self):
        self.write_risk(verdicts={0: "BLOCK"})
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED by trader-risk: window: no new entries before 09:30", text)
        code, _, err = self.place(1, "--price", "1416.30")
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED by trader-risk", err)
        self.assertFalse(os.path.exists(self.pf))
        self.assertFalse(os.path.exists(self.state))

    # ---- rendering: a plain ticket that asks for nothing

    def test_the_ticket_is_plain_and_asks_for_nothing(self):
        self.write_risk()
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("ORDER 1 of 1\n"))
        for part in RELIANCE_PLAIN.split(" / "):
            self.assertIn("  " + part + "\n", text)
        self.assertIn("READY: trader-risk PASS\n", text)
        self.assertIn("Place it: ticket.py place --index 1 --price P", text)
        self.assertIn("Fills are simulated: no order is sent to a broker.", text)
        self.assertNotRegex(text, GONE)
        for forbidden in ("Reply", "Claude will call", "place_order", "tradingsymbol", "Allow once",
                          "LIVE ACCOUNT", "place_stock_order", "Alpaca", "mcp.kite.trade", "Zerodha", "Kite",
                          "connector"):
            self.assertNotIn(forbidden, text)

    def test_the_bracket_plan_is_entry_plus_two_follow_up_legs(self):
        self.write_risk()
        text = run(self.args("show", "--index", "1"))[1]
        self.assertIn("bracket PLAN in three parts, not one order", text)
        self.assertIn("Placing it records the entry", text)
        self.assertIn("SEPARATE follow-up", text)
        self.assertIn("1. stop-loss leg: SELL 52 shares of RELIANCE (NSE, MIS), stop order at ₹1,406.40, good for today", text)
        self.assertIn("2. target leg: SELL 52 shares of RELIANCE (NSE, MIS), limit order at ₹1,426.20, good for today", text)
        self.assertIn("each with its own trader-risk check", text)
        self.assertIn("There is no automatic OCO", text)
        self.assertIn("record that sell (ticket.py fill) and skip the other leg", " ".join(text.split()))
        self.assertNotIn("call:", text)
        self.assertNotIn("cancel_order", text)
        # a plain order has no bracket text
        jdump(self.orders, {"orders": [SIMPLE]})
        self.write_risk()
        text = run(self.args("show", "--index", "1"))[1]
        self.assertNotIn("bracket PLAN", text)
        self.assertNotIn("OCO", text)

    def test_show_json(self):
        self.write_risk()
        code, text, _ = run(self.args("show", "--index", "1", "--json"))
        t = json.loads(text)
        self.assertEqual((code, t["ready"], t["bracket"]), (0, True, True))
        for gone in ("broker", "tool", "tool_input", "sends_to_broker", "paper"):
            self.assertNotIn(gone, t)
        self.assertEqual((t["max_loss"], t["stop"]), (514.8, 1406.40))
        self.assertEqual(len(t["exit_legs"]), 2)
        self.assertEqual(sorted(t["exit_legs"][0]), ["plain"])
        self.assertEqual(t["fingerprint"], ticket.fingerprint(order()))

    def test_a_notional_ticket_is_blocked_by_risk_and_never_ready(self):
        n = {"symbol": "TCS", "exchange": "NSE", "side": "buy", "notional": "5000", "type": "market",
             "product": "MIS", "time_in_force": "day"}
        jdump(self.orders, {"orders": [n]})
        self.write_risk(verdicts={0: "BLOCK"})
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("₹5,000", text)
        self.assertIn("whole shares only", text)
        self.assertIn("BLOCKED", text)

    # ---- the full flow: a PASS is the only gate

    def test_the_full_flow(self):
        self.write_risk()
        code, text, _ = self.place(1, "--price", "1416.30", now="2026-10-19 10:16")
        self.assertEqual(code, 0)
        self.assertIn("Placed order 1: " + RELIANCE_PLAIN + ".", text)
        self.assertIn("Filled 52 RELIANCE at ₹1,416.30 (simulated: nothing is sent to a broker). "
                      "Cash now ₹26,352.40.", text)
        self.assertIn("ticket.py exits --index 1", text)
        self.assertNotRegex(text, GONE)
        for forbidden in ("Now call", "place_order", "tradingsymbol", "Allow once", "place_stock_order"):
            self.assertNotIn(forbidden, text)
        st = self.state_doc()
        self.assertEqual([r["status"] for r in st["orders"]], ["placed", "filled"])
        plc = st["orders"][0]
        self.assertEqual((plc["kind"], plc["max_loss"], plc["date"], plc["side"], plc["symbol"], plc["qty"]),
                         ("entry", 514.8, TODAY, "buy", "RELIANCE", "52"))
        self.assertEqual(plc["at"], "2026-10-19T10:16:00+05:30")
        for gone in ("paper", "reply", "broker"):
            self.assertNotIn(gone, plc)
        pf = jload(self.pf)
        self.assertEqual(pf["cash"], 26352.4)          # 100000 - 52 x 1416.30 = 100000 - 73647.60
        self.assertEqual(pf["positions"][0]["shares"], 52)
        self.assertEqual(pf["positions"][0]["lots"][0]["cost"], 73647.6)

        # placed once: it is DONE, and a second place does not fill it twice
        self.assertIn("DONE: placed", run(self.args("show", "--index", "1"))[1])
        code, _, err = self.place(1, "--price", "1416.30")
        self.assertEqual(code, 1)
        self.assertIn("DONE: placed", err)
        self.assertEqual(jload(self.pf)["cash"], 26352.4)
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn("1 placed today", text)
        self.assertIn("max loss ₹514.80", text)
        self.assertNotRegex(text, GONE)

    def test_a_market_order_needs_the_price_it_fills_at(self):
        self.write_risk()
        code, _, err = self.place(1)
        self.assertEqual(code, 1)
        self.assertIn("a market order fills at once", err)
        for extra in (["--price", "abc"], ["--price", "0"], ["--price", "-5"],
                      ["--price", "1416.30", "--qty", "53"], ["--price", "1416.30", "--qty", "2.5"],
                      ["--price", "1416.30", "--qty", "0"], ["--qty", "10"]):
            code, _, err = self.place(1, *extra)
            self.assertEqual(code, 1, extra)
            self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.state))          # none of them recorded anything
        self.assertFalse(os.path.exists(self.pf))

    def test_a_partial_fill(self):
        self.write_risk()
        code, text, _ = self.place(1, "--price", "1416.30", "--qty", "20")
        self.assertEqual(code, 0)
        self.assertIn("Filled 20 RELIANCE at ₹1,416.30", text)
        self.assertEqual(jload(self.pf)["cash"], 71674.0)       # 100000 - 20 x 1416.30
        self.assertEqual(jload(self.pf)["positions"][0]["shares"], 20)
        code, text, _ = run(self.args("exits", "--index", "1", "--qty", "20"))
        self.assertEqual(code, 0)
        self.assertIn("Exit legs for RELIANCE (20 shares)", text)

    def test_a_limit_order_without_a_price_rests(self):
        jdump(self.orders, {"orders": [dict(RELIANCE_LIMIT)]})
        self.write_risk()
        self.assertEqual(self.place(1, "--qty", "5")[0], 1)     # --qty goes with --price
        code, text, _ = self.place(1)
        self.assertEqual(code, 0)
        self.assertIn("Resting: nothing has filled yet.", text)
        self.assertIn("ticket.py fill --symbol RELIANCE --side buy --qty 52 --price P", text)
        self.assertIn("Nothing triggers a resting order by itself", text)
        self.assertIn("Then: ticket.py exits --index 1.", text)
        self.assertFalse(os.path.exists(self.pf))               # no fill: the account is untouched
        self.assertEqual([r["status"] for r in self.state_doc()["orders"]], ["placed"])
        # the fill comes later, when the price is reached
        code, text, _ = run(["fill", "--symbol", "RELIANCE", "--side", "buy", "--qty", "52", "--price", "1416.30",
                             "--portfolio", self.pf, "--config", self.cfg, "--state", self.state, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertEqual(jload(self.pf)["cash"], 26352.4)

    def test_a_sell_of_shares_the_account_does_not_hold_is_not_recorded(self):
        sell = {"symbol": "RELIANCE", "exchange": "NSE", "side": "sell", "qty": "52", "type": "market",
                "product": "MIS", "time_in_force": "day"}
        jdump(self.orders, {"orders": [sell]})
        self.write_risk()
        code, _, err = self.place(1, "--price", "1420.10")
        self.assertEqual(code, 1)
        self.assertIn("cannot record selling 52 RELIANCE", err)
        self.assertFalse(os.path.exists(self.state))          # nothing was logged as placed

    # ---- the exit legs and flatten

    def place_entry(self):
        self.write_risk()
        self.place(1, "--price", "1416.30")

    def test_exits_writes_the_two_legs_after_the_entry(self):
        code, _, err = run(self.args("exits", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("is not recorded as placed today", err)
        self.place_entry()
        code, text, _ = run(self.args("exits", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("SELL 52 shares of RELIANCE (NSE, MIS), stop order at ₹1,406.40", text)
        self.assertIn("SELL 52 shares of RELIANCE (NSE, MIS), limit order at ₹1,426.20", text)
        self.assertIn("replaced 1 earlier proposal", text)
        self.assertIn("There is no automatic OCO", text)
        self.assertIn("ticket.py place --index 1 (the stop-loss leg) and --index 2 (the target leg)", text)
        self.assertNotRegex(text, GONE)
        legs = jload(self.orders)["orders"]
        self.assertEqual([(l["type"], l["side"], l["product"], l["exchange"]) for l in legs],
                         [("stop", "sell", "MIS", "NSE"), ("limit", "sell", "MIS", "NSE")])
        # each leg is its own ticket: it needs its own risk result, then it rests until it fills
        code, _, err = self.place(1)
        self.assertEqual(code, 1)                                # the last check was for the entry
        self.assertIn("not ready", err)
        self.write_risk()
        for index in (1, 2):
            code, text, _ = self.place(index)
            self.assertEqual(code, 0, index)
            self.assertIn("Resting: nothing has filled yet.", text)
        self.assertEqual(jload(self.pf)["positions"][0]["shares"], 52)      # nothing was sold by placing a leg
        self.assertEqual(self.place(3)[0], 1)
        self.assertEqual(run(self.args("exits", "--index", "3"))[0], 1)
        # the target is hit: record that sell, and the other leg is simply skipped
        code, text, _ = run(["fill", "--symbol", "RELIANCE", "--side", "sell", "--qty", "52", "--price", "1426.20",
                             "--portfolio", self.pf, "--config", self.cfg, "--state", self.state, "--now", NOW])
        self.assertEqual(code, 0)
        pf = jload(self.pf)
        self.assertEqual((pf["cash"], pf["positions"]), (100514.8, []))     # 26352.40 + 52 x 1426.20

    def test_exits_refuses_a_plain_order_and_a_fractional_qty(self):
        jdump(self.orders, {"orders": [SIMPLE]})
        self.write_risk()
        self.assertEqual(self.place(1)[0], 0)                   # a limit order with no price: it rests
        code, _, err = run(self.args("exits", "--index", "1"))
        self.assertIn("not a bracket plan", err)
        jdump(self.orders, {"orders": [order()]})
        self.place_entry()
        code, _, err = run(self.args("exits", "--index", "1", "--qty", "10.5"))
        self.assertEqual(code, 1)
        self.assertIn("whole shares", err)

    def test_flatten_by_flat_by(self):
        self.place_entry()
        late = "2026-10-19 15:00"                                   # flat_by: before every broker's auto square-off
        plan = os.path.join(self.d, "plan.json")
        jdump(plan, {"symbol": "RELIANCE", "flat_by": "15:00"})
        code, text, _ = run(["flatten", "--symbol", "RELIANCE", "--orders", self.orders, "--portfolio", self.pf,
                             "--plan", plan, "--config", self.cfg, "--now", late])
        self.assertEqual(code, 0)
        self.assertIn("SELL 52 shares of RELIANCE (NSE, MIS), market order, good for today (flat by 15:00 IST)", text)
        self.assertIn("replaced 1 earlier proposal", text)
        self.assertIn("ticket.py place --index 1 --price P", text)
        self.assertNotIn("cancel", text.lower())
        self.assertNotRegex(text, GONE)
        doc = jload(self.orders)
        o = doc["orders"][0]
        self.assertEqual((o["side"], o["exchange"], o["product"], o["type"], o["qty"], o["time_in_force"]),
                         ("sell", "NSE", "MIS", "market", "52", "day"))
        self.assertNotIn("paper", doc)
        self.write_risk(date=TODAY, kinds={0: "exit"})
        code, text, _ = run(self.args("show", now=late))
        self.assertEqual(code, 0)
        self.assertIn("READY", text)
        code, text, _ = self.place(1, "--price", "1420.10", now="2026-10-19 15:01")
        self.assertEqual(code, 0)
        self.assertIn("Filled 52 RELIANCE at ₹1,420.10", text)
        self.assertNotIn("ticket.py exits", text)                # a sell has no exit legs
        pf = jload(self.pf)
        self.assertEqual((pf["cash"], pf["positions"]), (100197.6, []))     # 26352.40 + 52 x 1420.10 = 100197.60
        code, _, err = run(["flatten", "--symbol", "RELIANCE", "--orders", self.orders, "--portfolio", self.pf,
                            "--now", late])
        self.assertEqual(code, 1)
        self.assertIn("nothing to flatten", err)

    def test_flatten_takes_the_quantity_from_the_portfolio(self):
        jdump(self.pf, {"cash": 26352.4, "positions": [{"ticker": "RELIANCE", "shares": 52, "lots": []}]})
        code, text, _ = run(["flatten", "--symbol", "reliance", "--orders", self.orders, "--portfolio", self.pf,
                             "--plan", self.none, "--config", self.cfg, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("flat by 15:00 IST", text)                            # the default
        self.assertIn("The qty comes from %s: check it before you place it." % self.pf, text)
        self.assertIn("ticket.py place --index 1 --price P", text)
        self.assertNotIn("broker's positions", text)
        o = jload(self.orders)["orders"][0]
        self.assertIn("(qty from %s)" % self.pf, o["why"])
        self.assertIn("before the MIS auto square-off window", o["why"])
        code, text, _ = run(["flatten", "--symbol", "reliance", "--qty", "52", "--orders", self.orders,
                             "--portfolio", self.none, "--plan", self.none, "--config", self.cfg, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertNotIn("The qty comes from", text)
        self.assertIn("(qty from your portfolio)", jload(self.orders)["orders"][0]["why"])

    def test_flatten_takes_flat_by_from_config_and_checks_qty(self):
        self.set_cfg(flat_by="15:05")
        code, text, _ = run(["flatten", "--symbol", "TCS", "--qty", "7", "--orders", self.orders,
                             "--plan", self.none, "--config", self.cfg, "--now", NOW])
        self.assertIn("flat by 15:05 IST", text)
        self.assertEqual(run(["flatten", "--symbol", "TCS", "--qty", "0", "--orders", self.orders])[0], 1)
        code, _, err = run(["flatten", "--symbol", "TCS", "--qty", "2.5", "--orders", self.orders,
                            "--config", self.cfg])
        self.assertEqual(code, 1)
        self.assertIn("whole shares only", err)
        code, text, _ = run(["flatten", "--symbol", "M&M", "--qty", "3", "--orders", self.orders,
                             "--plan", self.none, "--config", self.none])
        self.assertEqual(code, 0)
        self.assertIn("flat by 15:00 IST", text)                            # no config at all: the default
        self.assertEqual(jload(self.orders)["orders"][0]["symbol"], "M&M")

    # ---- portfolio / fills

    def test_fill_buy_and_sell_with_lots(self):
        pf = {"cash": 0, "positions": [{"ticker": "ITC", "shares": 30, "lots": [
            {"id": "ITC-1", "date": "2025-01-02", "shares": 20, "cost": 9000.0},
            {"id": "ITC-2", "date": "2026-01-02", "shares": 10, "cost": 4800.0}]}]}
        jdump(self.pf, pf)
        code, text, _ = run(["fill", "--symbol", "ITC", "--side", "sell", "--qty", "25", "--price", "480",
                             "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 0)
        self.assertIn("Cash now ₹12,000", text)
        pf = jload(self.pf)
        self.assertEqual(pf["cash"], 12000.0)
        lots = pf["positions"][0]["lots"]
        self.assertEqual([l["id"] for l in lots], ["ITC-2"])       # the oldest lot went first
        self.assertEqual(lots[0]["shares"], 5)
        self.assertEqual(lots[0]["cost"], 2400.0)
        self.assertEqual(pf["positions"][0]["shares"], 5)
        self.assertEqual(self.state_doc()["orders"][-1]["status"], "filled")
        code, _, err = run(["fill", "--symbol", "ITC", "--side", "sell", "--qty", "50", "--price", "480",
                            "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        self.assertEqual(run(["fill", "--symbol", "ITC", "--side", "buy", "--qty", "0", "--price", "480",
                              "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])[0], 1)

    def test_fill_starts_the_portfolio_from_the_budget(self):
        code, text, _ = run(["fill", "--symbol", "RELIANCE", "--side", "buy", "--qty", "52", "--price", "1416.30",
                             "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg, "--state", self.state,
                             "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("Cash now ₹26,352.40", text)
        self.assertEqual(jload(self.pf)["cash"], 26352.4)
        self.assertEqual(self.state_doc()["orders"][0]["at"], "2026-10-19T10:15:00+05:30")

    def test_a_new_account_starts_with_one_lakh_unless_the_config_says_otherwise(self):
        self.assertEqual(ticket.DEFAULT_BUDGET, 100000)
        code, text, _ = run(["fill", "--symbol", "RELIANCE", "--side", "buy", "--qty", "1", "--price", "1416.30",
                             "--portfolio", self.pf, "--config", self.none, "--state", self.state])
        self.assertEqual(code, 0)                              # no config at all: the account has 1,00,000
        self.assertIn("Cash now ₹98,583.70", text)
        self.assertEqual(jload(self.pf)["cash"], 98583.7)
        os.remove(self.pf)
        self.set_cfg(budget=250000)
        run(["fill", "--symbol", "RELIANCE", "--side", "buy", "--qty", "1", "--price", "1416.30",
             "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(jload(self.pf)["cash"], 248583.7)

    def test_a_config_that_still_says_paper_is_ignored(self):
        # an old config key does nothing: there is no mode to switch, and nothing is refused
        self.set_cfg(paper=False)
        self.write_risk()
        code, text, _ = self.place(1, "--price", "1416.30")
        self.assertEqual(code, 0)
        self.assertIn("Placed order 1", text)

    def test_log_all_and_empty(self):
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("No trades recorded today", text)
        self.write_risk()
        self.place(1, "--price", "1416.30")
        code, text, _ = run(["log", "--state", self.state, "--now", "2026-10-21 10:00"])
        self.assertEqual(code, 1)                              # nothing on the 21st
        code, text, _ = run(["log", "--state", self.state, "--now", "2026-10-21 10:00", "--all"])
        self.assertEqual(code, 0)
        self.assertIn("placed", text)
        self.assertIn("filled", text)


class Console(unittest.TestCase):
    def test_utf8_helper_survives_streams_that_cannot_reconfigure(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            ticket._utf8_stdio()                                # StringIO has no reconfigure: must not raise

    def test_the_rupee_sign_prints_through_main(self):
        d = tempfile.mkdtemp()
        try:
            code, text, _ = run(["fill", "--symbol", "TCS", "--side", "buy", "--qty", "1", "--price", "3500",
                                 "--portfolio", os.path.join(d, "p.json"), "--state", os.path.join(d, "s.json"),
                                 "--config", os.path.join(d, "none")])
            self.assertEqual(code, 0)                           # a fresh account: 1,00,000 less 3,500
            self.assertIn("Cash now ₹96,500", text)
        finally:
            shutil.rmtree(d)


class NoLeftovers(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(ROOT, "trader-broker", name), encoding="utf-8") as fh:
            return fh.read()

    def hunt(self, bad, names=("ticket.py", "SKILL.md")):
        for name in names:
            text = self.read(name)
            for m in bad.finditer(text):
                line = text[:m.start()].count("\n") + 1
                self.fail("%s:%d still says %r" % (name, line, m.group(0)))

    def test_no_us_market_words_in_the_script_or_skill(self):
        self.hunt(re.compile(r"Alpaca|alpaca|IBKR|Interactive Brokers|New York|NYSE|Nasdaq|\bPDT\b|\bEDT\b|\bEST\b|"
                             r"\bET\b|place_stock_order|cancel_order_by_id|get_open_position|TSLA|AAPL"))

    def test_no_live_broker_or_kite_left_in_the_script_or_skill(self):
        self.hunt(re.compile(r"Zerodha|Kite|kite|MCP|mcp|place_order|Allow once|Needs approval|Always allow|"
                             r"static IP|connector|LIVE ACCOUNT"))

    def test_no_paper_talk_and_no_approval_step_in_the_script_or_skill(self):
        self.hunt(GONE)


if __name__ == "__main__":
    unittest.main()
