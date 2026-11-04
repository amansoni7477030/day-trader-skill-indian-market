import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from _load import load, ROOT, FIX

check = load("trader-risk", "check")
ticket = load("trader-broker", "ticket")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "trader-risk", "orders-reliance.json")
WEIGHTS_ORDERS_PATH = os.path.join(FIX, "trader-risk", "orders-weights.json")
PRICES_FILE = os.path.join(FIX, "trader-budget", "prices-illustrative.json")
NOW = "2026-10-19 09:40"        # IST: the plan is from the 09:35 bar
TODAY = "2026-10-19"
RUPEE = "₹"
# SYNTHETIC / ILLUSTRATIVE prices for the five-name split (every price on NSE's tick for its level)
PRICES = {"RELIANCE": 1416.30, "HDFCBANK": 950.40, "TCS": 3150.30, "INFY": 1580.10, "ITC": 410.55}
QUOTE_TIME = datetime.datetime(2026, 10, 19, 5, 22, 13)     # a synthetic quote of 1419.50 at 10:52:13 IST
# the weights-mode rules with every day-trade check switched off: the long-term checks still hold
SPLIT = {"budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 40000,
         "min_order_inr": 100, "max_orders_per_day": 10, "require_stop": False, "no_entries_before": None,
         "no_entries_after": None, "max_trades_per_day": 0, "max_daily_loss_inr": 0,
         "max_loss_per_trade_inr": 0, "min_reward_risk": 0}
SELL = {"symbol": "RELIANCE", "exchange": "NSE", "side": "sell", "qty": "52", "type": "market", "product": "MIS",
        "time_in_force": "day"}


def at(text):
    return check.parse_now(text)


def cfg(**over):
    with open(CFG_PATH, encoding="utf-8") as fh:
        c = json.load(fh)
    c.update(over)
    return c


def split(**over):
    c = dict(SPLIT)
    c.update(over)
    return c


def reliance(**over):
    with open(ORDERS_PATH, encoding="utf-8") as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def bracket(sym, qty, entry, stop, target):
    return {"symbol": sym, "exchange": "NSE", "side": "buy", "qty": str(qty), "type": "market", "product": "MIS",
            "time_in_force": "day", "order_class": "bracket", "take_profit_limit_price": "%.2f" % target,
            "stop_loss_stop_price": "%.2f" % stop,
            "plan": {"entry": entry, "stop": stop, "target": target}}


def weight_orders():
    with open(WEIGHTS_ORDERS_PATH, encoding="utf-8") as fh:
        return json.load(fh)["orders"]


def buy(sym, qty):
    return {"symbol": sym, "exchange": "NSE", "side": "buy", "qty": str(qty), "type": "market", "product": "MIS",
            "time_in_force": "day"}


def placed(sym, side="buy", day=TODAY, max_loss=514.80, kind=None, status="placed"):
    r = {"symbol": sym, "side": side, "status": status, "date": day, "at": day + "T09:45:00+05:30"}
    if max_loss is not None:
        r["max_loss"] = max_loss
    if kind:
        r["kind"] = kind
    return r


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


def warnings(res, i=0):
    return " | ".join(res["results"][i]["warnings"])


def text_of(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class TheCanonicalTrade(unittest.TestCase):
    def test_the_reliance_bracket_passes_every_check(self):
        res = check.check([reliance()], cfg(), now=at(NOW))
        r = res["results"][0]
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual(r["reasons"], [])
        self.assertEqual(r["warnings"], [])
        self.assertEqual((r["kind"], r["entry_ref"], r["stop"], r["target"]), ("entry", 1416.30, 1406.40, 1426.20))
        self.assertEqual((r["risk_per_share"], r["max_loss"], r["reward_risk"]), (9.9, 514.8, 1.0))
        self.assertEqual(r["value_inr"], 73647.6)              # 52 x 1416.30
        self.assertEqual(res["cash_after"], 26352.40)          # 1,00,000 minus 73,647.60
        self.assertEqual(res["date"], TODAY)
        self.assertEqual(res["now_ist"], "2026-10-19 09:40")
        self.assertNotIn("paper", res)

    def test_the_keys_trader_broker_reads_are_there(self):
        res = check.check([reliance()], cfg(), now=at(NOW))
        for k in ("date", "results"):
            self.assertIn(k, res)
        for k in ("verdict", "reasons", "warnings", "kind", "fingerprint"):
            self.assertIn(k, res["results"][0])
        self.assertNotIn("now_new_york", res)
        self.assertNotIn("day_trades_5d", res["today"])

    def test_fifty_three_shares_is_blocked_by_the_position_cap_only(self):
        res = check.check([reliance(qty="53")], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertEqual(res["results"][0]["reasons"],
                         ["position: RELIANCE would be %s75,063.90, 75.1%% of the %s1,00,000 account, "
                          "over your 75%% cap" % (RUPEE, RUPEE)])

    def test_the_max_loss_blocks_alone_when_it_is_the_tighter_limit(self):
        res = check.check([reliance()], cfg(max_loss_per_trade_inr=500), now=at(NOW))
        self.assertEqual(res["results"][0]["reasons"],
                         ["loss: 52 shares x 9.90 risk a share = %s514.80 at the stop, over your %s500 max loss "
                          "per trade" % (RUPEE, RUPEE)])

    def test_fingerprint_is_shared_with_the_broker(self):
        o = reliance()
        self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))
        self.assertEqual(check.fingerprint(dict(o, why="anything", plan={})), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, qty="53")), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, stop_loss_stop_price="1406.30")), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, product="CNC")), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, exchange="BSE")), check.fingerprint(o))
        self.assertEqual(len(check.fingerprint(o)), 16)
        self.assertEqual(check.ORDER_FIELDS, ticket.ORDER_FIELDS)

    def test_the_shared_constants_are_the_contract(self):
        self.assertEqual(check.ORDER_FIELDS, (
            "symbol", "exchange", "side", "qty", "type", "product", "time_in_force", "limit_price", "stop_price",
            "client_order_id", "order_class", "take_profit_limit_price", "stop_loss_stop_price",
            "stop_loss_limit_price"))
        self.assertEqual(check.STRING_FIELDS, ("qty", "limit_price", "stop_price", "take_profit_limit_price",
                                               "stop_loss_stop_price", "stop_loss_limit_price"))
        self.assertEqual(check.PRICE_FIELDS, check.STRING_FIELDS)
        self.assertEqual((check.TYPES, check.TIFS, check.PRODUCTS, check.EXCHANGES),
                         ({"market", "limit", "stop", "stop_limit"}, {"day", "ioc"}, {"MIS", "CNC", "NRML"},
                          {"NSE", "BSE"}))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "state", "risk.json")
            none = os.path.join(d, "none")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", ORDERS_PATH, "--config", CFG_PATH, "--portfolio", none,
                                   "--prices", none, "--state", none, "--now", NOW, "--out", out])
            text = buf.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("PASS  #1 buy RELIANCE 52 shares, entry", text)
            self.assertIn("entry 1416.30 (the plan's entry), stop 1406.40, target 1426.20: risk 9.90 a share", text)
            self.assertIn("max loss %s514.80 (limit %s1,000), reward:risk 1.00 (min 1)" % (RUPEE, RUPEE), text)
            self.assertIn("today before this: 0 of 3 trades, %s0 of %s3,000 daily loss" % (RUPEE, RUPEE), text)
            self.assertIn("1 passed, 0 blocked at 2026-10-19 09:40 IST", text)
            self.assertIn("Cash after the passed orders: %s26,352.40" % RUPEE, text)
            self.assertIn("not financial advice", text)
            with open(out, encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], TODAY)
            self.assertEqual(rec["now_ist"], "2026-10-19 09:40")
            self.assertEqual(rec["results"][0]["kind"], "entry")
            self.assertEqual(rec["results"][0]["fingerprint"], check.fingerprint(reliance()))

    def test_the_rupee_sign_survives_a_cp1252_console(self):
        import sys
        raw = io.BytesIO()
        console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(sys, "stdout", console):
            none = os.path.join(d, "none")
            code = check.main(["--orders", ORDERS_PATH, "--config", CFG_PATH, "--portfolio", none,
                               "--prices", none, "--state", none, "--now", NOW, "--no-write"])
            sys.stdout.flush()
        self.assertEqual(code, 0)
        self.assertIn(RUPEE.encode("utf-8"), raw.getvalue())

    def test_no_pdt_anywhere(self):
        for name in ("day_trades", "business_days", "pdt_n"):
            self.assertFalse(hasattr(check, name), name)
        self.assertNotIn("pdt_guard", check.DEFAULTS)
        for parts in (("trader-risk", "check.py"), ("trader-risk", "SKILL.md"), ("trader-budget", "size.py"),
                      ("trader-budget", "SKILL.md"), ("trader-budget", "config.example.json")):
            low = text_of(*parts).lower()
            for word in ("pdt", "pattern day", "pattern-day", "day_trades_5d", "business_days"):
                self.assertNotIn(word, low, "%s in %s" % (word, "/".join(parts)))
        # a tripped "3 day trades" history changes nothing
        st = {"orders": [placed("X%d" % i, day="2026-10-1%d" % i) for i in range(3, 6)] +
                        [placed("X%d" % i, side="sell", day="2026-10-1%d" % i, kind="exit", max_loss=None)
                         for i in range(3, 6)]}
        self.assertEqual(verdicts(check.check([reliance()], cfg(pdt_guard=True), state=st, now=at(NOW))), ["PASS"])

    def test_no_us_leftovers_in_the_output(self):
        res = check.check([reliance(qty="53")], cfg(), now=at(NOW))
        text = check.report(res)
        for word in ("$", "New York", "NYSE", "Alpaca", "place_stock_order"):
            self.assertNotIn(word, text)


class DayTradeChecks(unittest.TestCase):
    def one(self, order, now=NOW, **over):
        return check.check([order], cfg(**over), now=at(now))

    def test_a_stop_is_required(self):
        o = reliance()
        for k in ("order_class", "take_profit_limit_price", "stop_loss_stop_price", "plan"):
            o.pop(k)
        o["plan"] = {"entry": 1416.30}
        res = self.one(o)
        self.assertIn("stop: no stop", reasons(res))
        res = self.one(o, require_stop=False, min_reward_risk=0)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("no stop", warnings(res))

    def test_a_plan_stop_counts_but_warns(self):
        o = reliance()
        for k in ("order_class", "take_profit_limit_price", "stop_loss_stop_price"):
            o.pop(k)
        res = self.one(o)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("in the plan only", warnings(res))
        self.assertEqual(res["results"][0]["max_loss"], 514.8)

    def test_stop_and_target_on_the_right_side(self):
        self.assertIn("must be below the entry 1416.30", reasons(self.one(reliance(stop_loss_stop_price="1420.00"))))
        res = self.one(reliance(take_profit_limit_price="1416.00"))
        self.assertIn("target: the target 1416.00 must be above the entry 1416.30", reasons(res))

    def test_reward_risk(self):
        res = self.one(reliance(take_profit_limit_price="1421.00"))
        self.assertIn("reward:risk: 0.47", reasons(res))
        self.assertEqual(verdicts(self.one(reliance(take_profit_limit_price="1421.00"), min_reward_risk=0.4)),
                         ["PASS"])

    def test_a_fresh_higher_price_is_the_entry(self):
        later = at("2026-10-19 10:55")
        res = check.check([reliance()], cfg(), prices={"RELIANCE": 1419.50}, now=later,
                          price_times={"RELIANCE": QUOTE_TIME})
        r = res["results"][0]
        # risk 1419.50 - 1406.40 = 13.10 ; reward 1426.20 - 1419.50 = 6.70 ; 6.70 / 13.10 = 0.51
        self.assertEqual((r["entry_ref"], r["entry_ref_from"], r["risk_per_share"]), (1419.50, "price", 13.1))
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertIn("reward:risk: 0.51 (target 1426.20, entry 1419.50, stop 1406.40)", reasons(res))
        res = check.check([reliance()], cfg(max_loss_per_trade_inr=600), prices={"RELIANCE": 1419.50}, now=later,
                          price_times={"RELIANCE": QUOTE_TIME})
        self.assertIn("loss: 52 shares x 13.10 risk a share = %s681.20 at the stop" % RUPEE, reasons(res))
        self.assertIn("reward:risk: 0.51", reasons(res))

    def test_a_stale_or_future_price_is_not_used(self):
        for now in ("2026-10-19 11:30", "2026-10-19 09:40"):          # 38 minutes old, and a quote from the future
            res = check.check([reliance()], cfg(), prices={"RELIANCE": 1419.50}, now=at(now),
                              price_times={"RELIANCE": QUOTE_TIME})
            self.assertEqual(verdicts(res), ["PASS"], now)
            self.assertEqual(res["results"][0]["entry_ref"], 1416.30)
            self.assertIn("older than 15 minutes", warnings(res))

    def test_a_lower_price_does_not_shrink_the_risk(self):
        res = check.check([reliance()], cfg(), prices={"RELIANCE": 1410.00}, now=at(NOW))
        self.assertEqual(res["results"][0]["entry_ref"], 1416.30)

    def test_a_limit_order_uses_its_limit_price(self):
        o = reliance(type="limit", limit_price="1415.00")
        res = check.check([o], cfg(), prices={"RELIANCE": 1419.50}, now=at(NOW))
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["max_loss"], r["verdict"]), (1415.0, 447.2, "PASS"))     # 52 x 8.60

    def test_daily_loss_and_trades_per_day(self):
        two = {"orders": [placed("INFY"), placed("TCS")]}
        res = check.check([reliance()], cfg(), state=two, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("today before this: 2 of 3 trades, %s1,029.60 of %s3,000 daily loss" % (RUPEE, RUPEE),
                      res["results"][0]["info"][-1])
        three = {"orders": [placed(s, max_loss=1000) for s in ("INFY", "TCS", "ITC")]}
        res = check.check([reliance()], cfg(), state=three, now=at(NOW))
        why = reasons(res)
        self.assertIn("trades: 3 entries placed today", why)
        self.assertIn("daily loss: %s3,000 already at risk today plus %s514.80 for this one = %s3,514.80, "
                      "over your %s3,000" % (RUPEE, RUPEE, RUPEE, RUPEE), why)
        yesterday = {"orders": [placed(s, day="2026-10-16") for s in ("INFY", "TCS", "ITC")]}
        self.assertEqual(verdicts(check.check([reliance()], cfg(), state=yesterday, now=at(NOW))), ["PASS"])

    def test_exits_and_declines_do_not_count_as_trades(self):
        st = {"orders": [placed("INFY"), placed("INFY", side="sell", kind="exit", max_loss=None),
                         placed("TCS", status="declined"), placed("ITC", status="failed")]}
        res = check.check([reliance()], cfg(), state=st, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertEqual(res["today"]["entries_placed"], 1)

    def test_the_batch_counts_toward_the_daily_limits(self):
        orders = [bracket(s, 25, 100.0, 60.0, 180.0) for s in ("AAA", "BBB", "CCC", "DDD")]
        res = check.check(orders, cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "PASS", "BLOCK"])
        why = reasons(res, 3)
        self.assertIn("trades: 0 entries placed today plus 3 passed earlier", why)
        self.assertIn("daily loss: %s3,000 already at risk today plus %s1,000 for this one = %s4,000"
                      % (RUPEE, RUPEE, RUPEE), why)

    def test_entry_window_ist(self):
        # no_entries_before 09:30 and no_entries_after 14:30 (the config's defaults); the session this pack trades
        # is 09:15 to 15:15: F&O stocks stop continuous trading at 15:15 and close in an auction
        cases = [("2026-10-19 09:29", "no new entries before 09:30 IST (it is 09:29)"),
                 ("2026-10-19 14:31", "no new entries after 14:30 IST (it is 14:31)"),
                 ("2026-10-19 08:00", "outside the session (Mon 2026-10-19 08:00 IST; entries run 09:15 to 15:15"),
                 ("2026-10-19 09:10", "outside the session"),
                 ("2026-10-19 15:15", "outside the session"),
                 ("2026-10-19 15:30", "outside the session"),
                 ("2026-10-17 10:15", "the market is closed today (weekend"),
                 ("2026-11-08 10:15", "the market is closed today (NSE holiday: Diwali Laxmi Pujan (Muhurat trading "
                                      "session only)"),
                 ("2026-11-10 10:15", "the market is closed today (NSE holiday: Diwali-Balipratipada"),
                 ("2026-10-19T03:50:00Z", "no new entries before 09:30 IST (it is 09:20)")]
        for now, want in cases:
            res = self.one(reliance(), now=now)
            self.assertIn("window: " + want, reasons(res), now)
        for now in ("2026-10-19 09:30", "2026-10-19 14:30", "2026-10-19T04:00:00Z", "2026-10-19T09:00:00Z",
                    "2026-10-30 10:00", "2026-10-21 11:00"):
            self.assertEqual(verdicts(self.one(reliance(), now=now)), ["PASS"], now)
        res = self.one(reliance(), now="2026-10-17 10:15", no_entries_before=None, no_entries_after=None)
        self.assertEqual(verdicts(res), ["PASS"])
        with self.assertRaises(check.RiskError):
            self.one(reliance(), no_entries_before="9.45am")
        # the two bounds are the config's: moving them moves the window
        self.assertEqual(verdicts(self.one(reliance(), now="2026-10-19 14:45", no_entries_after="14:45")), ["PASS"])
        self.assertIn("no new entries after 14:44", reasons(self.one(reliance(), now="2026-10-19 14:45",
                                                                      no_entries_after="14:44")))
        self.assertEqual((check.DEFAULTS["no_entries_before"], check.DEFAULTS["no_entries_after"],
                          check.DEFAULTS["flat_by"]), ("09:30", "14:30", "15:00"))

    def test_dussehra_2026_10_20_is_a_holiday(self):
        res = self.one(reliance(), now="2026-10-20 10:15")
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertIn("window: the market is closed today (NSE holiday: Dussehra, Tue 2026-10-20 10:15 IST)",
                      reasons(res))
        self.assertEqual(check.trading_day(datetime.date(2026, 10, 20)), (False, "NSE holiday: Dussehra"))
        self.assertEqual(check.trading_day(datetime.date(2026, 10, 19)), (True, ""))
        self.assertEqual(check.trading_day(datetime.date(2026, 10, 24)), (False, "weekend"))
        # the holiday list is read first, so a holiday on a weekend keeps its name (Muhurat trading is a special
        # evening session on that Sunday: not a normal day, not supported)
        self.assertEqual(check.trading_day(datetime.date(2026, 11, 8)),
                         (False, "NSE holiday: Diwali Laxmi Pujan (Muhurat trading session only)"))
        self.assertEqual(len(check.NSE_HOLIDAYS), 20)              # NSE's 2026 equity list, weekend dates included
        self.assertEqual(check.CALENDAR_YEARS, (2026,))
        self.assertEqual([d for d in check.NSE_HOLIDAYS if datetime.date.fromisoformat(d).weekday() >= 5],
                         ["2026-02-15", "2026-03-21", "2026-08-15", "2026-11-08"])

    def test_other_years_warn_that_the_holiday_list_is_2026_only(self):
        res = self.one(reliance(), now="2027-01-04 10:15")
        self.assertIn("the holiday list covers 2026 only: check nseindia.com for 2027-01-04", " ".join(res["warnings"]))

    def test_no_early_closes_today_but_the_machinery_works(self):
        self.assertEqual(check.NSE_EARLY_CLOSE, {})
        self.assertEqual(check.OPEN, datetime.time(9, 15))
        self.assertEqual((check.CLOSE, check.MARKET_CLOSE), (datetime.time(15, 15), datetime.time(15, 30)))
        # a special 13:30 session: the end of trading moved 1 h 45 min (15:15 -> 13:30), so the 14:30 cutoff moves to
        # 12:45 (by the same amount, not by a fixed 3 hours)
        with mock.patch.dict(check.NSE_EARLY_CLOSE, {"2026-10-21": ("13:30", "special short session (test)")}):
            res = self.one(reliance(), now="2026-10-21 12:46")
            self.assertIn("window: no new entries after 12:45 IST (it is 12:46)", reasons(res))
            res = self.one(reliance(), now="2026-10-21 13:35")
            self.assertIn("window: outside the session (Wed 2026-10-21 13:35 IST; entries run 09:15 to 13:30",
                          reasons(res))
            res = self.one(reliance(), now="2026-10-21 12:45")
            self.assertEqual(verdicts(res), ["PASS"])
            self.assertIn("early close at 13:30 today (special short session (test)): no new entries after 12:45",
                          " ".join(res["warnings"]))
        # a 14:30 close moved 45 minutes: 14:30 becomes 13:45
        with mock.patch.dict(check.NSE_EARLY_CLOSE, {"2026-10-21": ("14:30", "another test")}):
            self.assertIn("no new entries after 13:45 IST", reasons(self.one(reliance(), now="2026-10-21 13:46")))
            self.assertEqual(verdicts(self.one(reliance(), now="2026-10-21 13:45")), ["PASS"])
        self.assertEqual(verdicts(self.one(reliance(), now="2026-10-21 13:46")), ["PASS"])     # patch undone

    def test_the_window_uses_ist_not_the_machine(self):
        self.assertEqual(check.to_ist(at("2026-10-19 10:15")), datetime.datetime(2026, 10, 19, 10, 15))
        self.assertEqual(at("2026-10-19 10:15"), datetime.datetime(2026, 10, 19, 4, 45))
        self.assertEqual(at("2026-12-01 10:15"), datetime.datetime(2026, 12, 1, 4, 45))       # no daylight saving
        self.assertEqual(at("2026-03-09 10:15"), datetime.datetime(2026, 3, 9, 4, 45))
        self.assertEqual(check.ist_to_utc(datetime.datetime(2026, 10, 19, 10, 15)), datetime.datetime(2026, 10, 19, 4, 45))
        self.assertEqual(at("2026-10-19T10:15:00+05:30"), datetime.datetime(2026, 10, 19, 4, 45))

    def test_a_short_entry_when_allowed(self):
        short = {"symbol": "RELIANCE", "exchange": "NSE", "side": "sell", "qty": "52", "type": "market",
                 "product": "MIS", "time_in_force": "day", "order_class": "bracket",
                 "take_profit_limit_price": "1406.40", "stop_loss_stop_price": "1426.20",
                 "plan": {"entry": 1416.30}}
        res = self.one(short)
        self.assertIn("short: selling more RELIANCE than you hold", reasons(res))
        res = self.one(short, allow_short=True)
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"], r["max_loss"], r["reward_risk"]), ("PASS", "entry", 514.8, 1.0))
        res = self.one(dict(short, stop_loss_stop_price="1410.00"), allow_short=True)
        self.assertIn("must be above the entry 1416.30 for a sell", reasons(res))

    def test_bracket_format(self):
        self.assertIn("India trades whole shares only (no notional / fractional orders)",
                      reasons(self.one(dict(reliance(qty=None), notional="73650"))))
        self.assertIn("whole shares only", reasons(self.one(reliance(qty="52.5"))))
        self.assertIn("a bracket order needs time_in_force day, got 'ioc'", reasons(self.one(reliance(time_in_force="ioc"))))
        self.assertIn("unknown time_in_force 'gtc'", reasons(self.one(reliance(time_in_force="gtc"))))
        o = reliance()
        o.pop("take_profit_limit_price")
        self.assertIn("needs both take_profit_limit_price and stop_loss_stop_price", reasons(self.one(o)))
        self.assertIn("needs a stop_loss_stop_price", reasons(self.one(dict(reliance(), stop_loss_stop_price=None,
                                                                            stop_loss_limit_price="1405.00"))))
        self.assertIn("as a string", warnings(self.one(reliance(stop_loss_stop_price=1406.40))))
        self.assertIn("the stop-loss and target legs are placed after the entry fills",
                      " ".join(self.one(reliance())["results"][0]["info"]))


class FormatRules(unittest.TestCase):
    """Whole shares, tick, product, exchange: the India order shape."""

    def one(self, order, **over):
        return check.check([order], cfg(**over), now=at(NOW))

    def test_notional_and_fractional_orders_are_blocked(self):
        want = "format: India trades whole shares only (no notional / fractional orders)"
        for o in (dict(buy("ITC", 1), qty=None, notional="1000"), dict(buy("ITC", 1), notional="1000"),
                  buy("ITC", "2.5"), buy("ITC", "0.5")):
            res = check.check([o], split(), prices=PRICES, now=at(NOW))
            self.assertIn(want, res["results"][0]["reasons"], o)
            self.assertEqual(res["results"][0]["verdict"], "BLOCK")
        self.assertIn("no qty", reasons(check.check([dict(buy("ITC", 1), qty=None)], split(), prices=PRICES)))
        self.assertIn("qty must be a positive number", reasons(check.check([buy("ITC", 0)], split(), prices=PRICES)))

    def test_whole_shares_written_with_a_zero_decimal_are_fine(self):
        res = check.check([buy("ITC", "2.0")], split(), prices=PRICES, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])

    def test_prices_must_sit_on_the_tick(self):
        # RELIANCE trades between Rs 1,000 and Rs 5,000: NSE's tick there is 0.10 (circular NSE/CMTR/67133)
        for field, value in (("stop_loss_stop_price", "1406.45"), ("take_profit_limit_price", "1426.25"),
                             ("limit_price", "1415.05")):
            o = reliance(**{field: value})
            if field == "limit_price":
                o["type"] = "limit"
            res = self.one(o)
            self.assertIn("format: price %s is not a multiple of the %s0.10 tick" % (value, RUPEE), reasons(res), field)
            self.assertEqual(verdicts(res), ["BLOCK"])
        stop = {"symbol": "RELIANCE", "exchange": "NSE", "side": "sell", "qty": "52", "type": "stop",
                "product": "MIS", "time_in_force": "day", "stop_price": "1406.43"}
        self.assertIn("price 1406.43 is not a multiple of the %s0.10 tick" % RUPEE, reasons(self.one(stop)))
        self.assertIn("NSE's tick for a price in its band", reasons(self.one(stop)))
        self.assertEqual(verdicts(self.one(reliance(stop_loss_stop_price="1406.40"))), ["PASS"])
        self.assertEqual(verdicts(self.one(reliance(stop_loss_stop_price="1406.40", limit_price="1415.00",
                                                    type="limit"))), ["PASS"])
        # tick_size in the config forces one tick for every price (a broker window that shows another tick)
        self.assertEqual(verdicts(self.one(reliance(stop_loss_stop_price="1406.45"), tick_size=0.05)), ["PASS"])
        self.assertIn("%s0.05 tick" % RUPEE, reasons(self.one(reliance(stop_loss_stop_price="1406.47"),
                                                              tick_size=0.05)))
        self.assertNotIn("NSE's tick for a price in its band", reasons(self.one(reliance(
            stop_loss_stop_price="1406.47"), tick_size=0.05)))
        # a one-paisa tick (some ETFs) accepts paise prices; floats written as strings are checked exactly
        self.assertEqual(verdicts(self.one(reliance(stop_loss_stop_price="1406.47"), tick_size=0.01)), ["PASS"])
        self.assertIn("%s0.01 tick" % RUPEE, reasons(self.one(reliance(stop_loss_stop_price="1406.475"),
                                                              tick_size=0.01)))
        with self.assertRaises(check.RiskError):
            self.one(reliance(), tick_size=0.003)

    def test_every_price_is_judged_on_its_own_tick_band(self):
        itc = bracket("ITC", 100, 410.55, 405.05, 421.40)               # Rs 250 to 1,000: 0.05
        self.assertEqual(verdicts(self.one(itc)), ["PASS"])
        self.assertIn("price 405.06 is not a multiple of the %s0.05 tick" % RUPEE,
                      reasons(self.one(bracket("ITC", 100, 410.55, 405.06, 421.40))))
        small = bracket("SMALLCO", 400, 150.00, 148.75, 152.50)         # under Rs 250: 0.01, paise are fine
        self.assertEqual(verdicts(self.one(small)), ["PASS"])
        self.assertIn("price 148.755 is not a multiple of the %s0.01 tick" % RUPEE,
                      reasons(self.one(dict(small, stop_loss_stop_price="148.755"))))
        maruti = bracket("MARUTI", 4, 15500.0, 15480.0, 15520.0)        # Rs 10,000 to 20,000: whole rupees
        self.assertEqual(verdicts(self.one(maruti)), ["PASS"])
        self.assertIn("price 15480.50 is not a multiple of the %s1.00 tick" % RUPEE,
                      reasons(self.one(bracket("MARUTI", 4, 15500.0, 15480.5, 15520.0))))
        self.assertIn("%s5.00 tick" % RUPEE,
                      reasons(self.one(dict(maruti, symbol="BIGPRICE", take_profit_limit_price="20503.00"))))

    def test_tick_bands_match_nses_circular(self):
        for price, tick in ((9.40, 0.01), (249.99, 0.01), (250, 0.05), (1000, 0.05), (1000.05, 0.10), (5000, 0.10),
                            (5000.50, 0.50), (10000, 0.50), (10000.50, 1.00), (20000, 1.00), (20000.50, 5.00)):
            self.assertEqual(check.tick_for(price), tick, price)
        self.assertEqual(check.tick_for("1416.30"), 0.10)                 # prices arrive as strings
        self.assertIsNone(check.tick_of({}))
        self.assertIsNone(check.tick_of({"tick_size": None}))
        self.assertEqual(check.tick_of({"tick_size": 0.05}), 0.05)
        for bad in (0, -0.05, "x", 0.005, 0.003):
            with self.assertRaises(check.RiskError):
                check.tick_of({"tick_size": bad})

    def test_on_tick_helper(self):
        for v, ok in (("1406.40", True), ("1406.40", True), (1406.40, True), ("1406.5", True), ("1406.47", False),
                      ("1406.401", False), ("0.05", True), ("0.04", False), ("100", True)):
            self.assertEqual(check.on_tick(v, 0.05), ok, v)

    def test_product_validation(self):
        res = self.one(reliance(product="XYZ"))
        self.assertIn("format: unknown product 'XYZ' (use MIS, CNC or NRML)", reasons(res))
        for ok in ("MIS", "mis"):
            self.assertEqual(verdicts(self.one(reliance(product=ok))), ["PASS"])
        res = self.one(reliance(product="CNC"))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("not an intraday order: flat-by and square-off rules in this pack assume MIS", warnings(res))
        self.assertIn("NRML", warnings(self.one(reliance(product="NRML"))))
        self.assertNotIn("not an intraday order", warnings(self.one(reliance())))

    def test_a_missing_product_is_mis_with_a_warning(self):
        o = reliance()
        o.pop("product")
        res = self.one(o)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("no product in the order: treated as MIS", warnings(res))
        self.assertNotIn("not an intraday order", warnings(res))

    def test_exchange_validation(self):
        res = self.one(reliance(exchange="NYSE"))
        self.assertIn("format: unknown exchange 'NYSE' (use NSE or BSE)", reasons(res))
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertEqual(verdicts(self.one(reliance(exchange="nse"))), ["PASS"])
        res = self.one(reliance(exchange="BSE"))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("assume NSE", warnings(res))
        o = reliance()
        o.pop("exchange")
        res = self.one(o)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("no exchange in the order: treated as NSE", warnings(res))

    def test_types_and_time_in_force(self):
        self.assertIn("unknown order type 'trailing_stop'", reasons(self.one(reliance(type="trailing_stop"))))
        self.assertIn("unknown order type 'stop_loss'", reasons(self.one(reliance(type="stop_loss"))))
        for tif in ("opg", "cls", "gtc", "fok"):
            self.assertIn("unknown time_in_force", reasons(self.one(reliance(time_in_force=tif))), tif)
        market_ioc = {"symbol": "ITC", "exchange": "NSE", "side": "buy", "qty": "5", "type": "market",
                      "product": "MIS", "time_in_force": "ioc"}
        self.assertEqual(verdicts(check.check([market_ioc], split(), prices=PRICES)), ["PASS"])

    def test_us_only_fields_are_blocked(self):
        for k, v in (("trail_percent", "1"), ("trail_price", "2.00"), ("extended_hours", True),
                     ("advanced_instructions", {"x": 1})):
            res = self.one(reliance(**{k: v}))
            self.assertIn("format: %s is not supported by this pack" % k, reasons(res), k)

    def test_limit_and_stop_orders_need_their_prices(self):
        lim = {"symbol": "ITC", "exchange": "NSE", "side": "buy", "qty": "5", "type": "limit", "product": "MIS",
               "time_in_force": "day"}
        self.assertIn("needs a limit_price", reasons(check.check([lim], split(), prices=PRICES)))
        stop = dict(lim, type="stop")
        self.assertIn("a stop order needs a stop_price", reasons(check.check([stop], split(), prices=PRICES)))
        sl = dict(lim, type="stop_limit", limit_price="410.00")
        self.assertIn("a stop_limit order needs a stop_price", reasons(check.check([sl], split(), prices=PRICES)))

    def test_options_gate_blocks_derivative_looking_symbols(self):
        for sym in ("NIFTY26OCT24500CE", "NIFTY2610624500PE", "RELIANCE26OCTFUT", "BANKNIFTY26OCTFUT"):
            o = {"symbol": sym, "exchange": "NSE", "side": "buy", "qty": "20", "type": "limit", "limit_price": "12.50",
                 "product": "MIS", "time_in_force": "day"}
            res = check.check([o], split(), prices=PRICES)
            self.assertEqual(verdicts(res), ["BLOCK"], sym)
            self.assertIn("looks like an F&O contract", reasons(res))
            self.assertIn("allow_options", reasons(res))
            res = check.check([o], split(allow_options=True), prices=PRICES)
            self.assertEqual(verdicts(res), ["PASS"], sym)
            self.assertIn("derivative order", warnings(res))

    def test_plain_nse_symbols_pass_the_asset_check(self):
        for sym in ("RELIANCE", "M&M", "BAJAJ-AUTO", "20MICRONS", "J&KBANK", "INFY", "reliance"):
            res = check.check([buy(sym, 1)], split(), prices={sym.upper(): 100.0})
            self.assertNotIn("asset:", reasons(res), sym)

    def test_junk_symbols_are_blocked(self):
        for sym in ("BTC/USD", "RELIANCE.NS", "BRK.B", "TOOLONGSYMBOLNAMEXXXXX", "A B", "-X"):
            self.assertIn("does not look like an NSE equity symbol",
                          reasons(check.check([buy(sym, 1)], split(), prices=PRICES)), sym)


class Exits(unittest.TestCase):
    PF = {"cash": 26352.40, "positions": [{"ticker": "RELIANCE", "shares": 52,
                                            "lots": [{"date": TODAY, "shares": 52, "cost": 73647.60}]}]}

    def test_getting_out_is_never_blocked_by_the_day_limits(self):
        full = {"orders": [placed("RELIANCE"), placed("INFY"), placed("TCS")] +
                          [placed("T%d" % i, max_loss=1) for i in range(6)] +
                          [placed("RELIANCE", side="sell", kind="exit", max_loss=None)]}
        c = cfg(blocked=["RELIANCE"], max_order_inr=1000, max_daily_loss_inr=500)
        res = check.check([SELL], c, portfolio=self.PF, state=full, now=at("2026-10-19 15:20"))
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"]), ("PASS", "exit"), r["reasons"])
        why = warnings(res)
        for word in ("blocked:", "duplicate:", "daily limit:"):
            self.assertIn("exit, not blocked: " + word, why)
        self.assertNotIn("price", why)
        res = check.check([SELL], cfg(), portfolio=self.PF, now=at("2026-10-17 12:00"))      # a Saturday
        self.assertEqual(verdicts(res), ["PASS"])
        res = check.check([SELL], cfg(), portfolio=self.PF, now=at("2026-10-20 12:00"))      # the Dussehra holiday
        self.assertEqual(verdicts(res), ["PASS"])

    def test_an_exit_after_the_entry_cutoff_and_flat_by_passes(self):
        # after no_entries_after (14:30), after flat_by (15:00), after the end of continuous trading (15:15)
        for now in ("2026-10-19 14:50", "2026-10-19 15:10", "2026-10-19 15:20", "2026-10-19 15:40"):
            res = check.check([SELL], cfg(), portfolio=self.PF, now=at(now))
            self.assertEqual((verdicts(res), res["results"][0]["kind"]), (["PASS"], "exit"), now)

    def test_a_partial_exit_and_the_rest(self):
        half = dict(SELL, qty="26")
        res = check.check([half, half], cfg(), portfolio=self.PF, prices={"RELIANCE": 1426.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual([r["kind"] for r in res["results"]], ["exit", "exit"])
        self.assertIn("exit, not blocked: duplicate", warnings(res, 1))
        res = check.check([half, half, half], cfg(), portfolio=self.PF, prices={"RELIANCE": 1426.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "BLOCK"])
        self.assertIn("short:", reasons(res, 2))

    def test_format_asset_and_short_still_apply_to_a_sell(self):
        res = check.check([dict(SELL, type="limit")], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("needs a limit_price", reasons(res))
        res = check.check([dict(SELL, type="limit", limit_price="1426.22")], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("is not a multiple of the", reasons(res))
        res = check.check([dict(SELL, qty="53")], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("short: selling more RELIANCE than you hold (52 shares)", reasons(res))
        res = check.check([SELL], cfg(), now=at(NOW))
        self.assertIn("record the fill first", reasons(res))

    def test_buy_then_sell_in_one_batch(self):
        res = check.check([reliance(), SELL], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual(res["results"][1]["kind"], "exit")

    def test_the_two_exit_legs_of_a_bracket_are_alternatives(self):
        # ticket.py exits writes a stop-loss leg and a target leg for the same 52 shares: only one fills
        legs = ticket.exit_legs(reliance())
        res = check.check(legs, cfg(), portfolio=self.PF, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"], [r["reasons"] for r in res["results"]])
        self.assertEqual([r["kind"] for r in res["results"]], ["exit", "exit"])
        self.assertEqual(res["cash_after"], 26352.40)                  # neither has filled
        self.assertEqual(warnings(res, 1), "")
        self.assertIn("only one of its two legs fills", " ".join(res["results"][1]["info"]))
        # each leg still has to fit what is held
        res = check.check([dict(legs[1], qty="53")], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("short: selling more RELIANCE than you hold (52 shares)", reasons(res))
        # without the marker the second sell is a sale of shares the first one already sold
        loose = [{k: v for k, v in leg.items() if k != "exit_group"} for leg in legs]
        res = check.check(loose, cfg(), portfolio=self.PF, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "BLOCK"])
        self.assertIn("short:", reasons(res, 1))
        # the entry and its two legs in one batch pass too (the entry counts as filled for the legs)
        res = check.check([reliance()] + legs, cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "PASS"], [r["reasons"] for r in res["results"]])


class WeightsChecks(unittest.TestCase):
    """The long-term checks still hold (day-trade checks off): the whole-share buys of weights mode."""

    def one(self, order, **over):
        return check.check([order], split(**over), prices=PRICES, now=at(NOW))["results"][0]

    def test_the_five_buys_pass_on_a_fresh_one_lakh_dry_run_account(self):
        res = check.check(weight_orders(), split(), prices=PRICES, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"] * 5)
        self.assertEqual(res["cash_after"], 10145.35)           # 1,00,000 minus 89,854.65 (see test_budget)
        self.assertEqual(res["results"][0]["value_inr"], 24710.4)
        self.assertEqual(res["results"][0]["amount"], "26 shares")
        self.assertEqual(res["warnings"], [])

    def test_a_sixth_buy_is_blocked_three_ways(self):
        res = check.check(weight_orders() + [buy("HDFCBANK", 10)], split(), prices=PRICES, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"] * 5 + ["BLOCK"])
        why = reasons(res, 5)
        self.assertIn("cash:", why)
        self.assertIn("position:", why)
        self.assertIn("duplicate:", why)

    def test_the_default_config_blocks_buys_without_a_stop(self):
        res = check.check(weight_orders(), cfg(), prices=PRICES, now=at(NOW))
        self.assertEqual(verdicts(res)[0], "BLOCK")
        self.assertIn("stop: no stop", reasons(res))

    def test_max_and_min_order(self):
        self.assertIn("over your %s40,000 max order" % RUPEE,
                      " ".join(self.one(buy("ITC", 100), max_position_pct=100)["reasons"]))
        res = check.check([buy("CHEAP", 1)], split(), prices={"CHEAP": 50.0}, now=at(NOW))
        self.assertIn("under your %s100 minimum" % RUPEE, reasons(res))

    def test_blocked_list(self):
        r = self.one(buy("ITC", 5), blocked=["ITC"])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("blocked list", r["reasons"][0])

    def test_numbers_should_be_strings(self):
        r = self.one(dict(buy("ITC", 5), qty=5))
        self.assertEqual(r["verdict"], "PASS")
        self.assertIn("as a string", r["warnings"][0])

    def test_share_orders_need_a_price(self):
        q = buy("ZZZZ", 2)
        self.assertIn("no price for ZZZZ", " ".join(self.one(q)["reasons"]))
        r = self.one(buy("ITC", 2))
        self.assertEqual(r["verdict"], "PASS")
        self.assertAlmostEqual(r["value_inr"], 821.10)

    def test_margin_is_blocked_unless_allowed(self):
        small = check.check([buy("HDFCBANK", 10)], split(cash_buffer_pct=0, max_position_pct=1000),
                            portfolio={"cash": 5000, "positions": []}, prices=PRICES, now=at(NOW))["results"][0]
        self.assertEqual(small["verdict"], "BLOCK")
        self.assertIn("margin (MIS leverage, borrowed money)", " ".join(small["reasons"]))
        allowed = check.check([buy("HDFCBANK", 10)], split(cash_buffer_pct=0, max_position_pct=1000, allow_margin=True),
                              portfolio={"cash": 5000, "positions": []}, prices=PRICES, now=at(NOW))["results"][0]
        self.assertEqual(allowed["verdict"], "PASS")
        self.assertIn("MIS leverage", " ".join(allowed["warnings"]))

    PF = {"cash": 10000.0, "positions": [{"ticker": "ITC", "shares": 20,
                                           "lots": [{"date": "2026-10-13", "shares": 20, "cost": 8000.0}]}]}

    def test_short_sales_are_blocked(self):
        sell = {"symbol": "ITC", "exchange": "NSE", "side": "sell", "qty": "30", "type": "market", "product": "MIS",
                "time_in_force": "day"}
        r = check.check([sell], split(), portfolio=self.PF, prices=PRICES, now=at(NOW))["results"][0]
        self.assertIn("short:", " ".join(r["reasons"]))
        sell["qty"] = "10"
        r = check.check([sell], split(), portfolio=self.PF, prices=PRICES, now=at(NOW))["results"][0]
        self.assertEqual(r["verdict"], "PASS")

    def test_a_sell_frees_cash_for_a_later_buy(self):
        orders = [{"symbol": "ITC", "exchange": "NSE", "side": "sell", "qty": "10", "type": "market",
                   "product": "MIS", "time_in_force": "day"}, buy("HDFCBANK", 9)]
        res = check.check(orders, split(max_position_pct=100), portfolio=self.PF, prices=PRICES, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        res = check.check([buy("HDFCBANK", 9)], split(max_position_pct=100), portfolio=self.PF, prices=PRICES,
                          now=at(NOW))
        self.assertEqual(verdicts(res), ["BLOCK"])

    def test_daily_limit_and_already_placed_today(self):
        st = {"orders": [{"symbol": "T%d" % i, "side": "buy", "status": "placed", "at": TODAY + "T10:00:00"}
                         for i in range(10)]}
        res = check.check([buy("ITC", 5)], split(), prices=PRICES, state=st, today=TODAY)
        self.assertIn("daily limit", reasons(res))
        res = check.check([buy("ITC", 5)], split(), prices=PRICES, state=st, today="2026-10-21")
        self.assertEqual(verdicts(res), ["PASS"])
        itc = {"orders": [{"symbol": "ITC", "side": "buy", "status": "placed", "at": TODAY + "T10:00:00"}]}
        res = check.check([buy("ITC", 5)], split(), prices=PRICES, state=itc, today=TODAY)
        self.assertIn("already placed today", reasons(res))
        declined = {"orders": [{"symbol": "ITC", "side": "buy", "status": "declined", "at": TODAY + "T10:00:00"}]}
        self.assertEqual(verdicts(check.check([buy("ITC", 5)], split(), prices=PRICES, state=declined, today=TODAY)),
                         ["PASS"])

    def test_the_default_account_is_one_lakh_and_a_leftover_paper_key_is_ignored(self):
        self.assertEqual(check.DEFAULTS["budget"], 100000)
        self.assertNotIn("paper", check.DEFAULTS)
        no_budget = {k: v for k, v in split().items() if k != "budget"}      # a fresh account: 1,00,000
        res = check.check([buy("ITC", 5)], no_budget, prices=PRICES, now=at(NOW))
        self.assertAlmostEqual(res["cash_after"], 97947.25, places=2)       # 1,00,000 less 5 x 410.55
        old = check.check([buy("ITC", 5)], split(paper=False), prices=PRICES, now=at(NOW))
        self.assertEqual(verdicts(old), ["PASS"])                            # no mode to switch: nothing is refused


class Cli(unittest.TestCase):
    def test_prices_file_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w", encoding="utf-8") as fh:
                json.dump({"as_of": "2026-10-19T05:25:00Z", "prices": {"RELIANCE": 1419.50},
                           "detail": {"RELIANCE": {"time": "2026-10-19T05:22:13Z",
                                                   "fetched": "2026-10-19T05:25:00Z"}}}, fh)
            none = os.path.join(d, "none")
            base = ["--orders", ORDERS_PATH, "--config", CFG_PATH, "--portfolio", none, "--prices", pp,
                    "--state", none, "--no-write"]
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(check.main(base + ["--now", "2026-10-19 10:55"]), 1)
            self.assertIn("reward:risk: 0.51", buf.getvalue())
            self.assertIn("entry 1419.50 (the latest price)", buf.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(check.main(base + ["--now", "2026-10-19 11:30"]), 0)

    def test_weights_orders_with_a_weights_config_file(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "config.json")
            with open(c, "w", encoding="utf-8") as fh:
                json.dump(split(), fh)
            none = os.path.join(d, "none")
            out = os.path.join(d, "state", "risk.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", WEIGHTS_ORDERS_PATH, "--config", c, "--portfolio", none,
                                   "--prices", PRICES_FILE, "--state", none, "--now", NOW, "--out", out])
            self.assertEqual(code, 0, buf.getvalue())
            self.assertIn("5 passed, 0 blocked", buf.getvalue())
            with open(out, encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], TODAY)
            self.assertTrue(all(len(r["fingerprint"]) == 16 for r in rec["results"]))
            bad = os.path.join(d, "orders.json")
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump({"orders": weight_orders() + [buy("HDFCBANK", 10)]}, fh)
            with redirect_stdout(io.StringIO()):
                code = check.main(["--orders", bad, "--config", c, "--portfolio", none, "--prices", PRICES_FILE,
                                   "--state", none, "--now", NOW, "--no-write"])
            self.assertEqual(code, 1)

    def test_errors_are_one_line(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", "/nonexistent.json", "--no-write"]), 1)
        self.assertIn("no orders file", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", ORDERS_PATH, "--now", "noon", "--no-write"]), 1)
        self.assertIn("--now wants", err.getvalue())
        self.assertIn("(IST)", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", ORDERS_PATH, "--today", "monday", "--no-write"]), 1)
        self.assertIn("--today wants", err.getvalue())

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as d:
            none = os.path.join(d, "none")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", ORDERS_PATH, "--config", CFG_PATH, "--portfolio", none,
                                   "--prices", none, "--state", none, "--now", NOW, "--no-write", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(buf.getvalue())["now_ist"], "2026-10-19 09:40")


if __name__ == "__main__":
    unittest.main()
