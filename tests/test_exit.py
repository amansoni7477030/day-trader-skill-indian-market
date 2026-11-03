"""trader-exit tests (NSE, IST, rupees, the price-linked tick). Bars for the ATR stop reuse the SYNTHETIC
fixtures in tests/fixtures/trader-entry/ (RELIANCE, 2026-10-19; not real market data, see test_entry.py)."""

import copy
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal
from unittest import mock

from _load import load, FIX, ROOT

exitp = load("trader-exit", "exit")
entry = load("trader-entry", "entry")

RELIANCE = os.path.join(FIX, "trader-entry", "RELIANCE-2026-10-19-5m.json")
OPEN_TS = 1792381500       # 2026-10-19 09:15 IST
RS = "₹"
D = Decimal

# trader/plan.json for the canonical RELIANCE trade, exactly as the contract writes it
REEL_PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-10-19", "setup": "orb",
             "entry": 1416.30, "entry_time": "9:35 AM", "entry_bar": 3, "interval": "5m",
             "opening_range": {"high": 1413.40, "low": 1399.50, "minutes": 15},
             "stop": 1406.40, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
             "target": 1426.20, "target_r": 1.0, "target_label": "1R (one times the risk)",
             "risk_per_share": 9.90, "reward_risk": 1.0, "flat_by": "15:00", "tick_size": 0.1,
             "tick_override": False}
REEL_ENTRY = {"created_by": "trader-entry", "symbol": "RELIANCE", "setup": "orb", "side": "buy", "date": "2026-10-19",
              "status": "triggered", "time": "9:35 AM", "bar_start": "09:30", "bar_index": 3, "price": 1416.30,
              "level": 1413.40, "interval": "5m", "opening_range": {"high": 1413.40, "low": 1399.50, "minutes": 15},
              "source": "yahoo", "checked_at": "2026-10-19T04:05:00Z"}


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="2R", oh=1413.40, ol=1399.50, atr=None, n=None, tick=None):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n, tick)


def read_doc():
    with open(RELIANCE, encoding="utf-8") as fh:
        return json.load(fh)


def with_symbol(symbol):
    d = read_doc()
    d["chart"]["result"][0]["meta"]["symbol"] = symbol
    return d


def live_doc(closed):
    """The session mid-day: `closed` finished bars, the in-progress bar, and a live tick off the grid."""
    d = read_doc()
    r = d["chart"]["result"][0]
    keep = closed + 1
    r["timestamp"] = r["timestamp"][:keep]
    q = r["indicators"]["quote"][0]
    for k in q:
        q[k] = q[k][:keep]
    tick_ts = r["timestamp"][-1] + 285
    r["timestamp"].append(tick_ts)
    for k in ("open", "high", "low", "close"):
        q[k].append(q["close"][-1])
    q["volume"].append(0)
    r["meta"]["regularMarketTime"] = tick_ts
    return d


class TickByPrice(unittest.TestCase):
    """NSE's tick size depends on the price (circular NSE/CMTR/67133, in force since 15 April 2025)."""

    def test_the_bands_and_their_edges(self):
        t = exitp.tick_for
        for price, tick in (("0.50", "0.01"), ("249.99", "0.01"), ("250", "0.05"), ("250.05", "0.05"),
                            ("1000", "0.05"), ("1000.01", "0.10"), ("1416.30", "0.10"), ("5000", "0.10"),
                            ("5000.01", "0.50"), ("10000", "0.50"), ("10000.01", "1.00"), ("20000", "1.00"),
                            ("20000.01", "5.00"), ("45000", "5.00")):
            self.assertEqual(t(price), D(tick), price)

    def test_the_stop_and_the_target_use_their_own_price_band(self):
        # entry 1,002 is in the 0.10 band, but a stop at 999.97 is in the 0.05 band: floor to 999.95, not 999.90
        p = c(1002, "price:999.97", "price:1010")
        self.assertEqual(p["stop"], D("999.95"))
        # entry 998 is in the 0.05 band, a target at 1000.04 is in the 0.10 band: 1000.00, not 1000.05
        p = c(998, "price:996.30", "price:1000.04")
        self.assertEqual(p["target"], D("1000.00"))
        self.assertEqual(p["tick"], D("0.05"))                      # the entry's tick, for reference
        # a cheap stock trades in paise
        p = c(100, "price:98.505", "price:101.017")
        self.assertEqual((p["stop"], p["target"], p["tick"]), (D("98.50"), D("101.02"), D("0.01")))

    def test_a_forced_tick_applies_to_every_price(self):
        p = c(1416.30, "or-mid", "2R", tick=D("0.05"))
        self.assertEqual((p["stop"], p["risk"], p["target"], p["tick"]),
                         (D("1406.45"), D("9.85"), D("1436.00"), D("0.05")))
        p = c(1416.30, "or-mid", "2R", tick=D("0.50"))
        self.assertEqual(p["stop"], D("1406.00"))
        with self.assertRaises(exitp.PlanError):
            c(1416.30, "or-mid", tick=D("0"))

    def test_resolve_tick(self):
        self.assertIsNone(exitp.resolve_tick(None, {}))
        self.assertIsNone(exitp.resolve_tick(None, {"tick_size": None}))
        self.assertEqual(exitp.resolve_tick(None, {"tick_size": 0.05}), D("0.05"))
        self.assertEqual(exitp.resolve_tick("0.10", {"tick_size": 0.05}), D("0.10"))     # the flag beats the config
        for bad in ("0", "-1", "200"):
            with self.assertRaises(exitp.PlanError):
                exitp.resolve_tick(bad, {})


class Math(unittest.TestCase):
    def test_the_canonical_or_mid_1r(self):
        p = c(1416.30, "or-mid", "1R")
        self.assertEqual(p["stop"], D("1406.40"))                # floor-to-tick(1406.45): a half tick goes down
        self.assertEqual(p["risk"], D("9.90"))
        self.assertEqual(p["target"], D("1426.20"))
        self.assertEqual(p["reward_risk"], D("1.00"))
        self.assertEqual(p["tick"], D("0.10"))
        self.assertEqual(p["target_label"], "1R (one times the risk)")
        self.assertIn("rounded down to the tick", p["stop_detail"])
        self.assertEqual(p["stop_detail"], "(" + RS + "1,413.40 + " + RS + "1,399.50) / 2 = " + RS + "1,406.45, rounded down to the tick")

    def test_the_default_2r(self):
        p = c(1416.30, "or-mid")
        self.assertEqual(p["target"], D("1436.10"))
        self.assertEqual(p["target_label"], "2R (two times the risk)")

    def test_raw_yahoo_floats_give_the_same_paise(self):
        p = c(1416.300048828125, "or-mid", "1R", 1413.4000244140625, 1399.5)
        self.assertEqual((p["entry"], p["stop"], p["target"]), (D("1416.30"), D("1406.40"), D("1426.20")))

    def test_tick_rounding_stop_floors_away_from_the_entry(self):
        self.assertEqual(c(1416.30, "price:1406.475")["stop"], D("1406.40"))     # the half-tick: down, not up
        self.assertEqual(c(1416.30, "price:1406.399")["stop"], D("1406.30"))     # between ticks: floors
        self.assertEqual(c(1416.30, "price:1406.40")["stop"], D("1406.40"))      # already on the tick: unchanged
        self.assertEqual(c(1416.30, "price:1406.451")["stop"], D("1406.40"))
        self.assertEqual(c(1416.30, "price:1406.50")["stop"], D("1406.50"))
        p = c(1416.30, "price:1406.475", "1R")
        self.assertEqual((p["risk"], p["target"]), (D("9.90"), D("1426.20")))
        # every stop and target lands on a multiple of 0.10 (the tick of a stock between 1,000 and 5,000)
        for stop in ("or-mid", "or-low", "pct:1", "pct:0.77", "price:1405.123"):
            for target in ("1R", "2R", "1.5R", "price:1431.337"):
                p = c(1416.30, stop, target)
                self.assertEqual(p["stop"] % D("0.10"), 0, (stop, target))
                self.assertEqual(p["target"] % D("0.10"), 0, (stop, target))

    def test_other_stops(self):
        p = c(1416.30, "or-low")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (D("1399.50"), D("16.80"), D("1449.90")))
        p = c(1416.30, "pct:1")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (D("1402.10"), D("14.20"), D("1444.70")))
        self.assertIn("1,402.137, rounded down", p["stop_detail"])
        p = c(1416.30, "atr:1.5", atr=6.4181, n=9)
        self.assertEqual(p["stop"], D("1406.60"))                   # 1416.30 - 9.62715 = 1406.67285
        self.assertEqual((p["risk"], p["target"]), (D("9.70"), D("1435.70")))
        self.assertEqual(p["stop_label"], "1.5 x ATR(9) under the entry")
        self.assertIn("(fewer than 14 so far)", p["stop_detail"])

    def test_target_rounds_half_up_to_the_tick_and_price_targets(self):
        p = c(100, "price:99", "1.025R", None, None)
        self.assertEqual(p["target"], D("101.03"))                  # 100 + 1.025 = 101.025, a half paisa: up
        self.assertEqual(p["target_label"], "1.025R (1.025 times the risk)")
        p = c(1416.30, "or-mid", "1.5R")
        self.assertEqual(p["target"], D("1431.20"))                 # 1416.30 + 14.85 = 1431.15, a half tick: up
        self.assertEqual(p["target_label"], "1.5R (1.5 times the risk)")
        p = c(1416.30, "or-mid", "price:1430.025")
        self.assertEqual(p["target"], D("1430.00"))
        self.assertEqual(p["reward_risk"], D("1.38"))
        self.assertEqual(p["target_label"], "your price (1.38R)")
        self.assertEqual(p["target_r"], D("1.38"))
        self.assertEqual(c(1416.30, "or-mid", "price:1430.06")["target"], D("1430.10"))
        self.assertEqual(c(1416.30, "or-mid", "price:" + RS + "1,436")["target"], D("1436.00"))

    def test_refusals_say_why(self):
        cases = [("price:1416.30", "2R", "at or above the entry"),
                 ("price:1420", "2R", "at or above the entry"),
                 ("or-mid", "price:1416", "at or below the entry"),
                 ("price:1415.65", "2R", "too tight to survive the spread"),
                 ("pct:0.04", "2R", "too tight to survive the spread")]
        for stop, target, why in cases:
            with self.assertRaises(exitp.PlanError) as cm:
                c(1416.30, stop, target)
            self.assertIn(why, str(cm.exception), stop)
        with self.assertRaises(exitp.PlanError) as cm:
            c(1416.30, "price:1415.65")
        self.assertIn("the stop is " + RS + "0.70 under the entry, less than 0.05% of the price (" + RS + "0.71 on "
                      + RS + "1,416.30)", str(cm.exception))
        # 0.05% of 1416.30 is 0.708: a Rs 0.80 risk is allowed, Rs 0.70 is not; the rule is exactly 0.05%
        self.assertEqual(c(1416.30, "price:1415.50")["risk"], D("0.80"))
        self.assertEqual(c(100, "price:99.95", "price:101")["risk"], D("0.05"))
        with self.assertRaises(exitp.PlanError):
            c(1400.00, "or-mid")                                    # the midpoint is above this entry
        for bad in ("mid-ish", "pct:100", "price:0", "atr:-1"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_stop(bad)
        for bad in ("0R", "R", "lots", "price:x"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_target(bad)
        with self.assertRaises(exitp.PlanError):
            c(1416.30, "or-mid", oh=None, ol=None)
        with self.assertRaises(exitp.PlanError):
            c(1416.30, "or-mid", oh=1399.50, ol=1413.40)

    def test_rupee_parsing_and_grouping(self):
        self.assertEqual(exitp.D(RS + "1,23,456.78"), D("123456.78"))
        self.assertEqual(exitp.D(" 1,416.30 "), D("1416.30"))
        with self.assertRaises(exitp.PlanError):
            exitp.D("$100")
        self.assertEqual(exitp.parse_stop("price:" + RS + "1,406.40"), ("price", D("1406.40")))
        self.assertEqual(exitp.inr(1416.30), RS + "1,416.30")
        self.assertEqual(exitp.inr(D("123456.789")), RS + "1,23,456.79")
        self.assertEqual(exitp.inr(1234567.5), RS + "12,34,567.50")
        self.assertEqual(exitp.inr(5000, None), RS + "5,000")
        self.assertEqual(exitp.inr(5000.5, None), RS + "5,000.50")
        self.assertEqual(exitp.inr(-1234.5), "-" + RS + "1,234.50")
        self.assertEqual(exitp.money(0.05), RS + "0.05")
        self.assertEqual(exitp.money(100000), RS + "1,00,000.00")
        self.assertEqual(exitp.exact(D("1406.45")), RS + "1,406.45")
        self.assertEqual(exitp.exact(D("358")), RS + "358.00")

    def test_atr_on_the_session_bars(self):
        ch = exitp.load_chart("RELIANCE", "5m", None, RELIANCE)
        day = datetime.date(2026, 10, 19)
        bars = exitp.session_bars(ch, day, "5m", upto_slot=3)
        self.assertEqual(len(bars), 4)                              # 9:15 ... the 9:30 entry bar
        # by hand: the true range of each bar, the first one without the overnight gap
        trs, prev = [], None
        for b in bars:
            r = b["h"] - b["l"]
            if prev is not None:
                r = max(r, abs(b["h"] - prev), abs(b["l"] - prev))
            trs.append(r)
            prev = b["c"]
        self.assertAlmostEqual(trs[0], 1409.5 - 1399.5, places=4)
        self.assertEqual([round(t, 2) for t in trs], [10.00, 6.90, 6.60, 10.00])
        atr, n = exitp.atr_of(bars)
        self.assertEqual(n, 4)
        self.assertAlmostEqual(atr, sum(trs) / 4, places=9)
        self.assertAlmostEqual(atr, 8.375, places=4)
        # later in the day it uses the last 14 bars; the day ends at 3:15 PM, so there are 72 bars, not 75
        all_bars = exitp.session_bars(ch, day, "5m")
        self.assertEqual(len(all_bars), 72)
        self.assertEqual(exitp.atr_of(all_bars)[1], 14)

    def test_the_in_progress_bar_and_live_tick_never_count(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "RELIANCE.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(live_doc(15), fh)
            ch = exitp.load_chart("RELIANCE", "5m", None, path)
            self.assertEqual(ch["bars"][-1]["t"], OPEN_TS + 15 * 300 + 285)
            bars = exitp.session_bars(ch, datetime.date(2026, 10, 19), "5m")
            self.assertEqual(len(bars), 15)                         # the 10:30 bar is in progress at the file's time
            self.assertEqual(exitp.atr_of(bars)[1], 14)
        finally:
            shutil.rmtree(d)

    def test_the_chart_must_be_for_the_symbol(self):
        with self.assertRaises(exitp.PlanError) as cm:
            exitp.load_chart("TCS", "5m", None, RELIANCE)
        self.assertIn("holds RELIANCE, not TCS", str(cm.exception))
        self.assertEqual(exitp.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(exitp.yahoo_symbol("^NSEI"), "^NSEI")
        self.assertEqual(exitp.yahoo_symbol("RELIANCE.BO"), "RELIANCE.BO")
        self.assertEqual(exitp.yahoo_symbol("bajaj-auto"), "BAJAJ-AUTO.NS")
        self.assertEqual(exitp.plain_symbol("m&m.ns"), "M&M")

    def test_ist_is_a_fixed_offset(self):
        self.assertEqual(exitp.to_ist(datetime.datetime(2026, 10, 19, 4, 5)), datetime.datetime(2026, 10, 19, 9, 35))
        self.assertEqual(exitp.ist_to_utc(datetime.datetime(2026, 10, 19, 9, 35)), datetime.datetime(2026, 10, 19, 4, 5))
        self.assertEqual(exitp.parse_now("2026-10-19 09:35"), datetime.datetime(2026, 10, 19, 4, 5))
        self.assertEqual(exitp.parse_now("2026-10-19 09:35 IST"), datetime.datetime(2026, 10, 19, 4, 5))
        self.assertEqual(exitp.parse_now("2026-06-15T09:35:00+05:30"), datetime.datetime(2026, 6, 15, 4, 5))
        with self.assertRaises(exitp.PlanError):
            exitp.parse_now("tomorrow")


class Calendar(unittest.TestCase):
    def test_the_session_ends_at_315_for_the_pack(self):
        self.assertEqual((exitp.OPEN, exitp.CLOSE, exitp.MARKET_CLOSE),
                         (datetime.time(9, 15), datetime.time(15, 15), datetime.time(15, 30)))
        self.assertEqual(exitp.session(datetime.date(2026, 10, 19)), (datetime.time(9, 15), datetime.time(15, 15)))

    def test_nse_holidays_are_the_official_list(self):
        self.assertEqual(len(exitp.NSE_HOLIDAYS), 20)
        self.assertIsNone(exitp.session(datetime.date(2026, 10, 20)))           # Dussehra
        self.assertIsNone(exitp.session(datetime.date(2026, 10, 17)))           # a Saturday
        self.assertIn("2026-02-15", exitp.NSE_HOLIDAYS)                         # Mahashivratri, on a Sunday
        self.assertIn("Muhurat", exitp.NSE_HOLIDAYS["2026-11-08"])
        self.assertEqual(exitp.NSE_EARLY_CLOSE, {})


class Yahoo(unittest.TestCase):
    """Yahoo is never called: _open_url is swapped for a fake."""

    def setUp(self):
        self.calls = []
        self._open, self._now = exitp._open_url, exitp.utc_now
        exitp.utc_now = lambda: datetime.datetime(2026, 10, 19, 5, 0)
        self.body = json.dumps(with_symbol("M&M.NS"))

        def fake(url, timeout=20):
            self.calls.append(url)
            return self.body
        exitp._open_url = fake

    def tearDown(self):
        exitp._open_url, exitp.utc_now = self._open, self._now

    def test_symbols_with_an_ampersand_are_url_quoted(self):
        ch = exitp.load_chart("m&m", "5m", datetime.date(2026, 10, 19))
        self.assertEqual(len(ch["bars"]), 75)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com/v8/finance/chart/M%26M.NS?range=1d&interval=5m"])


class Transport(unittest.TestCase):
    """The real _open_url, with curl_cffi (Chrome's TLS fingerprint) and with the urllib fallback."""

    class Resp(object):
        def __init__(self, status, body=b"{}"):
            self.status_code, self.content = status, body

    class FakeCffi(object):
        def __init__(self, resp=None, exc=None):
            self.resp, self.exc, self.calls = resp, exc, []

        def get(self, url, **kw):
            self.calls.append((url, kw))
            if self.exc:
                raise self.exc
            return self.resp

    def test_curl_cffi_gets_chrome_headers_and_a_chrome_fingerprint(self):
        fake = self.FakeCffi(self.Resp(200, '{"ok": "₹"}'.encode("utf-8")))
        with mock.patch.object(exitp, "_cffi", fake):
            text = exitp._open_url("https://query2.finance.yahoo.com/x")
        self.assertEqual(text, '{"ok": "₹"}')
        url, kw = fake.calls[0]
        self.assertEqual(kw["impersonate"], "chrome")
        self.assertEqual(kw["headers"]["Referer"], "https://finance.yahoo.com/")
        self.assertIn("Accept-Language", kw["headers"])
        self.assertNotIn("User-Agent", kw["headers"])               # curl_cffi sets Chrome's own

    def test_http_errors_and_network_failures_look_like_urllib_to_the_callers(self):
        with mock.patch.object(exitp, "_cffi", self.FakeCffi(self.Resp(429, b"slow down"))):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                exitp._open_url("https://query2.finance.yahoo.com/x")
        self.assertEqual(cm.exception.code, 429)
        self.assertEqual(cm.exception.read(), b"slow down")
        with mock.patch.object(exitp, "_cffi", self.FakeCffi(exc=RuntimeError("timed out"))):
            with self.assertRaises(urllib.error.URLError):
                exitp._open_url("https://query2.finance.yahoo.com/x")

    def test_without_curl_cffi_urllib_sends_a_browser_user_agent(self):
        seen = {}

        class R(object):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        def fake_urlopen(req, timeout=0):
            seen["headers"] = dict(req.header_items())
            return R()
        with mock.patch.object(exitp, "_cffi", None), mock.patch.object(exitp.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(exitp._open_url("https://query2.finance.yahoo.com/x"), "{}")
        self.assertIn("Chrome/", seen["headers"]["User-agent"])
        self.assertNotIn("compatible", seen["headers"]["User-agent"])
        self.assertEqual(seen["headers"]["Referer"], "https://finance.yahoo.com/")


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def plan(self, *args):
        return run(exitp, ["plan", "--now", "2026-10-19 09:36"] + list(args))

    def read_plan(self):
        with open(os.path.join("trader", "plan.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_entry_to_plan_end_to_end(self):
        code, _, _ = run(entry, ["check", "RELIANCE", "--offline", RELIANCE, "--now", "2026-10-19 09:35", "--write"])
        self.assertEqual(code, 0)
        code, text, err = self.plan("--stop", "or-mid", "--target", "1R", "--write")
        self.assertEqual(code, 0, err)
        p = self.read_plan()
        for k, v in REEL_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertIn("Entry         " + RS + "1,416.30 at 9:35 AM (bar 3)", text)
        self.assertIn("Stop          " + RS + "1,406.40  opening-range midpoint", text)
        self.assertIn("Target        " + RS + "1,426.20  1R (one times the risk)", text)
        self.assertIn("Risk a share  " + RS + "9.90 (0.70% of the entry)", text)
        self.assertIn("Reward:risk   1.0", text)
        self.assertIn("Flat by       15:00 IST", text)
        self.assertIn("not a guarantee", text)
        self.assertIn("/trader-timemachine", text)
        self.assertNotIn("$", text)
        self.assertNotIn("pro" + "fit", text.lower())   # never on screen
        code, text, _ = self.plan("--write")                       # the defaults: or-mid, 2R
        self.assertEqual(self.read_plan()["target"], 1436.10)
        self.assertEqual(self.read_plan()["target_r"], 2.0)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-19 09:40"])
        self.assertEqual(code, 0)
        self.assertIn(RS + "1,436.10", text)
        self.assertNotIn("not today", text)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-21 09:40"])
        self.assertIn("not today", text)

    def test_manual_flags_and_json(self):
        code, text, _ = self.plan("--symbol", "reliance", "--entry", "1416.30", "--or-high", "1413.40", "--or-low", "1399.50",
                                  "--time", "9:35", "--entry-bar", "3", "--date", "2026-10-19", "--target", "1R",
                                  "--json")
        self.assertEqual(code, 0)
        p = json.loads(text)
        for k, v in REEL_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["warnings"], [])
        code, text, _ = self.plan("--symbol", "RELIANCE.NS", "--entry", "1416.30", "--or-high", "1413.40",
                                  "--or-low", "1399.50", "--json")
        self.assertEqual(json.loads(text)["symbol"], "RELIANCE")   # the plan file holds the plain NSE symbol
        code, text, _ = self.plan("--symbol", "m&m", "--entry", "3000", "--or-high", "2990", "--or-low", "2970", "--json")
        self.assertEqual(json.loads(text)["symbol"], "M&M")

    def test_the_tick_follows_the_price_unless_forced(self):
        # a 3,000 rupee stock: 0.10; a 400 rupee stock: 0.05; a 100 rupee stock: 0.01
        for sym, px, hi, lo, tick, stop in (("TCS", "3250.50", "3240.00", "3210.00", 0.1, 3225.00),
                                            ("ITC", "412.35", "411.00", "405.00", 0.05, 408.00),
                                            ("SBIN", "100.37", "100.20", "98.97", 0.01, 99.58)):
            code, text, err = self.plan("--symbol", sym, "--entry", px, "--or-high", hi, "--or-low", lo, "--json")
            self.assertEqual(code, 0, err)
            p = json.loads(text)
            self.assertEqual((p["tick_size"], p["tick_override"], p["stop"]), (tick, False, stop), sym)

    def test_tick_size_from_the_flag_and_the_config(self):
        args = ["--symbol", "RELIANCE", "--entry", "1416.30", "--or-high", "1413.40", "--or-low", "1399.50", "--json"]
        code, text, _ = self.plan(*(args + ["--tick-size", "0.01"]))
        p = json.loads(text)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"], p["tick_size"], p["tick_override"]),
                         (1406.45, 9.85, 1436.00, 0.01, True))
        self.write(os.path.join("trader", "config.json"), {"tick_size": 0.01})
        code, text, _ = self.plan(*args)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["tick_override"]), (1406.45, True))     # the config forces it
        code, text, _ = self.plan(*(args + ["--tick-size", "0.5"]))              # the flag beats the config
        self.assertEqual(json.loads(text)["stop"], 1406.00)
        code, _, err = self.plan(*(args[:-1] + ["--tick-size", "0"]))
        self.assertEqual(code, 1)
        self.assertIn("tick_size must be above 0", err)

    def test_atr_stop_reads_bars(self):
        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        code, text, err = self.plan("--stop", "atr:1.5", "--offline", RELIANCE, "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"]), (1403.70, 12.60, 1441.50))
        self.assertEqual((p["stop_rule"], p["atr_bars"]), ("atr", 4))
        self.assertAlmostEqual(p["atr"], 8.375, places=4)
        code, _, err = self.plan("--stop", "atr:1.5", "--offline", RELIANCE, "--entry-bar", "1")
        self.assertEqual(code, 1)
        self.assertIn("at least 3 closed", err)
        os.mkdir("charts")                                          # a folder, and a symbol with an ampersand
        self.write(os.path.join("charts", "M&M.json"), with_symbol("M&M.NS"))
        code, text, err = self.plan("--symbol", "M&M", "--entry", "1416.30", "--date", "2026-10-19", "--entry-bar", "3",
                                    "--stop", "atr:1.5", "--offline", "charts", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(text)["stop"], 1403.70)

    def test_refusals_from_the_cli(self):
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, status="waiting", time=None, price=None,
                                                              bar_index=None, bar_start=None))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("RELIANCE is waiting, not triggered", err)
        code, text, _ = self.plan("--entry", "1413.40", "--json")   # plan ahead at the level
        self.assertEqual(code, 0)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["stop"], p["entry_time"]), (1413.40, 1406.40, None))

        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, setup="level", level=1413.0))
        code, _, err = self.plan()
        self.assertIn("level setup has no opening-range stop", err)
        self.assertEqual(self.plan("--stop", "pct:1")[0], 0)

        code, _, err = self.plan("--side", "sell")
        self.assertIn("v2 plans longs only", err)
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, side="sell"))
        code, _, err = self.plan()
        self.assertIn("longs only", err)
        code, _, err = self.plan("--stop", "price:1416.00")
        self.assertIn("longs only", err)
        os.remove(os.path.join("trader", "entry.json"))
        code, _, err = self.plan()
        self.assertIn("no entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE")
        self.assertIn("needs --entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--from", "x.json", "--entry", "1")
        self.assertIn("not both", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--entry", "1416.30", "--or-high", "1413.40")
        self.assertIn("both --or-high and --or-low", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--entry", "1416.30", "--or-high", "1413.40", "--or-low", "1399.50",
                                 "--stop", "price:1415.65")
        self.assertEqual(code, 1)
        self.assertIn("too tight to survive the spread", err)
        self.assertNotIn("Traceback", err)

    def test_flat_by_config_and_the_square_off_warning(self):
        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:05", "min_reward_risk": 1.5})
        code, text, _ = self.plan("--target", "1R", "--json")
        p = json.loads(text)
        self.assertEqual(p["flat_by"], "15:05")
        self.assertTrue(any("under your min_reward_risk 1.5" in w for w in p["warnings"]))
        self.assertTrue(any("square off intraday (MIS) positions from about 15:05" in w for w in p["warnings"]))
        # 15:00 (the default) is before every broker's auto square-off: no warning
        os.remove(os.path.join("trader", "config.json"))
        code, text, _ = self.plan("--json")
        self.assertEqual(json.loads(text)["flat_by"], "15:00")
        self.assertEqual(json.loads(text)["warnings"], [])
        # an old config with 15:20 is refused: continuous trading of F&O stocks ended at 15:15
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:20"})
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("flat_by 15:20 in the config is at or after 3:15 PM", err)
        self.assertIn("stop-loss orders are cancelled", err)
        os.remove(os.path.join("trader", "config.json"))

    def test_early_close_machinery_and_late_entries(self):
        # NSE_EARLY_CLOSE is empty today; inject a short session to test the machinery
        self.assertEqual(exitp.NSE_EARLY_CLOSE, {})
        short = dict(REEL_ENTRY, date="2026-11-13")
        self.write(os.path.join("trader", "entry.json"), short)
        with mock.patch.object(exitp, "NSE_EARLY_CLOSE", {"2026-11-13": "test short session"}):
            code, text, _ = run(exitp, ["plan", "--now", "2026-11-13 09:36"])
            self.assertEqual(code, 0)
            self.assertIn("Flat by       12:15 IST", text)            # 15:00 moved up by the 2h45m the close moved
            self.assertIn("early close at 12:30 PM (test short session)", text)
            code, _, err = run(exitp, ["plan", "--now", "2026-11-13 09:36", "--flat-by", "14:00"])
            self.assertEqual(code, 1)
            self.assertIn("at or after 12:30 PM", err)
            with mock.patch.object(exitp, "EARLY", datetime.time(13, 0)):    # computed from CLOSE, not a fixed 2h45m
                code, text, _ = run(exitp, ["plan", "--now", "2026-11-13 09:36"])
                self.assertIn("Flat by       12:45 IST", text)
        code, text, _ = run(exitp, ["plan", "--now", "2026-11-13 09:36"])        # injected entry gone: a normal day
        self.assertIn("Flat by       15:00 IST", text)
        self.assertNotIn("early close", text)

        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        code, _, err = self.plan("--flat-by", "16:30")
        self.assertIn("at or after 3:15 PM", err)
        code, _, err = self.plan("--flat-by", "15:15")
        self.assertIn("at or after 3:15 PM", err)
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, time="3:20 PM"))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("no time left to hold it", err)

    def test_show_without_a_plan(self):
        code, text, _ = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("No plan", text)


class SourceHasNoUsLeftovers(unittest.TestCase):
    def test_exit_py(self):
        with open(os.path.join(ROOT, "trader-exit", "exit.py"), encoding="utf-8") as fh:
            text = fh.read()
        for never in ("NYSE", "New York", "Eastern", "EDT", "eastern", "to the cent", "CENT", "cents", "Thanksgiving",
                      "15:55", "1:00 p.m.", "compatible; day-trader-skill"):
            self.assertNotIn(never, text, never)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-exit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-exit\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("before the entry", "a stop is a plan, not a guarantee", "day trading loses most people money",
                     "/trader-timemachine", "/trader-budget", "longs only", RS + "1,406.40", RS + "1,426.20",
                     RS + "9.90", "1,436.10", "too tight to survive the spread", "tick", "nse", "ist",
                     "square-off", "not financial advice", "circuit", "closing auction", "0.10", "15:00"):
            self.assertIn(must, low)
        for never in ("2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "nyse", "new york",
                      "to the " + "cent"):
            self.assertNotIn(never, low)
        self.assertNotIn("$", low.replace("${claude_skill_dir}", ""))         # rupees, never dollars


if __name__ == "__main__":
    unittest.main()
