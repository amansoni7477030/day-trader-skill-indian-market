import datetime
import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from _load import FIX, ROOT, load

facts = load("trader-analyst", "facts")
DIR = os.path.join(FIX, "trader-analyst")
TODAY = "2026-11-03"       # pinned: the stale-data check must not depend on the clock
RUPEE = "₹"
# fundamentals-RELIANCE.json, -HDFCBANK.json, -NODATA.json and -EMPTY.json are SYNTHETIC (see
# tests/fixtures/make_analyst_fixtures.py): round made-up numbers in the shape of Yahoo's fundamentals-timeseries
# answer. fundamentals-RELIANCE-real.json is Yahoo's REAL answer, saved by tests/fixtures/capture_real_fixtures.py.


def document(sym):
    with open(os.path.join(DIR, "fundamentals-%s.json" % sym), encoding="utf-8") as fh:
        return json.load(fh)


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = facts.main(args)
    return code, out.getvalue(), err.getvalue()


def result_with(entries_by_type):
    """A minimal Yahoo-shaped document from {typeName: [entry, ...]}."""
    return {"timeseries": {"result": [{"meta": {"symbol": ["X.NS"], "type": [t]}, t: e}
                                      for t, e in entries_by_type.items()], "error": None}}


def e(end, raw, ptype="3M", ccy="INR"):
    return {"asOfDate": end, "periodType": ptype, "currencyCode": ccy, "reportedValue": {"raw": raw}}


class Reliance(unittest.TestCase):
    def setUp(self):
        self.r = facts.analyse(document("RELIANCE"), quarters=8, symbol="RELIANCE")

    def test_latest_quarter(self):
        q = self.r["rows"][0]
        self.assertEqual(q["end"], "2026-06-30")
        self.assertEqual(q["fiscal"], "Q1 FY27")
        self.assertEqual(q["revenue"], 250000.0)        # rupee crore (2.5e12 / 1e7)
        self.assertEqual(q["net_income"], 20000.0)
        self.assertEqual(q["eps_diluted"], 14.7)
        self.assertEqual(q["revenue_yoy"], 11.1)        # vs 2,25,000 crore a year earlier
        self.assertEqual(q["net_income_yoy"], 14.3)     # vs 17,500
        self.assertEqual(q["eps_yoy"], 14.0)            # vs 12.9
        self.assertEqual(q["gross_margin"], 32.8)
        self.assertEqual(q["operating_margin"], 19.0)
        self.assertEqual(q["net_margin"], 8.0)
        self.assertEqual(self.r["currency"], "INR")

    def test_fiscal_labels_from_the_period_end(self):
        labels = [r["fiscal"] for r in self.r["rows"]]
        self.assertEqual(labels, ["Q1 FY27", "Q4 FY26", "Q3 FY26", "Q2 FY26", "Q1 FY26",
                                  "Q4 FY25", "Q3 FY25", "Q2 FY25"])
        self.assertEqual(facts.fiscal_label("2026-03-31"), "Q4 FY26")     # Jan-Mar is Q4 of the FY ending then
        self.assertEqual(facts.fiscal_label("2026-01-31"), "Q4 FY26")
        self.assertEqual(facts.fiscal_label("2026-04-30"), "Q1 FY27")
        self.assertEqual(facts.fiscal_label("2025-12-31"), "Q3 FY26")
        self.assertEqual(facts.fiscal_label("2099-09-30"), "Q2 FY00")

    def test_no_derived_q4(self):
        # Q4 FY26 is just the reported 2026-03-31 quarter; the 12M entry on 2025-03-31 never counts
        q4 = next(r for r in self.r["rows"] if r["end"] == "2025-03-31")
        self.assertEqual(q4["revenue"], 240000.0)
        self.assertNotIn("derived", q4)

    def test_year_on_year_needs_the_same_quarter_a_year_earlier(self):
        rows = {r["end"]: r for r in self.r["rows"]}
        self.assertEqual(rows["2026-03-31"]["revenue_yoy"], 9.2)     # 2,62,000 vs 2,40,000
        self.assertIsNone(rows["2025-06-30"]["revenue_yoy"])         # nothing a year before it
        self.assertEqual(self.r["rows"][4]["eps_yoy"], None)

    def test_trailing_twelve_months(self):
        self.assertEqual(self.r["ttm"]["revenue"], 1002000.0)    # 2,50,000 + 2,62,000 + 2,50,000 + 2,40,000
        self.assertEqual(self.r["ttm"]["net_income"], 80300.0)
        self.assertEqual(self.r["ttm"]["eps_diluted"], 59.2)

    def test_what_changed_in_words(self):
        lines = facts.changes(self.r)
        self.assertIn("Quarter ending 2026-06-30 (Q1 FY27): revenue " + RUPEE + "2,50,000 crore "
                      "(+11.1% vs a year earlier)", lines[0])
        self.assertIn("net income " + RUPEE + "20,000 crore (+14.3%)", lines[0])
        self.assertIn("diluted EPS " + RUPEE + "14.70 (+14.0%)", lines[0])
        self.assertTrue(any(l.startswith("Gross margin 32.8% (+0.7 points") for l in lines))
        self.assertTrue(any(l.startswith("Revenue -4.6% vs the quarter before") for l in lines))
        self.assertFalse(any("above operating income" in l for l in lines))

    def test_a_missing_number_is_skipped_not_zeroed(self):
        # the gross-profit list ends with an entry that has no reportedValue
        gross, _ = facts.parse_timeseries(document("RELIANCE"))
        self.assertEqual(len(gross["gross_profit"]), 8)
        self.assertNotIn("2026-09-30", gross["gross_profit"])


class BankLike(unittest.TestCase):
    def setUp(self):
        self.r = facts.analyse(document("HDFCBANK"), quarters=8, symbol="HDFCBANK")

    def test_margins_are_blank_not_zero(self):
        q = self.r["rows"][0]
        self.assertEqual(q["revenue"], 95000.0)
        self.assertEqual(q["net_margin"], 20.6)
        self.assertIsNone(q["gross_profit"])
        self.assertIsNone(q["gross_margin"])
        self.assertIsNone(q["operating_margin"])
        self.assertEqual(q["revenue_yoy"], 13.1)
        self.assertEqual(self.r["types"].get("operating_income"), None)
        lines = facts.changes(self.r)
        self.assertFalse(any(l.startswith(("Gross", "Operating")) for l in lines))
        self.assertTrue(any(l.startswith("Net margin 20.6% (+0.2 points") for l in lines))

    def test_the_table_shows_dashes(self):
        code, out, err = run(["HDFCBANK", "--offline", DIR, "--today", TODAY])
        self.assertEqual(code, 0, err)
        row = next(l for l in out.splitlines() if l.startswith("2026-06-30"))
        self.assertIn("Q1 FY27", row)
        self.assertEqual(row.split()[5:7], ["-", "-"])     # gross and oper columns


class OneOffsAndSymbols(unittest.TestCase):
    def test_one_offs_are_flagged(self):
        result = {"currency": "INR", "rows": [
            {"end": "2026-06-30", "fiscal": "Q1 FY27", "revenue": 100.0, "net_income": 90.0,
             "operating_income": 30.0, "eps_diluted": 1.0, "revenue_yoy": 10.0,
             "net_income_yoy": 200.0, "eps_yoy": 200.0, "gross_margin": None,
             "operating_margin": 30.0, "net_margin": 90.0}]}
        lines = facts.changes(result)
        self.assertTrue(any("above operating income" in l and "filed result" in l for l in lines))

    def test_only_three_month_periods_count(self):
        doc = result_with({"quarterlyTotalRevenue": [e("2026-06-30", 5e9, "12M"), e("2026-06-30", 1e9),
                                                     e("2026-03-31", 2e9, "6M")]})
        s, found = facts.parse_timeseries(doc)
        self.assertEqual(s["revenue"], {"2026-06-30": {"value": 100.0, "end": "2026-06-30",
                                                       "currency": "INR"}})
        self.assertEqual(found, {"revenue": "quarterlyTotalRevenue"})

    def test_symbols(self):
        self.assertEqual(facts.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(facts.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(facts.plain_symbol("TCS.NS"), "TCS")
        url = facts.facts_url("M&M", datetime.date(2026, 11, 3))
        self.assertIn("/timeseries/M%26M.NS?symbol=M%26M.NS&type=quarterlyTotalRevenue,"
                      "quarterlyGrossProfit,quarterlyOperatingIncome,quarterlyNetIncome,"
                      "quarterlyDilutedEPS&period1=", url)
        self.assertTrue(url.endswith("&merge=false"))
        self.assertIn("period2=1793750400&", url)       # 2026-11-04 00:00 UTC

    def test_rupees_use_indian_grouping(self):
        self.assertEqual(facts.inr(1234567.891), RUPEE + "12,34,567.89")
        self.assertEqual(facts.group_in(250000), "2,50,000")
        self.assertEqual(facts.group_in(999), "999")
        self.assertEqual(facts.group_in(-12345.5, 1), "-12,345.5")

    def test_a_non_rupee_company_is_labelled(self):
        doc = result_with({"quarterlyTotalRevenue": [e("2026-06-30", 1e9, ccy="USD")],
                           "quarterlyNetIncome": [e("2026-06-30", 1e8, ccy="USD")]})
        r = facts.analyse(doc, symbol="X")
        self.assertEqual(r["currency"], "USD")
        self.assertIn("USD 100.0 crore", facts.changes(r)[0])
        self.assertIn("NOT in rupees", facts.render("X", r, datetime.date(2026, 11, 3)))


class LaggingData(unittest.TestCase):
    def test_not_stale_when_the_newest_quarter_is_recent(self):
        code, out, err = run(["RELIANCE", "--offline", DIR, "--today", TODAY])   # 126 days after 30 Jun
        self.assertEqual(code, 0, err)
        self.assertIn("RELIANCE (RELIANCE.NS)", out)
        self.assertNotIn("NOTE:", out)

    def test_says_when_yahoo_may_lag_the_filed_result(self):
        code, out, err = run(["RELIANCE", "--offline", DIR, "--today", "2026-11-12"])  # 135 days: still fine
        self.assertNotIn("NOTE:", out)
        code, out, err = run(["RELIANCE", "--offline", DIR, "--today", "2027-01-15"])
        self.assertEqual(code, 0, err)
        self.assertIn("NOTE: Yahoo's newest quarter ends 2026-06-30, 199 days ago. Yahoo may lag", out)
        self.assertIn("nseindia.com/companies-listing/corporate-filings-financial-results", out)
        self.assertIn("bseindia.com/corporates/Comp_Resultsnew.aspx", out)

    def test_texts_the_reader_needs(self):
        code, out, err = run(["RELIANCE", "--offline", DIR, "--today", TODAY])
        self.assertIn("usually consolidated", out)
        self.assertIn("consolidated vs standalone", out)
        self.assertIn("exceptional items", out)
        self.assertIn("Banks and NBFCs", out)
        self.assertIn("Source: Yahoo Finance fundamentals-timeseries (unofficial", out)
        self.assertIn("Last 4 quarters: revenue " + RUPEE + "10,02,000 crore, net income " + RUPEE
                      + "80,300 crore, diluted EPS " + RUPEE + "59.20.", out)
        self.assertNotIn(chr(0x2014), out)


class Failures(unittest.TestCase):
    def test_yahoo_error_json_prints_a_plain_message(self):
        code, out, err = run(["NODATA", "--offline", DIR, "--today", TODAY])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("Yahoo's unofficial endpoint answered with an error: No fundamentals data found", err)
        self.assertIn("No numbers were guessed", err)
        self.assertIn("nseindia.com/companies-listing/corporate-filings-financial-results", err)
        self.assertIn("bseindia.com/corporates/Comp_Resultsnew.aspx", err)

    def test_empty_answer(self):
        code, out, err = run(["EMPTY", "--offline", DIR])
        self.assertEqual(code, 2)
        self.assertIn("answered without data", err)

    def test_missing_offline_file_and_bad_symbol(self):
        code, _, err = run(["ZZZZ", "--offline", DIR])
        self.assertEqual(code, 2)
        self.assertIn("offline: no file", err)
        code, _, err = run(["bad symbol!", "--offline", DIR])
        self.assertEqual(code, 2)
        self.assertIn("not a plain NSE symbol", err)

    def test_partial_failure_exit_code(self):
        code, out, err = run(["RELIANCE", "NODATA", "--offline", DIR, "--today", TODAY])
        self.assertEqual(code, 1)
        self.assertIn("Q1 FY27", out)

    def test_no_revenue_or_net_income(self):
        with self.assertRaises(facts.FactsError):
            facts.analyse(result_with({"quarterlyGrossProfit": [e("2026-06-30", 1e9)]}))
        with self.assertRaises(facts.FactsError):
            facts.parse_timeseries(["not", "a", "document"])
        with self.assertRaises(facts.FactsError):
            facts.parse_timeseries({"finance": {}})

    def test_http_errors_and_garbage_from_yahoo(self):
        """The urllib fallback (curl_cffi not installed): the same messages as through curl_cffi."""
        fetch = facts.Fetcher(pause=0)
        err = urllib.error.HTTPError("https://x", 401, "Unauthorized", {}, None)
        with mock.patch.object(facts, "_cffi", None):
            with mock.patch.object(facts.urllib.request, "urlopen", side_effect=err):
                with self.assertRaises(facts.FactsError) as cm:
                    fetch.json("https://x", "n")
            self.assertIn("did not answer (HTTP 401)", str(cm.exception))
            with mock.patch.object(facts.urllib.request, "urlopen",
                                   side_effect=urllib.error.URLError("no route")):
                with self.assertRaises(facts.FactsError) as cm:
                    fetch.json("https://x", "n")
            self.assertIn("did not answer (could not reach it", str(cm.exception))
            resp = mock.MagicMock()
            resp.__enter__.return_value.read.return_value = b"<html>not json</html>"
            resp.__enter__.return_value.headers.get.return_value = None
            with mock.patch.object(facts.urllib.request, "urlopen", return_value=resp):
                with self.assertRaises(facts.FactsError) as cm:
                    fetch.json("https://x", "n")
            self.assertIn("not JSON", str(cm.exception))

    def test_a_live_failure_reaches_the_user_as_a_message(self):
        err = urllib.error.HTTPError("https://x", 500, "Server Error", {}, None)
        with mock.patch.object(facts, "_cffi", None), \
                mock.patch.object(facts.urllib.request, "urlopen", side_effect=err):
            code, out, e = run(["RELIANCE", "--today", TODAY])
        self.assertEqual(code, 2)
        self.assertIn("RELIANCE: Yahoo's unofficial endpoint did not answer (HTTP 500)", e)
        self.assertIn("No numbers were guessed", e)

    def test_a_live_failure_through_curl_cffi_reaches_the_user_as_a_message(self):
        with mock.patch.object(facts, "_cffi", Transport.FakeCffi(Transport.Resp(429, b"Too Many Requests"))):
            code, out, e = run(["RELIANCE", "--today", TODAY])
        self.assertEqual(code, 2)
        self.assertIn("RELIANCE: Yahoo's unofficial endpoint did not answer (HTTP 429)", e)
        self.assertIn("No numbers were guessed", e)


class Transport(unittest.TestCase):
    """The real Fetcher._fetch, with curl_cffi swapped for a fake (no network). Yahoo blocks plain Python
    clients, so the request carries Chrome's TLS fingerprint (curl_cffi impersonate) and a browser's headers."""

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

    def test_a_chrome_request_with_yahoo_headers(self):
        body = json.dumps({"timeseries": {"result": None, "error": None}}).encode("utf-8")
        fake = self.FakeCffi(self.Resp(200, body))
        url = facts.facts_url("RELIANCE", datetime.date(2026, 11, 3))
        with mock.patch.object(facts, "_cffi", fake):
            doc = facts.Fetcher(pause=0).json(url, "fundamentals-RELIANCE.json")
        self.assertEqual(doc, {"timeseries": {"result": None, "error": None}})
        got_url, kw = fake.calls[0]
        self.assertEqual(got_url, url)
        self.assertEqual((kw["impersonate"], kw["timeout"]), ("chrome", 60))
        self.assertEqual(kw["headers"]["Referer"], "https://finance.yahoo.com/")
        self.assertIn("application/json", kw["headers"]["Accept"])
        self.assertIn("Accept-Language", kw["headers"])
        self.assertNotIn("User-Agent", kw["headers"])        # curl_cffi sends Chrome's own, matching its TLS
        self.assertNotIn("Accept-Encoding", kw["headers"])   # and it decompresses by itself

    def test_the_request_is_the_fundamentals_endpoint_for_the_ns_symbol(self):
        url = facts.facts_url("M&M", datetime.date(2026, 11, 3))
        self.assertIn("/ws/fundamentals-timeseries/v1/finance/timeseries/M%26M.NS?symbol=M%26M.NS&type=", url)
        for typ in ("quarterlyTotalRevenue", "quarterlyGrossProfit", "quarterlyOperatingIncome",
                    "quarterlyNetIncome", "quarterlyDilutedEPS"):
            self.assertIn(typ, url)
        self.assertTrue(url.endswith("&merge=false"))

    def test_status_codes_and_network_errors_through_curl_cffi(self):
        for code in (401, 404, 429, 500):
            with mock.patch.object(facts, "_cffi", self.FakeCffi(self.Resp(code, b"nope"))):
                with self.assertRaises(facts.FactsError) as cm:
                    facts.Fetcher(pause=0).json("https://x", "n")
            self.assertEqual(str(cm.exception),
                             "Yahoo's unofficial endpoint did not answer (HTTP %d)" % code)
        with mock.patch.object(facts, "_cffi", self.FakeCffi(exc=RuntimeError("timed out"))):
            with self.assertRaises(facts.FactsError) as cm:
                facts.Fetcher(pause=0).json("https://x", "n")
        self.assertIn("did not answer (could not reach it: timed out)", str(cm.exception))
        with mock.patch.object(facts, "_cffi", self.FakeCffi(self.Resp(200, b"<html>not json</html>"))):
            with self.assertRaises(facts.FactsError) as cm:
                facts.Fetcher(pause=0).json("https://x", "n")
        self.assertIn("not JSON", str(cm.exception))

    def test_without_curl_cffi_urllib_sends_the_same_headers_and_a_chrome_user_agent(self):
        seen = []

        def fake(req, timeout=0):
            seen.append(req)
            resp = mock.MagicMock()
            resp.__enter__.return_value.read.return_value = b"{}"
            resp.__enter__.return_value.headers.get.return_value = None
            return resp
        with mock.patch.object(facts, "_cffi", None), mock.patch.object(facts.urllib.request, "urlopen", fake):
            self.assertEqual(facts.Fetcher(pause=0).json("https://x", "n"), {})
        self.assertEqual(seen[0].get_header("User-agent"), facts.USER_AGENT)
        self.assertIn("Chrome/", facts.USER_AGENT)
        self.assertNotIn("compatible", facts.USER_AGENT)       # not a bot-looking User-Agent
        self.assertEqual(seen[0].get_header("Referer"), "https://finance.yahoo.com/")


class RealReliance(unittest.TestCase):
    """Yahoo's REAL answer for RELIANCE.NS (saved 2026-11-04). It is patchy: five quarters and no 2025-09-30
    quarter at all, so some year-on-year figures cannot be computed and the tool leaves them blank."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(DIR, "fundamentals-RELIANCE-real.json"), encoding="utf-8") as fh:
            cls.doc = json.load(fh)
        cls.r = facts.analyse(cls.doc, quarters=8, symbol="RELIANCE")
        cls.rows = {r["end"]: r for r in cls.r["rows"]}
        # the raw numbers, read straight from the answer: {type: {period end: raw}}
        cls.raw = {t: {x["asOfDate"]: x["reportedValue"]["raw"] for x in res[t]}
                   for res in cls.doc["timeseries"]["result"] for t in res["meta"]["type"]}

    def test_five_quarters_and_the_gap_is_not_filled(self):
        self.assertEqual([r["end"] for r in self.r["rows"]],
                         ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-06-30"])
        self.assertEqual([r["fiscal"] for r in self.r["rows"]],
                         ["Q2 FY27", "Q1 FY27", "Q4 FY26", "Q3 FY26", "Q1 FY26"])       # no Q2 FY26 in the answer
        self.assertEqual(self.r["currency"], "INR")

    def test_every_number_is_yahoos_raw_value_in_rupee_crore(self):
        for end, row in self.rows.items():
            self.assertEqual(row["revenue"], round(self.raw["quarterlyTotalRevenue"][end] / 1e7, 2), end)
            self.assertEqual(row["gross_profit"], round(self.raw["quarterlyGrossProfit"][end] / 1e7, 2), end)
            self.assertEqual(row["operating_income"], round(self.raw["quarterlyOperatingIncome"][end] / 1e7, 2), end)
            self.assertEqual(row["net_income"], round(self.raw["quarterlyNetIncome"][end] / 1e7, 2), end)
            self.assertEqual(row["eps_diluted"], self.raw["quarterlyDilutedEPS"][end], end)       # rupees, not crore
        q = self.rows["2026-09-30"]
        self.assertEqual((q["revenue"], q["net_income"], q["eps_diluted"]), (330601.0, 25548.0, 18.88))
        # margins: 89,603 / 3,30,601 = 27.1 % ; 41,301 / 3,30,601 = 12.5 % ; 25,548 / 3,30,601 = 7.7 %
        self.assertEqual((q["gross_margin"], q["operating_margin"], q["net_margin"]), (27.1, 12.5, 7.7))

    def test_year_on_year_only_where_the_same_quarter_a_year_earlier_is_there(self):
        q = self.rows["2026-06-30"]            # against 2025-06-30: 3,09,468 / 2,43,632 ; 20,946 / 26,994 ; 15.48 / 19.95
        self.assertEqual((q["revenue_yoy"], q["net_income_yoy"], q["eps_yoy"]), (27.0, -22.4, -22.4))
        for end in ("2026-09-30", "2026-03-31", "2025-12-31", "2025-06-30"):   # 2025-09-30, 2025-03-31, 2024-12-31 ...
            q = self.rows[end]
            self.assertEqual((q["revenue_yoy"], q["net_income_yoy"], q["eps_yoy"]), (None, None, None), end)

    def test_the_last_four_quarters_are_four_consecutive_quarters(self):
        # December 2025, March, June and September 2026: 2,64,905 + 2,94,059 + 3,09,468 + 3,30,601 crore
        self.assertEqual(self.r["ttm"], {"revenue": 1199033.0, "net_income": 82110.0, "eps_diluted": 60.68})
        self.assertEqual(18645.0 + 16971.0 + 20946.0 + 25548.0, 82110.0)
        self.assertEqual(round(13.78 + 12.54 + 15.48 + 18.88, 2), 60.68)

    def test_four_entries_that_are_not_consecutive_quarters_make_no_ttm(self):
        doc = json.loads(json.dumps(self.doc))
        for res in doc["timeseries"]["result"]:
            t = res["meta"]["type"][0]
            res[t] = [x for x in res[t] if x["asOfDate"] != "2026-03-31"]      # a gap inside the last four entries
        r = facts.analyse(doc, quarters=8, symbol="RELIANCE")
        self.assertEqual(r["ttm"], {})                      # 2025-06-30 to 2026-09-30 is more than four quarters

    def test_the_report(self):
        text = facts.render("RELIANCE", self.r, today=datetime.date(2026, 11, 4))
        self.assertIn("RELIANCE (RELIANCE.NS). \u20b9 crore except EPS (\u20b9). Source: Yahoo Finance "
                      "fundamentals-timeseries (unofficial, an aggregator's numbers).", text)
        self.assertIn("Last 4 quarters: revenue \u20b911,99,033 crore, net income \u20b982,110 crore, diluted EPS "
                      "\u20b960.68.", text)
        self.assertIn("Quarter ending 2026-09-30 (Q2 FY27): revenue \u20b93,30,601 crore, net income \u20b925,548 crore, "
                      "diluted EPS \u20b918.88.", text)
        self.assertIn("Gross margin 27.1% (+0.3 points vs the quarter before).", text)
        self.assertIn("Operating margin 12.5% (+2.0 points vs the quarter before).", text)
        self.assertIn("Revenue +6.8% vs the quarter before", text)          # 3,30,601 / 3,09,468
        self.assertNotIn("NOTE:", text)                                      # the newest quarter ended 35 days ago
        self.assertIn("nseindia.com/companies-listing/corporate-filings-financial-results", text)
        self.assertNotIn("$", text)

    def test_the_stale_note_when_the_newest_quarter_is_old(self):
        text = facts.render("RELIANCE", self.r, today=datetime.date(2027, 3, 1))     # 152 days after 2026-09-30
        self.assertIn("NOTE: Yahoo's newest quarter ends 2026-09-30, 152 days ago. Yahoo may lag", text)

    def test_the_metrics_row(self):
        row = facts.metrics_row("RELIANCE", self.r)
        self.assertEqual((row["period_end"], row["fiscal"], row["revenue_ttm_cr"], row["net_income_ttm_cr"],
                          row["eps_ttm"]), ("2026-09-30", "Q2 FY27", 1199033.0, 82110.0, 60.68))
        self.assertIsNone(row["revenue_growth_yoy"])                           # no 2025-09-30 quarter to compare with


class Cli(unittest.TestCase):
    def test_metrics_file_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trader", "metrics.json")
            code, out, err = run(["RELIANCE", "--offline", DIR, "--metrics", path, "--today", TODAY])
            self.assertEqual(code, 0, err)
            code, out, err = run(["HDFCBANK.NS", "--offline", DIR, "--metrics", path, "--today", TODAY])
            self.assertEqual(code, 0, err)
            with open(path) as fh:
                data = json.load(fh)
        rows = {r["ticker"]: r for r in data["companies"]}
        self.assertEqual(sorted(rows), ["HDFCBANK", "RELIANCE"])
        self.assertEqual(data["as_of"], TODAY)
        self.assertEqual(rows["RELIANCE"]["eps_ttm"], 59.2)
        self.assertEqual(rows["RELIANCE"]["revenue_ttm_cr"], 1002000.0)
        self.assertEqual(rows["RELIANCE"]["net_income_ttm_cr"], 80300.0)
        self.assertEqual(rows["RELIANCE"]["revenue_growth_yoy"], 11.1)
        self.assertEqual(rows["RELIANCE"]["fiscal"], "Q1 FY27")
        self.assertIsNone(rows["HDFCBANK"]["gross_margin"])
        self.assertEqual(rows["HDFCBANK"]["period_end"], "2026-06-30")
        self.assertEqual(rows["HDFCBANK"]["source"], "Yahoo Finance fundamentals-timeseries")
        self.assertIn("wrote", out)
        self.assertNotIn(chr(0x2014), out)

    def test_no_arguments_prints_help(self):
        code, out, _ = run([])
        self.assertEqual(code, 2)
        self.assertIn("usage", out.lower())

    def test_json_output(self):
        code, out, err = run(["RELIANCE", "--offline", DIR, "--json", "--quarters", "4", "--today", TODAY])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(len(data[0]["result"]["rows"]), 4)
        self.assertEqual(data[0]["result"]["types"]["revenue"], "quarterlyTotalRevenue")
        self.assertEqual(data[0]["ticker"], "RELIANCE")
        self.assertIn("Q1 FY27", data[0]["changes"][0])
        self.assertIsNone(data[0]["note"])
        self.assertTrue(data[0]["links"]["nse_results"].startswith("https://www.nseindia.com/"))

    def test_quarters_option(self):
        code, out, err = run(["RELIANCE", "--offline", DIR, "--quarters", "2", "--today", TODAY])
        dated = [l for l in out.splitlines() if l[:4] == "2026" or l[:4] == "2025"]
        self.assertEqual(len(dated), 2)


class Docs(unittest.TestCase):
    """The folder's example file and SKILL.md must say what the tool really writes and prints."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(DIR, "fundamentals-RELIANCE-real.json"), encoding="utf-8") as fh:
            cls.result = facts.analyse(json.load(fh), quarters=8, symbol="RELIANCE")
        with open(os.path.join(ROOT, "trader-analyst", "SKILL.md"), encoding="utf-8") as fh:
            cls.skill = fh.read()

    def test_the_example_metrics_file_has_the_shape_the_tool_writes(self):
        with open(os.path.join(ROOT, "trader-analyst", "metrics.example.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(doc), ["as_of", "companies", "note"])
        self.assertIn("ILLUSTRATIVE", doc["note"])
        want = sorted(facts.metrics_row("RELIANCE", self.result))
        self.assertEqual(len(doc["companies"]), 5)
        for company in doc["companies"]:
            self.assertEqual(sorted(company), want, company["ticker"])

    def test_the_skill_example_is_what_the_tool_prints_for_the_real_answer(self):
        text = facts.render("RELIANCE", self.result, today=datetime.date(2026, 11, 4))
        shown = [l for l in text.splitlines()
                 if l.startswith(("RELIANCE (RELIANCE.NS)", "quarter end", "2026-", "2025-", "Last 4 quarters"))]
        self.assertEqual(len(shown), 8)                       # the title, the header, five quarters, the last four
        for line in shown:
            self.assertIn(line, self.skill)
        self.assertIn("pip install curl_cffi", self.skill)


if __name__ == "__main__":
    unittest.main()
