import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal
from unittest import mock

from _load import load, ROOT, FIX

size = load("trader-budget", "size")

CFG = os.path.join(ROOT, "trader-budget", "config.example.json")
PLAN = os.path.join(FIX, "trader-budget", "plan-reliance.json")
TARGETS = os.path.join(ROOT, "trader-budget", "targets.example.json")
RAW = os.path.join(FIX, "trader-budget", "nifty-illustrative-weights.json")
PRICES_FILE = os.path.join(FIX, "trader-budget", "prices-illustrative.json")
ORDERS_FIXTURE = os.path.join(FIX, "trader-risk", "orders-reliance.json")
NOW = "2026-10-19 09:40"            # IST: the 09:30 bar closed at 09:35, the plan is five minutes old
TODAY = "2026-10-19"
RUPEE = "₹"
# SYNTHETIC / ILLUSTRATIVE prices and weights for the five-name split (not real, not a recommendation); every
# price is on NSE's tick for its level (0.05 from 250 to 1,000, 0.10 from 1,000 to 5,000)
PRICES = {"RELIANCE": 1416.30, "HDFCBANK": 950.40, "TCS": 3150.30, "INFY": 1580.10, "ITC": 410.55}
SPLIT = {"HDFCBANK": 24710.40, "RELIANCE": 24077.10, "INFY": 17381.10, "TCS": 12601.20, "ITC": 11084.85}
SPLIT_SHARES = {"HDFCBANK": 26, "RELIANCE": 17, "INFY": 11, "TCS": 4, "ITC": 27}
# the contract's config, key for key
CONTRACT_CFG = {
    "budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 80000, "min_order_inr": 100,
    "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000, "max_trades_per_day": 3, "max_orders_per_day": 10,
    "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:30", "no_entries_after": "14:30", "flat_by": "15:00",
    "tick_size": None, "fractional": False, "paper": True, "blocked": [],
    "allow_options": False, "allow_margin": False, "allow_short": False,
    "brokerage_pct": 0.03, "brokerage_per_order_inr": 20,
}
# the rules for the weights-mode tests: a 25% cap per stock
SPLIT_CFG = {"budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 40000,
             "min_order_inr": 100, "paper": True, "blocked": []}


def cfg(**over):
    with open(CFG, encoding="utf-8") as fh:
        c = json.load(fh)
    c.update(over)
    return c


def split_cfg(**over):
    c = dict(SPLIT_CFG)
    c.update(over)
    return c


def plan(**over):
    with open(PLAN, encoding="utf-8") as fh:
        p = json.load(fh)
    p.update(over)
    return p


def rows(path):
    with open(path, encoding="utf-8") as fh:
        return size.parse_weights(json.load(fh))


def rupees(result):
    return {p["ticker"]: p["rupees"] for p in result["positions"]}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


def prices_doc(price, quote_time="2026-10-19T05:22:13Z"):          # 10:52:13 IST
    return {"as_of": quote_time, "source": "illustrative", "prices": {"RELIANCE": price},
            "detail": {"RELIANCE": {"prev_close": 1400.0, "time": quote_time, "source": "illustrative"}}}


class TheCanonicalTrade(unittest.TestCase):
    """RELIANCE, 2026-10-19. Rs 1,00,000 budget, 10% buffer, Rs 1,000 max loss: entry 1416.30, stop 1406.40
    (risk 9.90), 52 shares = Rs 73,647.60, max loss Rs 514.80, bound by the 75% position cap."""

    def test_config_example_is_the_contract(self):
        with open(CFG, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), CONTRACT_CFG)

    def test_reliance_is_52_shares_bound_by_the_position_cap(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 52)
        self.assertEqual(s["bound_by"], "position cap")
        self.assertEqual(s["risk_per_share"], 9.9)
        self.assertEqual(s["cost"], 73647.60)                  # 52 x 1416.30
        self.assertEqual(s["max_loss"], 514.80)                # 52 x 9.90
        self.assertEqual(s["reward_risk"], 1.0)
        self.assertEqual(s["cash_buffer"], 10000)
        self.assertEqual(s["tick_size"], 0.1)                  # a stock between Rs 1,000 and Rs 5,000
        # 1000 / 9.90 = 101.01 ; 75% of 1,00,000 = 75,000 / 1416.30 = 52.96 ; 80,000 / 1416.30 = 56.49 ;
        # 90,000 / 1416.30 = 63.55
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]],
                         [("max loss", 101), ("position cap", 52), ("max order", 56), ("cash", 63)])
        self.assertEqual([l["name"] for l in s["limits"] if l["binds"]], ["position cap"])
        self.assertIsNone(s["refusal"])
        self.assertEqual((s["exchange"], s["product"], s["tick_notes"]), ("NSE", "MIS", []))

    def test_the_bracket_order(self):
        o = size.bracket_order(size.size_trade(plan(), cfg(), today=TODAY))
        why = o.pop("why")
        self.assertEqual(o, {
            "symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "qty": "52", "type": "market",
            "product": "MIS", "time_in_force": "day",
            "order_class": "bracket", "take_profit_limit_price": "1426.20", "stop_loss_stop_price": "1406.40",
            "plan": {"entry": 1416.30, "stop": 1406.40, "target": 1426.20, "risk_per_share": 9.9,
                     "max_loss": 514.8, "setup": "orb", "entry_time": "9:35 AM", "flat_by": "15:00"}})
        self.assertIn("bound by your position cap", why)
        self.assertIn(RUPEE + "514.80", why)
        self.assertEqual(set(o) - {"limit_price"}, {"symbol", "exchange", "side", "qty", "type", "product",
                                                   "time_in_force", "order_class", "take_profit_limit_price",
                                                   "stop_loss_stop_price", "plan"})

    def test_the_fixture_orders_file_is_what_it_writes(self):
        with open(ORDERS_FIXTURE, encoding="utf-8") as fh:
            fixture = json.load(fh)["orders"][0]
        self.assertEqual(size.bracket_order(size.size_trade(plan(), cfg(), today=TODAY)), fixture)

    def test_a_limit_order_rounds_to_the_tick(self):
        o = size.bracket_order(size.size_trade(plan(), cfg(), limit=1416.30, today=TODAY))
        self.assertEqual((o["type"], o["limit_price"], o["qty"]), ("limit", "1416.30", "52"))
        s = size.size_trade(plan(), cfg(), limit=1415.00, today=TODAY)
        # risk 1415.00 - 1406.40 = 8.60 ; 75,000 / 1415.00 = 53.00 shares ; 53 x 8.60 = 455.80
        self.assertEqual((s["risk_per_share"], s["qty"], s["max_loss"], s["tick_notes"]), (8.6, 53, 455.8, []))
        self.assertEqual(size.bracket_order(s)["limit_price"], "1415.00")
        s = size.size_trade(plan(), cfg(), limit=1416.33, today=TODAY)          # 1416.33 -> 1416.30 on the 0.10 tick
        self.assertEqual((s["entry"], s["entry_from"], s["qty"]), (1416.30, "limit", 52))
        self.assertIn("limit price 1416.33 is not on the %s0.10 tick: rounded to 1416.30" % RUPEE,
                      s["tick_notes"][0])
        s = size.size_trade(plan(), cfg(), limit=1416.35, today=TODAY)          # half way: half up -> 1416.40
        self.assertEqual((s["entry"], s["reward_risk"]), (1416.40, 0.98))
        self.assertIn("reward:risk 0.98", s["refusal"])                          # reward 9.80 against a risk of 10.00
        s = size.size_trade(plan(), cfg(min_reward_risk=0.9), limit=1416.35, today=TODAY)
        self.assertEqual(size.bracket_order(s)["limit_price"], "1416.40")

    def test_cli_writes_one_bracket_order(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("Size: 52 shares of RELIANCE = %s73,647.60 at 1416.30" % RUPEE, text)
            self.assertIn("52 x 9.90 = %s514.80" % RUPEE, text)
            self.assertIn("<- binds", text)
            self.assertIn("(placed after the entry fills)", text)
            self.assertIn("no automatic one-cancels-the-other", text)
            self.assertIn("Flat by 15:00 IST", text)
            self.assertIn("auto square-off MIS positions, from about 15:05 (F&O stocks) to 15:20", text)
            self.assertIn("lower circuit", text)
            self.assertIn("IST", text)
            self.assertIn("not financial advice", text)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["status"], "proposed")
            self.assertIs(doc["paper"], True)
            self.assertEqual(doc["created_at"], "2026-10-19T04:10:00Z")        # 09:40 IST
            self.assertEqual(len(doc["orders"]), 1)
            self.assertEqual(doc["orders"][0]["plan"]["max_loss"], 514.8)
            self.assertEqual((doc["orders"][0]["exchange"], doc["orders"][0]["product"]), ("NSE", "MIS"))

    def test_cli_json_and_limit(self):
        code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW,
                             "--limit", "--json"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(out["sizing"]["qty"], 52)
        self.assertEqual(out["orders"][0]["limit_price"], "1416.30")

    def test_the_rupee_sign_survives_a_cp1252_console(self):
        raw = io.BytesIO()
        console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        with mock.patch.object(sys, "stdout", console):
            code = size.main(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW])
            sys.stdout.flush()
        self.assertEqual(code, 0)
        self.assertIn(RUPEE.encode("utf-8"), raw.getvalue())


class Charges(unittest.TestCase):
    """Estimated round-trip charges: informational, never part of the share count."""

    def test_hand_computed_round_trip_at_the_target(self):
        # buy 52 x 1416.30 = 73,647.60 ; sell 52 x 1426.20 = 74,162.40 ; both sides = 1,47,810.00
        # brokerage: 0.03% of each side is 22.09 and 22.25, both over the Rs 20 cap -> 20 + 20 = 40.00
        # STT 0.025% of the sell side = 18.5406 -> 18.54
        # stamp duty 0.003% of the buy side = 2.2094 -> 2.21
        # NSE transaction 0.00307% of 1,47,810.00 = 4.5378 -> 4.54 ; SEBI 0.0001% = 0.1478 -> 0.15
        # GST 18% of (40 + 4.5378 + 0.1478) = 8.0434 -> 8.04 ; total 40 + 18.54 + 2.21 + 4.54 + 0.15 + 8.04 = 73.48
        ch = size.charges_for(52, 1416.30, 1426.20, cfg())
        self.assertEqual(ch, {"exit_price": 1426.20, "brokerage": 40.0, "stt": 18.54, "stamp_duty": 2.21,
                              "exchange_txn": 4.54, "sebi_fee": 0.15, "gst": 8.04, "total": 73.48})

    def test_at_the_stop_and_at_the_entry(self):
        stop = size.charges_for(52, 1416.30, 1406.40, cfg())      # sell 52 x 1406.40 = 73,132.80
        self.assertEqual((stop["stt"], stop["exchange_txn"], stop["gst"], stop["total"]), (18.28, 4.51, 8.04, 73.19))
        flat = size.charges_for(52, 1416.30, 1416.30, cfg())      # sell 73,647.60: STT 18.41, exchange 4.52, GST 8.04
        self.assertEqual((flat["stt"], flat["exchange_txn"], flat["gst"], flat["total"]), (18.41, 4.52, 8.04, 73.33))

    def test_the_rates_are_the_ones_on_zerodhas_charge_page(self):
        # https://zerodha.com/charges, equity intraday, read 2026-11-04. Brokers differ in brokerage only; the rest
        # is statutory. The NSE transaction charge is 0.00307% (not the older 0.00297%).
        self.assertEqual((size.STT_SELL_PCT, size.STAMP_BUY_PCT, size.EXCHANGE_TXN_PCT, size.SEBI_PCT, size.GST_PCT),
                         (Decimal("0.025"), Decimal("0.003"), Decimal("0.00307"), Decimal("0.0001"), Decimal("18")))
        self.assertEqual((size.DEFAULTS["brokerage_pct"], size.DEFAULTS["brokerage_per_order_inr"]), (0.03, 20))

    def test_a_tiny_order_pays_the_percentage_not_the_cap(self):
        ch = size.charges_for(1, 100, 100, cfg())
        self.assertEqual(ch["brokerage"], 0.06)          # 0.03% of Rs 100 is 0.03 a side, under the Rs 20 cap
        self.assertEqual(ch["total"], 0.11)

    def test_brokerage_is_config_driven(self):
        free = size.charges_for(52, 1416.30, 1426.20, cfg(brokerage_per_order_inr=0))
        self.assertEqual(free["brokerage"], 0.0)
        flat = size.charges_for(52, 1416.30, 1426.20, cfg(brokerage_pct=1.0, brokerage_per_order_inr=100))
        self.assertEqual(flat["brokerage"], 200.0)

    def test_the_sizing_carries_the_estimate_and_the_share_count_ignores_it(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual([s["charges"][k]["total"] for k in ("at_entry", "at_stop", "at_target")],
                         [73.33, 73.19, 73.48])
        self.assertIn("verify with your broker", s["charges"]["note"])
        big = size.size_trade(plan(), cfg(brokerage_pct=5, brokerage_per_order_inr=5000), today=TODAY)
        self.assertEqual((big["qty"], big["max_loss"]), (52, 514.80))
        self.assertGreater(big["charges"]["at_stop"]["total"], 5000)

    def test_the_report_and_json_say_it(self):
        code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW])
        self.assertIn("Estimated charges at the stop %s73.19, at the target %s73.48: verify with your "
                      "broker's calculator" % (RUPEE, RUPEE), text)
        self.assertIn("never change the share count", text)
        out = json.loads(run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW,
                              "--json"])[1])
        self.assertEqual(out["sizing"]["charges"]["at_target"]["total"], 73.48)

    def test_no_estimate_when_there_is_no_order(self):
        s = size.size_trade(plan(stop=300.0), cfg(), today=TODAY)
        self.assertIsNone(s["charges"])


class TickAndMoney(unittest.TestCase):
    def test_tick_rounding_helpers(self):
        self.assertEqual(size.floor_tick(1406.475), 1406.40)
        self.assertEqual(size.floor_tick(1406.49), 1406.40)
        self.assertEqual(size.floor_tick(1406.50), 1406.50)
        self.assertEqual(size.round_tick(1426.225), 1426.20)         # half up
        self.assertEqual(size.round_tick(1426.22), 1426.20)
        self.assertEqual(size.round_tick(1416.375), 1416.40)
        self.assertEqual(size.round_tick(1406.47, 0.01), 1406.47)
        self.assertEqual(size.to_paise(1416.30), 141630)
        self.assertEqual(size.to_paise(1419.475), 141948)

    def test_nses_tick_depends_on_the_price(self):
        """Circular NSE/CMTR/67133: 0.01 under 250; 0.05 from 250 to 1,000; 0.10 above 1,000 to 5,000; 0.50 above
        5,000 to 10,000; 1.00 above 10,000 to 20,000; 5.00 above 20,000."""
        for price, tick in ((9.40, 0.01), (21.50, 0.01), (249.99, 0.01), (250, 0.05), (410.55, 0.05), (1000, 0.05),
                            (1000.05, 0.10), (1416.30, 0.10), (3050.30, 0.10), (5000, 0.10), (5000.50, 0.50),
                            (8800, 0.50), (10000, 0.50), (10000.50, 1.00), (15500, 1.00), (20000, 1.00),
                            (20000.50, 5.00), (45000, 5.00)):
            self.assertEqual(size.tick_for(price), tick, price)
        self.assertEqual(size.floor_tick(1406.475), 1406.40)                    # the tick is taken from the price
        self.assertEqual(size.floor_tick(410.57), 410.55)
        self.assertEqual(size.floor_tick(148.756), 148.75)
        self.assertEqual(size.floor_tick(8801.3), 8801.0)
        self.assertEqual(size.floor_tick(15499.9), 15499.0)
        self.assertEqual(size.round_tick(24012.4), 24010.0)                     # 5.00 above Rs 20,000
        self.assertEqual(size.round_tick(24012.5), 24015.0)
        self.assertEqual(size.round_tick(410.57), 410.55)
        self.assertIsNone(size.tick_of({}))
        self.assertIsNone(size.tick_of({"tick_size": None}))
        self.assertEqual(size.tick_of({"tick_size": 0.05}), 0.05)

    def test_each_price_is_rounded_on_its_own_tick(self):
        # a Rs 410 stock trades on the 0.05 tick: stop 405.07 rounds DOWN to 405.05, target 421.38 to 421.40
        cheap = plan(symbol="ITC", entry=410.55, stop=405.07, target=421.38)
        s = size.size_trade(cheap, cfg(), today=TODAY)
        self.assertEqual((s["stop"], s["target"], s["tick_size"]), (405.05, 421.40, 0.05))
        self.assertIn("stop 405.07 is not on the %s0.05 tick: rounded DOWN to 405.05" % RUPEE, s["tick_notes"][0])
        # a Rs 150 stock on the 0.01 tick keeps paise
        small = plan(symbol="X", entry=150.00, stop=148.756, target=151.50)
        s = size.size_trade(small, cfg(), today=TODAY)
        self.assertEqual((s["stop"], s["target"], s["tick_size"]), (148.75, 151.50, 0.01))
        # a Rs 15,500 stock on the Rs 1 tick: whole rupees only
        big = plan(symbol="MARUTI", entry=15500.0, stop=15480.4, target=15520.4)
        s = size.size_trade(big, cfg(), today=TODAY)
        self.assertEqual((s["stop"], s["target"], s["tick_size"]), (15480.0, 15520.0, 1.0))
        self.assertEqual(size.bracket_order(s)["stop_loss_stop_price"], "15480.00")

    def test_a_plan_made_with_a_tick_override_keeps_it(self):
        # trader-exit --tick-size 0.05 writes tick_override: true; the config has none, so the plan's tick rules
        s = size.size_trade(plan(tick_override=True, tick_size=0.05, stop=1406.47), cfg(), today=TODAY)
        self.assertEqual((s["stop"], s["tick_size"]), (1406.45, 0.05))
        s = size.size_trade(plan(tick_override=False, tick_size=0.05, stop=1406.47), cfg(), today=TODAY)
        self.assertEqual((s["stop"], s["tick_size"]), (1406.40, 0.1))       # not an override: NSE's tick
        s = size.size_trade(plan(tick_override=True, tick_size=0.05, stop=1406.47), cfg(tick_size=0.01), today=TODAY)
        self.assertEqual((s["stop"], s["tick_size"]), (1406.47, 0.01))       # the config wins over the plan

    def test_stop_rounds_down_and_target_to_the_nearest_tick_and_it_says_so(self):
        s = size.size_trade(plan(stop=1406.49, target=1426.225), cfg(), today=TODAY)
        self.assertEqual((s["stop"], s["target"]), (1406.40, 1426.20))
        self.assertEqual(len(s["tick_notes"]), 2)
        self.assertIn("stop 1406.49 is not on the %s0.10 tick: rounded DOWN to 1406.40" % RUPEE, s["tick_notes"][0])
        self.assertIn("target 1426.225 is not on the %s0.10 tick: rounded to 1426.20" % RUPEE, s["tick_notes"][1])
        o = size.bracket_order(s)
        self.assertEqual((o["stop_loss_stop_price"], o["take_profit_limit_price"]), ("1406.40", "1426.20"))
        s = size.size_trade(plan(stop=1406.47, target=1426.22), cfg(), today=TODAY)
        self.assertEqual((s["stop"], s["target"], s["risk_per_share"]), (1406.40, 1426.20, 9.9))
        code, text, _ = run(["--plan", self._plan_file(plan(stop=1406.47)), "--config", CFG,
                             "--portfolio", "/nonexistent", "--now", NOW])
        self.assertIn("rounded DOWN to 1406.40", text)

    def _plan_file(self, p):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        path = os.path.join(d, "plan.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(p, fh)
        return path

    def test_every_order_price_is_a_multiple_of_the_tick(self):
        s = size.size_trade(plan(stop=1406.49, target=1426.225), cfg(), limit=1416.33, today=TODAY)
        o = size.bracket_order(s)
        self.assertEqual((o["limit_price"], o["stop_loss_stop_price"], o["take_profit_limit_price"]),
                         ("1416.30", "1406.40", "1426.20"))
        for k in ("limit_price", "take_profit_limit_price", "stop_loss_stop_price"):
            self.assertAlmostEqual(float(o[k]) / 0.10, round(float(o[k]) / 0.10), places=6, msg=k)

    def test_a_one_paisa_tick_keeps_paise_prices(self):
        s = size.size_trade(plan(stop=1406.47), cfg(tick_size=0.01), today=TODAY)
        self.assertEqual((s["stop"], s["tick_notes"]), (1406.47, []))
        for bad in (0, -0.05, "x", 0.005):
            with self.assertRaises(size.SizeError):
                size.size_trade(plan(), cfg(tick_size=bad), today=TODAY)

    def test_indian_digit_grouping(self):
        for x, want in ((73650.2, "73,650.20"), (100000, "1,00,000"), (123456.78, "1,23,456.78"), (5000, "5,000"),
                        (999, "999"), (12345678.9, "1,23,45,678.90"), (0, "0"), (3000, "3,000")):
            self.assertEqual(size.inr(x), RUPEE + want, x)
        self.assertEqual(size.inr(-1234567.5), "-" + RUPEE + "12,34,567.50")
        self.assertEqual(size.inr(1000, 2), RUPEE + "1,000.00")
        self.assertEqual(size.inr(0.04), RUPEE + "0.04")

    def test_ist_clock_has_no_daylight_saving(self):
        utc = datetime.datetime(2026, 10, 19, 4, 10)
        self.assertEqual(size.to_ist(utc), datetime.datetime(2026, 10, 19, 9, 40))
        self.assertEqual(size.ist_to_utc(datetime.datetime(2026, 10, 19, 9, 40)), utc)
        self.assertEqual(size.parse_now("2026-10-19 09:40"), utc)
        self.assertEqual(size.parse_now("2026-12-01 09:40"), datetime.datetime(2026, 12, 1, 4, 10))   # same offset
        self.assertEqual(size.parse_now("2026-10-19T04:10:00Z"), utc)
        self.assertEqual(size.parse_now("2026-10-19T09:40:00+05:30"), utc)

    def test_the_plan_date_is_checked_against_the_ist_date(self):
        # 18:45 UTC on the 18th is 00:15 IST on the 19th: today's plan is fine
        self.assertEqual(run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent",
                              "--now", "2026-10-18T19:00:00Z"])[0], 0)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent",
                            "--now", "2026-10-19T18:45:00Z"])        # 00:15 IST on the 20th
        self.assertEqual(code, 1)
        self.assertIn("make a new plan", err)
        self.assertIn("in IST", err)


class PlanRules(unittest.TestCase):
    def test_zero_shares_is_a_plain_refusal(self):
        s = size.size_trade(plan(stop=300.0), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 0)
        self.assertIn("one share risks %s1,116.30" % RUPEE, s["refusal"])
        self.assertIn("more than your %s1,000 max loss per trade" % RUPEE, s["refusal"])
        self.assertIsNone(size.bracket_order(s))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "plan.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(plan(stop=300.0), fh)
            out = os.path.join(d, "orders.json")
            code, text, _ = run(["--plan", p, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 1)
            self.assertIn("No order:", text)
            self.assertFalse(os.path.exists(out))

    def test_each_limit_can_bind(self):
        s = size.size_trade(plan(), cfg(max_order_inr=40000), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (28, "max order"))
        s = size.size_trade(plan(), cfg(max_position_pct=20), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (14, "position cap"))
        s = size.size_trade(plan(), cfg(), cash=30000, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["cash_available"]), (14, "cash", 20000))
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=300), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["max_loss"]), (30, "max loss", 297.0))
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=2000), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (52, "position cap"))

    def test_a_fresh_higher_price_means_a_bigger_risk(self):
        s = size.size_trade(plan(), cfg(min_reward_risk=0, max_loss_per_trade_inr=500), price=1419.50, today=TODAY)
        # risk 1419.50 - 1406.40 = 13.10 ; 500 / 13.10 = 38.17 shares ; 38 x 13.10 = 497.80 ; reward 6.70
        self.assertEqual((s["entry"], s["entry_from"], s["risk_per_share"]), (1419.50, "price", 13.1))
        self.assertEqual((s["qty"], s["bound_by"], s["max_loss"], s["reward_risk"]), (38, "max loss", 497.8, 0.51))
        self.assertIsNone(s["refusal"])
        s = size.size_trade(plan(), cfg(), price=1419.50, today=TODAY)
        self.assertIn("reward:risk 0.51 (target 1426.20, entry 1419.50, stop 1406.40) is under your minimum 1",
                      s["refusal"])
        self.assertIsNone(size.bracket_order(s))
        lower = size.size_trade(plan(), cfg(), price=1410.00, today=TODAY)
        self.assertEqual((lower["entry"], lower["qty"]), (1416.30, 52))

    def test_prices_file_is_used_only_when_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w", encoding="utf-8") as fh:
                json.dump(prices_doc(1419.50), fh)
            base = ["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"), "--prices", pp, "--json"]
            code, text, _ = run(base + ["--now", "2026-10-19 10:55"])
            out = json.loads(text)
            self.assertEqual((code, out["sizing"]["entry"], out["orders"]), (1, 1419.5, []))
            self.assertIn("above the plan's entry", out["notes"][0])
            self.assertIn("10:52 IST", out["notes"][0])
            out = json.loads(run(base + ["--now", "2026-10-19 11:30"])[1])
            self.assertEqual((out["sizing"]["qty"], out["sizing"]["entry"]), (52, 1416.30))
            self.assertIn("not used", out["notes"][0])

    def test_the_portfolio_cash_caps_the_size(self):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "portfolio.json")
            with open(pf, "w", encoding="utf-8") as fh:
                json.dump({"cash": 30000, "positions": []}, fh)
            out = json.loads(run(["--plan", PLAN, "--config", CFG, "--portfolio", pf, "--now", NOW, "--json"])[1])
            self.assertEqual((out["sizing"]["qty"], out["sizing"]["bound_by"]), (14, "cash"))

    def test_bad_plans(self):
        for p, word in ((plan(stop=1420.0), "not below the entry"),
                        (plan(target=1410.0), "already happened"),
                        (plan(target=None), "positive target"),
                        (plan(side="sell"), "long (buy) day trades only"),
                        (plan(symbol=""), "no symbol")):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(p, cfg(), today=TODAY)
            self.assertIn(word, str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), price=1430.00, today=TODAY)
        self.assertIn("already happened", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(blocked=["reliance"]), today=TODAY)
        self.assertIn("blocked list", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), today="2026-10-20")
        self.assertIn("make a new plan", str(e.exception))

    def test_under_the_minimum_order(self):
        cheap = plan(symbol="ABC", entry=40.0, stop=30.0, target=50.0)
        s = size.size_trade(cheap, cfg(max_loss_per_trade_inr=10), today=TODAY)
        self.assertEqual(s["qty"], 1)
        self.assertIn("under your %s100 minimum order" % RUPEE, s["refusal"])
        self.assertIsNone(size.bracket_order(s))

    def test_whole_shares_only_and_no_fractional_flag_effect(self):
        s = size.size_trade(plan(), cfg(fractional=True), today=TODAY)
        self.assertEqual(s["qty"], 52)
        self.assertEqual(size.shares_for(75000, 1416.30), 52)
        self.assertEqual(size.shares_for(1000, 9.90), 101)
        self.assertEqual(size.shares_for(0, 10), 0)

    def test_missing_plan_and_weights_only_flags(self):
        code, _, err = run(["--plan", "/nonexistent/plan.json", "--config", CFG, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", NOW, "--top", "2"])
        self.assertEqual(code, 1)
        self.assertIn("--weights", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", "tomorrow"])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)

    def test_nothing_us_is_left_in_the_output(self):
        code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW])
        for word in ("$", "Alpaca", "New York", "place_stock_order", "USD"):
            self.assertNotIn(word, text)
        self.assertNotIn("pdt", json.dumps(size.DEFAULTS).lower())


class WeightsMode(unittest.TestCase):
    """v1's split, kept behind --weights / --targets, in whole shares: Rs 1,00,000, 10% cash, 25% cap."""

    def check_split(self, r):
        self.assertEqual(rupees(r), SPLIT)
        self.assertEqual({p["ticker"]: p["shares"] for p in r["positions"]}, SPLIT_SHARES)
        self.assertEqual(r["cash_buffer"], 10000)
        self.assertEqual(r["invested"], 89854.65)
        self.assertEqual(r["idle"], 145.35)
        self.assertEqual(r["cap"], 25000)
        self.assertEqual([p["ticker"] for p in r["positions"] if p["capped"]], ["HDFCBANK", "RELIANCE"])

    def test_from_the_raw_weights(self):
        self.check_split(size.size(rows(RAW), split_cfg(), prices=PRICES))

    def test_from_the_renormalised_weights_file(self):
        self.check_split(size.size(rows(TARGETS), split_cfg(), prices=PRICES))

    def test_from_inline_weights(self):
        w = size.parse_weights(size.weights_from_arg("HDFCBANK=12.5,RELIANCE=8.5,INFY=5,TCS=4,ITC=3"))
        self.check_split(size.size(w, split_cfg(), prices=PRICES))

    def test_the_example_file_says_illustrative(self):
        with open(TARGETS, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertIn("ILLUSTRATIVE", doc["source"])
        self.assertIn("not a recommendation", doc["source"].lower())
        self.assertEqual([w["ticker"] for w in doc["weights"]], ["HDFCBANK", "RELIANCE", "INFY", "TCS", "ITC"])
        self.assertAlmostEqual(sum(w["weight"] for w in doc["weights"]), 100.0, places=2)

    def test_renormalised_weights(self):
        r = size.size(rows(RAW), split_cfg(), prices=PRICES)
        shown = {p["ticker"]: round(p["weight"], 1) for p in r["positions"]}
        self.assertEqual(shown, {"HDFCBANK": 37.9, "RELIANCE": 25.8, "INFY": 15.2, "TCS": 12.1, "ITC": 9.1})

    def test_one_decimal_weights_stay_within_a_share(self):
        w = size.parse_weights({"HDFCBANK": 37.9, "RELIANCE": 25.8, "INFY": 15.2, "TCS": 12.1, "ITC": 9.1})
        r = size.size(w, split_cfg(), prices=PRICES)
        self.assertLessEqual(r["invested"], r["investable"])
        for p in r["positions"]:
            self.assertLessEqual(abs(p["shares"] - SPLIT_SHARES[p["ticker"]]), 1, p["ticker"])
            self.assertLessEqual(p["rupees"], 25000)

    def test_orders_are_whole_share_orders_in_the_order_shape(self):
        r = size.size(rows(RAW), split_cfg(), prices=PRICES)
        orders = size.orders_from(r, "an illustrative file")
        self.assertEqual(orders[0], {"symbol": "HDFCBANK", "exchange": "NSE", "side": "buy", "qty": "26",
                                     "type": "market", "product": "MIS", "time_in_force": "day",
                                     "why": orders[0]["why"]})
        self.assertIn("capped at 25%", orders[0]["why"])
        self.assertIn(RUPEE + "1,00,000", orders[0]["why"])
        for o in orders:
            self.assertIsInstance(o["qty"], str)
            self.assertNotIn("notional", o)
        self.assertEqual([o["qty"] for o in orders], ["26", "17", "11", "4", "27"])
        self.assertEqual(size.orders_from(r, product="CNC")[0]["product"], "CNC")

    def test_paise_rounding_keeps_the_total(self):
        out = size.round_to_unit({"A": 333.333, "B": 333.333, "C": 333.334}, set(), ["C", "A", "B"], 1000, 0.01)
        self.assertEqual(out, {"A": 333.33, "B": 333.33, "C": 333.34})
        self.assertAlmostEqual(sum(out.values()), 1000.0, places=2)

    def test_cap_redistributes_until_nobody_is_over(self):
        w = size.parse_weights({"A": 60, "B": 30, "C": 10})
        r = size.size(w, {"budget": 1000, "cash_buffer_pct": 0, "max_position_pct": 40, "min_order_inr": 1},
                      prices={"A": 100, "B": 50, "C": 20})
        self.assertEqual(rupees(r), {"A": 400, "B": 400, "C": 200})
        self.assertEqual(sorted(p["ticker"] for p in r["positions"] if p["capped"]), ["A", "B"])
        self.assertEqual({p["ticker"]: p["shares"] for p in r["positions"]}, {"A": 4, "B": 8, "C": 10})

    def test_everything_capped_leaves_money_idle(self):
        w = size.parse_weights({"RELIANCE": 50, "ITC": 50})
        r = size.size(w, split_cfg(), prices=PRICES)
        self.assertEqual(rupees(r), {"RELIANCE": 24077.10, "ITC": 24633.00})
        self.assertEqual(r["idle"], 41289.90)
        self.assertIn("every stock is at its cap", size.report(r, size.orders_from(r)))

    def test_slices_under_the_minimum_are_dropped_and_shared_out(self):
        w = size.parse_weights({"A": 90, "B": 7, "C": 3})
        r = size.size(w, {"budget": 10000, "cash_buffer_pct": 10, "max_position_pct": 100, "min_order_inr": 500},
                      prices={"A": 100, "B": 10, "C": 10})
        self.assertNotIn("C", rupees(r))
        self.assertEqual(sum(rupees(r).values()), 9000)
        self.assertTrue(all(v >= 500 for v in rupees(r).values()))
        self.assertEqual(r["dropped"][0]["ticker"], "C")
        self.assertIn("its slice (%s270) is under your %s500 minimum order" % (RUPEE, RUPEE), r["dropped"][0]["reason"])

    def test_a_stock_too_expensive_for_its_slice_is_dropped(self):
        w = size.parse_weights({"A": 50, "B": 50})
        r = size.size(w, {"budget": 10000, "cash_buffer_pct": 0, "max_position_pct": 100, "min_order_inr": 100},
                      prices={"A": 100, "B": 9000})
        self.assertEqual(list(rupees(r)), ["A"])
        self.assertIn("one share costs %s9,000" % RUPEE, r["dropped"][0]["reason"])

    def test_blocked_tickers_are_dropped_first(self):
        r = size.size(rows(RAW), split_cfg(blocked=["itc"]), prices=PRICES)
        self.assertNotIn("ITC", rupees(r))
        self.assertIn("blocked", r["dropped"][0]["reason"])
        self.assertLessEqual(r["invested"], r["investable"])

    def test_top_n(self):
        r = size.size(rows(RAW), split_cfg(max_position_pct=100), prices=PRICES, top=2)
        self.assertEqual(sorted(rupees(r)), ["HDFCBANK", "RELIANCE"])

    def test_whole_shares_need_prices(self):
        with self.assertRaises(size.SizeError) as e:
            size.size(rows(RAW), split_cfg())
        self.assertIn("whole shares need a price for", str(e.exception))
        with self.assertRaises(size.SizeError):
            size.size(rows(RAW), cfg(), prices={"RELIANCE": 1416.30})      # the others are missing

    def test_every_position_is_whole_shares_inside_the_cap(self):
        r = size.size(rows(RAW), split_cfg(), prices=PRICES)
        self.assertTrue(r["whole_shares"])
        for p in r["positions"]:
            self.assertEqual(p["shares"], int(p["shares"]))
            self.assertAlmostEqual(p["rupees"], p["shares"] * PRICES[p["ticker"]], places=2)
            self.assertLessEqual(p["rupees"], 25000)
        self.assertAlmostEqual(r["invested"] + r["idle"], 90000, places=2)
        self.assertLess(r["idle"], min(PRICES.values()) * 2)

    def test_no_budget_and_duplicates_are_errors(self):
        with self.assertRaises(size.SizeError):
            size.size(rows(RAW), {"cash_buffer_pct": 10}, prices=PRICES)
        with self.assertRaises(size.SizeError):
            size.size([{"ticker": "ITC", "name": "", "weight": 1}, {"ticker": "ITC", "name": "", "weight": 2}],
                      split_cfg(), prices=PRICES)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "config.json")
            with open(c, "w", encoding="utf-8") as fh:
                json.dump(split_cfg(), fh)
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--targets", TARGETS, "--config", c, "--prices", PRICES_FILE, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("left idle", text)
            self.assertIn("require_stop", text)
            self.assertIn(RUPEE + "89,854.65", text)
            self.assertIn("ILLUSTRATIVE", text)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual([o["symbol"] for o in doc["orders"]], ["HDFCBANK", "RELIANCE", "INFY", "TCS", "ITC"])
            self.assertEqual(doc["orders"][0]["product"], "MIS")
            code, text, _ = run(["--targets", TARGETS, "--config", c, "--prices", PRICES_FILE, "--json",
                                 "--product", "CNC"])
            self.assertEqual(json.loads(text)["result"]["idle"], 145.35)
            self.assertEqual(json.loads(text)["orders"][0]["product"], "CNC")
            code, _, err = run(["--targets", "/nonexistent/targets.json", "--config", c, "--prices", PRICES_FILE])
            self.assertEqual(code, 1)
            self.assertIn("no weights file", err)
            code, _, err = run(["--targets", TARGETS, "--config", c, "--prices", os.path.join(d, "none")])
            self.assertEqual(code, 1)
            self.assertIn("no prices", err)


if __name__ == "__main__":
    unittest.main()
