"""trader-entry tests (NSE, IST, rupees). Fixtures in tests/fixtures/trader-entry/ are SYNTHETIC
(seeded random bars, NOT real market data), written by tests/fixtures/make_entry_fixtures.py:

RELIANCE-2026-10-19-5m.json  RELIANCE.NS, Monday 2026-10-19, one full session of 75 five-minute bars
                             (9:15 to 15:30 IST), Yahoo chart JSON v8 shape. Opening range 1399.50 to
                             1413.40; the 9:30 bar closes at 1416.30 (9:35 AM), above the range high.
                             Later bars never close above the high again, so the tests derive variants
                             (a late breakout, no breakout, a live session cut off at the clock).
DEMO-1m-synthetic.json       one-minute bars for an invented symbol: a wick above the range high, a
                             close AT the high, a null bar, a live tick.
"""

import copy
import datetime
import gzip
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from _load import load, FIX, ROOT

entry = load("trader-entry", "entry")

EDIR = os.path.join(FIX, "trader-entry")
RELIANCE = os.path.join(EDIR, "RELIANCE-2026-10-19-5m.json")
DEMO = os.path.join(EDIR, "DEMO-1m-synthetic.json")
OPEN_TS = 1792381500       # 2026-10-19 09:15 IST (03:45 UTC)
DT = datetime.datetime
RS = "₹"


def ist(text):
    return entry.parse_now(text)


def doc(path=RELIANCE):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def chart(d=None, sym="RELIANCE"):
    return entry.parse_chart(json.dumps(d if d is not None else doc()), sym)


def at(text, d=None, **kw):
    return entry.evaluate(chart(d), ist(text), **kw)


def set_bar(d, slot, o, h, l, c, v):
    d = copy.deepcopy(d)
    q = d["chart"]["result"][0]["indicators"]["quote"][0]
    for key, val in zip(("open", "high", "low", "close", "volume"), (o, h, l, c, v)):
        q[key][slot] = val
    return d


def no_breakout(d=None):
    """Every bar from the 9:30 bar on stays under the 1413.40 range high."""
    d = copy.deepcopy(d if d is not None else doc())
    q = d["chart"]["result"][0]["indicators"]["quote"][0]
    for i in range(3, len(q["close"])):
        q["close"][i] = min(q["close"][i], 1413.00)
        q["high"][i] = min(q["high"][i], 1413.30)
        q["open"][i] = min(q["open"][i], 1413.00)
    return d


def late_breakout(d=None):
    """The canonical day plus a second, high-volume close above the range: the 9:55 bar (bar 8),
    1420.00 on 300000 shares (1.06x the range's average)."""
    return set_bar(d if d is not None else doc(), 8, 1408.00, 1421.50, 1407.50, 1420.00, 300000)


def break8():
    """Only the 9:55 bar breaks out (the 9:30 bar is pulled back under the high): trigger at 10:00 AM."""
    return late_breakout(no_breakout())


def live(d, closed, tick_after=285):
    """The session as Yahoo serves it mid-day: `closed` finished bars, the in-progress bar after them,
    and a live tick `tick_after` seconds into that bar (off the 5-minute grid)."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    keep = closed + 1
    r["timestamp"] = r["timestamp"][:keep]
    q = r["indicators"]["quote"][0]
    for k in q:
        q[k] = q[k][:keep]
    tick_ts = r["timestamp"][-1] + tick_after
    r["timestamp"].append(tick_ts)
    last = q["close"][-1]
    for k in ("open", "high", "low", "close"):
        q[k].append(last)
    q["volume"].append(0)
    r["meta"]["regularMarketTime"] = tick_ts
    return d


def shift_to(d, day):
    """The same session moved to another date (IST has no daylight saving: 9:15 is 03:45 UTC all year)."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    new_open = int((DT.combine(day, datetime.time(3, 45)) - DT(1970, 1, 1)).total_seconds())
    delta = new_open - OPEN_TS
    r["timestamp"] = [t + delta for t in r["timestamp"]]
    r["meta"]["regularMarketTime"] += delta
    return d


def rename(d, symbol):
    d = copy.deepcopy(d)
    d["chart"]["result"][0]["meta"]["symbol"] = symbol
    return d


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


class Basics(unittest.TestCase):
    def test_rupee_grouping(self):
        self.assertEqual(entry.inr(1416.30), RS + "1,416.30")
        self.assertEqual(entry.inr(123456.789), RS + "1,23,456.79")
        self.assertEqual(entry.inr(1234567.5), RS + "12,34,567.50")
        self.assertEqual(entry.inr(5000, None), RS + "5,000")
        self.assertEqual(entry.inr(5000.5, None), RS + "5,000.50")
        self.assertEqual(entry.inr(0.05), RS + "0.05")
        self.assertEqual(entry.inr(-1234.5), "-" + RS + "1,234.50")
        self.assertEqual(entry.inr(99999.999), RS + "1,00,000.00")

    def test_ist_is_a_fixed_offset(self):
        self.assertEqual(entry.to_ist(DT(2026, 10, 19, 4, 5)), DT(2026, 10, 19, 9, 35))
        self.assertEqual(entry.ist_to_utc(DT(2026, 10, 19, 9, 35)), DT(2026, 10, 19, 4, 5))
        for day in ("2026-01-05", "2026-04-06", "2026-07-06", "2026-10-26"):      # no daylight saving, any month
            self.assertEqual(ist(day + " 09:15"), DT.fromisoformat(day + "T03:45:00"))

    def test_symbols(self):
        self.assertEqual(entry.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(entry.yahoo_symbol("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(entry.yahoo_symbol("reliance.bo"), "RELIANCE.BO")
        self.assertEqual(entry.yahoo_symbol("^nsei"), "^NSEI")
        self.assertEqual(entry.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")        # hyphens stay, dots are not turned into hyphens
        self.assertEqual(entry.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(entry.plain_symbol("RELIANCE.NS"), "RELIANCE")
        self.assertEqual(entry.plain_symbol("m&m.ns"), "M&M")
        self.assertEqual(entry.plain_symbol("^NSEI"), "^NSEI")

    def test_utf8_helper_makes_the_rupee_sign_printable(self):
        raw = io.BytesIO()
        wrapper = io.TextIOWrapper(raw, encoding="cp1252")
        with redirect_stdout(wrapper):
            entry._utf8()
            print(RS + "1,416.30")
            sys.stdout.flush()
        self.assertEqual(raw.getvalue().decode("utf-8").strip(), RS + "1,416.30")


class TheCanonicalDay(unittest.TestCase):
    def test_triggers_at_935_on_the_930_bar(self):
        r = at("2026-10-19 09:35")
        self.assertEqual(r["status"], "triggered")
        self.assertEqual(r["opening_range"]["high"], 1413.40)
        self.assertEqual(r["opening_range"]["low"], 1399.50)
        self.assertEqual(r["opening_range"]["bars"], 3)
        self.assertEqual(r["opening_range"]["avg_volume"], 283333)
        t = r["trigger"]
        self.assertEqual(t["time"], "9:35 AM")
        self.assertEqual(t["close_time"], "09:35")
        self.assertEqual(t["bar_start"], "09:30")
        self.assertEqual(t["bar_index"], 3)
        self.assertEqual(t["price"], 1416.30)
        self.assertEqual(t["close"], 1416.30)
        self.assertEqual(t["volume"], 230000)
        self.assertEqual(t["volume_x"], 0.81)
        self.assertEqual(t["pct_over"], 0.21)
        self.assertEqual(r["level"], 1413.40)
        self.assertEqual(r["prev_close"], 1376.70)
        self.assertEqual((r["opens"], r["closes"]), ("09:15", "15:15"))
        self.assertEqual((r["no_entries_before"], r["no_entries_after"]), ("09:30", "14:30"))
        self.assertEqual(r["data_time"], "2026-10-19 15:30:00")
        text = "\n".join(entry.describe(r))
        self.assertIn("IST", text)
        self.assertIn("TRIGGERED 9:35 AM at " + RS + "1,416.30: the 9:30 bar (bar 3) closed above the range high "
                      + RS + "1,413.40 (+0.21%)", text)
        self.assertIn("Opening range 9:15-9:30: high " + RS + "1,413.40, low " + RS + "1,399.50 (3 of 3 bars).", text)
        self.assertNotIn("$", text)

    def test_at_934_the_930_bar_is_still_forming(self):
        r = at("2026-10-19 09:34")
        self.assertEqual(r["status"], "waiting")
        self.assertIsNone(r["trigger"])
        self.assertEqual(r["last"]["bar_start"], "09:25")
        self.assertEqual(r["last"]["close"], 1408.60)
        self.assertEqual(r["forming"]["bar_start"], "09:30")
        self.assertEqual(r["forming"]["closes"], "9:35 AM")
        self.assertIn(RS + "4.80 under the range high " + RS + "1,413.40", r["reason"])
        self.assertEqual(at("2026-10-19 09:34:59")["status"], "waiting")
        text = "\n".join(entry.describe(r))
        self.assertIn("The 9:30 bar is still forming (" + RS + "1,416.30 so far, closes 9:35 AM)", text)

    def test_before_the_open_and_while_the_range_forms(self):
        r = at("2026-10-19 09:10")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertIn("the NSE opens at 9:15 and the 15-minute range is set at 9:30", r["reason"])
        r = at("2026-10-19 09:29")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        self.assertEqual(r["opening_range"]["bars"], 2)
        self.assertFalse(r["opening_range"]["complete"])
        r = at("2026-10-19 09:31")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "open"))
        self.assertEqual(r["opening_range"]["bars"], 3)
        self.assertTrue(r["opening_range"]["complete"])

    def test_the_live_tick_and_the_in_progress_bar_are_dropped(self):
        d = live(doc(), 15)                                         # 15 closed bars, the 10:30 bar in progress
        c = chart(d)
        self.assertEqual(c["bars"][-1]["t"], OPEN_TS + 15 * 300 + 285)       # 10:34:45 is in the file
        r = entry.evaluate(c, entry.utc_of(c["data_time"]))        # read as of the file's own time
        self.assertEqual(r["now"], "2026-10-19 10:34:45")
        self.assertEqual(r["_bars"][-1]["slot"], 14)               # the 10:25 bar
        self.assertEqual(r["forming"]["bar_start"], "10:30")
        self.assertEqual(r["bars_closed"], 15)
        # 10:35: closed by the clock, but Yahoo's last trade (10:34:45) is before its close
        r = at("2026-10-19 10:35", d)
        self.assertEqual(r["_bars"][-1]["slot"], 14)
        self.assertIn("Yahoo's last trade is 10:34:45", r["lag_note"])
        # a minute later with no newer trade it counts; the 10:34:45 tick never becomes a bar
        r = at("2026-10-19 10:36", d)
        self.assertEqual(r["_bars"][-1]["slot"], 15)
        self.assertEqual(r["bars_closed"], 16)
        self.assertTrue(all(b["t"] % 300 == 0 for b in r["_bars"]))

    def test_after_the_close_it_stays_triggered_and_says_it_is_late(self):
        # 15:20 is after the pack's day (continuous trading of F&O stocks ended at 15:15) but before NSE's 15:30
        for now in ("2026-10-19 15:20", "2026-10-19 15:35"):
            r = at(now)
            self.assertEqual((r["status"], r["phase"]), ("triggered", "after-close"), now)
            self.assertEqual(r["trigger"]["time"], "9:35 AM")
            text = "\n".join(entry.describe(r))
            self.assertIn("minutes ago", text)
            self.assertIn("the last close is " + RS + "1,393.10", text)     # the 15:10 bar, the last one it reads
            self.assertNotIn("This is the moment", text)


class Rules(unittest.TestCase):
    def test_volume_confirmation_moves_the_trigger(self):
        d = late_breakout()
        r = at("2026-10-19 10:05", d, min_volume_x=1)
        self.assertEqual(r["opening_range"]["avg_volume"], 283333)
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("10:00 AM", "09:55", 8, 1420.00))
        self.assertEqual(t["volume_x"], 1.06)
        self.assertEqual(t["pct_over"], 0.47)
        self.assertEqual(at("2026-10-19 10:05", d)["trigger"]["time"], "9:35 AM")           # no filter: the first close
        r = at("2026-10-19 09:36", min_volume_x=1)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("0.81x the range's volume, under your 1.00x", r["reason"])
        self.assertEqual(at("2026-10-19 09:36", min_volume_x=0.8)["trigger"]["time"], "9:35 AM")
        text = "\n".join(entry.describe(at("2026-10-19 10:05", d, min_volume_x=1)))
        self.assertIn("1.06x the range's volume", text)

    def test_level_setup(self):
        # a level under the first closes: bar 1 closes above it at 9:25, before no_entries_before, so it is skipped
        r = at("2026-10-19 10:30", setup="level", level=1410)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"], r["trigger"]["price"]),
                         ("9:35 AM", 3, 1416.30))
        # a lower level: the first eligible close is the 9:25 bar's, at 9:30
        r = at("2026-10-19 10:30", setup="level", level=1400)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"], r["trigger"]["price"]),
                         ("9:30 AM", 2, 1408.60))
        self.assertIn("close above " + RS + "1,400.00", "\n".join(entry.describe(r)))
        # a close exactly AT the level is not above it
        r = at("2026-10-19 10:30", setup="level", level=1416.30)
        self.assertEqual(r["status"], "waiting")
        with self.assertRaises(entry.EntryError):
            at("2026-10-19 10:30", setup="level")

    def test_no_breakout_waits_then_expires_at_the_cutoff(self):
        d = no_breakout()
        r = at("2026-10-19 10:49", d)
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(at("2026-10-19 14:29", d)["status"], "waiting")
        r = at("2026-10-19 14:30", d)
        self.assertEqual(r["status"], "expired")
        self.assertIn("by 14:30", r["reason"])
        self.assertIn("no close above the range high " + RS + "1,413.40", r["reason"])
        self.assertEqual(r["phase"], "after-cutoff")
        self.assertEqual(at("2026-10-19 16:30", d)["phase"], "after-close")

    def test_the_bar_closing_at_the_cutoff_still_counts_when_yahoo_lags(self):
        d = copy.deepcopy(no_breakout())
        r0 = d["chart"]["result"][0]
        r0["timestamp"] = r0["timestamp"][:15]                       # live: up to the 10:25 bar (bar 14)
        for k in r0["indicators"]["quote"][0]:
            r0["indicators"]["quote"][0][k] = r0["indicators"]["quote"][0][k][:15]
        r0["meta"]["regularMarketTime"] = OPEN_TS + 14 * 300 + 280    # last trade 10:29:40, before that bar's close
        cfg = {"no_entries_before": "09:30", "no_entries_after": "10:30"}
        r = at("2026-10-19 10:30:10", d, cfg=cfg)                    # the 10:25 bar closed by the clock only
        self.assertEqual(r["status"], "waiting")
        self.assertIn("Yahoo's last trade is 10:29:40", r["lag_note"])
        self.assertEqual(at("2026-10-19 10:31", d, cfg=cfg)["status"], "expired")

    def test_config_cutoff_and_floor(self):
        cfg = {"no_entries_before": "09:30", "no_entries_after": "09:55"}
        r = at("2026-10-19 10:00", break8(), cfg=cfg)
        self.assertEqual(r["status"], "expired")                     # the breakout bar closes at 10:00, after the cutoff
        cfg = {"no_entries_before": "09:40", "no_entries_after": "14:30"}
        r = at("2026-10-19 09:36", cfg=cfg)                          # a close above, but before no_entries_before
        self.assertEqual(r["status"], "waiting")
        self.assertIn("but no entries before 9:40 (config)", r["reason"])
        r = at("2026-10-19 10:05", late_breakout(), cfg=cfg)
        self.assertEqual(r["trigger"]["time"], "10:00 AM")           # the first bar to close at or after 9:40

    def test_bad_inputs(self):
        with self.assertRaises(entry.EntryError):
            at("2026-10-19 10:15", or_minutes=7)
        with self.assertRaises(entry.EntryError):
            at("2026-10-19 10:15", interval="15m")
        with self.assertRaises(entry.EntryError):
            entry.parse_now("tomorrow")
        with self.assertRaises(entry.EntryError):
            entry.parse_hhmm("25:00", "x")
        self.assertEqual(entry.parse_now("2026-10-19 10:15"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(entry.parse_now("2026-10-19T04:45:00Z"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(entry.parse_now("2026-10-19 10:15 IST"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(entry.parse_now("2026-10-19T10:15:00+05:30"), DT(2026, 10, 19, 4, 45))

    def test_or_minutes_30(self):
        r = at("2026-10-19 10:49", late_breakout(), or_minutes=30)
        self.assertEqual(r["opening_range"]["bars"], 6)
        self.assertEqual(r["opening_range"]["high"], 1418.20)       # the 9:30 bar's wick is now inside the range
        self.assertEqual(r["trigger"]["time"], "10:00 AM")
        self.assertEqual(r["trigger"]["pct_over"], 0.13)
        self.assertEqual(at("2026-10-19 10:49", or_minutes=30)["status"], "waiting")


class OneMinute(unittest.TestCase):
    def c(self):
        return entry.parse_chart(json.dumps(doc(DEMO)), "DEMO")

    def test_wick_and_close_at_the_high_do_not_trigger(self):
        c = self.c()
        self.assertEqual(c["symbol"], "DEMO")                       # Yahoo's DEMO.NS, plain in our output
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m")
        self.assertEqual(r["now"], "2026-10-19 09:51:30")
        self.assertEqual(r["opening_range"], {"high": 100.5, "low": 99.0, "minutes": 15, "bars": 15, "of": 15,
                                              "complete": True, "avg_volume": 10667})
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("9:44 AM", "09:43", 28, 100.60))
        r = entry.evaluate(c, ist("2026-10-19 09:43:59"), interval="1m")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["forming"]["bar_start"], "09:43")
        self.assertEqual(r["last"]["close"], 100.5)             # the 9:42 bar closed AT the high (and an earlier wick hit 100.70)

    def test_null_bar_tick_and_volume(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m", min_volume_x=2)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:47 AM", 31))
        slots = [b["slot"] for b in r["_bars"]]
        self.assertNotIn(19, slots)                               # 9:34 came as nulls
        self.assertEqual(slots[-1], 35)                           # 9:51 still forming, the 9:51:30 tick dropped
        self.assertEqual(r["forming"]["bar_start"], "09:51")

    def test_the_file_and_the_interval_must_agree(self):
        code, _, err = run(["check", "DEMO", "--offline", DEMO])
        self.assertEqual(code, 1)
        self.assertIn("1m bars, not 5m", err)


class Calendar(unittest.TestCase):
    def test_same_wall_clock_all_year(self):
        for day in (datetime.date(2026, 12, 7), datetime.date(2026, 11, 2), datetime.date(2026, 10, 30),
                    datetime.date(2026, 6, 29), datetime.date(2026, 3, 9)):
            d = shift_to(doc(), day)
            r = entry.evaluate(chart(d), ist("%s 09:35" % day))
            self.assertEqual(r["status"], "triggered", day)
            self.assertEqual(r["trigger"]["time"], "9:35 AM", day)
            self.assertEqual(r["trigger"]["bar_index"], 3, day)
            self.assertEqual(entry.evaluate(chart(d), ist("%s 09:34" % day))["status"], "waiting", day)
        # Dec 7 at 09:35 IST is 04:05 UTC
        self.assertEqual(ist("2026-12-07 09:35"), DT(2026, 12, 7, 4, 5))

    def test_early_close_machinery_moves_the_cutoff_by_the_amount_the_close_moved(self):
        # NSE_EARLY_CLOSE is empty today (no early closes on normal days); a test injects one.
        self.assertEqual(entry.NSE_EARLY_CLOSE, {})
        day = datetime.date(2026, 11, 13)
        with mock.patch.object(entry, "NSE_EARLY_CLOSE", {"2026-11-13": "test short session"}):
            d = shift_to(no_breakout(), day)
            r = entry.evaluate(chart(d), ist("2026-11-13 11:44"))
            self.assertEqual(r["status"], "waiting")
            self.assertEqual((r["closes"], r["no_entries_after"]), ("12:30", "11:45"))     # close moved 2h45m: 14:30 -> 11:45
            r = entry.evaluate(chart(d), ist("2026-11-13 11:45"))
            self.assertEqual(r["status"], "expired")
            self.assertIn("early close at 12:30 PM today", r["reason"])
            r = entry.evaluate(chart(shift_to(doc(), day)), ist("2026-11-13 09:35"))
            self.assertEqual(r["trigger"]["time"], "9:35 AM")
            self.assertIn("Early close today at 12:30 PM (test short session): no new entries after 11:45.",
                          "\n".join(entry.describe(r)))
            # a cutoff already before the early close stays put
            cfg = {"no_entries_before": "09:30", "no_entries_after": "11:00"}
            self.assertEqual(entry.evaluate(chart(d), ist("2026-11-13 10:00"), cfg=cfg)["no_entries_after"], "11:00")
            # the amount comes from CLOSE, not a hard-coded 2h45m: a 13:00 close moved 2h15m
            with mock.patch.object(entry, "EARLY", datetime.time(13, 0)):
                r = entry.evaluate(chart(d), ist("2026-11-13 11:00"))
                self.assertEqual((r["closes"], r["no_entries_after"]), ("13:00", "12:15"))
        # without the injected entry the same day is a normal session
        r = entry.evaluate(chart(shift_to(no_breakout(), day)), ist("2026-11-13 11:50"))
        self.assertEqual((r["status"], r["closes"], r["no_entries_after"]), ("waiting", "15:15", "14:30"))

    def test_weekend_holiday_and_empty_data(self):
        r = at("2026-10-17 10:30")                                # a Saturday
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertIn("weekend", r["reason"])
        self.assertEqual(r["next_open"], "2026-10-19 09:15")
        r = at("2026-10-20 10:30")                                # Dussehra
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertIn("NSE holiday: Dussehra", r["reason"])
        self.assertEqual(r["next_open"], "2026-10-21 09:15")
        self.assertIn("Next open: 2026-10-21 09:15 IST.", "\n".join(entry.describe(r)))
        for day, name in (("2026-01-26", "Republic Day"), ("2026-10-02", "Mahatma Gandhi Jayanti"),
                          ("2026-11-10", "Diwali-Balipratipada"), ("2026-12-25", "Christmas")):
            self.assertIn(name, at(day + " 10:30")["reason"], day)
        self.assertIsNone(entry.session(datetime.date(2026, 10, 20)))
        self.assertEqual(entry.session(datetime.date(2026, 10, 19)), (datetime.time(9, 15), datetime.time(15, 15)))
        # the weekend holidays carry their names too (Mahashivratri fell on a Sunday, Diwali Laxmi Pujan on a Sunday)
        self.assertIn("Mahashivratri", at("2026-02-15 10:30")["reason"])
        self.assertIn("Muhurat", at("2026-11-08 14:30")["reason"])
        empty = copy.deepcopy(doc())
        res = empty["chart"]["result"][0]
        del res["timestamp"]
        res["indicators"] = {"quote": [{}]}
        r = at("2026-10-19 10:15", empty)
        self.assertEqual((r["status"], r["phase"]), ("no-data", "open"))
        r = at("2026-10-21 10:15")                                # Yahoo sent Monday's bars only
        self.assertEqual((r["status"], r["phase"]), ("no-data", "open"))

    def test_calendar_not_covered_note(self):
        r = at("2027-01-04 10:30")                                # a 2027 Monday: the holiday list covers 2026 only
        self.assertFalse(r["calendar_known"])
        text = "\n".join(entry.describe(r))
        self.assertIn("Holiday list covers 2026 only: check nseindia.com (Market Timings & Holidays) for this date.", text)
        self.assertFalse(at("2028-03-01 10:30")["calendar_known"])
        r = at("2026-10-19 10:30")
        self.assertTrue(r["calendar_known"])
        self.assertNotIn("Holiday list covers", "\n".join(entry.describe(r)))
        self.assertEqual(entry.CALENDAR_YEARS, (2026,))

    def test_yahoo_errors(self):
        bad = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                              "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(entry.EntryError) as cm:
            entry.parse_chart(bad, "ZZZZ")
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(entry.EntryError):
            entry.parse_chart("<html>", "RELIANCE")


class Network(unittest.TestCase):
    """Yahoo is never called: _open_url and _sleep are swapped for fakes."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._open, self._sleep, self._now = entry._open_url, entry._sleep, entry.utc_now
        entry._sleep = lambda s: self.sleeps.append(s)
        entry.utc_now = lambda: DT(2026, 10, 19, 5, 0)              # 10:30 IST on the session day
        with open(RELIANCE, encoding="utf-8") as fh:
            self.body = fh.read()

    def tearDown(self):
        entry._open_url, entry._sleep, entry.utc_now = self._open, self._sleep, self._now

    def answers(self, *codes, body=None):
        seq = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url)
            code = seq.pop(0)
            if code == 200:
                return body or self.body
            raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b'{"chart":{"result":null,'
                                                                           b'"error":{"description":"No data found"}}}'))
        entry._open_url = fake

    def test_url_and_429_retry_once_after_a_pause(self):
        self.answers(429, 200)
        c = entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertEqual(len(c["bars"]), 75)
        self.assertEqual(c["symbol"], "RELIANCE")                   # plain, though Yahoo says RELIANCE.NS
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m"] * 2)
        self.assertEqual(self.sleeps, [entry.BACKOFF])

    def test_429_twice_falls_back_to_query1(self):
        self.answers(429, 429, 200)
        entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertEqual([u.split("/")[2] for u in self.calls],
                         ["query2.finance.yahoo.com", "query2.finance.yahoo.com", "query1.finance.yahoo.com"])

    def test_5xx_falls_back_without_waiting(self):
        self.answers(503, 200)
        entry.get_chart("RELIANCE", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertEqual(self.sleeps, [])
        self.assertIn("query1", self.calls[1])

    def test_still_busy_is_one_plain_error(self):
        self.answers(429, 429, 429, 429)
        code, out, err = run(["check", "RELIANCE", "--now", "2026-10-19 10:15"])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 429", err)
        self.assertNotIn("Traceback", err)

    def test_unknown_ticker_404(self):
        self.answers(404)
        with self.assertRaises(entry.EntryError) as cm:
            entry.get_chart("ZZZZ", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertIn("No data found", str(cm.exception))

    def test_ampersand_symbols_are_url_quoted(self):
        body = json.dumps(rename(doc(), "M&M.NS"))
        self.answers(200, 200, body=body)
        c = entry.get_chart("m&m", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertEqual(c["symbol"], "M&M")
        self.assertEqual(self.calls[0], "https://query2.finance.yahoo.com/v8/finance/chart/M%26M.NS?range=1d&interval=5m")
        self.assertNotIn("&NS", self.calls[0])
        c = entry.get_chart("M&M.NS", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertEqual(c["symbol"], "M&M")
        self.assertEqual(self.calls[1], self.calls[0])
        self.assertEqual(entry.chart_path("^nsei", "1d", "5m"), "/v8/finance/chart/%5ENSEI?range=1d&interval=5m")
        self.assertEqual(entry.chart_path("bajaj-auto", "1d", "5m"), "/v8/finance/chart/BAJAJ-AUTO.NS?range=1d&interval=5m")
        self.assertEqual(entry.chart_path("J&KBANK", "5d", "5m"), "/v8/finance/chart/J%26KBANK.NS?range=5d&interval=5m")

    def test_the_chart_must_be_for_the_symbol_asked(self):
        self.answers(200)
        with self.assertRaises(entry.EntryError) as cm:
            entry.get_chart("TCS", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertIn("holds RELIANCE, not TCS", str(cm.exception))

    def test_ranges_for_past_sessions(self):
        today = datetime.date(2026, 10, 21)
        self.assertEqual(entry.chart_range(today, today, "5m"), "1d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 19), today, "5m"), "5d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 1), today, "5m"), "1mo")
        self.assertEqual(entry.chart_range(datetime.date(2026, 8, 31), today, "5m"), "60d")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 10, 1), today, "1m")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 7, 1), today, "5m")
        self.assertIn("/chart/RELIANCE.NS?", entry.chart_path("reliance", "1d", "5m"))


class Transport(unittest.TestCase):
    """The real _open_url and yahoo_get, with curl_cffi swapped for a fake (no network). Yahoo blocks plain Python
    clients (HTTP 429), so every request carries Chrome's TLS fingerprint (curl_cffi impersonate) and the
    headers a browser sends."""

    class Resp(object):
        def __init__(self, status=200, body=b""):
            self.status_code, self.content = status, body

    class FakeCffi(object):
        def __init__(self, resp=None, exc=None):
            self.resp, self.exc, self.calls = resp, exc, []

        def get(self, url, **kw):
            self.calls.append((url, kw))
            if self.exc:
                raise self.exc
            return self.resp

    URL = "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m"

    def test_a_chrome_request_with_yahoo_headers(self):
        fake = self.FakeCffi(self.Resp(200, "caf\u00e9 \u20b9".encode("utf-8")))
        with mock.patch.object(entry, "_cffi", fake):
            self.assertEqual(entry._open_url(self.URL, timeout=9), "caf\u00e9 \u20b9")
        url, kw = fake.calls[0]
        self.assertEqual(url, self.URL)
        self.assertEqual((kw["impersonate"], kw["timeout"]), ("chrome", 9))
        self.assertEqual(kw["headers"]["Referer"], "https://finance.yahoo.com/")
        self.assertIn("application/json", kw["headers"]["Accept"])
        self.assertIn("Accept-Language", kw["headers"])
        self.assertNotIn("User-Agent", kw["headers"])           # curl_cffi sends Chrome's own, matching its TLS

    def test_a_4xx_or_5xx_is_an_http_error_with_its_body_and_a_failure_is_a_url_error(self):
        for code in (404, 429, 503):
            with mock.patch.object(entry, "_cffi", self.FakeCffi(self.Resp(code, b'{"chart": 1}'))):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    entry._open_url(self.URL)
            self.assertEqual(cm.exception.code, code)
            self.assertEqual(cm.exception.read(), b'{"chart": 1}')
        with mock.patch.object(entry, "_cffi", self.FakeCffi(exc=RuntimeError("timed out"))):
            with self.assertRaises(urllib.error.URLError):
                entry._open_url(self.URL)

    def test_yahoo_get_rotates_hosts_and_waits_once_on_a_429_through_curl_cffi(self):
        answers = [self.Resp(429), self.Resp(429), self.Resp(200, b'{"ok": true}')]
        hosts, sleeps = [], []

        class Seq(object):
            def get(self, url, **kw):
                hosts.append(url.split("/")[2])
                return answers.pop(0)
        with mock.patch.object(entry, "_cffi", Seq()), mock.patch.object(entry, "_sleep", sleeps.append):
            self.assertEqual(entry.yahoo_get("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m"), '{"ok": true}')
        self.assertEqual(hosts, ["query2.finance.yahoo.com", "query2.finance.yahoo.com", "query1.finance.yahoo.com"])
        self.assertEqual(sleeps, [entry.BACKOFF])

    def test_an_unknown_ticker_404_returns_yahoos_json_error(self):
        body = b'{"chart":{"result":null,"error":{"code":"Not Found","description":"No data found"}}}'
        with mock.patch.object(entry, "_cffi", self.FakeCffi(self.Resp(404, body))):
            self.assertIn("No data found", entry.yahoo_get("/v8/finance/chart/ZZZZ.NS?range=1d&interval=5m"))

    def test_without_curl_cffi_urllib_sends_the_same_headers_and_a_chrome_user_agent(self):
        seen = []

        def fake(req, timeout=0):
            seen.append((req, timeout))
            return io.BytesIO(b"ok")
        with mock.patch.object(entry, "_cffi", None), mock.patch.object(entry.urllib.request, "urlopen", fake):
            self.assertEqual(entry._open_url(self.URL, timeout=7), "ok")
        req, timeout = seen[0]
        self.assertEqual(timeout, 7)
        self.assertEqual(req.get_header("User-agent"), entry.BROWSER_UA)
        self.assertIn("Chrome/", entry.BROWSER_UA)
        self.assertNotIn("compatible", entry.BROWSER_UA)         # not a bot-looking User-Agent
        self.assertEqual(req.get_header("Referer"), "https://finance.yahoo.com/")


REAL = os.path.join(EDIR, "RELIANCE-1d5m-real-2026-11-03.json")


class RealSession(unittest.TestCase):
    """Yahoo's REAL 5-minute chart of RELIANCE for Tue 2026-11-03, saved after the close (tests/fixtures/
    capture_real_fixtures.py). Since the Closing Auction Session began (3 Aug 2026) a stock with F&O contracts
    trades continuously only until 15:15: Yahoo shows a one-trade stub bar at 15:15, a null bar at 15:20 and the
    auction at 15:25 (1307.90 on 41 lakh shares)."""

    @classmethod
    def setUpClass(cls):
        with open(REAL, encoding="utf-8") as fh:
            cls.text = fh.read()
        cls.raw = json.loads(cls.text)["chart"]["result"][0]
        cls.chart = entry.parse_chart(cls.text, "RELIANCE")

    def test_the_answer_has_the_auction_shape(self):
        stamps = [entry.to_ist(entry.utc_of(t)).strftime("%H:%M") for t in self.raw["timestamp"]]
        self.assertEqual((len(stamps), stamps[0], stamps[-4:]), (75, "09:15", ["15:10", "15:15", "15:20", "15:25"]))
        close = self.raw["indicators"]["quote"][0]["close"]
        self.assertEqual([None if c is None else round(c, 2) for c in close[-3:]], [1300.8, None, 1307.9])
        self.assertEqual(self.raw["indicators"]["quote"][0]["volume"][-3], 7161)       # the 15:15 stub: one trade
        self.assertEqual(self.raw["meta"]["regularMarketPrice"], 1307.9)
        self.assertEqual(len(self.chart["bars"]), 74)                                   # the null bar is dropped
        self.assertEqual(self.chart["symbol"], "RELIANCE")

    def test_the_range_and_a_wait_at_9_35(self):
        res = entry.evaluate(self.chart, ist("2026-11-03 09:35"))
        self.assertEqual((res["status"], res["phase"]), ("waiting", "open"))
        self.assertEqual((res["opening_range"]["high"], res["opening_range"]["low"]), (1306.60, 1284.30))
        self.assertEqual(res["reason"], "last close %s1,291.30 (9:30 bar), %s15.30 under the range high %s1,306.60 "
                                        "(-1.17%%)" % (RS, RS, RS))

    def test_no_close_above_the_range_by_the_cutoff_is_expired(self):
        res = entry.evaluate(self.chart, ist("2026-11-03 14:31"))
        self.assertEqual((res["status"], res["phase"], res["trigger"]), ("expired", "after-cutoff", None))
        self.assertEqual(res["reason"], "no close above the range high %s1,306.60 by 14:30, the last time for a "
                                        "new entry" % RS)

    def test_the_auction_print_is_not_a_breakout(self):
        """The 15:25 auction closed at 1307.90, above the range high 1306.60: a tool that read it would report a
        breakout at 3:30 PM. The pack reads 9:15 to 15:10 only, so even with the cutoff pushed to 15:30 nothing
        triggers."""
        pack = [b for b in self.chart["bars"] if entry.to_ist(entry.utc_of(b["t"])).time() < datetime.time(15, 15)]
        self.assertEqual(len(pack), 72)                              # 9:15 to 15:10, the bars the tools read
        self.assertLessEqual(max(b["c"] for b in pack[3:]), 1306.60)     # none from 9:30 to 15:10 closed above it
        self.assertGreater(self.chart["bars"][-1]["c"], 1306.60)     # the auction print did
        late = {"no_entries_before": "09:30", "no_entries_after": "15:30"}
        res = entry.evaluate(self.chart, ist("2026-11-03 15:40"), cfg=late)
        self.assertEqual((res["status"], res["trigger"], res["bars_closed"]), ("expired", None, 72))
        self.assertIn("by 15:30", res["reason"])

    def test_the_next_morning_it_is_a_new_day(self):
        res = entry.evaluate(self.chart, ist("2026-11-04 00:57"))        # Wed 4 Nov before the open: Tuesday's bars
        self.assertEqual((res["status"], res["phase"]), ("waiting", "pre-open"))
        self.assertIsNone(res["trigger"])


def live_provider(d, lag_until=None, calls=None):
    """Bars the way Yahoo serves them at `now`: closed bars final, the in-progress bar
    showing a spike to 1415.00 (above the range high, then it closes under), plus a live tick.
    lag_until: before this UTC time Yahoo's last trade lags 30 seconds behind."""
    base = chart(d)

    def provider(now):
        if calls is not None:
            calls.append(now)
        nts = entry.ts_of(now)
        lagging = lag_until is not None and now < lag_until
        data_ts = nts - 30 if lagging else nts
        bars = []
        for b in base["bars"]:
            if b["t"] % 300 or b["t"] > data_ts:
                continue
            b = dict(b)
            if b["t"] + 300 > data_ts:
                b["c"], b["h"] = 1415.00, max(b["h"], 1415.00)
            bars.append(b)
        if data_ts % 300:
            bars.append({"t": data_ts, "o": 1415.0, "h": 1415.0, "l": 1415.0, "c": 1415.0, "v": 0})
        return {"symbol": "RELIANCE", "meta": {}, "bars": bars, "prev_close": base["prev_close"], "data_time": data_ts}
    return provider


class Watch(unittest.TestCase):
    OPTS = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None, "cfg": None}

    def loop(self, provider, start, **kw):
        clock = entry.SimClock(ist(start))
        lines, fired = [], []
        code, res = entry.watch_loop(provider, clock.now, clock.sleep, lines.append, dict(self.OPTS),
                                     on_trigger=lambda r, now: fired.append((r, now)), **kw)
        return code, res, lines, fired, clock

    def test_fires_on_the_first_closed_bar_not_the_spike_in_progress(self):
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(break8(), calls=calls), "2026-10-19 09:52", every=60)
        self.assertEqual(code, 0)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:00 AM")
        self.assertEqual(fired[0][0]["trigger"]["price"], 1420.00)
        # it looked at 9:52 (the 9:50 bar spiking to 1415 in progress) and at 9:55:02 (that bar closed 1407.20)
        self.assertEqual([entry.to_ist(c).strftime("%H:%M:%S") for c in calls], ["09:52:00", "09:55:02", "10:00:02"])
        text = "\n".join(lines)
        self.assertIn(" 9:50 bar  closed " + RS + "1,407.20  " + RS + "6.20 under " + RS + "1,413.40", text)
        self.assertIn(" 9:55 bar  closed " + RS + "1,420.00  TRIGGERED", text)
        self.assertEqual(text.count("TRIGGERED"), 1)
        self.assertNotIn("$", text)

    def test_waits_for_yahoo_to_catch_up(self):
        lag = ist("2026-10-19 10:00:30")
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(break8(), lag_until=lag, calls=calls),
                                                   "2026-10-19 09:57", every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times, ["09:57:00", "10:00:02", "10:00:32"])   # 10:00:02: Yahoo still at 09:59:32
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:00 AM")

    def test_from_before_the_open_prints_the_range_bar_by_bar(self):
        code, res, lines, fired, clock = self.loop(live_provider(break8()), "2026-10-19 09:00", every=30)
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("not open yet", lines[0])
        self.assertIn("IST", lines[0])
        self.assertIn(" 9:25 bar  closed " + RS + "1,408.60  opening range set: high " + RS + "1,413.40, low " + RS + "1,399.50", text)
        self.assertEqual(sum(1 for x in lines if " bar  closed " in x), 9)    # 9:15 ... 9:55, one line each

    def test_a_computer_that_dozes_off_catches_up_when_it_wakes(self):
        class Dozing(entry.SimClock):
            naps = 0

            def sleep(self, seconds):
                entry.SimClock.sleep(self, seconds)
                self.naps += 1
                if self.naps == 3:                      # the laptop sleeps for 17 minutes mid-wait
                    self.t += datetime.timedelta(minutes=17)
        clock = Dozing(ist("2026-10-19 09:31"))
        calls, lines = [], []
        code, res = entry.watch_loop(live_provider(break8(), calls=calls), clock.now, clock.sleep, lines.append,
                                     dict(self.OPTS), every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times[:2], ["09:31:00", "09:49:30"])   # straight after waking, not a long sleep later
        text = "\n".join(lines)
        for start in (" 9:30", " 9:35", " 9:40"):
            self.assertIn(start + " bar  closed", text)          # the bars it slept through, one line each
        self.assertEqual(res["trigger"]["time"], "10:00 AM")

    def test_expires_and_until(self):
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), "2026-10-19 14:15", every=300)
        self.assertEqual(code, 1)
        self.assertEqual(res["status"], "expired")
        self.assertEqual(fired, [])
        self.assertEqual(entry.to_ist(clock.now()).strftime("%H:%M"), "14:30")
        self.assertIn("EXPIRED", lines[-1])
        until = DT(2026, 10, 19, 11, 0)
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), "2026-10-19 10:30", every=300,
                                                   until=until)
        self.assertEqual(code, 1)
        self.assertIn("--until 11:00", lines[-1])

    def test_closed_day_and_repeated_errors(self):
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-10-17 10:00")
        self.assertEqual(code, 1)
        self.assertIn("weekend", lines[0])
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-10-20 10:00")
        self.assertEqual(code, 1)
        self.assertIn("Dussehra", lines[0])

        def broken(now):
            raise entry.EntryError("Yahoo did not answer")
        code, _, lines, _, _ = self.loop(broken, "2026-10-19 10:00", every=30)
        self.assertEqual(code, 1)
        self.assertIn("Stopping after 5 failed reads", lines[-1])
        self.assertIn("IST  could not read bars", lines[0])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_check_write_is_the_contract_shape(self):
        code, text, _ = run(["check", "RELIANCE", "--offline", RELIANCE, "--now", "2026-10-19 09:35", "--write"])
        self.assertEqual(code, 0)
        self.assertIn("TRIGGERED 9:35 AM at " + RS + "1,416.30", text)
        self.assertIn("/trader-exit", text)
        self.assertIn("Nothing was placed.", text)
        with open(os.path.join("trader", "entry.json"), encoding="utf-8") as fh:
            e = json.load(fh)
        want = {"created_by": "trader-entry", "symbol": "RELIANCE", "setup": "orb", "side": "buy", "date": "2026-10-19",
                "status": "triggered", "time": "9:35 AM", "bar_start": "09:30", "bar_index": 3, "price": 1416.30,
                "level": 1413.40, "interval": "5m", "opening_range": {"high": 1413.40, "low": 1399.50, "minutes": 15},
                "source": "yahoo", "checked_at": "2026-10-19T04:05:00Z"}
        for k, v in want.items():
            self.assertEqual(e[k], v, k)

    def test_check_waiting_json_and_exit_codes(self):
        code, text, _ = run(["check", "RELIANCE", "--offline", RELIANCE, "--now", "2026-10-19 09:34", "--json"])
        self.assertEqual(code, 1)
        r = json.loads(text)
        self.assertEqual(r["status"], "waiting")
        self.assertNotIn("_bars", r)
        code, text, _ = run(["check", "RELIANCE", "--offline", RELIANCE, "--now", "2026-10-19 09:34", "--write", "e.json"])
        with open("e.json", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["status"], "waiting")
        self.assertIn("still forming", text)

    def test_offline_gz_folder_and_mismatch(self):
        os.mkdir("charts")
        with open(RELIANCE, "rb") as src, gzip.open(os.path.join("charts", "RELIANCE.json.gz"), "wb") as dst:
            dst.write(src.read())
        code, text, _ = run(["check", "reliance", "--offline", "charts", "--now", "2026-10-19 09:35"])
        self.assertEqual(code, 0)
        code, text, _ = run(["check", "RELIANCE.NS", "--offline", "charts", "--now", "2026-10-19 09:35"])
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("RELIANCE  "))             # plain symbol, not RELIANCE.NS
        code, _, err = run(["check", "TCS", "--offline", RELIANCE])
        self.assertEqual(code, 1)
        self.assertIn("holds RELIANCE, not TCS", err)

    def test_ampersand_symbol_offline_file(self):
        os.mkdir("charts")
        with open(os.path.join("charts", "M&M.json"), "w", encoding="utf-8") as fh:
            json.dump(rename(doc(), "M&M.NS"), fh)
        code, text, _ = run(["check", "m&m", "--offline", "charts", "--now", "2026-10-19 09:35", "--write", "m.json"])
        self.assertEqual(code, 0)
        with open("m.json", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["symbol"], "M&M")

    def test_longs_only_and_flag_errors(self):
        code, _, err = run(["check", "RELIANCE", "--offline", RELIANCE, "--side", "short"])
        self.assertEqual(code, 1)
        self.assertIn("v2 plans longs only", err)
        code, _, err = run(["check", "RELIANCE", "--offline", RELIANCE, "--level", "1300"])
        self.assertIn("--level is for --setup level", err)
        code, _, err = run(["check", "RELIANCE", "--offline", RELIANCE, "--setup", "level"])
        self.assertIn("needs --level", err)

    def test_config_is_read(self):
        os.mkdir("trader")
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump({"no_entries_after": "09:30"}, fh)
        code, text, _ = run(["check", "RELIANCE", "--offline", RELIANCE, "--now", "2026-10-19 09:35"])
        self.assertEqual(code, 1)
        self.assertIn("EXPIRED", text)
        with open(os.path.join("trader", "config.json"), "w") as fh:
            fh.write("{nope")
        code, _, err = run(["check", "RELIANCE", "--offline", RELIANCE])
        self.assertIn("not valid JSON", err)

    def test_watch_replays_the_file_and_writes_the_entry(self):
        code, text, _ = run(["watch", "RELIANCE", "--offline", RELIANCE, "--now", "2026-10-19 09:20", "--every", "30"])
        self.assertEqual(code, 0)
        self.assertIn(" 9:30 bar  closed " + RS + "1,416.30  TRIGGERED", text)
        with open(os.path.join("trader", "entry.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["bar_index"], 3)
        code, text, _ = run(["watch", "RELIANCE", "--offline", RELIANCE, "--now", "2026-10-19 09:20", "--until", "09:25",
                             "--out", "x.json", "--setup", "level", "--level", "1500"])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists("x.json"))
        self.assertIn("--until 9:25", text)

    def test_watchlist(self):
        os.mkdir("charts")
        shutil.copy(RELIANCE, os.path.join("charts", "RELIANCE.json"))
        shutil.copy(DEMO, os.path.join("charts", "DEMO.json"))
        os.mkdir("trader")
        with open(os.path.join("trader", "watchlist.json"), "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [
                {"symbol": "RELIANCE", "setups": ["orb"], "or_high": 1413.40, "or_low": 1399.50},
                {"symbol": "ZZZZ", "setups": ["orb"]}, {"symbol": "DEMO", "setups": ["orb"]}]}, fh)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-10-19 09:35"])
        self.assertEqual(code, 0)
        rows = text.splitlines()
        self.assertEqual(rows[1].split(), ["RELIANCE", "triggered", "9:35", "AM", RS + "1,416.30", RS + "1,413.40",
                                           RS + "1,416.30", "+0.21%"])
        self.assertTrue(rows[2].startswith("ZZZZ") and "error: no offline file" in rows[2], rows[2])
        self.assertIn("DEMO          error: DEMO has 1m bars", rows[3])
        self.assertIn("1 of 3 triggered", text)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-10-19 09:34", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)[0]["result"]["status"], "waiting")
        code, _, err = run(["watchlist", "--watchlist", "missing.json"])
        self.assertIn("run /trader-scanner first", err)


class SourceHasNoUsLeftovers(unittest.TestCase):
    def test_entry_py(self):
        with open(os.path.join(ROOT, "trader-entry", "entry.py"), encoding="utf-8") as fh:
            text = fh.read()
        for never in ("NYSE", "New York", "Eastern", "EDT", "eastern", "nyse.com", "Thanksgiving", "1:00 p.m."):
            self.assertNotIn(never, text, never)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-entry\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("bar by bar", "can lag", "day trading loses most people money", "/trader-exit", "longs only",
                     "in the background", "/loop", "nothing was placed", "9:35 am", RS + "1,416.30", "bar 3",
                     "nse", "ist", "nseindia.com", "square off", "not financial advice"):
            self.assertIn(must, low)
        for never in ("2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "nyse", "new york"):
            self.assertNotIn(never, low)
        self.assertNotIn("$", low.replace("${claude_skill_dir}", ""))         # rupees, never dollars


if __name__ == "__main__":
    unittest.main()
