import calendar
import gzip
import hashlib
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from _load import load, FIX, ROOT

tm = load("trader-timemachine", "timemachine")

DIR = os.path.join(FIX, "trader-timemachine")
# SYNTHETIC Yahoo-shaped chart (tests/fixtures/make_timemachine_fixtures.py), not real market data:
# range=60d interval=5m, as if fetched at 10:41:45 IST on 2026-10-19 (the 10:40 bar still in progress)
REL = os.path.join(DIR, "RELIANCE-60d5m.json.gz")
with gzip.open(REL, "rt", encoding="utf-8") as _fh:
    TEXT = _fh.read()
# REAL Yahoo chart (tests/fixtures/capture_real_fixtures.py): RELIANCE.NS, range=60d interval=5m, captured after
# the close on 2026-11-03 (60 sessions, the last ones in the Closing Auction Session shape: a stub at 15:15, a null
# 15:20 bar, the auction at 15:25)
REAL = os.path.join(DIR, "RELIANCE-60d5m-real.json.gz")
with gzip.open(REAL, "rt", encoding="utf-8") as _fh:
    REAL_TEXT = _fh.read()
NOW = "2026-10-19 10:42"
NOW_UTC = tm.parse_now(NOW)
RS = "₹"

# the canonical plan, as trader-exit writes it
PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-10-19", "setup": "orb",
        "entry": 1416.30, "entry_time": "9:35 AM", "entry_bar": 3, "interval": "5m",
        "opening_range": {"high": 1413.40, "low": 1399.50, "minutes": 15},
        "stop": 1406.40, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 1426.20, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 9.90, "reward_risk": 1.0, "flat_by": "15:00", "tick_size": 0.1, "tick_override": False}

# regression pins of this port's output on the synthetic fixture (seed 19, 10,000 replays, the replay ends at the
# plan's flat-by, 15:00). The algorithm and its random draws are the reel's sim.py; only the session constants
# (72 bars, 9:15 to 15:15) and the tick differ from the US version.
SIM_BANDS = "05d7dc18c06ac296"
SIM_PATHS = "bb6f87aa4d6e67cc"
SIM_TRADES = "29e6c765846b71e8"
SIM_HIST = {"lo": 1386.95, "hi": 1447.7,
            "bins": [107, 119, 143, 203, 282, 396, 379, 537, 576, 640, 768, 746, 808, 736, 661, 647, 557, 443,
                     348, 242, 181, 142, 111, 72]}


def sha(x):
    return hashlib.sha256(json.dumps(x).encode("utf-8")).hexdigest()[:16]


def plan(**kw):
    raw = dict(PLAN)
    raw.update(kw)
    return tm.normalize_plan(raw)


def machine(p=None, text=TEXT, now=NOW_UTC, **kw):
    return tm.time_machine(text, p or plan(), now, **kw)


def edited(fn):
    """The fixture's chart JSON, changed by fn(result), back as text."""
    doc = json.loads(TEXT)
    fn(doc["chart"]["result"][0])
    return json.dumps(doc)


def keep_bars(result, keep):
    """Keep only the bars where keep(ts) is true."""
    q = result["indicators"]["quote"][0]
    idx = [i for i, ts in enumerate(result["timestamp"]) if keep(ts)]
    result["timestamp"] = [result["timestamp"][i] for i in idx]
    for k in q:
        q[k] = [q[k][i] for i in idx]


def ist(ts):
    return tm.to_ist(tm.utc_of(ts))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


class Constants(unittest.TestCase):
    def test_the_nse_session(self):
        # the pack's day ends at 15:15: continuous trading of F&O stocks stops there (closing auction after)
        self.assertEqual((tm.SESSION_BARS, tm.OPEN_MIN, tm.CLOSE_MIN), (72, 555, 915))
        self.assertEqual((tm.clock12(tm.OPEN_MIN), tm.clock12(tm.CLOSE_MIN)), ("9:15 AM", "3:15 PM"))
        self.assertEqual(tm.OPEN_MIN + 5 * tm.SESSION_BARS, tm.CLOSE_MIN)
        self.assertEqual(tm.NSE_EARLY_CLOSE, {})        # no early close on a normal NSE day
        self.assertEqual(tm.MIN_SESSIONS, 20)

    def test_tick_by_price(self):
        for price, tick in ((0.5, 0.01), (249.99, 0.01), (250, 0.05), (1000, 0.05), (1000.01, 0.10), (1416.3, 0.10),
                            (5000, 0.10), (5000.01, 0.50), (10000, 0.50), (10000.01, 1.00), (20000, 1.00),
                            (20000.01, 5.00), (45000, 5.00)):
            self.assertEqual(tm.tick_for(price), tick, price)

    def test_rupees_with_indian_grouping(self):
        self.assertEqual(tm.inr(1416.30), RS + "1,416.30")
        self.assertEqual(tm.inr(123456.789), RS + "1,23,456.79")
        self.assertEqual(tm.inr(1234567.5), RS + "12,34,567.50")
        self.assertEqual(tm.inr(5000, None), RS + "5,000")
        self.assertEqual(tm.inr(5000.5, None), RS + "5,000.50")
        self.assertEqual(tm.inr(99.5), RS + "99.50")
        self.assertEqual(tm.inr(-73650.2), "-" + RS + "73,650.20")
        self.assertEqual(tm.inr(10000000, 0), RS + "1,00,00,000")

    def test_symbols(self):
        self.assertEqual(tm.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(tm.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(tm.yahoo_symbol("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(tm.yahoo_symbol("500325.bo"), "500325.BO")
        self.assertEqual(tm.yahoo_symbol("^nsei"), "^NSEI")
        self.assertEqual(tm.plain_symbol("RELIANCE.NS"), "RELIANCE")
        self.assertEqual(tm.plain_symbol("M&M.NS"), "M&M")
        self.assertEqual(tm.plain_symbol("bajaj-auto"), "BAJAJ-AUTO")

    def test_ist_has_no_daylight_saving(self):
        for day in ("2026-03-02", "2026-07-01", "2026-11-02"):
            utc = tm.parse_now(day + "T03:45Z")
            self.assertEqual(tm.to_ist(utc).strftime("%H:%M"), "09:15")
            self.assertEqual(tm.ist_to_utc(tm.to_ist(utc)), utc)
        self.assertEqual(tm.parse_now("2026-10-19 10:15"), tm.parse_now("2026-10-19T04:45Z"))
        with self.assertRaises(tm.TMError) as cm:
            tm.parse_now("yesterday")
        self.assertIn("IST", str(cm.exception))

    def test_clock_reads(self):
        self.assertEqual([tm.parse_clock(t, "x") for t in ("9:35 AM", "09:35", "3:10 PM", "15:10", "3:10", "12:00 AM")],
                         [575, 575, 910, 910, 910, 0])
        with self.assertRaises(tm.TMError) as cm:
            tm.parse_clock("noon", "--until")
        self.assertIn("IST time", str(cm.exception))


class Reel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = machine()

    def test_the_canonical_odds(self):
        m = self.doc["mc"]
        # the replay ends at the plan's flat-by: 15:00, 65 bars after the 9:35 entry
        self.assertEqual((m["replays"], m["seed"], m["window"], m["until"]), (10000, 19, 2, "15:00"))
        self.assertEqual(m["pool_sessions"], 39)       # 60 calendar days hold about 40 full NSE sessions
        self.assertEqual(m["bars_left"], 65)
        self.assertEqual((m["pool_from"], m["pool_to"]), ("2026-08-21", "2026-10-16"))
        self.assertEqual(m["p_target"], 0.4902)
        self.assertEqual(m["p_stop"], 0.389)
        self.assertEqual(m["p_neither"], 0.1208)
        self.assertEqual(m["p_green"], 0.5539)
        self.assertEqual(m["median_close"], 1417.46)
        self.assertEqual(m["p5_close"], 1395.65)
        self.assertEqual(m["p95_close"], 1439.11)
        self.assertEqual(m["avg_R"], 0.102)
        self.assertEqual(m["r_at_target"], 1.0)

    def test_pinned_draws(self):
        m = self.doc["mc"]
        self.assertEqual(sha(m["bands"]), SIM_BANDS)
        self.assertEqual(len(m["sample_paths"]), 160)
        self.assertEqual(sha(m["sample_paths"]), SIM_PATHS)
        self.assertEqual(m["hist"], SIM_HIST)
        self.assertEqual(m["bands"][-1], [1395.94, 1408.78, 1417.22, 1425.87, 1438.05])

    def test_the_history_replay(self):
        h = self.doc["history"]
        self.assertEqual((h["sessions"], h["triggered"]), (39, 30))
        self.assertEqual((h["target"], h["stop"], h["close"], h["green"]), (15, 13, 2, 15))
        self.assertEqual(h["avg_R"], 0.04)
        self.assertEqual(h["rule"], {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid", "target_r": 1.0,
                                     "tick_size": None})          # None: NSE's tick for each price
        slim = [{"date": t["date"], "entry": t["entry"], "outcome": t["outcome"], "R": t["R"]} for t in h["trades"]]
        self.assertEqual(sha(slim), SIM_TRADES)
        first = h["trades"][0]
        self.assertEqual((first["date"], first["time"], first["entry"], first["stop"], first["target"],
                          first["outcome"]), ("2026-08-21", "9:40 AM", 1407.5, 1402.5, 1412.5, "stop"))
        for t in h["trades"]:       # every stop sits on the tick of its own price (0.10 here)
            tick = tm.tick_for(t["stop"])
            self.assertAlmostEqual(t["stop"] / tick, round(t["stop"] / tick), places=6, msg=t)

    def test_the_verdict(self):
        self.assertEqual(self.doc["verdict"], "Leans toward the target: target first 49.0 % vs stop first 38.9 %.")
        # 1R breaks even at 50 % of the replays that hit one side; this target got 55.8 %
        gap, share, even = tm.lean(self.doc["mc"])
        self.assertEqual((round(share, 3), even, round(gap, 3)), (0.558, 0.5, 0.058))
        self.assertEqual(self.doc["verdict_notes"], [])     # only 12.1 % hit neither

    def test_bands_start_at_the_entry(self):
        m = self.doc["mc"]
        self.assertEqual(m["bands"][0], [1416.30] * 5)
        self.assertEqual(len(m["bands"]), 66)
        self.assertEqual((m["band_times"][0], m["band_times"][-1], len(m["band_times"])), ("09:35", "15:00", 66))
        self.assertTrue(all(p[0] == 1416.30 and len(p) == 66 for p in m["sample_paths"]))

    def test_the_plan_is_carried_through(self):
        p = self.doc["plan"]
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["target"], p["entry_bar"], p["entry_time"]),
                         ("RELIANCE", 1416.30, 1406.40, 1426.20, 3, "9:35 AM"))
        self.assertEqual((p["risk_per_share"], p["target_r"], p["flat_by"]), (9.9, 1.0, "15:00"))
        self.assertNotIn("tick_size", p)          # not forced: NSE's tick for each price (the plan's is only a reference)
        self.assertEqual(self.doc["warnings"], [])
        self.assertEqual(self.doc["data"]["symbol"], "RELIANCE")
        self.assertEqual(self.doc["data"]["last_bar"], "2026-10-19 10:35 IST")

    def test_the_text_report(self):
        text = "\n".join(tm.report(self.doc))
        for line in ("RELIANCE buy, Monday 2026-10-19: entry " + RS + "1,416.30 at 9:35 AM (bar 3), stop "
                     + RS + "1,406.40, target " + RS + "1,426.20 (1R, risk " + RS + "9.90 a share)",
                     "10,000 replays", "65 five-minute bars", "9:35 AM to 3:00 PM", "seed 19", "39 full sessions",
                     "target first   49.0 %", "stop first     38.9 %", "neither        12.1 %   still open at 3:00 PM",
                     "green          55.4 %",
                     "median " + RS + "1,417.46, 90 % of replays between " + RS + "1,395.65 and " + RS + "1,439.11",
                     "Average R: +0.102 (R = the " + RS + "9.90 risk",
                     "stop at the opening-range midpoint, 1R target",
                     "fired 30 times: 15 target, 13 stop, 2 neither. Average R +0.040",
                     "Leans toward the target: target first 49.0 % vs stop first 38.9 %.",
                     "not a prediction", "circuit limits", "--seed 19"):
            self.assertIn(line, text)
        for word in ("profit", "P&L", "guarantee", "real time", "tick by tick", "$", "New York", " ET"):
            self.assertNotIn(word, text)


class Seeds(unittest.TestCase):
    def test_same_seed_same_numbers(self):
        a = machine(replays=2000, seed=5)["mc"]
        b = machine(replays=2000, seed=5)["mc"]
        self.assertEqual(a, b)

    def test_another_seed_gives_other_numbers(self):
        m = machine(seed=7)["mc"]
        self.assertNotEqual((m["p_target"], m["p_stop"], m["median_close"]), (0.4902, 0.389, 1417.46))
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertNotEqual(sha(m["bands"]), SIM_BANDS)

    def test_the_draws_are_stable_across_pythons(self):
        # random.Random(seed), randint and choice are stable since Python 3.2; this pins the first draws so
        # a failure points at the RNG
        r = random.Random(19)
        self.assertEqual([r.randint(-2, 2) for _ in range(8)], [-2, 2, -2, 2, -1, 1, 0, 2])
        self.assertEqual(r.choice(range(39)), 18)

    def test_window_and_replays(self):
        a = machine(replays=500, window=0)["mc"]
        self.assertEqual((a["replays"], a["window"]), (500, 0))
        self.assertNotEqual(a["bands"], machine(replays=500)["mc"]["bands"])
        with self.assertRaises(tm.TMError):
            machine(replays=0)
        with self.assertRaises(tm.TMError):
            machine(window=11)


class Bars(unittest.TestCase):
    def setUp(self):
        self.result = tm.chart_result(TEXT, "RELIANCE")

    def test_the_in_progress_tick_is_dropped(self):
        meta, bars, dropped = tm.parse_bars(self.result, NOW_UTC)
        # 117 = the 15:15, 15:20 and 15:25 bars of each of the 39 earlier sessions: the pack's day ends at 15:15
        self.assertEqual(dropped, {"no_price": 0, "off_grid": 1, "not_complete": 1, "outside_hours": 117})
        last = bars[-1]
        self.assertEqual((last["date"], last["start"], last["slot"]), ("2026-10-19", "10:35", 16))
        # with the real clock (any time later) the data's own time still marks 10:40 as in progress
        self.assertEqual(tm.parse_bars(self.result, tm.parse_now(None))[1][-1]["start"], "10:35")
        entry = [b for b in bars if b["date"] == "2026-10-19" and b["slot"] == 3][0]
        self.assertEqual((entry["start"], entry["c"]), ("09:30", 1416.30))

    def test_now_pins_the_clock(self):
        bars = tm.parse_bars(self.result, tm.parse_now("2026-10-19 09:42"))[1]
        today = [b for b in bars if b["date"] == "2026-10-19"]
        self.assertEqual(len(today), 5)     # the 9:15 ... 9:35 bars; the 9:35 bar closes at 9:40
        with self.assertRaises(tm.TMError) as cm:
            machine(now=tm.parse_now("2026-10-19 09:32"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("09:25 bar", str(cm.exception))
        self.assertEqual(machine(now=tm.parse_now("2026-10-19 09:35"), replays=200)["mc"]["pool_sessions"], 39)

    def test_ist_timestamps(self):
        def at(day, hhmm):          # UTC seconds of an IST wall-clock minute
            h, m = int(hhmm[:2]), int(hhmm[3:])
            return calendar.timegm((int(day[:4]), int(day[5:7]), int(day[8:]), h, m, 0)) - 19800
        stamps = [at("2026-10-30", "09:15"), at("2026-11-02", "09:15"),   # US clocks changed on Nov 1: IST did not
                  at("2026-11-02", "09:10"),                             # pre-open, before the 9:15 start
                  at("2026-11-02", "15:10"), at("2026-11-02", "15:15")]   # last bar in, the one after is out
        res = {"meta": {"symbol": "X.NS"}, "timestamp": stamps,
               "indicators": {"quote": [{"open": [1] * 5, "high": [1] * 5, "low": [1] * 5, "close": [1] * 5,
                                         "volume": [0] * 5}]}}
        meta, bars, dropped = tm.parse_bars(res, tm.parse_now("2026-11-03T00:00Z"))
        self.assertEqual([(b["date"], b["start"], b["slot"]) for b in bars],
                         [("2026-10-30", "09:15", 0), ("2026-11-02", "09:15", 0), ("2026-11-02", "15:10", 71)])
        self.assertEqual(dropped["outside_hours"], 2)

    def test_a_short_session_is_left_out(self):
        def cut(res):  # 2026-09-04 ends at 12:30 PM, like a special short session: 39 bars
            keep_bars(res, lambda ts: not (ist(ts).date().isoformat() == "2026-09-04"
                                           and ist(ts).hour * 60 + ist(ts).minute >= 12 * 60 + 30))
        doc = machine(text=edited(cut), replays=300)
        self.assertEqual(doc["mc"]["pool_sessions"], 38)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": "2026-09-04", "bars": 39}])
        self.assertEqual(doc["history"]["sessions"], 38)
        self.assertNotIn("2026-09-04", [t["date"] for t in doc["history"]["trades"]])
        self.assertIn("Left out 1 short session", "\n".join(tm.report(doc)))

    def test_today_is_not_in_the_pool(self):
        doc = machine(replays=200)
        self.assertEqual(doc["mc"]["pool_to"], "2026-10-16")
        self.assertNotIn("2026-10-19", [t["date"] for t in doc["history"]["trades"]])

    def test_a_missing_price_is_dropped(self):
        def hole(res):
            res["indicators"]["quote"][0]["close"][5] = None
        doc = machine(text=edited(hole), replays=200)
        self.assertEqual(doc["data"]["dropped"]["no_price"], 1)
        self.assertEqual(doc["mc"]["pool_sessions"], 38)   # 2026-08-21 now has a gap

    def test_empty_and_broken_data(self):
        empty = json.dumps({"chart": {"result": [{"meta": {"symbol": "RELIANCE.NS"}, "timestamp": [],
                                                  "indicators": {"quote": [{}]}}], "error": None}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=empty)
        self.assertIn("no complete 5-minute bars", str(cm.exception))
        gone = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                               "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=gone)
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(tm.TMError):
            machine(text="<html>rate limited</html>")

    def test_fewer_than_20_sessions_is_refused(self):
        def trim(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-09-21")
        with self.assertRaises(tm.TMError) as cm:
            machine(text=edited(trim))
        self.assertIn("only 19 full session(s)", str(cm.exception))
        self.assertIn("at least 20", str(cm.exception))

        def twenty(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-09-18")
        self.assertEqual(machine(text=edited(twenty), replays=200)["mc"]["pool_sessions"], 20)

    def test_the_data_must_be_the_plans_symbol(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(symbol="TCS"))
        self.assertIn("data is for RELIANCE", str(cm.exception))

    def test_m_and_m_matches_its_yahoo_symbol(self):
        text = edited(lambda res: res["meta"].update(symbol="M&M.NS"))
        doc = machine(plan(symbol="m&m"), text=text, replays=200)
        self.assertEqual((doc["plan"]["symbol"], doc["data"]["symbol"]), ("M&M", "M&M"))
        with self.assertRaises(tm.TMError):
            machine(plan(symbol="RELIANCE"), text=text, replays=10)


class RealData(unittest.TestCase):
    """The real Yahoo answer for RELIANCE.NS (60 sessions captured after the close on 2026-11-03). Every session
    has a stub bar at 15:15, a null 15:20 bar and the closing-auction bar at 15:25: with the old 75-bar day
    (to 15:30) not one session was 'full' and the time machine refused to run."""

    @classmethod
    def setUpClass(cls):
        cls.result = tm.chart_result(REAL_TEXT, "RELIANCE")

    def test_the_tail_of_a_session_is_the_auction_shape(self):
        stamps = [t for t in self.result["timestamp"] if ist(t).date().isoformat() == "2026-11-03"]
        q = self.result["indicators"]["quote"][0]
        idx = {ist(t).strftime("%H:%M"): i for i, t in enumerate(self.result["timestamp"])
               if ist(t).date().isoformat() == "2026-11-03"}
        self.assertEqual(len(stamps), 75)
        self.assertIsNotNone(q["close"][idx["15:15"]])      # the stub
        self.assertIsNone(q["close"][idx["15:20"]])         # null, every day
        self.assertIsNotNone(q["close"][idx["15:25"]])      # the auction

    def test_every_full_session_has_72_bars_and_the_tail_is_ignored(self):
        now = tm.parse_now("2026-11-04 08:00")
        meta, bars, dropped = tm.parse_bars(self.result, now)
        days = tm.split_sessions(bars)
        full, short = tm.full_sessions(days, "2026-11-04")
        self.assertEqual(len(days), 60)
        self.assertGreaterEqual(len(full), 55)               # 59 of the 60 sessions are complete
        self.assertEqual([x["date"] for x in short], ["2026-10-15"])       # one session has gaps (70 bars)
        self.assertTrue(all(len(bars_) == 72 for _, bars_ in full))
        self.assertTrue(all(b["slot"] <= 71 for b in bars))
        self.assertGreaterEqual(dropped["no_price"], 60)     # at least the null 15:20 bar of every session
        self.assertGreater(dropped["outside_hours"], 60)     # the 15:15 stub and the 15:25 auction bar, mostly

    def test_the_time_machine_runs_on_real_data(self):
        real_plan = plan(date="2026-11-03", entry=1300.0, stop=1290.0, target=1320.0, entry_time="9:35 AM",
                         entry_bar=None, opening_range={"minutes": 15}, stop_rule="pct", flat_by="15:00",
                         tick_size=None)
        doc = tm.time_machine(REAL_TEXT, real_plan, tm.parse_now("2026-11-04 08:00"), replays=2000)
        m = doc["mc"]
        self.assertEqual(m["until"], "15:00")
        self.assertEqual(m["pool_sessions"], 58)             # the sessions before 2026-11-03 that are complete
        self.assertEqual((m["pool_from"], m["pool_to"]), ("2026-08-07", "2026-11-02"))
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": "2026-10-15", "bars": 70}])
        self.assertTrue(doc["history"]["triggered"] > 10)

    def test_the_old_75_bar_day_would_have_found_no_full_session(self):
        meta, bars, dropped = tm.parse_bars(self.result, tm.parse_now("2026-11-04 08:00"))
        with mock.patch.object(tm, "SESSION_BARS", 75):
            full, short = tm.full_sessions(tm.split_sessions(bars), "2026-11-04")
        self.assertEqual(len(full), 0)


class ShortSessions(unittest.TestCase):
    """NSE_EARLY_CLOSE is empty today (no early closes on a normal day), but a listed special short session
    must still end the replay at its own close. Covered here by injecting an entry."""

    def test_the_replay_ends_at_the_plans_flat_by_else_the_end_of_the_day(self):
        self.assertEqual(tm.NSE_EARLY_CLOSE, {})
        self.assertEqual(machine(replays=100)["mc"]["until"], "15:00")                      # the plan's flat_by
        self.assertEqual(machine(plan(flat_by=None), replays=100)["mc"]["until"], "15:15")  # no flat_by: the day's end
        self.assertEqual(machine(plan(flat_by="15:20"), replays=100)["mc"]["until"], "15:15")   # never past the end
        doc = machine(plan(flat_by="15:03"), replays=100)       # not on a bar boundary: the earlier boundary, said so
        self.assertEqual(doc["mc"]["until"], "15:00")
        self.assertIn("the plan's flat_by 15:03 is not a 5-minute bar boundary", doc["warnings"][0])
        self.assertEqual(machine(replays=100, until="15:10")["mc"]["until"], "15:10")      # --until beats the plan
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(flat_by="09:35"), replays=10)
        self.assertIn("the plan's flat_by 09:35 leaves nothing to replay after the 9:35 AM entry", str(cm.exception))

    def test_a_listed_short_session_ends_the_replay_at_its_close(self):
        table = {"2026-10-19": ("13:15", "a special short session")}
        with mock.patch.object(tm, "NSE_EARLY_CLOSE", table):
            doc = machine(replays=300)
            self.assertEqual((doc["mc"]["until"], doc["mc"]["bars_left"], doc["mc"]["band_times"][-1]),
                             ("13:15", 44, "13:15"))
            self.assertEqual(doc["mc"]["pool_sessions"], 39)
            self.assertIn("to 1:15 PM", "\n".join(tm.report(doc)))
            with self.assertRaises(tm.TMError) as cm:
                machine(until="15:10", replays=10)
            self.assertIn("short session", str(cm.exception))
            self.assertIn("no later than 1:15 PM", str(cm.exception))
            self.assertEqual(machine(until="12:30", replays=50)["mc"]["until"], "12:30")
        self.assertEqual(machine(until="15:10", replays=10)["mc"]["until"], "15:10")     # table restored


class Plans(unittest.TestCase):
    def test_an_entry_not_in_the_data_is_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-10-20"))        # Dussehra, an NSE holiday: no bars
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("no bars for that day", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:      # 10:55 has not happened in this data yet
            machine(plan(entry_time="10:55 AM", entry_bar=None))
        self.assertIn("not in the data", str(cm.exception))

    def test_plan_checks(self):
        cases = [
            (dict(entry_time="10:17 AM", entry_bar=None), "5-minute grid"),
            (dict(entry_time="3:15 PM", entry_bar=None), "between 9:20 AM and 3:10 PM"),
            (dict(entry_time="3:30 PM", entry_bar=None), "between 9:20 AM and 3:10 PM"),
            (dict(entry_time="9:15 AM", entry_bar=None), "between 9:20 AM and 3:10 PM"),
            (dict(entry_bar=4), "disagrees with itself"),
            (dict(entry_time=None, entry_bar=None), "entry_time"),
            (dict(interval="1m"), "7 days of 1-min bars"),
            (dict(stop=1420.0), "stop < entry < target"),
            (dict(side="short"), "buy or sell"),
            (dict(symbol=""), "NSE symbol"),
            (dict(symbol="not a symbol!"), "NSE symbol"),
            (dict(entry="abc"), "number for entry"),
            (dict(date="Oct 19"), "date like"),
            (dict(opening_range={"minutes": 7}), "multiple of 5"),
            (dict(tick_size="x", tick_override=True), "tick_size"),
            (dict(tick_size=50, tick_override=True), "tick_size"),
        ]
        for change, words in cases:
            with self.assertRaises(tm.TMError, msg=change) as cm:
                plan(**change)
            self.assertIn(words, str(cm.exception), change)

    def test_the_entry_bounds_come_from_the_constants(self):
        self.assertEqual(plan(entry_time="9:20 AM", entry_bar=None)["entry_bar"], 0)
        self.assertEqual(plan(entry_time="3:10 PM", entry_bar=None)["entry_bar"], tm.SESSION_BARS - 2)

    def test_symbols_in_plans(self):
        self.assertEqual(plan(symbol="reliance.ns")["symbol"], "RELIANCE")
        self.assertEqual(plan(symbol="m&m")["symbol"], "M&M")
        self.assertEqual(plan(symbol="BAJAJ-AUTO")["symbol"], "BAJAJ-AUTO")
        self.assertEqual(plan(symbol="20MICRONS")["symbol"], "20MICRONS")

    def test_entry_bar_or_entry_time_alone(self):
        self.assertEqual(plan(entry_time=None)["entry_time"], "9:35 AM")
        self.assertEqual(plan(entry_bar=None)["entry_bar"], 3)
        self.assertEqual(plan(entry_time="09:35", entry_bar=None)["entry_bar"], 3)
        self.assertEqual(plan(entry_time="9:35am", entry_bar=None)["entry_bar"], 3)

    def test_until_cuts_the_replay(self):
        doc = machine(until="14:45", replays=500)
        m = doc["mc"]
        self.assertEqual((m["until"], m["bars_left"], m["band_times"][-1]), ("14:45", 62, "14:45"))
        self.assertIn("still open at 2:45 PM", "\n".join(tm.report(doc)))
        self.assertEqual(machine(until="2:45 PM", replays=500)["mc"], m)
        self.assertEqual(machine(until="3:15 PM", replays=100)["mc"]["until"], "15:15")      # the day's end is allowed
        for bad, words in (("9:35 AM", "nothing to replay"), ("15:20", "no later than 3:15 PM"),
                           ("15:35", "no later than 3:15 PM"), ("15:12", "5-minute grid"), ("noon", "IST time")):
            with self.assertRaises(tm.TMError) as cm:
                machine(until=bad, replays=10)
            self.assertIn(words, str(cm.exception), bad)

    def test_an_entry_outside_its_bar_is_flagged(self):
        doc = machine(plan(entry=1430.0, stop=1420.0, target=1440.0), replays=200)
        self.assertIn("outside that bar's range " + RS + "1,408.20 to " + RS + "1,418.20", doc["warnings"][0])

    def test_stop_rounds_away_from_the_entry_to_the_tick(self):
        self.assertEqual(tm.tick_away(1406.475, True), 1406.40)
        self.assertEqual(tm.tick_away(1406.475, False), 1406.5)
        self.assertEqual(tm.tick_away(1399.50, True), 1399.50)
        self.assertEqual(tm.tick_away(1399.5000000000002, True), 1399.50)
        self.assertEqual(tm.tick_away(1399.5499999999997, True), 1399.50)
        self.assertEqual(tm.tick_away(1399.56, True), 1399.50)
        self.assertEqual(tm.tick_away(1399.56, False), 1399.6)
        self.assertEqual(tm.tick_away(358.325, True, 0.01), 358.32)      # a forced tick of Rs 0.01
        self.assertEqual(tm.tick_away(358.325, False, 0.01), 358.33)
        # with no forced tick every price uses its own band
        self.assertEqual(tm.tick_away(249.995, True), 249.99)            # under 250: 0.01
        self.assertEqual(tm.tick_away(250.02, True), 250.00)             # 250 to 1,000: 0.05
        self.assertEqual(tm.tick_away(999.97, True), 999.95)
        self.assertEqual(tm.tick_away(1000.04, True), 1000.00)           # above 1,000: 0.10
        self.assertEqual(tm.tick_away(5000.4, True), 5000.0)             # above 5,000: 0.50
        self.assertEqual(tm.tick_away(10000.9, False), 10001.0)          # above 10,000: 1.00
        self.assertEqual(tm.tick_away(20000.1, True), 20000.0)           # above 20,000: 5.00

    def test_the_stop_rule_is_worked_out_when_not_given(self):
        rule = machine(plan(stop_rule=None), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_mid")
        rule = machine(plan(stop_rule=None, stop=1399.50, target=1433.10), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["target_r"]), ("or_low", 1.0))
        rule = machine(plan(stop_rule="atr", stop=1400.0, target=1448.90), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["stop_pct"], rule["target_r"]), ("pct", 1.1509, 2.0))

    def test_the_tick_size_reaches_the_history_rule_only_when_forced(self):
        doc = machine(plan(stop_rule=None, tick_size=0.01, tick_override=True), replays=100)
        self.assertEqual(doc["history"]["rule"]["tick_size"], 0.01)
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "pct")      # 1406.45 is not the plan's 1406.40
        # a plan's tick_size is only the entry's tick for reference: without tick_override it is not forced
        doc = machine(plan(stop_rule=None, tick_size=0.01), replays=100)
        self.assertIsNone(doc["history"]["rule"]["tick_size"])
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_mid")

    def test_a_setup_other_than_orb_skips_the_history(self):
        doc = machine(plan(setup="flag"), replays=200)
        self.assertIsNone(doc["history"]["rule"])
        self.assertIn("orb", doc["history"]["note"])
        self.assertEqual(doc["mc"]["pool_sessions"], 39)
        self.assertIn("History: skipped", "\n".join(tm.report(doc)))

    def test_a_sell_plan_mirrors_the_rules(self):
        doc = machine(plan(side="sell", stop=1426.20, target=1406.40, stop_rule="or_high"), replays=2000)
        m = doc["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertEqual(m["r_at_target"], 1.0)
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_high")
        for t in doc["history"]["trades"]:
            self.assertTrue(t["target"] < t["entry"] < t["stop"], t)
        self.assertIn("below the entry", "\n".join(tm.report(doc)))

    def test_the_verdict_thresholds_at_1r(self):
        # 1R: the target's share of the replays that hit one side against 50 %
        self.assertTrue(tm.verdict({"p_target": 0.30, "p_stop": 0.26}).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict({"p_target": 0.40, "p_stop": 0.30}).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict({"p_target": 0.20, "p_stop": 0.30}).startswith("Leans toward the stop"))
        self.assertTrue(tm.verdict({"p_target": 0.60, "p_stop": 0.30}).startswith("Strongly toward the target"))
        self.assertEqual(tm.verdict({"p_target": 0.10, "p_stop": 0.60, "r_at_target": 1.0}),
                         "Strongly toward the stop: target first 10.0 % vs stop first 60.0 %.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0, "until": "15:00"}),
                         "No replay reached the target or the stop by 3:00 PM: the price at 3:00 PM decides every one.")
        self.assertIn("by 3:15 PM", tm.verdict({"p_target": 0.0, "p_stop": 0.0}))      # default: the end of the day

    def test_a_2r_target_is_judged_against_its_break_even(self):
        # an example run (2R): it needs 33 % of the replays that hit one side, got 32 %
        mc = {"p_target": 0.241, "p_stop": 0.505, "p_neither": 0.254, "r_at_target": 2.0, "avg_R": 0.095}
        self.assertEqual(tm.verdict(mc), "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 32 % (target first 24.1 % vs stop "
                                         "first 50.5 %).")
        self.assertEqual(tm.verdict_notes(mc), [])
        lean = dict(mc, p_target=0.15, p_stop=0.55)
        self.assertTrue(tm.verdict(lean).startswith("Leans toward the stop: at 2R the target has to come first in "
                                                    "33 % of the replays that hit one side; it did in 21 %"))
        self.assertEqual(tm.verdict_notes(lean), ["Average R is +0.095 all the same: the replays that hit neither "
                                                  "pull it the other way."])
        self.assertTrue(tm.verdict(dict(mc, p_target=0.30, p_stop=0.40)).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict(dict(mc, p_target=0.40, p_stop=0.40)).startswith("Strongly toward the target"))
        self.assertIn("at 1.5R the target has to come first in 40 %", tm.verdict(dict(mc, r_at_target=1.5)))

    def test_2r_on_the_fixture_leans_toward_the_stop(self):
        # the canonical entry and stop with a 2R target 1436.10: 16.0 % vs 42.1 %; 2R needs a third of the replays
        # that hit one side and got 28 %. Many replays hit neither by 15:00, and they pull the average R up
        doc = machine(plan(target=1436.10))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["p_neither"], m["avg_R"]),
                         (2.0, 0.1602, 0.4213, 0.4185, 0.1))
        self.assertEqual(doc["verdict"], "Leans toward the stop: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 28 % (target first 16.0 % vs stop "
                                         "first 42.1 %).")
        self.assertEqual(doc["verdict_notes"], [
            "41.9 % of the replays hit neither by 3:00 PM; the price at 3:00 PM decides those.",
            "Average R is +0.100 all the same: the replays that hit neither pull it the other way."])

    def test_2r_on_the_fixture_a_clear_lean(self):
        # a wider stop at 1400 and a 2R target 1448.90: target first only 1.7 % vs stop first 19.6 %
        doc = machine(plan(stop=1400.0, target=1448.90, stop_rule="pct"))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["p_neither"], m["avg_R"]),
                         (2.0, 0.0171, 0.1957, 0.7872, 0.063))
        self.assertEqual(doc["verdict"], "Strongly toward the stop: at 2R the target has to come first in 33 % of "
                                         "the replays that hit one side; it did in 8 % (target first 1.7 % vs stop "
                                         "first 19.6 %).")
        self.assertEqual(doc["verdict_notes"], [
            "78.7 % of the replays hit neither by 3:00 PM; the price at 3:00 PM decides those.",
            "Average R is +0.063 all the same: the replays that hit neither pull it the other way."])
        text = "\n".join(tm.report(doc))
        self.assertIn(doc["verdict"] + "\n" + doc["verdict_notes"][0] + "\n" + doc["verdict_notes"][1], text)


class Network(unittest.TestCase):
    def setUp(self):
        self.calls, self.urls, self.sleeps = [], [], []
        self._get, self._sleep = tm._get, tm._sleep
        tm._sleep = self.sleeps.append

    def tearDown(self):
        tm._get, tm._sleep = self._get, self._sleep

    def answer(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.urls.append(url)
            self.calls.append(url.split(".finance")[0].split("//")[1])
            code = codes.pop(0)
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return TEXT
        tm._get = fake

    def test_url_and_browser_headers(self):
        self.assertEqual(tm.chart_url("reliance"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=60d&interval=5m")
        self.assertEqual(tm.chart_url("RELIANCE.NS"), tm.chart_url("reliance"))
        self.assertIn("/chart/BAJAJ-AUTO.NS?", tm.chart_url("bajaj-auto", "query1"))
        self.assertIn("/chart/M%26M.NS?", tm.chart_url("m&m"))
        self.assertIn("/chart/%5ENSEI?", tm.chart_url("^nsei"))
        self.assertEqual(tm.IMPERSONATE, "chrome")
        self.assertIn("Chrome/", tm.BROWSER_UA)
        self.assertNotIn("compatible", tm.BROWSER_UA)
        self.assertEqual(tm.YAHOO_HEADERS["Referer"], "https://finance.yahoo.com/")

    def test_429_waits_retries_then_falls_back_to_query1(self):
        self.answer([429, 429, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2", "query1"])
        self.assertEqual(self.sleeps, [tm.BACKOFF, tm.PAUSE])

    def test_one_429_then_ok(self):
        self.answer([429, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2"])

    def test_5xx_falls_back(self):
        self.answer([503, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query1"])

    def test_rate_limited_everywhere_is_one_plain_line(self):
        self.answer([429, 429, 429, 429])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("RELIANCE")
        self.assertIn("rate limiting", str(cm.exception))

    def test_404(self):
        self.answer([404])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("ZZZZ")
        self.assertIn("check the NSE symbol", str(cm.exception))

    def test_run_over_the_network_path(self):
        self.answer([200])
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(PLAN, fh)
            code, text, _ = run(["run", "--plan", p, "--now", NOW, "--replays", "300"])
            self.assertEqual(code, 0)
            self.assertEqual(self.calls, ["query2"])
            self.assertIn("/chart/RELIANCE.NS?", self.urls[0])
        finally:
            shutil.rmtree(d)


class Transport(unittest.TestCase):
    """The real _get, with curl_cffi (Chrome's TLS fingerprint) and with the urllib fallback."""

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
        with mock.patch.object(tm, "_cffi", fake):
            text = tm._get("https://query2.finance.yahoo.com/x")
        self.assertEqual(text, '{"ok": "₹"}')
        url, kw = fake.calls[0]
        self.assertEqual(kw["impersonate"], "chrome")
        self.assertEqual(kw["headers"]["Referer"], "https://finance.yahoo.com/")
        self.assertNotIn("User-Agent", kw["headers"])               # curl_cffi sets Chrome's own

    def test_http_errors_and_network_failures_look_like_urllib_to_the_callers(self):
        with mock.patch.object(tm, "_cffi", self.FakeCffi(self.Resp(429, b"slow down"))):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                tm._get("https://query2.finance.yahoo.com/x")
        self.assertEqual(cm.exception.code, 429)
        with mock.patch.object(tm, "_cffi", self.FakeCffi(exc=RuntimeError("timed out"))):
            with self.assertRaises(urllib.error.URLError):
                tm._get("https://query2.finance.yahoo.com/x")
        # fetch_chart turns both into one plain TMError
        with mock.patch.object(tm, "_cffi", self.FakeCffi(exc=RuntimeError("timed out"))):
            with self.assertRaises(tm.TMError) as cm:
                tm.fetch_chart("RELIANCE")
        self.assertIn("could not reach Yahoo", str(cm.exception))

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
        with mock.patch.object(tm, "_cffi", None), mock.patch.object(tm.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(tm._get("https://query2.finance.yahoo.com/x"), "{}")
        self.assertIn("Chrome/", seen["headers"]["User-agent"])
        self.assertEqual(seen["headers"]["Referer"], "https://finance.yahoo.com/")


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.plan = os.path.join(self.d, "trader", "plan.json")
        os.makedirs(os.path.dirname(self.plan))
        with open(self.plan, "w") as fh:
            json.dump(PLAN, fh, indent=2)
        self.out = os.path.join(self.d, "trader", "timemachine.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_run_with_the_plan_end_to_end(self):
        code, text, err = run(["run", "--plan", self.plan, "--offline", REL, "--now", NOW, "--write", self.out])
        self.assertEqual(code, 0, err)
        self.assertIn("target first   49.0 %", text)
        self.assertIn("Leans toward the target: target first 49.0 % vs stop first 38.9 %.", text)
        self.assertIn(RS + "1,416.30", text)
        self.assertIn("Wrote %s." % self.out, text)
        self.assertIn("/trader-budget", text)
        with open(self.out) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(k for k in ("plan", "mc", "history") if k in doc), ["history", "mc", "plan"])
        self.assertEqual(doc["created_by"], "trader-timemachine")
        self.assertEqual(doc["source"], "offline")
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["avg_R"]), (0.4902, 0.102))
        self.assertEqual(sha(doc["mc"]["sample_paths"]), SIM_PATHS)
        self.assertEqual((doc["history"]["triggered"], doc["history"]["avg_R"]), (30, 0.04))
        self.assertEqual(doc["plan"]["from"], self.plan)

    def test_json_output(self):
        code, text, _ = run(["run", "--plan", self.plan, "--offline", REL, "--now", NOW, "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual(doc["mc"]["p_stop"], 0.389)
        self.assertNotIn("sample_paths", doc["mc"])
        self.assertEqual(doc["verdict"][:21], "Leans toward the targ")
        self.assertFalse(os.path.exists(self.out))

    def test_the_plan_as_flags_matches_the_file(self):
        flags = ["run", "--symbol", "reliance", "--date", "2026-10-19", "--entry", "1416.30",
                 "--stop", "1406.40", "--target", "1426.20", "--entry-time", "9:35 AM", "--until", "15:00",
                 "--offline", REL, "--now", NOW, "--json"]
        code, text, err = run(flags)
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["p_green"]), (0.4902, 0.5539))
        self.assertEqual(doc["plan"]["stop_rule"], "or_mid")
        self.assertEqual((doc["history"]["target"], doc["history"]["stop"], doc["history"]["avg_R"]), (15, 13, 0.04))
        # a plan given as flags has no flat-by: the replay then runs to the end of the day, 15:15
        doc = json.loads(run(flags[:flags.index("--until")] + flags[flags.index("--until") + 2:])[1])
        self.assertEqual((doc["mc"]["until"], doc["mc"]["p_target"]), ("15:15", 0.4986))
        code, _, err = run(["run", "--symbol", "RELIANCE", "--entry", "1416.30", "--offline", REL])
        self.assertEqual(code, 1)
        self.assertIn("--stop", err)

    def test_tick_size_flag(self):
        flags = ["run", "--symbol", "reliance", "--date", "2026-10-19", "--entry", "1416.30",
                 "--stop", "1406.40", "--target", "1426.20", "--entry-time", "9:35 AM",
                 "--offline", REL, "--now", NOW, "--json", "--replays", "200"]
        doc = json.loads(run(flags)[1])
        self.assertNotIn("tick_size", doc["plan"])                   # not forced: NSE's tick for each price
        self.assertIsNone(doc["history"]["rule"]["tick_size"])
        doc = json.loads(run(flags + ["--tick-size", "0.01"])[1])
        self.assertEqual((doc["plan"]["tick_size"], doc["history"]["rule"]["tick_size"]), (0.01, 0.01))
        code, _, err = run(flags + ["--tick-size", "0"])
        self.assertEqual(code, 1)
        self.assertIn("tick_size", err)

    def test_offline_folder_and_default_paths(self):
        shutil.copy(REL, self.d)
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            code, text, err = run(["run", "--offline", ".", "--now", NOW, "--replays", "500", "--write"])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.exists(os.path.join("trader", "timemachine.json")))
            code, text, err = run(["chart", "--from"])
            self.assertEqual(code, 0, err)
            self.assertIn("* median", text)
            with open(os.path.join("trader", "timemachine.json")) as fh:
                self.assertEqual(json.load(fh)["mc"]["replays"], 500)
        finally:
            os.chdir(cwd)

    def test_offline_file_names_with_an_ampersand(self):
        # M&M is a legal file name on Windows and POSIX: <PLAIN SYMBOL>-60d5m.json.gz
        shutil.copy(REL, os.path.join(self.d, "M&M-60d5m.json.gz"))
        self.assertEqual(tm.read_offline(self.d, "m&m"), TEXT)
        with self.assertRaises(tm.TMError) as cm:
            tm.read_offline(self.d, "TCS")
        self.assertIn("TCS-60d5m.json", str(cm.exception))

    def test_errors_are_one_plain_line(self):
        for argv in (["run", "--plan", os.path.join(self.d, "nope.json")],
                     ["run", "--plan", self.plan, "--offline", os.path.join(self.d, "missing.json.gz")],
                     ["run", "--plan", self.plan, "--offline", REL, "--until", "9:00"],
                     ["chart", "--from", os.path.join(self.d, "nope.json")]):
            code, text, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertTrue(err.startswith("timemachine.py: "), err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(run([])[0], 1)

    def test_chart(self):
        run(["run", "--plan", self.plan, "--offline", REL, "--now", NOW, "--write", self.out])
        code, text, _ = run(["chart", "--from", self.out])
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertIn("2,000 replay paths from the 9:35 AM entry (seed 19)", lines[0])
        self.assertTrue(any(l.startswith("  1426.20 |") and l.endswith("  target") for l in lines))
        self.assertTrue(any(l.startswith("  1406.40 |") and l.endswith("  stop") for l in lines))
        self.assertTrue(any(l.startswith("  1416.30 |*") and l.endswith("  entry") for l in lines))
        self.assertIn("9:35", text)
        self.assertIn("3:00", text)
        self.assertEqual(lines[-1], "Leans toward the target: target first 49.0 % vs stop first 38.9 %.")
        code, again, _ = run(["chart", "--plan", self.plan, "--offline", REL, "--now", NOW])
        self.assertEqual(again, text)

    def test_rupee_sign_survives_a_legacy_windows_console(self):
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        env.pop("PYTHONUTF8", None)
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "trader-timemachine", "timemachine.py"), "run",
                               "--plan", self.plan, "--offline", REL, "--now", NOW, "--replays", "200"],
                              capture_output=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertIn((RS + "1,416.30").encode("utf-8"), proc.stdout)


if __name__ == "__main__":
    unittest.main()
