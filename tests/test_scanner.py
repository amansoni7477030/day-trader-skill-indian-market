import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from _load import load, FIX, ROOT

scan = load("trader-scanner", "scan")

# Chart files named wall5m-, daily-spark-, spark-3mo-live-, spark-5m-live- and RELIANCE-5m-live- are SYNTHETIC
# (tests/fixtures/make_scanner_fixtures.py), not market data. REAL are real-spark-*-2026-11-03.json (Yahoo's
# answers, saved by tests/fixtures/capture_real_fixtures.py) and symdir/ (excerpts of NSE's EQUITY_L.csv and
# ind_nifty500list.csv).
SDIR = os.path.join(FIX, "trader-scanner")
WALL = os.path.join(SDIR, "wall5m-2026-10-19.json.gz")        # 61 synthetic 5-min charts, Mon Oct 19 2026, 11:13:07 IST
DAILY = os.path.join(SDIR, "daily-spark-2026-10-19.json")     # 20 synthetic daily charts as one spark answer
LIVE = os.path.join(SDIR, "spark-3mo-live-2026-10-21.json")   # a 19-symbol spark answer, Wed Oct 21 10:01:48 IST
SYMDIR = os.path.join(SDIR, "symdir")                         # real excerpts: EQUITY_L.csv, ind_nifty500list.csv
SPARK5 = os.path.join(SDIR, "spark-5m-live-2026-10-21-1001.json")      # 5-min spark, 20 symbols, fetched 10:01:47 IST
RELIANCE5 = os.path.join(SDIR, "RELIANCE-5m-live-2026-10-21-1001.json")  # v8, fetched with it
REAL3 = os.path.join(SDIR, "real-spark-3mo-2026-11-03.json")  # REAL Yahoo spark, 3mo of 1d bars, 8 symbols
REAL5 = os.path.join(SDIR, "real-spark-5m-2026-11-03.json")   # REAL Yahoo spark, 1d of 5m bars, the same 8 symbols
REAL_V8 = os.path.join(FIX, "trader-entry", "RELIANCE-1d5m-real-2026-11-03.json")   # REAL v8 chart, 1d of 5m bars
DT = datetime.datetime
D = datetime.date
T = datetime.time
UNTIL = T(14, 30)                                             # no_entries_after, the pack's default

LIVE19 = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "ITC", "SBIN", "LT", "KOTAKBANK", "HINDUNILVR",
          "M&M", "BAJAJ-AUTO", "MARUTI", "SUNPHARMA", "TITAN", "NTPC", "TATASTEEL", "YESBANK", "IDEA"]


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


def squash(text):
    return " ".join(text.split())


def ts(ist_text):
    """IST wall-clock text -> unix seconds, by plain arithmetic (UTC+5:30, no daylight saving)."""
    local = DT.strptime(ist_text, "%Y-%m-%d %H:%M:%S" if ist_text.count(":") == 2 else "%Y-%m-%d %H:%M")
    return int((local - DT(1970, 1, 1) - datetime.timedelta(hours=5, minutes=30)).total_seconds())


def bars_series(day, closes, highs=None, lows=None, start="09:15", extra=None):
    """A v8-like series of 5-min bars from `start` on `day` (YYYY-MM-DD)."""
    t0 = DT.strptime(day + " " + start, "%Y-%m-%d %H:%M")
    t = [ts((t0 + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M")) for i in range(len(closes))]
    s = {"t": t, "c": list(closes), "h": list(highs or [c + 0.1 if c is not None else None for c in closes]),
         "l": list(lows or [c - 0.1 if c is not None else None for c in closes]), "o": None,
         "v": [1000] * len(closes), "meta": {}}
    for when, c in (extra or []):
        s["t"].append(ts(when))
        for k in ("c", "h", "l"):
            s[k].append(c)
        s["v"].append(10)
    return s


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class Money(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(scan.inr(123456.78), "₹1,23,456.78")
        self.assertEqual(scan.inr(1234567.5), "₹12,34,567.50")
        self.assertEqual(scan.inr(10000000), "₹1,00,00,000.00")
        self.assertEqual(scan.inr(999), "₹999.00")
        self.assertEqual(scan.inr(0.5), "₹0.50")
        self.assertEqual(scan.inr(-1234.5), "-₹1,234.50")
        self.assertEqual(scan.inr(100000, 0), "₹1,00,000")

    def test_whole_amounts_without_decimals(self):
        self.assertEqual(scan.inr(5000, None), "₹5,000")
        self.assertEqual(scan.inr(50.0, None), "₹50")
        self.assertEqual(scan.inr(1416.30, None), "₹1,416.30")
        self.assertEqual(scan.num(500000), "5,00,000")
        self.assertEqual(scan.num(1999), "1,999")
        self.assertEqual(scan.num(1399.50, 2), "1,399.50")

    def test_the_rupee_sign_survives_an_ascii_console(self):
        """main() switches stdout to UTF-8, so a Windows code page (or ASCII) cannot raise UnicodeEncodeError."""
        env = dict(os.environ, PYTHONIOENCODING="ascii")
        env.pop("PYTHONUTF8", None)
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, os.path.join(ROOT, "trader-scanner", "scan.py"), "daily", "--offline",
                                DAILY, "--now", "2026-10-19 09:00", "--no-write", "--min-volume", "0"],
                               capture_output=True, cwd=tmp, env=env)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertIn("₹1,376.70".encode("utf-8"), r.stdout)


class Universe(unittest.TestCase):
    def setUp(self):
        self.text = read_text(os.path.join(SYMDIR, scan.EQUITY_FILE))
        self.kept, self.counts = scan.build_universe({scan.EQUITY_FILE: self.text})
        self.rows = {r["Symbol"]: r for r in scan.parse_equity_csv(self.text)}

    def why(self, sym):
        return scan.classify(self.rows[sym])

    def test_the_real_header_has_leading_spaces_and_is_read(self):
        self.assertEqual(self.text.splitlines()[0],
                         "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, "
                         "FACE VALUE")
        self.assertEqual(set(self.rows["RELIANCE"]), {"Symbol", "Name", "Series"})
        self.assertEqual(self.rows["RELIANCE"], {"Symbol": "RELIANCE", "Name": "Reliance Industries Limited",
                                                 "Series": "EQ"})

    def test_only_series_eq_is_kept(self):
        self.assertEqual([s for s, _ in self.kept],
                         ["20MICRONS", "21STCENMGM", "AUSTENG", "BAJAJ-AUTO", "HDFCBANK", "IDEA", "INFY", "ITC",
                          "J&KBANK", "JPPOWER", "M&M", "RELIANCE", "RPOWER", "SBIN", "TCS", "YESBANK"])
        self.assertEqual(self.counts, {"rows": 20, "keep": 16, "series BE": 2, "series BZ": 2})
        for sym, series in (("3IINFOLTD", "BE"), ("AARNAV", "BE"), ("AGSTRA", "BZ"), ("ANKITMETAL", "BZ")):
            self.assertEqual(self.why(sym), "series " + series, sym)
            self.assertNotIn(sym, dict(self.kept))

    def test_symbols_with_ampersand_hyphen_and_digits_are_plain(self):
        for sym in ("M&M", "BAJAJ-AUTO", "20MICRONS", "21STCENMGM", "J&KBANK", "RELIANCE"):
            self.assertEqual(self.why(sym), "keep", sym)
        self.assertEqual(dict(self.kept)["M&M"], "Mahindra & Mahindra Limited")

    def test_names_are_trimmed_and_squeezed(self):
        self.assertEqual(self.rows["AUSTENG"]["Name"], "Austin Engineering Company  Limited")   # the file has two spaces
        self.assertEqual(dict(self.kept)["AUSTENG"], "Austin Engineering Company Limited")

    def test_classify_rules_on_single_rows(self):
        def why(sym, series="EQ"):
            return scan.classify({"Symbol": sym, "Name": "x", "Series": series})
        self.assertEqual(why("RELIANCE"), "keep")
        self.assertEqual(why("reliance", " eq "), "keep")                   # case and spaces do not matter
        self.assertEqual(why("RELIANCE", "BE"), "series BE")
        self.assertEqual(why("RELIANCE", "SM"), "series SM")                # every other series is dropped, counted
        self.assertEqual(why("RELIANCE", ""), "series blank")
        self.assertEqual(why("A B"), "not a plain symbol")
        self.assertEqual(why("-ABC"), "not a plain symbol")
        self.assertEqual(why("ABC.NS"), "not a plain symbol")               # the suffix is Yahoo's, never in our files
        self.assertEqual(why("A" * 20), "keep")
        self.assertEqual(why("A" * 21), "not a plain symbol")

    def test_not_a_symbol_file(self):
        with self.assertRaises(scan.ScanError):
            scan.parse_equity_csv("<html>maintenance</html>")
        with self.assertRaises(scan.ScanError):
            scan.parse_equity_csv("Date,Open,Close\n2026-10-19,1,2\n")
        with self.assertRaises(scan.ScanError):
            scan.parse_equity_csv("")
        with self.assertRaises(scan.ScanError) as cm:
            scan.build_universe({})
        self.assertIn("missing EQUITY_L.csv", str(cm.exception))

    def test_bom_and_windows_line_ends(self):
        crlf = "﻿" + self.text.replace("\n", "\r\n")
        self.assertEqual(scan.build_universe({scan.EQUITY_FILE: crlf}), (self.kept, self.counts))

    def test_a_pipe_in_a_name_cannot_break_the_universe_file(self):
        text = "SYMBOL,NAME OF COMPANY, SERIES\nABC,A | B Limited,EQ\n"
        self.assertEqual(scan.build_universe({scan.EQUITY_FILE: text})[0], [("ABC", "A / B Limited")])

    def test_index_list(self):
        text = read_text(os.path.join(SYMDIR, "ind_nifty500list.csv"))
        self.assertEqual(text.splitlines()[0], "Company Name,Industry,Symbol,Series,ISIN Code")
        kept, counts = scan.build_universe({"ind_nifty500list.csv": text}, "nifty500")
        self.assertEqual(counts, {"rows": 12, "keep": 12})
        self.assertEqual([s for s, _ in kept], ["360ONE", "BAJAJ-AUTO", "HDFCBANK", "IDEA", "INFY", "ITC", "J&KBANK",
                                                "M&M", "RELIANCE", "SBIN", "TCS", "YESBANK"])
        self.assertEqual(dict(kept)["M&M"], "Mahindra & Mahindra Ltd.")
        self.assertEqual(scan.index_file("nifty50"), "ind_nifty50list.csv")
        with self.assertRaises(scan.ScanError):                             # the index list is not EQUITY_L.csv
            scan.build_universe({scan.EQUITY_FILE: self.text}, "nifty500")
        non_eq = "Company Name,Industry,Symbol,Series,ISIN Code\nA Ltd.,X,AAA,EQ,I1\nB Ltd.,X,BBB,BE,I2\n"
        kept, counts = scan.build_universe({"ind_nifty50list.csv": non_eq}, "nifty50")
        self.assertEqual((kept, counts["series BE"]), ([("AAA", "A Ltd.")], 1))


class Time(unittest.TestCase):
    def test_ist_has_no_daylight_saving(self):
        self.assertEqual(scan.to_ist(DT(2026, 10, 19, 3, 45)), DT(2026, 10, 19, 9, 15))
        self.assertEqual(scan.to_ist(DT(2026, 7, 1, 3, 45)), DT(2026, 7, 1, 9, 15))
        self.assertEqual(scan.to_ist(DT(2026, 1, 1, 3, 45)), DT(2026, 1, 1, 9, 15))
        self.assertEqual(scan.ist_to_utc(DT(2026, 10, 19, 9, 15)), DT(2026, 10, 19, 3, 45))
        self.assertEqual(scan.ist_to_utc(scan.to_ist(DT(2026, 12, 1, 23, 59))), DT(2026, 12, 1, 23, 59))
        self.assertEqual(scan.to_ist(DT(2026, 10, 19, 22, 0)), DT(2026, 10, 20, 3, 30))     # crosses midnight
        self.assertEqual(scan.ts_to_ist(1792381500), DT(2026, 10, 19, 9, 15))
        self.assertEqual(scan.ts_to_ist(ts("2026-12-01 09:15")), DT(2026, 12, 1, 9, 15))

    def test_now_is_ist_unless_it_says_otherwise(self):
        self.assertEqual(scan.parse_now("2026-10-19 10:15"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(scan.parse_now("2026-12-01 10:15"), DT(2026, 12, 1, 4, 45))
        self.assertEqual(scan.parse_now("2026-10-19T04:45:00Z"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(scan.parse_now("2026-10-19T10:15:00+05:30"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(scan.clock_label(DT(2026, 10, 19, 10, 15)), "10:15 AM")
        self.assertEqual(scan.clock_label(T(12, 5)), "12:05 PM")
        self.assertEqual(scan.clock_label(T(15, 30)), "3:30 PM")
        self.assertEqual(scan.clock_label(T(9, 35)), "9:35 AM")
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_now("tomorrow")
        self.assertIn("IST", str(cm.exception))
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_hhmm("25:00")
        self.assertIn("IST", str(cm.exception))
        self.assertEqual(scan.parse_hhmm("9:35"), T(9, 35))

    def test_the_pack_day_is_9_15_to_3_15_with_72_bars(self):
        """Since 3 Aug 2026 F&O stocks stop continuous trading at 15:15 (closing auction after it): the pack's day is
        72 five-minute bars, 9:15 to 3:15 PM. NSE's own close stays 15:30 and the post-close session ends 16:00."""
        self.assertEqual((scan.OPEN, scan.CLOSE), (T(9, 15), T(15, 15)))
        self.assertEqual((scan.MARKET_CLOSE, scan.DAY_END), (T(15, 30), T(16, 0)))
        self.assertEqual(scan.session(D(2026, 10, 19)), (T(9, 15), T(15, 15)))
        minutes = (DT.combine(D(2026, 10, 19), scan.CLOSE) - DT.combine(D(2026, 10, 19), scan.OPEN)).seconds // 60
        self.assertEqual(minutes // scan.BAR_MIN, 72)
        self.assertEqual(scan.slot(D(2026, 10, 19), 0), DT(2026, 10, 19, 9, 15))
        self.assertEqual(scan.slot(D(2026, 10, 19), 71), DT(2026, 10, 19, 15, 10))      # the last bar the tools read
        self.assertEqual(scan.slot(D(2026, 10, 19), 72), DT(2026, 10, 19, 15, 15))      # the stub bar: never read
        self.assertEqual(scan.slot(D(2026, 10, 19), 75), DT(2026, 10, 19, 15, 30))

    def test_weekends_holidays_and_the_calendar(self):
        self.assertEqual(scan.trading_day(D(2026, 10, 17)), (False, "weekend"))          # Saturday
        self.assertEqual(scan.trading_day(D(2026, 10, 18)), (False, "weekend"))          # Sunday
        # the holiday list is read first, so a holiday that falls on a weekend still has its name
        self.assertEqual(scan.trading_day(D(2026, 11, 8)),
                         (False, "NSE holiday: Diwali Laxmi Pujan (Muhurat trading session only)"))   # a Sunday
        self.assertEqual(scan.trading_day(D(2026, 2, 15)), (False, "NSE holiday: Mahashivratri"))         # a Sunday
        self.assertEqual(scan.trading_day(D(2026, 10, 20)), (False, "NSE holiday: Dussehra"))
        self.assertEqual(scan.trading_day(D(2026, 10, 19)), (True, ""))
        self.assertEqual(scan.trading_day(D(2026, 10, 21)), (True, ""))
        self.assertIsNone(scan.session(D(2026, 10, 20)))
        self.assertIsNone(scan.session(D(2026, 10, 17)))
        self.assertEqual(len(scan.NSE_HOLIDAYS), 20)              # NSE's 2026 equity list, weekend dates included
        self.assertEqual(scan.NSE_HOLIDAYS["2026-11-10"], "Diwali-Balipratipada")
        self.assertEqual(scan.NSE_HOLIDAYS["2026-01-26"], "Republic Day")
        self.assertEqual(scan.NSE_HOLIDAYS["2026-01-15"], "Municipal Corporation Election - Maharashtra")
        weekend = [iso for iso in scan.NSE_HOLIDAYS if D.fromisoformat(iso).weekday() >= 5]
        self.assertEqual(weekend, ["2026-02-15", "2026-03-21", "2026-08-15", "2026-11-08"])   # Sun, Sat, Sat, Sun
        for iso in scan.NSE_HOLIDAYS:
            self.assertIn(D.fromisoformat(iso).year, scan.CALENDAR_YEARS)
        self.assertEqual(scan.CALENDAR_YEARS, (2026,))
        self.assertEqual(scan.NSE_EARLY_CLOSE, {})                # NSE has no normal early closes

    def test_calendar_year_not_covered(self):
        self.assertFalse(scan.calendar_known(D(2027, 1, 4)))
        self.assertTrue(scan.calendar_known(D(2026, 12, 31)))
        self.assertIn("covers 2026 only", scan.calendar_warning(D(2027, 1, 4)))
        self.assertIn("nseindia.com", scan.calendar_warning(D(2027, 1, 4)))
        with self.assertRaises(scan.ScanError) as cm:                       # a Monday in 2027: unknown, not a guess
            scan.orb_window(scan.parse_now("2027-01-04 10:15"), None, 15, UNTIL)
        self.assertIn("covers 2026 only", str(cm.exception))
        with self.assertRaises(scan.ScanError) as cm:                       # but a weekend is a weekend in any year
            scan.orb_window(scan.parse_now("2027-01-02 10:15"), None, 15, UNTIL)
        self.assertIn("weekend", str(cm.exception))

    def test_a_special_short_session_can_be_injected(self):
        with mock.patch.dict(scan.NSE_EARLY_CLOSE, {"2026-11-12": "a special short session"}):
            self.assertEqual(scan.session(D(2026, 11, 12)), (T(9, 15), scan.EARLY))
            self.assertEqual(scan.session(D(2026, 11, 11)), (T(9, 15), T(15, 15)))
        self.assertEqual(scan.session(D(2026, 11, 12)), (T(9, 15), T(15, 15)))


class Spark(unittest.TestCase):
    def test_a_spark_answer(self):
        text = read_text(LIVE)
        wanted = {scan.yahoo_symbol(s): s for s in LIVE19 + ["ZZZQXIND"]}
        data = scan.parse_spark(text, wanted)
        self.assertEqual(len(data), 19)            # 20 asked; Yahoo silently drops the made-up ZZZQXIND
        self.assertNotIn("ZZZQXIND", data)
        self.assertIn("M&M", data)                 # M&M.NS comes back as the plain M&M
        self.assertIn("BAJAJ-AUTO", data)
        self.assertEqual(sorted(data), sorted(LIVE19))
        r = data["RELIANCE"]
        self.assertEqual(len(r["t"]), 64)
        self.assertIsNone(r["h"])                  # spark is closes only
        self.assertEqual(r["c"][-3:], [1376.7, 1395.3, 1406.4])
        self.assertEqual(r["meta"]["regularMarketVolume"], 1776000)
        self.assertEqual(r["meta"]["currency"], "INR")
        self.assertEqual(r["meta"]["timezone"], "IST")
        self.assertEqual(r["meta"]["gmtoffset"], 19800)
        self.assertEqual(data["YESBANK"]["meta"]["regularMarketPrice"], 21.85)
        # no wanted map: the .NS suffix is stripped anyway
        self.assertEqual(sorted(scan.parse_spark(text)), sorted(LIVE19))
        # read just before Wednesday's open: Tuesday was the Dussehra holiday, so Monday is the last full bar
        closes = scan.daily_closes(r, scan.parse_now("2026-10-21 09:14"))
        self.assertEqual(closes[-2:], [(D(2026, 10, 16), 1376.7), (D(2026, 10, 19), 1395.3)])
        # the same answer read during Monday's session: Monday's bar is still in progress
        closes = scan.daily_closes(r, scan.parse_now("2026-10-19 12:00"))
        self.assertEqual(closes[-1][0], D(2026, 10, 16))
        # and during Wednesday's session the bar of the day is left out as well
        closes = scan.daily_closes(r, scan.parse_now("2026-10-21 12:00"))
        self.assertEqual(closes[-1], (D(2026, 10, 19), 1395.3))

    def test_spark_errors(self):
        too_many = '{"spark":{"result":null,"error":{"code":"Bad Request",' \
                   '"description":"Number of symbols needs to be less than or equal to 20"}}}'
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_spark(too_many)
        self.assertIn("less than or equal to 20", str(cm.exception))
        self.assertEqual(scan.parse_spark('{"spark":{"result":[],"error":null}}'), {})
        with self.assertRaises(scan.ScanError):
            scan.parse_spark('{"chart":{}}')

    def test_yahoo_symbols(self):
        self.assertEqual(scan.yahoo_symbol("RELIANCE"), "RELIANCE.NS")
        self.assertEqual(scan.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(scan.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(scan.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")          # the hyphen stays
        self.assertEqual(scan.yahoo_symbol("20MICRONS"), "20MICRONS.NS")
        self.assertEqual(scan.yahoo_symbol("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(scan.yahoo_symbol("500325.BO"), "500325.BO")
        self.assertEqual(scan.yahoo_symbol("^NSEI"), "^NSEI")
        self.assertEqual(scan.plain_symbol("M&M.NS"), "M&M")
        self.assertEqual(scan.plain_symbol("bajaj-auto.ns"), "BAJAJ-AUTO")
        self.assertEqual(scan.plain_symbol("RELIANCE"), "RELIANCE")
        self.assertEqual(scan.plain_symbol("^NSEI"), "^NSEI")

    def test_urls_are_quoted(self):
        self.assertEqual(scan.spark_path(["M&M", "reliance", "BAJAJ-AUTO"], "3mo", "1d"),
                         "/v7/finance/spark?symbols=M%26M.NS,RELIANCE.NS,BAJAJ-AUTO.NS&range=3mo&interval=1d")
        self.assertEqual(scan.chart_path("RELIANCE", "1d", "5m"), "/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")
        self.assertEqual(scan.chart_path("M&M", "5d", "5m"), "/v8/finance/chart/M%26M.NS?range=5d&interval=5m")
        self.assertEqual(scan.chart_path("^NSEI", "1d", "5m"), "/v8/finance/chart/%5ENSEI?range=1d&interval=5m")
        self.assertNotIn("&NS", scan.spark_path(["M&M"], "1d", "1d"))     # an unquoted & would cut the query in two


class Network(unittest.TestCase):
    """429s and 5xx without the network."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = scan.http_get, scan.SLEEP
        scan.SLEEP = self.sleeps.append
        self.body = read_text(LIVE)

    def tearDown(self):
        scan.http_get, scan.SLEEP = self._get, self._sleep

    def answers(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url.split("/v")[0])
            code = codes.pop(0) if codes else 200
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return self.body
        scan.http_get = fake

    def test_429_falls_back_then_waits_once(self):
        self.answers([429, 429, 200])
        text = scan.yahoo_get("/v7/finance/spark?symbols=RELIANCE.NS&range=3mo&interval=1d")
        self.assertEqual(text, self.body)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com",
                                      "https://query2.finance.yahoo.com"])
        self.assertEqual(self.sleeps, [scan.BACKOFF])

    def test_5xx_goes_to_query1_without_waiting(self):
        self.answers([503, 200])
        scan.yahoo_get("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.sleeps, [])

    def test_429_for_good_is_reported_as_partial(self):
        self.answers([200] + [429] * 10)
        syms = LIVE19 + ["ZZZQXIND"] + ["X%02d" % i for i in range(20)]
        data, failed = scan.fetch_spark(syms, "3mo", "1d", 0.3)
        self.assertEqual(len(data), 19)
        self.assertIn("M&M", data)                        # mapped back from M&M.NS
        self.assertEqual(failed["ZZZQXIND"], "no data from Yahoo")
        self.assertEqual(len(failed), 21)
        self.assertIn("HTTP 429", failed["X00"])
        self.assertEqual(self.sleeps, [0.3, scan.BACKOFF])  # a pause between batches, one backoff
        hits, counts, _ = scan.scan_daily(data, syms, scan.parse_now("2026-10-21 09:14"), 50, 500000)
        self.assertEqual(counts["requested"], 40)
        self.assertEqual(counts["fetched"], 19)

    def test_a_404_is_final(self):
        self.answers([404])
        with self.assertRaises(scan.ScanError):
            scan.yahoo_get("/v8/finance/chart/NOPE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 1)


class Transport(unittest.TestCase):
    """The real http_get, with curl_cffi swapped for a fake (no network). NSE and Yahoo block plain Python
    clients (HTTP 403 / 429), so every request carries Chrome's TLS fingerprint (curl_cffi impersonate) and the
    headers a browser sends."""

    class Resp(object):
        def __init__(self, status, body=b""):
            self.status_code, self.content = status, body

    class FakeCffi(object):
        def __init__(self, resp=None, exc=None):
            self.resp, self.exc, self.calls = resp, exc, []

        def get(self, url, **kw):
            self.calls.append((url, kw))
            if self.exc:
                raise self.exc
            return self.resp

    def test_a_yahoo_request_is_a_chrome_request(self):
        fake = self.FakeCffi(self.Resp(200, "caf\u00e9 \u20b9".encode("utf-8")))
        url = "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m"
        with mock.patch.object(scan, "_cffi", fake):
            self.assertEqual(scan.http_get(url, timeout=7), "caf\u00e9 \u20b9")
        got_url, kw = fake.calls[0]
        self.assertEqual(got_url, url)
        self.assertEqual((kw["impersonate"], kw["timeout"]), ("chrome", 7))
        self.assertEqual(kw["headers"]["Referer"], "https://finance.yahoo.com/")
        self.assertIn("application/json", kw["headers"]["Accept"])
        self.assertIn("Accept-Language", kw["headers"])
        self.assertNotIn("User-Agent", kw["headers"])       # curl_cffi sends Chrome's own, matching its fingerprint

    def test_an_nse_request_has_nse_as_its_referer(self):
        fake = self.FakeCffi(self.Resp(200, b"SYMBOL,NAME OF COMPANY\n"))
        with mock.patch.object(scan, "_cffi", fake):
            scan.http_get(scan.EQUITY_URL, timeout=60)
        kw = fake.calls[0][1]
        self.assertEqual(kw["headers"]["Referer"], "https://www.nseindia.com/")
        self.assertIn("text/csv", kw["headers"]["Accept"])
        self.assertEqual((kw["impersonate"], kw["timeout"]), ("chrome", 60))

    def test_browser_headers_per_host(self):
        y = scan.browser_headers("https://query1.finance.yahoo.com/v7/finance/spark?symbols=X")
        n = scan.browser_headers("https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv")
        o = scan.browser_headers("https://example.com/x")
        self.assertEqual((y["Referer"], n["Referer"]), ("https://finance.yahoo.com/", "https://www.nseindia.com/"))
        self.assertNotIn("Referer", o)
        for h in (y, n, o):
            self.assertIn("Accept-Language", h)

    def test_a_4xx_or_5xx_is_an_http_error_with_its_body(self):
        for code in (403, 404, 429, 503):
            fake = self.FakeCffi(self.Resp(code, b'{"chart":{"error":{"code":"Not Found"}}}'))
            with mock.patch.object(scan, "_cffi", fake):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    scan.http_get("https://query2.finance.yahoo.com/x")
            self.assertEqual(cm.exception.code, code)
            self.assertIn(b"Not Found", cm.exception.read())

    def test_a_network_failure_is_a_url_error(self):
        with mock.patch.object(scan, "_cffi", self.FakeCffi(exc=RuntimeError("timed out"))):
            with self.assertRaises(urllib.error.URLError):
                scan.http_get("https://query2.finance.yahoo.com/x")

    def test_yahoo_get_rotates_hosts_and_waits_once_on_a_429(self):
        answers = [self.Resp(429), self.Resp(429), self.Resp(200, b'{"ok": true}')]
        hosts, sleeps = [], []

        class Seq(object):
            def get(self, url, **kw):
                hosts.append(url.split("/")[2])
                return answers.pop(0)
        with mock.patch.object(scan, "_cffi", Seq()), mock.patch.object(scan, "SLEEP", sleeps.append):
            self.assertEqual(scan.yahoo_get("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m"), '{"ok": true}')
        self.assertEqual(hosts, ["query2.finance.yahoo.com", "query1.finance.yahoo.com", "query2.finance.yahoo.com"])
        self.assertEqual(sleeps, [scan.BACKOFF])

    def test_yahoos_json_error_body_reaches_the_caller_and_other_4xx_is_final(self):
        body = (b'{"chart":{"result":null,"error":{"code":"Not Found",'
                b'"description":"No data found, symbol may be delisted"}}}')
        with mock.patch.object(scan, "_cffi", self.FakeCffi(self.Resp(404, body))):
            self.assertIn("No data found", scan.yahoo_get("/v8/finance/chart/NOPE.NS?range=1d&interval=5m"))
        with mock.patch.object(scan, "_cffi", self.FakeCffi(self.Resp(403, b"<html>Forbidden</html>"))):
            with self.assertRaises(scan.ScanError) as cm:
                scan.yahoo_get("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")
        self.assertIn("Yahoo answered HTTP 403", str(cm.exception))

    def test_without_curl_cffi_urllib_sends_the_same_headers_and_a_chrome_user_agent(self):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["headers"] = {k.lower(): v for k, v in req.header_items()}
            seen["timeout"] = timeout
            return io.BytesIO(b"ok")
        with mock.patch.object(scan, "_cffi", None), mock.patch.object(scan.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(scan.http_get(scan.EQUITY_URL, timeout=9), "ok")
        self.assertEqual(seen["timeout"], 9)
        self.assertEqual(seen["headers"]["user-agent"], scan.BROWSER_UA)
        self.assertIn("Chrome/", scan.BROWSER_UA)
        self.assertNotIn("compatible", scan.BROWSER_UA)           # not a bot-looking User-Agent
        self.assertEqual(seen["headers"]["referer"], "https://www.nseindia.com/")


class Daily(unittest.TestCase):
    # Closes through Fri Oct 16 2026 (the Oct 19 bar is in progress). Tags checked by hand from the closes:
    EXPECT = {
        "RELIANCE": ["breakout_20d"],     # 1376.70 > 1360.40 (1.2 % over the best close of the 20 before it)
        "TCS": ["breakout_20d"],
        "HDFCBANK": ["breakout_20d"],
        "MARUTI": ["breakout_20d"],
        "M&M": ["bull_flag"],             # up 15.0 % over the 10 sessions before the last 5, last 5 in a 1.3 % range
        "BAJAJ-AUTO": ["bull_flag"],
        "INFY": ["reclaim_ma20"],         # yesterday under its 20-day average, today over it
        "ITC": ["reclaim_ma20"],
        "SBIN": ["reclaim_ma20"],
        "ICICIBANK": ["oversold"],        # RSI 22.8
        "TATASTEEL": ["oversold"],        # RSI 18.0
        "YESBANK": ["oversold"],          # RSI 18.3
        "IDEA": ["oversold"],             # RSI 16.2
        "LT": ["oversold_bounce"],        # RSI 27.4 on Thursday, closed up Friday, RSI 40.6
    }
    NONE = ["AXISBANK", "HINDUNILVR", "KOTAKBANK", "NTPC", "SUNPHARMA", "TITAN"]

    def setUp(self):
        self.data, self.errors = scan.load_offline(DAILY)
        self.syms = sorted(self.data)

    def test_setups_before_the_open(self):
        self.assertEqual(len(self.syms), 20)
        hits, counts, last = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 09:00"), 0, 0)
        self.assertEqual({h["symbol"]: h["setups"] for h in hits}, self.EXPECT)
        self.assertEqual(sorted(set(self.syms) - {h["symbol"] for h in hits}), self.NONE)
        self.assertEqual(last, D(2026, 10, 16))
        self.assertEqual(counts, {"requested": 20, "fetched": 20, "illiquid": 0, "short": 0, "scanned": 20,
                                  "quote_closes": 0, "hits": 14})
        rel = [h for h in hits if h["symbol"] == "RELIANCE"][0]
        self.assertEqual(rel, {"symbol": "RELIANCE", "setups": ["breakout_20d"], "price": 1376.7,
                               "close_date": "2026-10-16", "rsi": 65.3, "volume": 3331467})
        self.assertEqual(hits[0]["symbol"], "HDFCBANK")       # most rupees traded first: 960.00 x 4,916,153
        self.assertEqual([h["symbol"] for h in hits][-2:], ["BAJAJ-AUTO", "MARUTI"])

    def test_the_setup_math_on_hand_made_closes(self):
        flat = [100.0] * 30
        self.assertEqual(scan.find_setups(flat)[0], [])
        self.assertEqual(scan.find_setups(flat[:-1] + [101.0])[0], ["breakout_20d"])
        self.assertEqual(scan.find_setups(flat[:-1] + [100.0])[0], [])      # equal to the best close is not above it
        flag = [100.0] * 14 + [100 + 1.5 * k for k in range(1, 11)] + [114.0, 115.0, 114.5, 114.2, 114.0, 114.4]
        flag = flag[-30:]
        self.assertEqual(scan.find_setups(flag)[0], ["bull_flag"])          # +15 % then a 0.9 % range
        wide = flag[:-5] + [110.0, 118.0, 114.0, 116.0, 112.0]              # last 5 in a 7 % range: not a flag
        self.assertNotIn("bull_flag", scan.find_setups(wide)[0])
        # 20 closes of 100, then one at 99 (under its average) and one at 100.6 (back over it)
        reclaim = [100.0] * 28 + [99.0, 100.6]
        tags, nums = scan.find_setups(reclaim)
        self.assertIn("reclaim_ma20", tags)
        self.assertAlmostEqual(nums["ma20"], 99.98, places=2)

    def test_the_in_progress_bar_is_dropped(self):
        before = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 09:00"), 0, 0)
        during = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 10:50"), 0, 0)
        self.assertEqual(before, during)
        # F&O stocks close in an auction (3:15 to 3:35 PM) and the post-close session runs to 4:00 PM, so the
        # day's bar is final only at 4:00 PM, not at the 3:30 PM close
        for when in ("2026-10-19 15:15", "2026-10-19 15:30", "2026-10-19 15:59"):
            bars = scan.daily_closes(self.data["RELIANCE"], scan.parse_now(when))
            self.assertEqual(bars[-1], (D(2026, 10, 16), 1376.7), when)
        bars = scan.daily_closes(self.data["RELIANCE"], scan.parse_now("2026-10-19 16:00"))
        self.assertEqual(bars[-1], (D(2026, 10, 19), 1400.1))

    def test_a_special_short_session_bar_is_final_at_4pm_too(self):
        # the pack does not guess when a special short session's auction and post-close session end
        s = {"t": [ts("2026-11-11 09:15"), ts("2026-11-12 09:15")], "c": [10.0, 11.0], "meta": {}}
        with mock.patch.dict(scan.NSE_EARLY_CLOSE, {"2026-11-12": "test"}):
            self.assertEqual(len(scan.daily_closes(s, scan.parse_now("2026-11-12 13:00"))), 1)
            self.assertEqual(len(scan.daily_closes(s, scan.parse_now("2026-11-12 15:59"))), 1)
            self.assertEqual(len(scan.daily_closes(s, scan.parse_now("2026-11-12 16:00"))), 2)

    def test_a_holiday_bar_is_complete(self):
        # a bar dated on a closed day (should not exist) never blocks the scan: the day counts as done
        s = {"t": [ts("2026-10-19 09:15"), ts("2026-10-20 09:15")], "c": [10.0, 11.0], "meta": {}}
        self.assertEqual(len(scan.daily_closes(s, scan.parse_now("2026-10-20 10:00"))), 2)

    def test_liquidity_filter(self):
        # meta is Yahoo's as of 11:13 AM Monday, so the volume is the day's so far
        hits, counts, _ = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 11:14"), 50, 500000)
        self.assertEqual(sorted(h["symbol"] for h in hits), ["HDFCBANK", "ICICIBANK", "INFY", "ITC", "LT", "M&M",
                                                             "RELIANCE", "SBIN", "TATASTEEL", "TCS"])
        # YESBANK and IDEA are under Rs 50; BAJAJ-AUTO (181,962), MARUTI (97,997) and TITAN (482,867) are under
        # 5,00,000 shares so far
        self.assertEqual(counts["illiquid"], 5)
        self.assertEqual(counts["scanned"], 15)
        hits, counts, _ = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 11:14"), 50, 0)
        self.assertEqual(counts["illiquid"], 2)                           # no volume floor: only the price floor bites
        self.assertNotIn("YESBANK", [h["symbol"] for h in hits])
        self.assertIn("MARUTI", [h["symbol"] for h in hits])

    def test_short_and_empty_charts(self):
        s = {"t": [ts("2026-10-%02d 09:15" % d) for d in range(1, 11)], "c": [100.0] * 10,
             "meta": {"regularMarketPrice": 100, "regularMarketVolume": 5000000}}
        hits, counts, last = scan.scan_daily({"X": s, "E": {"t": [], "c": [], "meta": {}}}, ["X", "E", "GONE"],
                                             scan.parse_now("2026-10-22 09:00"), 50, 500000)
        self.assertEqual(hits, [])
        self.assertEqual((counts["fetched"], counts["short"], counts["illiquid"], counts["scanned"]), (2, 1, 1, 0))
        self.assertIsNone(last)

    def test_rsi_matches_wilders_math(self):
        self.assertIsNone(scan.rsi([1.0] * 10))
        self.assertEqual(scan.rsi([float(x) for x in range(1, 40)]), 100.0)
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
                  46.28, 46.28]
        self.assertAlmostEqual(scan.rsi(closes), 70.46, places=1)  # Wilder's textbook example


class Orb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.errors = scan.load_offline(WALL)
        cls.syms = sorted(set(cls.data) | set(cls.errors))

    def scan_at(self, now, min_price=0, syms=None, or_minutes=15):
        day, cutoff = scan.orb_window(scan.parse_now(now), None, or_minutes, UNTIL)
        return scan.scan_orb(self.data, syms or self.syms, day, cutoff, or_minutes, min_price, 0)

    def test_the_scan_at_0935(self):
        hits, counts, failed = self.scan_at("2026-10-19 09:35")
        self.assertEqual(len(self.syms), 61)
        self.assertEqual(self.errors, {"MINDTREE": "no data for MINDTREE: No data found, symbol may be delisted"})
        self.assertEqual(counts["scanned"], 58)           # usable charts
        self.assertEqual(counts["unusable"], 2)           # RPOWER (no bars), JPPOWER (bars missing)
        self.assertEqual(counts["candidates"], 21)
        self.assertEqual(len(hits), 16)
        rel = [h for h in hits if h["symbol"] == "RELIANCE"][0]
        self.assertEqual(rel, {"symbol": "RELIANCE", "setups": ["orb"], "price": 1416.30, "at": "9:35 AM",
                               "bar_start": "09:30", "bar_index": 3, "or_high": 1413.4, "or_low": 1399.50,
                               "pct_over": 0.21})
        self.assertEqual([h["at"] for h in hits], ["9:35 AM"] * 16)     # the first bar after the 9:15-9:30 range
        self.assertEqual([h["symbol"] for h in hits], sorted(h["symbol"] for h in hits))   # a tie: by symbol
        self.assertEqual([h["symbol"] for h in hits],
                         ["AXISBANK", "BAJAJFINSV", "BAJFINANCE", "DRREDDY", "GRASIM", "HCLTECH", "HINDALCO",
                          "ICICIBANK", "INDUSINDBK", "PNB", "RELIANCE", "SBIN", "SUZLON", "TRENT", "ULTRACEMCO",
                          "WIPRO"])

    def test_the_scan_at_1000_is_sorted_by_time(self):
        hits, counts, _ = self.scan_at("2026-10-19 10:00")
        self.assertEqual(len(hits), 29)
        self.assertEqual(counts["scanned"], 58)
        order = [(int(h["at"].split(":")[0]) % 12, int(h["at"][-5:-3])) for h in hits]
        self.assertEqual(order, sorted(order))              # 9:35, 9:40, 9:45, 9:50, 9:55, 10:00 (12-hour clock, a.m.)
        self.assertEqual(hits[0]["symbol"], "AXISBANK")
        self.assertEqual(sum(1 for h in hits if h["at"] == "10:00 AM"), 2)
        tcs = [h for h in hits if h["symbol"] == "TCS"][0]
        # closed 3063.80 over a range high of 3063.60: twenty paise over (two ticks of Rs 0.10), rounds to 0.01 %
        self.assertEqual((tcs["price"], tcs["or_high"], tcs["pct_over"]), (3063.8, 3063.6, 0.01))
        self.assertEqual(tcs["at"], "10:00 AM")
        later = [h for h in hits if h["at"] != "9:35 AM"][0]
        self.assertEqual(later["symbol"], "ASIANPAINT")     # 9:40 AM, the first of the later breakouts

    def test_every_hit_matches_a_plain_reading_of_the_raw_bars(self):
        """Recompute each chart's first close above its opening-range high straight from the JSON arrays, with no
        help from scan.py (bar k starts at 9:15 + 5k minutes, IST), and compare the whole 10:00 AM scan."""
        raw = json.loads(scan._read_maybe_gz(WALL))
        open_ts = ts("2026-10-19 09:15")
        expect = {}
        for sym, doc in raw.items():
            res = (doc["chart"]["result"] or [None])[0]
            if not res or not res.get("timestamp"):
                continue                                       # delisted (MINDTREE) or no bars (RPOWER)
            q = res["indicators"]["quote"][0]
            row = {t: (h, c) for t, h, c in zip(res["timestamp"], q["high"], q["close"])}
            want = [open_ts + 300 * k for k in range(9)]         # 9:15 to 9:55: the bars complete at 10:00
            if any(t not in row or row[t][1] is None for t in want):
                continue                                       # a missing bar (JPPOWER)
            top = max(row[t][0] for t in want[:3])
            for k in range(3, 9):
                if row[want[k]][1] > top:
                    expect[sym] = (k, round(row[want[k]][1], 2), round(top, 2))
                    break
        hits, _, _ = self.scan_at("2026-10-19 10:00")
        got = {h["symbol"]: (h["bar_index"], h["price"], h["or_high"]) for h in hits}
        self.assertEqual(got, expect)
        self.assertEqual(len(expect), 29)

    def test_price_floor(self):
        hits, counts, _ = self.scan_at("2026-10-19 10:00", min_price=50)
        self.assertEqual(len(hits), 27)                     # YESBANK (Rs 21.xx) and IRFC (Rs 38.xx) drop out of 29
        self.assertEqual(counts["illiquid"], 3)             # YESBANK, IDEA, IRFC are under Rs 50
        self.assertNotIn("YESBANK", [h["symbol"] for h in hits])
        self.assertNotIn("IRFC", [h["symbol"] for h in hits])
        self.assertIn("SUZLON", [h["symbol"] for h in hits])  # Rs 56.24 is over the floor
        hits, counts, _ = self.scan_at("2026-10-19 09:35", min_price=50)
        self.assertEqual((len(hits), counts["scanned"], counts["illiquid"]), (16, 55, 3))

    def test_in_progress_bar_and_no_breakout_yet(self):
        day = D(2026, 10, 19)
        hits, _, _ = scan.scan_orb(self.data, ["RELIANCE"], day, DT(2026, 10, 19, 9, 34), 15, 0, 0)
        self.assertEqual(hits, [])                         # the 9:30 bar is not done until 9:35
        hits, _, _ = self.scan_at("2026-10-19 09:35", syms=["RELIANCE"])
        self.assertEqual(hits[0]["at"], "9:35 AM")
        hits, _, _ = self.scan_at("2026-10-19 09:35", syms=["TCS"])
        self.assertEqual(hits, [])                         # TCS only breaks out at 10:00

    def test_the_live_tick_is_dropped(self):
        s = self.data["RELIANCE"]
        self.assertEqual(scan.ts_to_ist(s["t"][-1]), DT(2026, 10, 19, 11, 13, 7))   # off the grid
        self.assertEqual(len(s["t"]), 25)                  # 9:15 .. 11:10 (24 bars) and the tick
        bars = scan.session_bars(s, D(2026, 10, 19), DT(2026, 10, 19, 11, 14))
        self.assertEqual(len(bars), 23)                    # 9:15 to 11:05; 11:10 in progress, the tick dropped
        self.assertTrue(scan.usable(bars, D(2026, 10, 19), DT(2026, 10, 19, 11, 14), 3))
        hits, _, _ = self.scan_at("2026-10-19 11:14", syms=["RELIANCE"])
        self.assertEqual(hits[0]["price"], 1416.30)        # still the first close above the range

    def test_stage_one_is_a_necessary_condition(self):
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 15)
        seen = 0
        for sym, s in self.data.items():
            bars = scan.session_bars(s, day, cut)
            if scan.usable(bars, day, cut, 3) and scan.orb_hit(bars, 3):
                seen += 1
                self.assertTrue(scan.orb_candidate(bars, 3), sym)
        self.assertEqual(seen, 30)

    def test_confirm_uses_the_v8_highs(self):
        spark_like = {k: dict(v, h=None, l=None) for k, v in self.data.items()}  # closes only, like spark
        asked = []

        def confirm(sym):
            asked.append(sym)
            return self.data[sym]
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 15)
        hits, counts, failed = scan.scan_orb(spark_like, self.syms, day, cut, 15, 0, 0, confirm)
        self.assertEqual(len(hits), 30)
        self.assertEqual(counts["candidates"], len(asked))
        self.assertLess(len(asked), counts["scanned"])     # stage 1 skipped the charts that never reached the range top

        def broken(sym):
            raise scan.ScanError("Yahoo answered HTTP 429 for /v8/finance/chart/%s.NS" % sym)
        hits, counts, failed = scan.scan_orb(spark_like, ["RELIANCE", "ITC"], day, DT(2026, 10, 19, 9, 35), 15, 0, 0,
                                             broken)
        self.assertEqual(hits, [])
        self.assertIn("429", failed["RELIANCE"])

    def test_a_missing_bar_makes_a_chart_unusable(self):
        s = bars_series("2026-10-21", [10, 10.2, 10.1, None, 10.5, 10.6, 10.7, 10.8, 10.9])
        bars = scan.session_bars(s, D(2026, 10, 21), DT(2026, 10, 21, 10, 0))
        self.assertFalse(scan.usable(bars, D(2026, 10, 21), DT(2026, 10, 21, 10, 0), 3))
        jp = self.data["JPPOWER"]                          # the fixture's chart with two bars missing
        bars = scan.session_bars(jp, D(2026, 10, 19), DT(2026, 10, 19, 9, 35))
        self.assertFalse(scan.usable(bars, D(2026, 10, 19), DT(2026, 10, 19, 9, 35), 3))
        self.assertEqual(self.data["RPOWER"]["t"], [])      # a halted stock: no bars at all

    def test_a_30_minute_range(self):
        closes = [100, 101, 100.5, 100.8, 101.2, 100.9, 101.5] + [101.0] * 40
        s = bars_series("2026-10-21", closes, highs=[c + 0.2 for c in closes])
        hits, counts, _ = scan.scan_orb({"X": s}, ["X"], D(2026, 10, 21), DT(2026, 10, 21, 12, 0), 30, 0, 0)
        self.assertEqual(counts["scanned"], 1)
        self.assertEqual(hits[0]["at"], "9:50 AM")          # range 9:15 to 9:45 (high 101.4), the 9:45 bar closes 101.5
        self.assertEqual(hits[0]["or_high"], 101.4)
        self.assertEqual(hits[0]["bar_index"], 6)

    def test_a_special_short_session_closes_early(self):
        closes = [100, 101, 100.5, 100.8, 101.2, 100.9, 101.5] + [101.0] * 40      # 47 bars from 9:15
        s = bars_series("2026-11-12", closes, highs=[c + 0.2 for c in closes])
        with mock.patch.dict(scan.NSE_EARLY_CLOSE, {"2026-11-12": "a special short session"}):
            bars = scan.session_bars(s, D(2026, 11, 12), DT(2026, 11, 12, 12, 59))
            self.assertEqual(len(bars), 44)                # 9:15 to 12:50 done at 12:59; bars from 1:00 PM dropped
            bars = scan.session_bars(s, D(2026, 11, 12), DT(2026, 11, 12, 14, 0))
            self.assertEqual(len(bars), 45)                # 9:15 .. 12:55, the last bar before the 1:00 PM close
            self.assertTrue(scan.usable(bars, D(2026, 11, 12), DT(2026, 11, 12, 14, 0), 3))
            with self.assertRaises(scan.ScanError) as cm:
                scan.orb_window(scan.parse_now("2026-11-12 13:10"), None, 15, UNTIL)
            self.assertIn("Continuous trading ended at 1:00 PM IST", str(cm.exception))
        self.assertEqual(len(scan.session_bars(s, D(2026, 11, 12), DT(2026, 11, 12, 14, 0))), 47)  # a normal day

    def test_window_messages(self):
        def msg(now, at=None, until=UNTIL):
            with self.assertRaises(scan.ScanError) as cm:
                scan.orb_window(scan.parse_now(now), at, 15, until)
            return str(cm.exception)
        self.assertIn("No session on Sat 2026-10-17 (weekend)", msg("2026-10-17 10:15"))
        # Muhurat trading (Diwali Laxmi Pujan, a Sunday) is a special evening session: not a normal day, not supported
        self.assertIn("No session on Sun 2026-11-08 (NSE holiday: Diwali Laxmi Pujan (Muhurat trading session only))",
                      msg("2026-11-08 18:15"))
        self.assertIn("No session on Tue 2026-10-20 (NSE holiday: Dussehra)", msg("2026-10-20 10:15"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-19 09:20"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-19 09:34"))
        self.assertIn("opening range runs 9:15 AM to 9:30 AM", msg("2026-10-19 09:34"))
        self.assertIn("IST", msg("2026-10-19 09:34"))
        self.assertIn("expired", msg("2026-10-19 15:00"))                   # after the default 14:30
        self.assertIn("after 14:30", msg("2026-10-19 15:00"))
        self.assertIn("expired", msg("2026-10-19 14:31"))
        self.assertIn("expired", msg("2026-10-19 11:30", until=T(11, 0)))
        # F&O stocks stop continuous trading at 3:15 PM (closing auction), so the pack's day ends there
        self.assertIn("Continuous trading ended at 3:15 PM IST on Mon 2026-10-19", msg("2026-10-19 15:15", until=None))
        self.assertIn("Continuous trading ended at 3:15 PM IST", msg("2026-10-19 16:30", until=None))
        self.assertIn("later than now", msg("2026-10-19 10:00", at=T(10, 15)))
        self.assertIn("--at 15:20 is after continuous trading ends (3:15 PM IST)", msg("2026-10-19 17:00", at=T(15, 20)))
        self.assertIn("--at 16:30 is after continuous trading ends (3:15 PM IST)", msg("2026-10-19 17:00", at=T(16, 30)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 16:30"), T(10, 15), 15, UNTIL),
                         (D(2026, 10, 19), DT(2026, 10, 19, 10, 15)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 16:30"), T(14, 30), 15, UNTIL)[1],
                         DT(2026, 10, 19, 14, 30))                         # the last time for new entries is allowed
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 16:30"), T(15, 15), 15, None)[1],
                         DT(2026, 10, 19, 15, 15))                         # 3:15 PM itself: the 3:10 bar is complete
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 09:35"), None, 15, UNTIL)[1],
                         DT(2026, 10, 19, 9, 35))                          # the earliest run for a 15-minute range
        with self.assertRaises(scan.ScanError) as cm:                      # a 30-minute range: 9:15 + 30 + one bar
            scan.orb_window(scan.parse_now("2026-10-19 09:40"), None, 30, UNTIL)
        self.assertIn("Run orb again after 9:50 AM", str(cm.exception))
        with self.assertRaises(scan.ScanError):
            scan.orb_window(scan.parse_now("2026-10-19 10:15"), None, 7, UNTIL)


class LiveFiveMinute(unittest.TestCase):
    """A synthetic spark 5-minute answer and RELIANCE's v8 chart, 'fetched' at 10:01:47 IST on Wed Oct 21 2026."""

    def setUp(self):
        self.spark = scan.parse_spark(read_text(SPARK5))
        self.v8 = scan.parse_chart(read_text(RELIANCE5), "RELIANCE")
        self.now = scan.parse_now("2026-10-21T04:31:48Z")

    def test_in_progress_bar_and_live_tick_are_dropped(self):
        self.assertEqual(len(self.spark), 20)
        self.assertIn("M&M", self.spark)
        self.assertIn("BAJAJ-AUTO", self.spark)
        t = self.spark["RELIANCE"]
        self.assertEqual(len(t["t"]), 11)                  # 9:15 ... 10:00 (in progress) + the 10:01:47 tick
        self.assertEqual(scan.ts_to_ist(t["t"][-1]), DT(2026, 10, 21, 10, 1, 47))
        day, cut = scan.orb_window(self.now, None, 15, UNTIL)
        self.assertEqual((day, cut), (D(2026, 10, 21), DT(2026, 10, 21, 10, 1)))
        bars = scan.session_bars(t, day, cut)
        self.assertEqual([b["start"].strftime("%H:%M") for b in bars],
                         ["09:15", "09:20", "09:25", "09:30", "09:35", "09:40", "09:45", "09:50", "09:55"])
        self.assertEqual(len(scan.session_bars(self.v8, day, cut)), 9)

    def test_reliance_broke_its_range_at_ten(self):
        day, cut = scan.orb_window(self.now, None, 15, UNTIL)
        hits, counts, failed = scan.scan_orb(self.spark, ["RELIANCE", "YESBANK"], day, cut, 15, 50, 0,
                                             lambda s: self.v8)
        # range 9:15 to 9:30: high 1404.30 (the 9:20 bar), low 1392.30; the 9:55 bar closed 1405.80
        self.assertEqual(hits, [{"symbol": "RELIANCE", "setups": ["orb"], "price": 1405.8, "at": "10:00 AM",
                                 "bar_start": "09:55", "bar_index": 8, "or_high": 1404.3, "or_low": 1392.3,
                                 "pct_over": 0.11}])
        self.assertEqual(counts["illiquid"], 1)            # YESBANK under the Rs 50 floor
        self.assertEqual(failed, {})
        # on closes alone the 9:35, 9:45 and 9:50 bars already reached the range's top close, 1401.80 (stage 1 only
        # sees closes); only the 9:55 bar closed above the range HIGH, 1404.30
        t = scan.session_bars(self.spark["RELIANCE"], day, cut)
        self.assertTrue(scan.orb_candidate(t, 3))
        self.assertIsNone(scan.orb_hit(scan.session_bars(self.v8, day, DT(2026, 10, 21, 9, 55)), 3))   # not yet at 9:55

    def test_stage_two_cap_takes_the_most_traded(self):
        data, _ = scan.load_offline(WALL)
        spark_like = {k: dict(v, h=None, l=None) for k, v in data.items()}
        asked = []

        def confirm(sym):
            asked.append(sym)
            return data[sym]
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 0)
        hits, counts, _ = scan.scan_orb(spark_like, sorted(data), day, cut, 15, 0, 0, confirm, max_confirm=5)
        self.assertEqual((counts["candidates"], counts["confirmed"], counts["unconfirmed"]), (35, 5, 30))
        self.assertEqual(asked, ["RELIANCE", "ICICIBANK", "BAJFINANCE", "SBIN", "KOTAKBANK"])   # most rupees traded
        self.assertLessEqual(len(hits), 5)
        hits_all, _, _ = scan.scan_orb(spark_like, sorted(data), day, cut, 15, 0, 0, confirm, max_confirm=0)
        self.assertEqual(len(hits_all), 29)
        for h in hits:
            self.assertIn(h, hits_all)


class RealYahoo(unittest.TestCase):
    """Yahoo's REAL answers for Tue 2026-11-03, saved after the close by tests/fixtures/capture_real_fixtures.py:
    8 large caps, 5-minute and daily spark, plus RELIANCE's v8 chart. It is a session with the Closing Auction
    Session (since 3 Aug 2026): F&O stocks stop continuous trading at 15:15 and close in an auction."""

    DAY = D(2026, 11, 3)

    @classmethod
    def setUpClass(cls):
        cls.five = scan.parse_spark(read_text(REAL5))
        cls.daily = scan.parse_spark(read_text(REAL3))
        cls.v8 = scan.parse_chart(read_text(REAL_V8), "RELIANCE")

    def starts(self, s):
        return [scan.ts_to_ist(t).strftime("%H:%M") for t, c in zip(s["t"], s["c"]) if c is not None]

    def usable_at(self, sym, hh, mm):
        cut = DT(2026, 11, 3, hh, mm)
        return scan.usable(scan.session_bars(self.five[sym], self.DAY, cut), self.DAY, cut, 3)

    def test_the_answer_holds_the_auction_shape_and_the_tools_read_9_15_to_3_10_only(self):
        self.assertEqual(sorted(self.five), ["BAJAJ-AUTO", "HDFCBANK", "INFY", "ITC", "M&M", "RELIANCE", "SBIN", "TCS"])
        r = self.five["RELIANCE"]
        self.assertEqual(len(r["t"]), 74)                  # 72 bars to 3:10 PM, the 3:15 stub and the 3:25 auction
        self.assertEqual(self.starts(r)[:2], ["09:15", "09:20"])
        self.assertEqual(self.starts(r)[-3:], ["15:10", "15:15", "15:25"])      # there is no 3:20 bar
        self.assertEqual(r["c"][-3:], [1301.0, 1300.8, 1307.9])
        self.assertEqual(r["meta"]["regularMarketPrice"], 1307.9)               # the auction price is the close
        self.assertEqual((r["meta"]["currency"], r["meta"]["timezone"], r["meta"]["gmtoffset"]),
                         ("INR", "IST", 19800))
        cut = DT(2026, 11, 3, 16, 0)
        bars = scan.session_bars(r, self.DAY, cut)
        self.assertEqual((len(bars), bars[0]["start"].strftime("%H:%M"), bars[-1]["start"].strftime("%H:%M")),
                         (72, "09:15", "15:10"))
        self.assertTrue(scan.usable(bars, self.DAY, cut, 3))
        self.assertEqual(bars[-1]["c"], 1301.0)           # the 3:15 stub (1300.8) and the auction (1307.9) are not read

    def test_the_auction_print_is_never_a_breakout(self):
        """RELIANCE's range (9:15 to 9:30) was 1284.30 to 1306.60. Its auction closed at 1307.90, above the high:
        a tool that read the 3:25 PM bar would report a breakout at 3:30 PM. Nothing in the pack's day closed above."""
        v = self.v8
        self.assertEqual(len(v["t"]), 75)
        self.assertEqual([None if c is None else round(c, 2) for c in v["c"][-3:]], [1300.8, None, 1307.9])
        self.assertIsNone(v["c"][-2])                      # the 3:20 PM bar is a null in the v8 chart
        range_high = max(v["h"][:3])
        self.assertEqual(round(range_high, 2), 1306.6)
        self.assertEqual(round(min(v["l"][:3]), 2), 1284.3)
        self.assertEqual(round(v["c"][-1], 2), 1307.9)
        self.assertGreater(v["c"][-1], range_high)         # what a 75-bar tool would have taken for a breakout
        self.assertLessEqual(max(c for c in v["c"][3:72]), range_high)       # but no bar to 3:10 PM did
        for hh, mm in ((10, 15), (14, 30), (15, 15), (16, 0)):
            hits, counts, _ = scan.scan_orb({"RELIANCE": v}, ["RELIANCE"], self.DAY, DT(2026, 11, 3, hh, mm), 15, 0, 0)
            self.assertEqual((hits, counts["scanned"], counts["unusable"]), ([], 1, 0), (hh, mm))

    def test_real_5_minute_gaps_make_a_chart_unusable(self):
        """Yahoo leaves 5-minute bars out of a real answer. ITC lacks 9:30 (the first bar after the range), 11:00 and
        12:35; M&M lacks 11:20; BAJAJ-AUTO lacks 14:00. A chart with a bar missing before the cutoff is skipped."""
        want = [scan.slot(self.DAY, k).strftime("%H:%M") for k in range(72)]
        missing = {sym: [w for w in want if w not in self.starts(s)] for sym, s in self.five.items()}
        self.assertEqual(missing, {"BAJAJ-AUTO": ["14:00"], "HDFCBANK": [], "INFY": [], "ITC": ["09:30", "11:00", "12:35"],
                                   "M&M": ["11:20"], "RELIANCE": [], "SBIN": [], "TCS": []})
        self.assertEqual([self.usable_at("ITC", 9, 35), self.usable_at("ITC", 10, 15)], [False, False])
        self.assertEqual([self.usable_at("M&M", 11, 20), self.usable_at("M&M", 11, 25)], [True, False])
        self.assertEqual([self.usable_at("BAJAJ-AUTO", 14, 0), self.usable_at("BAJAJ-AUTO", 14, 5)], [True, False])
        for sym in ("RELIANCE", "TCS", "HDFCBANK", "INFY", "SBIN"):
            self.assertTrue(self.usable_at(sym, 15, 15), sym)

    def test_the_stage_one_scan_of_the_real_answer_counts_the_skipped_charts(self):
        day, cut = scan.orb_window(scan.parse_now("2026-11-03 10:15"), None, 15, UNTIL)
        hits, counts, failed = scan.scan_orb(self.five, sorted(self.five), day, cut, 15, 50, 0)
        self.assertEqual((counts["fetched"], counts["unusable"], counts["scanned"]), (8, 1, 7))   # ITC: no 9:30 bar
        self.assertEqual(failed, {})
        self.assertEqual(hits, [])                         # closes only: highs and lows come from stage 2

    def test_the_newest_daily_close_is_empty_and_comes_from_the_quote(self):
        r = self.daily["RELIANCE"]
        self.assertEqual(len(r["t"]), 64)
        self.assertEqual(scan.ts_to_ist(r["t"][-1]), DT(2026, 11, 3, 9, 15))
        self.assertIsNone(r["c"][-1])                      # Tuesday's bar has no close, even hours after the session
        self.assertEqual(r["c"][-3:-1], [1164.9, 1300.0])
        self.assertEqual(r["meta"]["regularMarketPrice"], 1307.9)
        self.assertEqual(scan.ts_to_ist(r["meta"]["regularMarketTime"]), DT(2026, 11, 3, 15, 29, 39))
        self.assertEqual(scan.quote_close(r), (self.DAY, 1307.9))
        # during the day Tuesday's bar is not final, and until 4 PM it stays out
        bars = scan.daily_closes(r, scan.parse_now("2026-11-03 15:59"))
        self.assertEqual((len(bars), bars[-1]), (63, (D(2026, 11, 2), 1300.0)))
        # from 4 PM the quote is Tuesday's close
        fixed = []
        bars = scan.daily_closes(r, scan.parse_now("2026-11-03 16:00"), fixed)
        self.assertEqual((len(bars), bars[-1], fixed), (64, (self.DAY, 1307.9), [self.DAY]))
        bars = scan.daily_closes(r, scan.parse_now("2026-11-04 08:45"))     # the next morning, before the open
        self.assertEqual((bars[-2], bars[-1]), ((D(2026, 11, 2), 1300.0), (self.DAY, 1307.9)))

    def test_a_pre_market_scan_uses_tuesdays_close(self):
        hits, counts, last = scan.scan_daily(self.daily, sorted(self.daily), scan.parse_now("2026-11-04 08:45"), 50,
                                             500000)
        self.assertEqual(last, self.DAY)
        self.assertEqual(counts, {"requested": 8, "fetched": 8, "illiquid": 1, "short": 0, "scanned": 7,
                                  "quote_closes": 7, "hits": 4})        # BAJAJ-AUTO: 4,97,022 shares, under 5,00,000
        self.assertEqual([(h["symbol"], h["setups"], h["price"], h["rsi"]) for h in hits],
                         [("RELIANCE", ["breakout_20d"], 1307.9, 66.6), ("HDFCBANK", ["reclaim_ma20"], 725.5, 58.4),
                          ("TCS", ["bull_flag"], 2499.1, 66.1), ("ITC", ["breakout_20d"], 273.9, 61.3)])
        # read before 4 PM Tuesday the bar is not there: the scan is one session older and says so
        hits, counts, last = scan.scan_daily(self.daily, sorted(self.daily), scan.parse_now("2026-11-03 15:59"), 50,
                                             500000)
        self.assertEqual((last, counts["quote_closes"]), (D(2026, 11, 2), 0))

    def test_the_real_setups_by_hand(self):
        def closes(sym):
            s = self.daily[sym]
            return [c for c in s["c"][:-1] if c is not None] + [s["meta"]["regularMarketPrice"]]
        c = closes("RELIANCE")                             # 1307.90 over the best of the 20 closes before it, 1300.00
        self.assertEqual((c[-1], max(c[-21:-1])), (1307.9, 1300.0))
        c = closes("ITC")                                  # 273.90 over 268.90
        self.assertEqual((c[-1], max(c[-21:-1])), (273.9, 268.9))
        c = closes("HDFCBANK")                             # 700.00 under its 20-day average, then 725.50 over the new one
        ma_prev, ma = sum(c[-21:-1]) / 20.0, sum(c[-20:]) / 20.0
        self.assertEqual((c[-2], round(ma_prev, 2), c[-1], round(ma, 2)), (700.0, 701.96, 725.5, 703.0))
        c = closes("TCS")                                  # up 17.5 % in the 10 sessions before the last 5; those 5 in 1.7 %
        self.assertEqual(round((c[-6] / c[-16] - 1) * 100, 1), 17.5)
        self.assertEqual(round((max(c[-5:]) - min(c[-5:])) / c[-1] * 100, 1), 1.7)
        self.assertEqual(c[-5:], [2460.0, 2478.0, 2502.6, 2501.7, 2499.1])

    def test_the_quote_repairs_a_null_close_only_when_it_is_a_closing_price(self):
        base = {"t": [ts("2026-11-02 09:15"), ts("2026-11-03 09:15")], "c": [100.0, None]}
        closing = {"regularMarketTime": ts("2026-11-03 15:29"), "regularMarketPrice": 105.5}
        monday = [(D(2026, 11, 2), 100.0)]

        def read(meta, now="2026-11-04 08:45", series=base):
            fixed = []
            return scan.daily_closes(dict(series, meta=meta), scan.parse_now(now), fixed), fixed
        self.assertEqual(read(closing), (monday + [(D(2026, 11, 3), 105.5)], [D(2026, 11, 3)]))
        self.assertEqual(read(dict(closing, regularMarketTime=ts("2026-11-03 15:15")))[0][-1], (D(2026, 11, 3), 105.5))
        self.assertEqual(read(dict(closing, regularMarketTime=ts("2026-11-03 15:14")))[0], monday)   # a live price
        self.assertEqual(read(dict(closing, regularMarketTime=ts("2026-11-02 15:29")))[0], monday)   # another day's
        self.assertEqual(read(dict(closing, regularMarketPrice=None))[0], monday)
        self.assertEqual(read({})[0], monday)
        self.assertEqual(read(closing, now="2026-11-03 15:40")[0], monday)    # that day is not over: its bar is out
        both = {"t": [ts("2026-11-03 09:15"), ts("2026-11-03 09:15")], "c": [None, 101.0]}
        self.assertEqual(read(closing, series=both), ([(D(2026, 11, 3), 101.0)], []))   # a real close wins
        both = {"t": [ts("2026-11-03 09:15"), ts("2026-11-03 09:15")], "c": [101.0, None]}
        self.assertEqual(read(closing, series=both), ([(D(2026, 11, 3), 101.0)], []))   # and is not overwritten


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = os.path.join(self.d, "trader")
        self.wl = os.path.join(self.t, "watchlist.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_universe_daily_orb_end_to_end(self):
        uni = os.path.join(self.t, "universe.txt")
        code, text, _ = run(["universe", "--offline", SYMDIR, "--out", uni])
        self.assertEqual(code, 0)
        self.assertIn("NSE equity list (EQUITY_L.csv): 20 rows.", text)
        self.assertIn("Kept 16 NSE stocks in series EQ.", text)
        self.assertIn("Dropped: 2 series BE, 2 series BZ.", text)
        with open(uni, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 16)
        self.assertIn("M&M|Mahindra & Mahindra Limited", lines)
        self.assertIn("BAJAJ-AUTO|Bajaj Auto Limited", lines)
        self.assertIn("20MICRONS|20 Microns Limited", lines)
        self.assertNotIn("3IINFOLTD", " ".join(lines))

        # daily over that universe with the fixture charts: 10 of the 16 are in both, so partial
        code, text, _ = run(["daily", "--universe", uni, "--offline", DAILY, "--now", "2026-10-19 09:00",
                             "--out", self.wl, "--min-volume", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Fetched 10 of 16 charts (6 failed or had no data)", text)
        self.assertIn("8 passed the liquidity filter (₹50+ and 0+ shares)", text)    # YESBANK and IDEA under Rs 50
        self.assertIn("RELIANCE", text)
        self.assertIn("₹1,376.70", text)
        doc = self.read(self.wl)
        self.assertEqual((doc["mode"], doc["universe"], doc["scanned"], doc["failed"]), ("daily", 16, 8, 6))
        self.assertEqual([h["symbol"] for h in doc["hits"]],
                         ["HDFCBANK", "RELIANCE", "SBIN", "INFY", "TCS", "M&M", "ITC", "BAJAJ-AUTO"])
        self.assertIn({"symbol": "M&M", "setups": ["bull_flag"], "price": 3300.0, "close_date": "2026-10-16",
                       "rsi": 72.0, "volume": 587264}, doc["hits"])
        self.assertEqual(doc["as_of"], "2026-10-16 close")
        self.assertEqual(doc["filters"], {"min_price": 50.0, "min_volume": 0})

        # the 9:35 scan
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-19 09:35", "--out", self.wl])
        self.assertEqual(code, 0)
        flat = squash(text)
        self.assertIn("Opening-range breakout scan, Mon 2026-10-19 9:35 AM IST: 15-minute range (9:15 to 9:30 AM)",
                      flat)
        self.assertIn("16 of 55 charts closed above their opening-range high by 9:35 AM", flat)
        self.assertIn("2 charts skipped for a missing 5-minute bar between 9:15 and 9:35 AM", flat)
        self.assertIn("9:35 AM RELIANCE ₹1,416.30 range 1,399.50 to 1,413.40 +0.21% over", flat)
        self.assertIn("9:35 AM ULTRACEMCO ₹12,006.00 range 11,925.00 to 11,989.00 +0.14% over", flat)
        self.assertIn("9:35 AM SUZLON ₹56.24 range 55.86 to 56.22 +0.04% over", flat)
        self.assertIn("not a list of stocks to buy", text)
        doc = self.read(self.wl)
        self.assertEqual({k: doc[k] for k in ("created_by", "created_at", "mode", "as_of", "universe", "scanned",
                                              "source")},
                         {"created_by": "trader-scanner", "created_at": "2026-10-19T04:05:00Z", "mode": "orb",
                          "as_of": "2026-10-19 09:35 IST", "universe": 61, "scanned": 55, "source": "yahoo"})
        self.assertEqual(len(doc["hits"]), 16)
        self.assertEqual(doc["filters"], {"min_price": 50.0, "min_volume": 0})
        self.assertIn({"symbol": "RELIANCE", "setups": ["orb"], "price": 1416.30, "at": "9:35 AM",
                       "bar_start": "09:30", "bar_index": 3, "or_high": 1413.4, "or_low": 1399.50,
                       "pct_over": 0.21}, doc["hits"])

        # the hits read back as a watchlist, with --at instead of --now
        code, text, _ = run(["orb", "--offline", WALL, "--at", "09:35", "--watchlist", self.wl, "--json",
                             "--no-write"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], len(out["hits"])), (16, 16, 16))
        self.assertEqual(out["as_of"], "2026-10-19 09:35 IST")

    def test_universe_index_option(self):
        uni = os.path.join(self.t, "nifty.txt")
        code, text, _ = run(["universe", "--offline", SYMDIR, "--index", "nifty500", "--out", uni])
        self.assertEqual(code, 0)
        self.assertIn("NSE Nifty 500 list (ind_nifty500list.csv): 12 rows.", text)
        self.assertIn("Kept 12 NSE stocks in series EQ.", text)
        self.assertNotIn("Dropped", text)
        with open(uni, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(lines[0], "360ONE|360 ONE WAM Ltd.")
        self.assertIn("M&M|Mahindra & Mahindra Ltd.", lines)
        self.assertEqual(len(lines), 12)
        code, text, _ = run(["universe", "--offline", SYMDIR, "--index", "nifty500", "--json", "--no-write"])
        out = json.loads(text)
        self.assertEqual((out["count"], out["index"], out["source"], out["out"]),
                         (12, "nifty500", "ind_nifty500list.csv", None))
        # the list for another index is not in the folder
        code, _, err = run(["universe", "--offline", SYMDIR, "--index", "nifty50", "--out", uni])
        self.assertEqual(code, 1)
        self.assertIn("no ind_nifty50list.csv in", err)
        with self.assertRaises(SystemExit):                       # argparse: not one of the four Nifty lists
            run(["universe", "--offline", SYMDIR, "--index", "sensex"])

    def test_universe_from_the_network(self):
        urls = []
        text = read_text(os.path.join(SYMDIR, "EQUITY_L.csv"))
        idx = read_text(os.path.join(SYMDIR, "ind_nifty500list.csv"))
        old = scan.http_get

        def fake(url, timeout=20):
            urls.append(url)
            return idx if "ind_nifty" in url else text
        scan.http_get = fake
        try:
            code, out, _ = run(["universe", "--no-write"])
            self.assertEqual(code, 0)
            code, out2, _ = run(["universe", "--no-write", "--index", "nifty100"])
        finally:
            scan.http_get = old
        self.assertEqual(urls, ["https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
                                "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv"])
        self.assertIn("Kept 16", out)
        self.assertIn("Nifty 100 list", out2)

        def refused(url, timeout=20):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b""))
        scan.http_get = refused
        try:
            code, _, err = run(["universe", "--no-write"])
        finally:
            scan.http_get = old
        self.assertEqual(code, 1)
        self.assertIn("answered HTTP 403 for EQUITY_L.csv", err)
        self.assertNotIn("Traceback", err)

    def test_partial_stage_two_is_said(self):
        """The networked path with a fake Yahoo that answers from the Oct 19 bundle."""
        bundle = json.loads(scan._read_maybe_gz(WALL))
        calls = []

        def get(path):
            calls.append(path)
            if path.startswith("/v7/finance/spark"):
                res = []
                for y in path.split("symbols=")[1].split("&range=")[0].split(","):
                    ysym = scan.urllib.parse.unquote(y)
                    r = ((bundle.get(scan.plain_symbol(ysym)) or {}).get("chart") or {}).get("result")
                    if r:
                        closes = r[0]["indicators"]["quote"][0].get("close", [])
                        res.append({"symbol": ysym, "response": [dict(r[0], indicators={"quote": [{"close": closes}]})]})
                return json.dumps({"spark": {"result": res, "error": None}})
            ysym = scan.urllib.parse.unquote(path.split("/chart/")[1].split("?")[0])
            return json.dumps(bundle[scan.plain_symbol(ysym)])
        old_get, old_sleep, real_now = scan.yahoo_get, scan.SLEEP, scan.parse_now
        scan.yahoo_get, scan.SLEEP = get, (lambda s: None)
        scan.parse_now = lambda text: real_now(text or "2026-10-19 10:05")  # the machine clock, pinned
        uni = os.path.join(self.d, "u.txt")
        with open(uni, "w") as fh:
            fh.write("\n".join(sorted(bundle)) + "\n")
        try:
            code, text, _ = run(["orb", "--universe", uni, "--now", "2026-10-19 10:00", "--min-price", "0",
                                 "--max-confirm", "5", "--no-write"])
        finally:
            scan.yahoo_get, scan.SLEEP, scan.parse_now = old_get, old_sleep, real_now
        self.assertEqual(code, 0)
        self.assertIn("58 usable; 35 candidates, 5 confirmed", squash(text))
        self.assertIn("PARTIAL: 30 more candidates were not checked", text)
        self.assertIn("5 of the 5 checked charts closed above their opening-range high by 10:00 AM", text)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v8/")), 5)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v7/")), 4)    # 61 symbols, 20 a request
        self.assertTrue(all("range=1d&interval=5m" in c for c in calls))
        spark_calls = [c for c in calls if c.startswith("/v7/")]
        self.assertTrue(any("M%26M.NS" in c for c in spark_calls))             # the & is quoted, not a query separator
        self.assertTrue(any("BAJAJ-AUTO.NS" in c for c in spark_calls))        # the hyphen is kept
        self.assertFalse(any("BAJAJ.AUTO" in c for c in calls))
        self.assertTrue(all("RELIANCE.NS" in c for c in calls if c.startswith("/v8/RELIANCE")))
        self.assertIn("1 failed or had no data", text)                          # MINDTREE: Yahoo has no such chart

    def test_plain_messages_and_exit_codes(self):
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-17 10:15", "--out", self.wl])
        self.assertEqual(code, 1)
        self.assertIn("No session on Sat 2026-10-17 (weekend)", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.wl))
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-20 10:15", "--out", self.wl])   # Dussehra
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Dussehra", err)
        self.assertFalse(os.path.exists(self.wl))
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-19 09:24", "--out", self.wl])
        self.assertIn("after 9:35 AM", err)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2027-01-04 10:15", "--out", self.wl])
        self.assertEqual(code, 1)
        self.assertIn("covers 2026 only", err)
        cfg = os.path.join(self.t, "config.json")
        os.makedirs(self.t)
        with open(cfg, "w") as fh:
            json.dump({"no_entries_after": "10:00"}, fh)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-19 10:15", "--config", cfg])
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-19 14:50"])    # default no_entries_after 14:30
        self.assertEqual(code, 1)
        self.assertIn("after 14:30", err)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-19 10:15", "--until", "25:00"])
        self.assertEqual(code, 1)
        self.assertIn("--until wants an IST time", err)
        code, _, err = run(["orb", "--offline", WALL])
        self.assertIn("--at HH:MM", err)
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-19 09:35", "--symbols", "TCS",
                             "--out", self.wl])
        self.assertEqual(code, 1)                          # no breakout yet, but the empty list is written
        self.assertIn("No chart closed above its opening-range high by 9:35 AM", text)
        self.assertEqual(self.read(self.wl)["hits"], [])
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-19 09:35", "--symbols", "not a symbol!"])
        self.assertEqual(code, 1)
        self.assertIn("is not an NSE symbol", err)
        code, _, err = run(["daily", "--universe", os.path.join(self.d, "nope.txt")])
        self.assertEqual(code, 1)
        self.assertIn("scan.py universe", err)
        code, _, err = run(["daily", "--offline", os.path.join(self.d, "nothing-here")])
        self.assertIn("no offline file", err)
        empty = os.path.join(self.d, "empty.json")
        with open(empty, "w") as fh:
            fh.write("{}")
        code, _, err = run(["daily", "--offline", empty])
        self.assertEqual(code, 1)
        self.assertIn("no symbols to scan", err)

    def test_a_year_without_a_holiday_list_is_said_for_daily_too(self):
        code, text, err = run(["daily", "--offline", DAILY, "--now", "2027-01-04 09:00", "--no-write",
                               "--min-volume", "0"])
        self.assertIn("covers 2026 only", err)
        self.assertIn("nseindia.com", err)
        code, text, err = run(["daily", "--offline", DAILY, "--now", "2027-01-04 09:00", "--no-write", "--json",
                               "--min-volume", "0"])
        self.assertEqual(err, "")                                              # --json stays machine-readable

    def test_offline_folder_of_v8_files(self):
        folder = os.path.join(self.d, "charts")
        os.makedirs(folder)
        bundle = json.loads(scan._read_maybe_gz(WALL))
        # the file stem is the symbol: plain, with the .NS suffix, or with & and -
        for sym, fname in (("RELIANCE", "RELIANCE.json"), ("TCS", "TCS.NS.json"), ("BAJAJ-AUTO", "BAJAJ-AUTO.json"),
                           ("M&M", "M&M.json"), ("MINDTREE", "MINDTREE.json")):
            with open(os.path.join(folder, fname), "w") as fh:
                json.dump(bundle[sym], fh)
        data, errors = scan.load_offline(folder)
        self.assertEqual(sorted(data), ["BAJAJ-AUTO", "M&M", "RELIANCE", "TCS"])
        self.assertEqual(list(errors), ["MINDTREE"])
        code, text, _ = run(["orb", "--offline", folder, "--now", "2026-10-19 10:00", "--no-write", "--json"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], out["failed"]), (5, 4, 1))
        self.assertEqual([h["symbol"] for h in out["hits"]], ["RELIANCE", "BAJAJ-AUTO", "TCS"])
        self.assertEqual(code, 0)

    def test_a_chart_file_without_a_name_is_named_by_its_meta_symbol(self):
        bundle = json.loads(scan._read_maybe_gz(WALL))
        path = os.path.join(self.d, "somefile.json")
        with open(path, "w") as fh:
            json.dump(bundle["M&M"], fh)
        data, _ = scan.load_offline(path)
        self.assertEqual(list(data), ["M&M"])                    # meta.symbol "M&M.NS" -> M&M
        self.assertEqual(data["M&M"]["meta"]["symbol"], "M&M.NS")

    def test_symbol_lists(self):
        txt = os.path.join(self.d, "list.txt")
        with open(txt, "w") as fh:
            fh.write("# my list\nRELIANCE|Reliance Industries Limited\nm&m\n\nRELIANCE\ntcs.ns|Tata Consultancy\n")
        self.assertEqual(scan.symbols_from_file(txt), ["RELIANCE", "M&M", "RELIANCE", "TCS"])
        js = os.path.join(self.d, "wl.json")
        with open(js, "w") as fh:
            json.dump({"hits": [{"symbol": "reliance"}, {"symbol": "M&M"}]}, fh)
        self.assertEqual(scan.symbols_from_file(js), ["RELIANCE", "M&M"])
        code, text, _ = run(["daily", "--offline", DAILY, "--watchlist", txt, "--now", "2026-10-19 09:00",
                             "--json", "--no-write", "--min-volume", "0"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"]), (3, 3))        # RELIANCE, M&M, TCS (the duplicate is dropped)
        code, text, _ = run(["daily", "--offline", DAILY, "--limit", "3", "--now", "2026-10-19 09:00", "--json",
                             "--no-write", "--min-volume", "0"])
        self.assertEqual(json.loads(text)["universe"], 3)
        code, text, _ = run(["daily", "--offline", DAILY, "--symbols", "m&m, reliance.ns", "--now",
                             "2026-10-19 09:00", "--json", "--no-write"])
        out = json.loads(text)
        self.assertEqual((out["universe"], sorted(h["symbol"] for h in out["hits"])), (2, ["M&M", "RELIANCE"]))
        self.assertEqual(out["filters"], {"min_price": 50.0, "min_volume": 500000})

    def test_daily_json(self):
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-10-19 09:00", "--json", "--no-write",
                             "--min-volume", "0", "--min-price", "0"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(len(out["hits"]), 14)
        self.assertEqual(out["note"], "A scan is a list of charts that match a rule, not a list of stocks to buy.")
        self.assertEqual(out["as_of"], "2026-10-16 close")

    def test_a_real_pre_market_scan_says_where_the_newest_close_came_from(self):
        code, text, _ = run(["daily", "--offline", REAL3, "--now", "2026-11-04 08:45", "--no-write"])
        self.assertEqual(code, 0)
        flat = squash(text)
        self.assertIn("Daily scan, Wed 2026-11-04 08:45 IST, closes through 2026-11-03", flat)
        self.assertIn("Fetched 8 of 8 charts. 7 passed the liquidity filter (\u20b950+ and 5,00,000+ shares); 7 scanned.",
                      flat)
        self.assertIn("Yahoo left the newest daily close empty for 7 of those charts", flat)
        self.assertIn("RELIANCE \u20b91,307.90 RSI 66.6 20-day closing-high breakout", flat)
        code, text, _ = run(["daily", "--offline", REAL3, "--now", "2026-11-04 08:45", "--no-write", "--json"])
        self.assertEqual(json.loads(text)["counts"]["quote_closes"], 7)
        code, text, _ = run(["daily", "--offline", REAL3, "--now", "2026-11-03 15:59", "--no-write"])
        self.assertIn("closes through 2026-11-02", text)                       # Tuesday's bar is not final yet
        self.assertNotIn("left the newest daily close empty", text)

    def test_a_real_orb_scan_counts_the_chart_skipped_for_a_missing_bar(self):
        code, text, _ = run(["orb", "--offline", REAL5, "--now", "2026-11-03 10:15", "--no-write"])
        flat = squash(text)
        self.assertIn("Fetched 8 of 8 charts; 7 usable", flat)
        self.assertIn("1 chart skipped for a missing 5-minute bar between 9:15 and 10:15 AM", flat)

    def test_daily_defaults_are_the_indian_floors(self):
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-10-19 11:14", "--no-write"])
        self.assertEqual(code, 0)
        flat = squash(text)
        self.assertIn("Daily scan, Mon 2026-10-19 11:14 IST, closes through 2026-10-16", flat)
        self.assertIn("Fetched 20 of 20 charts. 15 passed the liquidity filter (₹50+ and 5,00,000+ shares); 15 scanned.",
                      flat)
        self.assertIn("10 charts matched a setup", flat)
        self.assertIn("HDFCBANK ₹960.00 RSI 62.6 20-day closing-high breakout", flat)
        self.assertNotIn("YESBANK", text)                       # under Rs 50
        self.assertNotIn("$", text)

    def test_help_text_is_indian(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), self.assertRaises(SystemExit):
            scan.main(["orb", "--help"])
        text = out.getvalue()
        self.assertIn("IST", text)
        self.assertIn("14:30", text)
        self.assertIn("(50)", text)                             # --min-price default 50
        for word in ("New York", "Nasdaq", " ET", "$"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
