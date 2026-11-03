import datetime
import gzip
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

from _load import load, ROOT, FIX

alerts = load("trader-alerts", "alerts")

# SYNTHETIC Yahoo-shaped quote files and the canonical plan (tests/fixtures/make_alerts_fixtures.py), not real data
QDIR = os.path.join(FIX, "trader-alerts")
PLAN = os.path.join(QDIR, "plan-reliance.json")
DT = datetime.datetime
RS = "₹"

# NSE capital-market trading holidays 2026, exactly as NSE publishes them (api/holiday-master, segment CM)
NSE_2026 = ["2026-01-15", "2026-01-26", "2026-02-15", "2026-03-03", "2026-03-21", "2026-03-26", "2026-03-31",
            "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-08-15", "2026-09-14",
            "2026-10-02", "2026-10-20", "2026-11-08", "2026-11-10", "2026-11-24", "2026-12-25"]
SHORT = {"2026-10-19": ("13:15", "a special short session")}      # injected: NSE_EARLY_CLOSE is empty today


def ist(text):
    return alerts.parse_now(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def plan_rules():
    with open(PLAN) as fh:
        return alerts.rules_from_plan(json.load(fh))


class Calendar(unittest.TestCase):
    def test_holidays_match_the_list(self):
        self.assertEqual(sorted(alerts.NSE_HOLIDAYS), NSE_2026)
        self.assertEqual(alerts.NSE_HOLIDAYS["2026-10-20"], "Dussehra")
        self.assertEqual(alerts.NSE_EARLY_CLOSE, {})          # no early close on a normal NSE day
        self.assertEqual(alerts.CALENDAR_YEARS, (2026,))
        # four of the twenty fall on a weekend (Mahashivratri, Id-Ul-Fitr, Independence Day, Diwali Laxmi Pujan)
        weekend = [d for d in alerts.NSE_HOLIDAYS if datetime.date.fromisoformat(d).weekday() >= 5]
        self.assertEqual(weekend, ["2026-02-15", "2026-03-21", "2026-08-15", "2026-11-08"])
        self.assertIn("Muhurat", alerts.NSE_HOLIDAYS["2026-11-08"])

    def test_session_constants(self):
        # the pack's day ends at 15:15 (continuous trading of F&O stocks, then the closing auction); NSE's is 15:30
        self.assertEqual((alerts.OPEN, alerts.CLOSE, alerts.MARKET_CLOSE),
                         (datetime.time(9, 15), datetime.time(15, 15), datetime.time(15, 30)))

    def test_ist_has_no_daylight_saving(self):
        for day in ("2026-03-02", "2026-07-01", "2026-11-02"):
            utc = DT.fromisoformat(day + "T03:45:00")
            self.assertEqual(alerts.to_ist(utc), DT.fromisoformat(day + "T09:15:00"))
            self.assertEqual(alerts.ist_to_utc(alerts.to_ist(utc)), utc)

    def test_parse_now(self):
        self.assertEqual(ist("2026-10-13T09:30"), DT(2026, 10, 13, 4, 0))
        self.assertEqual(ist("2026-10-19 10:15"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(ist("2026-12-01T09:30"), DT(2026, 12, 1, 4, 0))
        self.assertEqual(ist("2026-10-13T20:00:01Z"), DT(2026, 10, 13, 20, 0, 1))
        self.assertEqual(ist("2026-10-13T20:00:01+05:30"), DT(2026, 10, 13, 14, 30, 1))
        with self.assertRaises(alerts.AlertError) as cm:
            ist("tomorrow")
        self.assertIn("IST", str(cm.exception))

    def test_market_status(self):
        st = alerts.market_status(ist("2026-10-13T10:00"))
        self.assertTrue(st["open"])
        self.assertTrue(st["trading_open"])
        self.assertEqual((st["opens"], st["closes"], st["market_closes"]), ("09:15", "15:15", "15:30"))
        self.assertIn("now_ist", st)
        self.assertNotIn("now_new_york", st)
        self.assertEqual(alerts.market_status(ist("2026-10-19 10:15"))["now_ist"], "2026-10-19 10:15")
        self.assertFalse(alerts.market_status(ist("2026-10-13T09:14"))["open"])
        self.assertEqual(alerts.market_status(ist("2026-10-13T09:14"))["next_open"], "2026-10-13 09:15")
        self.assertTrue(alerts.market_status(ist("2026-10-13T09:15"))["open"])
        self.assertTrue(alerts.market_status(ist("2026-10-13T15:14"))["trading_open"])
        # 15:15 to 15:30: NSE is still open (the closing auction for F&O stocks, normal trading for the rest), but
        # this pack's trading day is over
        auction = alerts.market_status(ist("2026-10-13T15:15"))
        self.assertEqual((auction["open"], auction["trading_open"]), (True, False))
        self.assertIn("closing auction", auction["reason"])
        self.assertIn("trading day is over", auction["reason"])
        self.assertTrue(alerts.market_status(ist("2026-10-13T15:29"))["open"])
        after = alerts.market_status(ist("2026-10-13T15:30"))
        self.assertEqual((after["open"], after["trading_open"]), (False, False))
        self.assertEqual(after["reason"], "after the close")
        self.assertEqual(after["next_open"], "2026-10-14 09:15")
        friday = alerts.market_status(ist("2026-10-16T15:31"))
        self.assertEqual(friday["next_open"], "2026-10-19 09:15")

    def test_a_holiday_2026_10_20_is_closed(self):
        dussehra = alerts.market_status(ist("2026-10-20T11:00"))
        self.assertFalse(dussehra["open"])
        self.assertEqual(dussehra["reason"], "NSE holiday: Dussehra")
        self.assertEqual(dussehra["next_open"], "2026-10-21 09:15")
        self.assertNotIn("opens", dussehra)
        self.assertTrue(alerts.market_status(ist("2026-10-19T11:00"))["open"])      # the day before is a normal session
        self.assertTrue(alerts.market_status(ist("2026-10-21T11:00"))["open"])
        diwali = alerts.market_status(ist("2026-11-10T10:00"))
        self.assertIn("Diwali-Balipratipada", diwali["reason"])
        self.assertEqual(diwali["next_open"], "2026-11-11 09:15")
        xmas = alerts.market_status(ist("2026-12-25T10:00"))
        self.assertFalse(xmas["open"])
        self.assertEqual(xmas["next_open"], "2026-12-28 09:15")

    def test_weekends_are_closed(self):
        sat = alerts.market_status(ist("2026-10-17T10:00"))
        self.assertEqual((sat["open"], sat["reason"]), (False, "weekend"))
        self.assertEqual(sat["next_open"], "2026-10-19 09:15")
        # Muhurat trading (Sunday 8 Nov 2026, Diwali Laxmi Pujan) is a special session this pack does not support:
        # closed here, and the holiday list names it even though it falls on a weekend
        sun = alerts.market_status(ist("2026-11-08T18:30"))
        self.assertEqual((sun["open"], sun["reason"]),
                         (False, "NSE holiday: Diwali Laxmi Pujan (Muhurat trading session only)"))
        shivratri = alerts.market_status(ist("2026-02-15T10:00"))
        self.assertEqual((shivratri["open"], shivratri["reason"]), (False, "NSE holiday: Mahashivratri"))
        self.assertEqual(alerts.market_status(ist("2026-02-22T10:00"))["reason"], "weekend")

    def test_the_holiday_list_covers_2026_only(self):
        self.assertTrue(alerts.market_status(ist("2026-10-13T10:00"))["calendar_known"])
        self.assertFalse(alerts.market_status(ist("2027-01-05T10:00"))["calendar_known"])
        self.assertFalse(alerts.market_status(ist("2028-03-01T10:00"))["calendar_known"])

    def test_a_listed_short_session_moves_the_close(self):
        with mock.patch.object(alerts, "NSE_EARLY_CLOSE", SHORT):
            st = alerts.market_status(ist("2026-10-19T13:14"))
            self.assertTrue(st["open"])
            self.assertEqual((st["closes"], st["market_closes"], st["early_close"]),
                             ("13:15", "13:15", "a special short session"))
            self.assertFalse(alerts.market_status(ist("2026-10-19T13:15"))["open"])
            self.assertEqual(alerts.market_status(ist("2026-10-20T10:00"))["early_close"], None)
        # table restored (empty): a normal day again, in the auction until 15:30
        self.assertTrue(alerts.market_status(ist("2026-10-19T15:15"))["open"])
        self.assertFalse(alerts.market_status(ist("2026-10-19T15:15"))["trading_open"])


class Quotes(unittest.TestCase):
    def test_yahoo_fixtures(self):
        with open(os.path.join(QDIR, "yahoo-HDFCBANK.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "HDFCBANK")
        self.assertEqual(q["price"], 1004.30)
        self.assertEqual(q["prev_close"], 1003.4)
        self.assertEqual(q["time"], "2026-10-13T09:59:58Z")
        self.assertEqual((q["currency"], q["source"], q["symbol"]), ("INR", "yahoo", "HDFCBANK"))
        with open(os.path.join(QDIR, "yahoo-RELIANCE.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "RELIANCE")
        self.assertEqual((q["price"], q["prev_close"], q["open"]), (1419.80, 1398.7, 1402.0))
        self.assertEqual(q["time"], "2026-10-19T05:22:13Z")          # 10:52:13 IST

    def test_no_stooq(self):
        self.assertFalse(hasattr(alerts, "stooq_url"))
        self.assertFalse(hasattr(alerts, "parse_stooq"))
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            alerts.main(["quote", "RELIANCE", "--source", "stooq"])

    def test_urls_and_browser_headers(self):
        self.assertEqual(alerts.IMPERSONATE, "chrome")
        self.assertIn("Chrome/", alerts.BROWSER_UA)
        self.assertNotIn("compatible", alerts.BROWSER_UA)
        self.assertEqual(alerts.YAHOO_HEADERS["Referer"], "https://finance.yahoo.com/")
        self.assertEqual(alerts.yahoo_url("reliance"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=1d")
        self.assertEqual(alerts.yahoo_url("RELIANCE.NS"), alerts.yahoo_url("reliance"))
        self.assertIn("/chart/BAJAJ-AUTO.NS?", alerts.yahoo_url("bajaj-auto"))
        self.assertIn("/chart/M%26M.NS?", alerts.yahoo_url("M&M"))
        self.assertIn("/chart/%5ENSEI?", alerts.yahoo_url("^nsei"))
        self.assertIn("//query1.finance.yahoo.com/", alerts.yahoo_url("TCS", alerts.YAHOO_HOSTS[1]))

    def test_symbols(self):
        self.assertEqual(alerts.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(alerts.yahoo_symbol("500325.bo"), "500325.BO")
        self.assertEqual(alerts.plain_symbol("m&m.ns"), "M&M")
        for good in ("RELIANCE", "M&M", "BAJAJ-AUTO", "20MICRONS", "J&KBANK", "^NSEI"):
            self.assertTrue(alerts.SYMBOL.match(good), good)
        for bad in ("", "bad symbol", "A" * 21, "-X", "TCS/../x"):
            self.assertFalse(alerts.SYMBOL.match(bad), bad)

    def test_rupees_with_indian_grouping(self):
        self.assertEqual(alerts.inr(1419.80), RS + "1,419.80")
        self.assertEqual(alerts.inr(123456.789), RS + "1,23,456.79")
        self.assertEqual(alerts.inr(1234567.5), RS + "12,34,567.50")
        self.assertEqual(alerts.inr(1000, None), RS + "1,000")
        self.assertEqual(alerts.inr(1406.40, None), RS + "1,406.40")
        self.assertEqual(alerts.inr(99.5), RS + "99.50")
        self.assertEqual(alerts.inr(-5000, None), "-" + RS + "5,000")

    def test_offline_quotes_and_errors(self):
        q, errors = alerts.get_quotes(["HDFCBANK", "TCS", "M&M", "ZZZZ", "NOPE"], "yahoo", QDIR)
        self.assertEqual(sorted(q), ["HDFCBANK", "M&M", "TCS"])          # keyed by the plain symbol
        self.assertEqual(q["TCS"]["price"], 3185.5)
        self.assertEqual(q["M&M"]["price"], 3412.6)
        self.assertEqual(len(errors), 2)
        self.assertIn("No data found", errors[0])
        self.assertIn("yahoo-NOPE.json", errors[1])

    def test_a_ns_suffix_and_case_are_plain_symbols(self):
        q, errors = alerts.get_quotes(["reliance.ns", "m&m"], "yahoo", QDIR)
        self.assertEqual((sorted(q), errors), (["M&M", "RELIANCE"], []))

    def test_junk_symbols_and_other_sources_are_refused(self):
        q, errors = alerts.get_quotes(["bad symbol", "../x"], "yahoo", QDIR)
        self.assertEqual(q, {})
        self.assertEqual(len(errors), 2)
        self.assertIn("not an NSE symbol", errors[0])
        with self.assertRaises(alerts.AlertError):
            alerts.get_quotes(["RELIANCE"], "stooq", QDIR)

    def test_offline_gz(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(QDIR, "yahoo-RELIANCE.json"), "rb") as src:
                with gzip.open(os.path.join(d, "yahoo-RELIANCE.json.gz"), "wb") as dst:
                    dst.write(src.read())
            q, errors = alerts.get_quotes(["RELIANCE"], "yahoo", d)
            self.assertEqual((q["RELIANCE"]["price"], errors), (1419.80, []))
        finally:
            shutil.rmtree(d)


class FakeNet(object):
    """Stands in for urllib: answers each request with the next status code."""

    def __init__(self, codes, body=None):
        self.codes, self.calls, self.urls, self.sleeps = list(codes), [], [], []
        with open(os.path.join(QDIR, "yahoo-RELIANCE.json")) as fh:
            self.body = body or fh.read()

    def urlopen(self, req, timeout=None):
        self.calls.append((req.full_url.split("/")[2], req.get_header("User-agent")))
        self.urls.append(req.full_url)
        code = self.codes.pop(0)
        if code != 200:
            fp = io.BytesIO(b'{"chart":{"result":null,"error":{"code":"Not Found",'
                            b'"description":"No data found, symbol may be delisted"}}}' if code == 404 else b"")
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, fp)
        return io.BytesIO(self.body.encode("utf-8"))


class Network(unittest.TestCase):
    """The retry logic, with urllib swapped for a fake (curl_cffi off): the tests never touch the network."""

    def setUp(self):
        self.saved = (alerts._urlopen, alerts._sleep, alerts._cffi)
        alerts._cffi = None

    def tearDown(self):
        alerts._urlopen, alerts._sleep, alerts._cffi = self.saved

    def net(self, codes):
        f = FakeNet(codes)
        alerts._urlopen = f.urlopen
        alerts._sleep = f.sleeps.append
        return f

    def hosts(self, f):
        return [h.split(".")[0] for h, _ in f.calls]

    def test_query2_first_with_a_browser_user_agent(self):
        f = self.net([200])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertEqual((q["RELIANCE"]["price"], errors), (1419.80, []))
        self.assertEqual(f.calls, [("query2.finance.yahoo.com", alerts.BROWSER_UA)])
        self.assertIn("/v8/finance/chart/RELIANCE.NS?", f.urls[0])

    def test_the_request_path_is_url_quoted(self):
        f = self.net([200, 200])
        q, _ = alerts.get_quotes(["M&M", "^NSEI"])
        self.assertEqual(sorted(q), ["M&M", "^NSEI"])
        self.assertIn("/chart/M%26M.NS?", f.urls[0])
        self.assertIn("/chart/%5ENSEI?", f.urls[1])

    def test_one_retry_with_a_pause_on_429(self):
        f = self.net([429, 200])
        self.assertEqual(alerts.get_quotes(["RELIANCE"])[0]["RELIANCE"]["price"], 1419.80)
        self.assertEqual(self.hosts(f), ["query2", "query2"])
        self.assertEqual(f.sleeps, [alerts.BACKOFF])

    def test_falls_back_to_query1_on_429_and_5xx(self):
        f = self.net([429, 429, 200])
        self.assertIn("RELIANCE", alerts.get_quotes(["RELIANCE"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query2", "query1"])
        f = self.net([503, 200])
        self.assertIn("RELIANCE", alerts.get_quotes(["RELIANCE"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query1"])
        f = self.net([500, 502])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertEqual(q, {})
        self.assertIn("query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS answered HTTP 502", errors[0])

    def test_a_404_is_yahoos_answer_and_a_403_is_not_retried(self):
        f = self.net([404])
        q, errors = alerts.get_quotes(["NOPE"])
        self.assertIn("No data found", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])
        f = self.net([403])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertIn("HTTP 403", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])

    def test_a_pause_between_symbols(self):
        f = self.net([200, 200])
        alerts.get_quotes(["RELIANCE", "TCS"])
        self.assertEqual(f.sleeps, [alerts.PAUSE])


class Transport(unittest.TestCase):
    """The real _get, with curl_cffi (Chrome's TLS fingerprint): the quotes come from Yahoo, which blocks plain
    Python clients with HTTP 429."""

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
        fake = self.FakeCffi(self.Resp(200, '{"ok": "\u20b9"}'.encode("utf-8")))
        with mock.patch.object(alerts, "_cffi", fake):
            self.assertEqual(alerts._get("https://query2.finance.yahoo.com/x"), (200, '{"ok": "\u20b9"}'))
        url, kw = fake.calls[0]
        self.assertEqual(kw["impersonate"], "chrome")
        self.assertEqual(kw["headers"]["Referer"], "https://finance.yahoo.com/")
        self.assertIn("Accept-Language", kw["headers"])
        self.assertNotIn("User-Agent", kw["headers"])               # curl_cffi sets Chrome's own

    def test_status_codes_come_back_for_the_retry_logic(self):
        with mock.patch.object(alerts, "_cffi", self.FakeCffi(self.Resp(429, b"slow down"))):
            self.assertEqual(alerts._get("https://query2.finance.yahoo.com/x"), (429, "slow down"))
        with mock.patch.object(alerts, "_cffi", self.FakeCffi(self.Resp(404, b'{"chart": 1}'))):
            self.assertEqual(alerts._get("https://query2.finance.yahoo.com/x"), (404, '{"chart": 1}'))

    def test_a_network_failure_is_an_alert_error(self):
        with mock.patch.object(alerts, "_cffi", self.FakeCffi(exc=RuntimeError("timed out"))):
            with self.assertRaises(alerts.AlertError) as cm:
                alerts._get("https://query2.finance.yahoo.com/x")
        self.assertIn("could not reach query2.finance.yahoo.com", str(cm.exception))

    def test_fetch_yahoo_retries_through_curl_cffi(self):
        answers = [self.Resp(429), self.Resp(200, b'{"chart": {"result": null}}')]

        class Seq(object):
            calls = []

            def get(self, url, **kw):
                Seq.calls.append(url.split("/")[2])
                return answers.pop(0)
        slept = []
        with mock.patch.object(alerts, "_cffi", Seq()), mock.patch.object(alerts, "_sleep", slept.append):
            self.assertEqual(alerts.fetch_yahoo("RELIANCE"), '{"chart": {"result": null}}')
        self.assertEqual((Seq.calls, slept), (["query2.finance.yahoo.com"] * 2, [alerts.BACKOFF]))


class Rules(unittest.TestCase):
    def setUp(self):
        self.q, _ = alerts.get_quotes(["HDFCBANK", "ITC", "TCS", "RELIANCE", "M&M"], "yahoo", QDIR)
        self.open = alerts.market_status(ist("2026-10-13T10:00"))
        self.now = ist("2026-10-13T10:00")

    def at(self, text, rules, state=None, quotes=None):
        now = ist(text)
        return alerts.evaluate(rules, self.q if quotes is None else quotes, alerts.market_status(now),
                               state or {}, now)

    def test_a_target_rings_once(self):
        rules = [{"id": "hdfc-1000", "symbol": "HDFCBANK", "type": "above", "price": 1000}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual(len(rings), 1)
        self.assertIn("HDFCBANK is " + RS + "1,004.30: at or above your alert level " + RS + "1,000",
                      rings[0]["message"])
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])

    def test_a_symbol_with_an_ampersand_rings(self):
        rules = [{"id": "mm-3400", "symbol": "m&m", "type": "target", "price": 3400}]
        rings, _ = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual(rings[0]["symbol"], "M&M")
        self.assertIn("M&M is " + RS + "3,412.60: at or above your target " + RS + "3,400", rings[0]["message"])

    def test_stop_and_rearm(self):
        rules = [{"id": "itc-stop", "symbol": "ITC", "type": "stop", "price": 415, "rearm": True}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertIn("your stop " + RS + "415", rings[0]["message"])
        self.q["ITC"]["price"] = 418.0
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])
        self.assertNotIn("itc-stop", st["rung"])
        self.q["ITC"]["price"] = 410.5
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(len(rings), 1)

    def test_pct_move_from_previous_close(self):
        big = [{"id": "itc-3", "symbol": "ITC", "type": "pct_move", "pct": 3}]
        self.assertEqual(alerts.evaluate(big, self.q, self.open, {}, self.now)[0], [])
        small = [{"id": "itc-1", "symbol": "ITC", "type": "pct_move", "pct": 1, "direction": "down"}]
        rings, st = alerts.evaluate(small, self.q, self.open, {}, self.now)
        self.assertIn("-1.14%", rings[0]["message"])
        self.assertIn("to " + RS + "412.35 (from " + RS + "417.10)", rings[0]["message"])
        self.assertEqual(alerts.evaluate(small, self.q, self.open, st, self.now)[0], [])
        up = [{"id": "itc-up", "symbol": "ITC", "type": "pct_move", "pct": 1, "direction": "up"}]
        self.assertEqual(alerts.evaluate(up, self.q, self.open, {}, self.now)[0], [])
        rel = [{"id": "rel-1.5", "symbol": "RELIANCE", "type": "pct_move", "pct": 1.5}]
        rings, _ = self.at("2026-10-19 10:52", rel)
        self.assertIn("RELIANCE moved +1.51% to " + RS + "1,419.80 (from " + RS + "1,398.70)", rings[0]["message"])

    def test_opening_bell_once_per_trading_day(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        closed = alerts.market_status(ist("2026-10-13T08:00"))
        self.assertEqual(alerts.evaluate(rules, {}, closed, {}, ist("2026-10-13T08:00"))[0], [])
        rings, st = alerts.evaluate(rules, {}, self.open, {}, self.now)
        self.assertIn("Opening bell: NSE is open (2026-10-13 10:00 IST).", rings[0]["message"])
        self.assertEqual(alerts.evaluate(rules, {}, self.open, st, ist("2026-10-13T11:00"))[0], [])
        nxt = alerts.market_status(ist("2026-10-14T09:16"))
        self.assertEqual(len(alerts.evaluate(rules, {}, nxt, st, ist("2026-10-14T09:16"))[0]), 1)
        # no bell on the Dussehra holiday
        self.assertEqual(self.at("2026-10-20 10:00", rules)[0], [])

    def test_the_opening_bell_names_a_short_session(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        with mock.patch.object(alerts, "NSE_EARLY_CLOSE", SHORT):
            rings, _ = self.at("2026-10-19 09:20", rules)
        self.assertIn("a short session today: it closes at 13:15 (a special short session)", rings[0]["message"])

    def test_the_plan_gives_a_stop_a_target_and_flat_by(self):
        rules = plan_rules()
        self.assertEqual([(r["id"], r["type"]) for r in rules],
                         [("reliance-plan-stop", "stop"), ("reliance-plan-target", "target"),
                          ("reliance-flat-by", "time")])
        self.assertEqual((rules[0]["price"], rules[1]["price"]), (1406.40, 1426.20))
        self.assertEqual((rules[2]["at"], rules[2]["label"], rules[2]["before_close"]), ("15:00", "flat by", True))
        self.assertTrue(all(r["date"] == "2026-10-19" for r in rules))
        rings, st = self.at("2026-10-19 10:52", rules)          # the 10:52 quote, 1419.80
        self.assertEqual(rings, [])
        q = json.loads(json.dumps(self.q))
        q["RELIANCE"]["price"] = 1406.10
        rings, st = self.at("2026-10-19 11:30", rules, st, q)
        self.assertEqual([r["id"] for r in rings], ["reliance-plan-stop"])
        self.assertIn("RELIANCE is " + RS + "1,406.10: at or below your stop " + RS
                      + "1,406.40 (plan stop: opening-range midpoint)", rings[0]["message"])
        q["RELIANCE"]["price"] = 1426.20
        self.assertEqual([r["id"] for r in self.at("2026-10-19 12:00", rules, st, q)[0]], ["reliance-plan-target"])

    def test_flat_by_rings_once_per_trading_day(self):
        rules = plan_rules()[2:]
        self.assertEqual(self.at("2026-10-19 14:59", rules)[0], [])
        rings, st = self.at("2026-10-19 15:00", rules)
        self.assertEqual(len(rings), 1)
        self.assertIn("Flat by: it is 15:00 IST (set for 15:00), continuous trading of F&O stocks ends at 15:15.",
                      rings[0]["message"])
        self.assertIn("trader-broker flatten --symbol RELIANCE", rings[0]["message"])
        self.assertEqual(self.at("2026-10-19 15:10", rules, st)[0], [])
        rings, _ = self.at("2026-10-19 15:40", rules)
        self.assertIn("continuous trading of F&O stocks has ended", rings[0]["message"])
        self.assertEqual(self.at("2026-10-21 15:01", rules, st)[0], [])     # plan rules: their day only

    def test_a_normal_day_has_no_shift(self):
        rule = {"id": "flat", "type": "time", "at": "15:00", "before_close": True}
        st = alerts.market_status(ist("2026-10-19 10:00"))
        self.assertEqual(alerts.ring_time(rule, st), datetime.time(15, 0))

    def test_a_time_alert_moves_with_a_short_session(self):
        rule = [{"id": "flat", "type": "time", "at": "15:00", "label": "flat by", "before_close": True}]
        plain = [{"id": "t", "type": "time", "at": "15:00", "label": "check"}]
        with mock.patch.object(alerts, "NSE_EARLY_CLOSE", SHORT):      # the close moved 2 hours earlier, to 13:15
            self.assertEqual(alerts.ring_time(rule[0], alerts.market_status(ist("2026-10-19 10:00"))),
                             datetime.time(13, 0))
            self.assertEqual(self.at("2026-10-19 12:59", rule)[0], [])
            rings, _ = self.at("2026-10-19 13:00", rule)
            self.assertIn("set for 13:00), the short session closes at 13:15", rings[0]["message"])
            self.assertEqual(self.at("2026-10-19 13:00", plain)[0], [])    # without before_close it stays at 15:00
            rings, _ = self.at("2026-10-19 15:00", plain)
            self.assertIn("the short session has closed", rings[0]["message"])
            self.assertEqual(self.at("2026-10-20 12:56", rule)[0], [])     # the holiday after it: no session
        self.assertEqual(self.at("2026-10-19 13:00", rule)[0], [])         # table empty again: rings at 15:00
        self.assertEqual(len(self.at("2026-10-19 15:00", rule)[0]), 1)

    def test_time_alerts_skip_weekends_and_holidays(self):
        rule = [{"id": "flat", "type": "time", "at": "15:00", "label": "flat by", "before_close": True}]
        self.assertEqual(self.at("2026-10-17 15:01", rule)[0], [])         # Saturday: no session
        self.assertEqual(self.at("2026-10-20 15:01", rule)[0], [])         # Dussehra
        self.assertEqual(self.at("2026-11-08 15:01", rule)[0], [])         # Sunday, Diwali Laxmi Pujan (Muhurat)
        rings, st = self.at("2026-12-01 15:00", rule)                      # a normal Tuesday
        self.assertEqual(len(rings), 1)
        self.assertEqual(st["rung"]["flat"], "2026-12-01T09:30:00Z")       # 15:00 IST in UTC

    def test_the_example_rules_are_valid(self):
        with open(os.path.join(ROOT, "trader-alerts", "alerts.example.json"), encoding="utf-8") as fh:
            rules = json.load(fh)["rules"]
        for r in rules:
            alerts.validate(r)
        self.assertEqual([r for r in rules if r["id"].startswith("reliance-plan") or r["id"] == "reliance-flat-by"],
                         [dict(r, date="2026-10-19") for r in plan_rules()])
        move = [r for r in rules if r["type"] == "pct_move"]
        self.assertEqual([(r["symbol"], r["pct"]) for r in move], [("RELIANCE", 3)])

    def test_bad_rules(self):
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "moon"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "TCS"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError) as cm:
            alerts.evaluate([{"id": "x", "type": "time", "at": "4pm"}], {}, self.open, {}, self.now)
        self.assertIn("IST time", str(cm.exception))
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "not a symbol", "price": 1}], {}, self.open,
                            {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"symbol": "RELIANCE", "stop": 1406.40})


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "alerts.json")
        self.state = os.path.join(self.d, "state", "alerts.json")
        self.prices = os.path.join(self.d, "prices.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def check(self, now):
        return run(["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices,
                    "--offline", QDIR, "--now", now])

    def test_the_canonical_trade_from_the_plan(self):
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state,
                             "--config", os.path.join(self.d, "none")])
        self.assertEqual(code, 0)
        self.assertIn("Added reliance-flat-by", text)
        self.assertIn("matter more", text)
        self.assertNotIn("bracket orders are what", text)
        self.assertEqual(run(["add", "--rules", self.rules, "--opening-bell"])[0], 0)

        code, text, _ = self.check("2026-10-19 10:52")
        self.assertEqual(code, 0)                                      # the opening bell rings first
        self.assertIn("NSE OPEN (2026-10-19 10:52 IST, regular session).", text)
        code, text, _ = self.check("2026-10-19 10:53")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit. 4 rule(s) watched today, RELIANCE " + RS + "1,419.80.", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"RELIANCE": 1419.80})
        self.assertEqual(doc["detail"]["RELIANCE"]["time"], "2026-10-19T05:22:13Z")
        self.assertEqual(doc["detail"]["RELIANCE"]["fetched"], "2026-10-19T05:23:00Z")      # 10:53 IST

        code, text, _ = self.check("2026-10-19 15:00")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Flat by", text)
        self.assertIn("continuous trading of F&O stocks ends at 15:15", text)
        self.assertIn("not an order", text)
        self.assertIn("India has no bracket orders", text)                 # the alerts are the only watchers
        self.assertIn("in paper trading nothing sits at a broker", text)
        self.assertIn("Check your paper portfolio", text)
        self.assertNotIn("already sit", text)
        self.assertEqual(self.check("2026-10-19 15:12")[0], 1)
        code, text, _ = self.check("2026-10-20 10:00")                     # Dussehra
        self.assertEqual(code, 1)
        self.assertIn("NSE closed (2026-10-20 10:00 IST, NSE holiday: Dussehra)", text)
        code, text, _ = self.check("2026-10-21 10:00")
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-10-21 10:05")
        self.assertIn("Nothing hit. 1 rule(s) watched today.", text)

        # a new plan replaces the plan rules and re-arms them
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state])
        with open(self.rules) as fh:
            ids = [r["id"] for r in json.load(fh)["rules"]]
        self.assertEqual(sorted(ids), ["opening-bell", "reliance-flat-by", "reliance-plan-stop",
                                       "reliance-plan-target"])
        with open(self.state) as fh:
            self.assertNotIn("reliance-flat-by", json.load(fh)["rung"])

    def test_the_portfolio_evening_and_morning(self):
        for args in (["--opening-bell"], ["--symbol", "HDFCBANK", "--above", "1010"],
                     ["--symbol", "ITC", "--stop", "400"]):
            code, _, _ = run(["add", "--rules", self.rules] + args)
            self.assertEqual(code, 0)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "HDFCBANK", "--above", "1010"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

        code, text, _ = self.check("2026-10-13T20:00")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"HDFCBANK": 1004.30, "ITC": 412.35})

        code, text, _ = self.check("2026-10-14T09:16")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-10-14T09:45")
        self.assertEqual(code, 1)

        code, text, _ = run(["list", "--rules", self.rules, "--state", self.state])
        self.assertIn("rang", text)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 0)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 1)

    def test_add_time_and_target(self):
        code, text, _ = run(["add", "--rules", self.rules, "--at", "15:00", "--label", "flat by",
                             "--before-close", "--symbol", "reliance"])
        self.assertEqual(code, 0)
        code, text, _ = run(["add", "--rules", self.rules, "--symbol", "RELIANCE", "--target", "1426.20",
                             "--date", "2026-10-19"])
        self.assertEqual(code, 0)
        code, text, _ = run(["add", "--rules", self.rules, "--symbol", "m&m", "--stop", "3400"])
        self.assertEqual(code, 0)
        with open(self.rules) as fh:
            rules = json.load(fh)["rules"]
        self.assertEqual(rules[0], {"id": "time-1500", "type": "time", "at": "15:00", "label": "flat by",
                                    "symbol": "RELIANCE", "before_close": True})
        self.assertEqual(rules[1], {"id": "reliance-target-1426.20", "symbol": "RELIANCE", "type": "target",
                                    "price": 1426.20, "date": "2026-10-19"})
        self.assertEqual((rules[2]["id"], rules[2]["symbol"]), ("m&m-stop-3400", "M&M"))
        self.assertEqual(run(["add", "--rules", self.rules, "--at", "4pm"])[0], 1)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "bad symbol", "--stop", "1"])
        self.assertEqual(code, 1)
        self.assertIn("not an NSE symbol", err)
        code, _, err = run(["add", "--rules", self.rules, "--from-plan", "--plan", os.path.join(self.d, "none")])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)

    def test_quote_write_merges(self):
        code, text, _ = run(["quote", "HDFCBANK", "TCS", "--offline", QDIR, "--write", self.prices,
                             "--now", "2026-10-13T20:00:01Z"])
        self.assertEqual(code, 0)
        self.assertIn("HDFCBANK   " + RS + "1,004.30 (+0.09% vs prev close " + RS + "1,003.40)", text)
        self.assertIn("delayed", text)
        self.assertNotIn("$", text)
        run(["quote", "ITC", "M&M", "--offline", QDIR, "--write", self.prices])
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(doc["prices"]), ["HDFCBANK", "ITC", "M&M", "TCS"])
        self.assertEqual(doc["detail"]["TCS"]["prev_close"], 3170.2)
        self.assertEqual(doc["detail"]["TCS"]["fetched"], "2026-10-13T20:00:01Z")

    def test_quote_output_uses_indian_grouping(self):
        code, text, _ = run(["quote", "TCS", "--offline", QDIR])
        self.assertEqual(code, 0)
        self.assertIn(RS + "3,185.50", text)

    def test_quote_holdings_from_portfolio_and_plan(self):
        pf = os.path.join(self.d, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"cash": 0, "positions": [{"ticker": "TCS", "shares": 1}]}, fh)
        code, text, _ = run(["quote", "--holdings", "--portfolio", pf, "--plan", PLAN, "--offline", QDIR, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(json.loads(text)["quotes"]), ["RELIANCE", "TCS"])

    def test_clock_exit_codes(self):
        code, text, _ = run(["clock", "--now", "2026-10-19 10:15"])
        self.assertEqual(code, 0)
        self.assertIn("IST 2026-10-19 10:15 (Monday): NSE OPEN, regular session.", text)
        self.assertIn("Today's session: 09:15 to 15:30.", text)
        self.assertIn("stop continuous trading at 15:15 and close in an auction; this pack's trading day ends at 15:15", text)
        self.assertNotIn("New York", text)
        # 15:15 to 15:30: the exchange is open (closing auction), the pack's day is over
        code, text, _ = run(["clock", "--now", "2026-10-19 15:20"])
        self.assertEqual(code, 0)
        self.assertIn("NSE OPEN, closing auction for stocks with F&O contracts", text)
        self.assertIn("this pack's trading day is over", text)
        self.assertEqual(run(["clock", "--now", "2026-10-19 15:30"])[0], 1)
        code, text, _ = run(["clock", "--now", "2026-11-08 18:30"])         # Muhurat Sunday: not supported
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Diwali Laxmi Pujan (Muhurat trading session only)", text)
        code, text, _ = run(["clock", "--now", "2026-12-25T10:00"])
        self.assertEqual(code, 1)
        self.assertIn("Christmas", text)
        code, text, _ = run(["clock", "--now", "2026-10-20 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Dussehra", text)
        self.assertIn("Next open: 2026-10-21 09:15 IST.", text)
        code, text, _ = run(["clock", "--now", "2027-01-05 10:00"])
        self.assertIn("Holiday list covers 2026 only: check nseindia.com", text)
        code, text, _ = run(["clock", "--now", "2026-10-19 10:15", "--json"])
        doc = json.loads(text)
        self.assertEqual((doc["now_ist"], doc["open"]), ("2026-10-19 10:15", True))

    def test_clock_with_a_listed_short_session(self):
        with mock.patch.object(alerts, "NSE_EARLY_CLOSE", SHORT):
            code, text, _ = run(["clock", "--now", "2026-10-19 10:15"])
        self.assertEqual(code, 0)
        self.assertIn("Today's session: 09:15 to 13:15 (short session: a special short session).", text)

    def test_check_without_rules(self):
        code, _, err = run(["check", "--rules", self.rules, "--offline", QDIR])
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)

    def test_rupee_sign_survives_a_legacy_windows_console(self):
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        env.pop("PYTHONUTF8", None)
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "trader-alerts", "alerts.py"), "quote", "TCS",
                               "--offline", QDIR], capture_output=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertIn((RS + "3,185.50").encode("utf-8"), proc.stdout)


if __name__ == "__main__":
    unittest.main()
