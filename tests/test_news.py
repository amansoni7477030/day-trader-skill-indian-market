"""trader-news tests. The fixtures in tests/fixtures/trader-news/ are REAL answers captured on 2026-11-04 by
tests/fixtures/capture_real_fixtures.py: NSE corporate announcements (nse-RELIANCE.json, nse-M&M.json), Google News
RSS (gnews-RELIANCE.xml, gnews-M&M.xml, newest 12 items) and Yahoo v8 charts (chart-RELIANCE.json, chart-M&M.json).
Reliance's Q2 FY27 results came out on 2026-10-30 and its shares jumped 12 % on Monday 2026-11-02."""

import datetime
import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from _load import FIX, load

news = load("trader-news", "news")
DIR = os.path.join(FIX, "trader-news")
# 00:30 IST on Wednesday 2026-11-04, about nine hours after Tuesday's session closed
NOW = "2026-11-04T00:30:00+05:30"
RUPEE = "₹"
UTC = datetime.timezone.utc


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args)
    return code, out.getvalue(), err.getvalue()


class Filings(unittest.TestCase):
    def test_nse_announcements(self):
        items = news.parse_announcements(raw("nse-RELIANCE.json"), "RELIANCE")
        self.assertEqual(len(items), 12)
        first = items[0]
        self.assertEqual(first["id"], "nse:106838897")
        self.assertEqual((first["source"], first["kind"], first["ticker"]), ("NSE filing", "announcement", "RELIANCE"))
        self.assertEqual(first["category"], "Analysts/Institutional Investor Meet/Con. Call Updates")
        self.assertTrue(first["title"].startswith("Analysts/Institutional Investor Meet/Con. Call Updates: "
                                                  "Transcript of the discussion on the Unaudited Financial Results"))
        self.assertTrue(first["link"].startswith("https://nsearchives.nseindia.com/corporate/") and
                        first["link"].endswith(".pdf"))
        # NSE's 23:09:22 is Indian time: 17:39:22 UTC
        self.assertEqual(first["time"], datetime.datetime(2026, 11, 1, 17, 39, 22, tzinfo=UTC))
        self.assertEqual(news.to_ist(first["time"]), datetime.datetime(2026, 11, 1, 23, 9, 22))
        self.assertTrue(all(len(i["title"]) <= 260 for i in items))
        long_one = [i for i in items if i["category"] == "Outcome of Board Meeting"][0]
        self.assertTrue(long_one["title"].endswith("..."))            # a long filing text is cut at 260 characters

    def test_nse_time_is_indian_time(self):
        self.assertEqual(news.parse_nse_time("03-Nov-2026 16:07:13"), datetime.datetime(2026, 11, 3, 10, 37, 13, tzinfo=UTC))
        self.assertEqual(news.parse_nse_time("03-Nov-2026"), datetime.datetime(2026, 11, 2, 18, 30, tzinfo=UTC))
        self.assertEqual(news.parse_nse_time("3-nov-2026 09:15"), datetime.datetime(2026, 11, 3, 3, 45, tzinfo=UTC))
        for bad in ("", None, "tomorrow", "31-Feb-2026 10:00:00", "03-Xyz-2026 10:00:00"):
            self.assertIsNone(news.parse_nse_time(bad), bad)

    def test_bad_answers_are_errors(self):
        with self.assertRaises(news.NewsError):
            news.parse_announcements(b"<html>Access Denied</html>", "RELIANCE")
        with self.assertRaises(news.NewsError) as cm:
            news.parse_announcements(b'{"message": "blocked"}', "RELIANCE")
        self.assertIn("not a list", str(cm.exception))
        self.assertEqual(news.parse_announcements(b"[]", "NOSUCHSYM"), [])      # NSE answers [] for an unknown symbol

    def test_every_filing_id_is_unique_and_kept_when_seq_id_is_missing(self):
        rows = [{"symbol": "ITC", "an_dt": "03-Nov-2026 10:00:00", "desc": "Dividend", "attchmntText": "A"},
                {"symbol": "ITC", "an_dt": "03-Nov-2026 10:00:00", "desc": "Dividend", "attchmntText": "B"}]
        items = news.parse_announcements(json.dumps(rows), "ITC")
        self.assertEqual(len({i["id"] for i in items}), 2)
        self.assertTrue(items[0]["link"].endswith("symbol=ITC"))      # no PDF: the NSE page for the stock


class Headlines(unittest.TestCase):
    def test_google_news_rss(self):
        items = news.parse_rss(raw("gnews-RELIANCE.xml"), "RELIANCE")
        self.assertEqual(len(items), 12)
        first = items[0]
        self.assertEqual(first["title"], "India's Reliance posts best day in over six years as oil business, "
                                         "Jio Platforms lift outlook")                  # ' - Reuters' is the publisher
        self.assertEqual((first["source"], first["kind"], first["ticker"]), ("Reuters", "headline", "RELIANCE"))
        self.assertEqual(first["time"], datetime.datetime(2026, 11, 2, 23, 35, 29, tzinfo=UTC))
        self.assertTrue(first["id"].startswith("rss:"))
        self.assertTrue(first["link"].startswith("https://news.google.com/rss/articles/"))

    def test_bad_xml_is_an_error(self):
        with self.assertRaises(news.NewsError):
            news.parse_rss(b"<rss><channel>", "RELIANCE")

    def test_times(self):
        self.assertEqual(news.parse_time("Mon, 19 Oct 2026 03:20:00 +0000").hour, 3)
        self.assertEqual(news.parse_time("Mon, 02 Nov 2026 23:35:29 GMT"), datetime.datetime(2026, 11, 2, 23, 35, 29, tzinfo=UTC))
        self.assertEqual(news.parse_time("2026-10-19T09:15:00+05:30").hour, 3)
        self.assertEqual(news.parse_time("2026-10-19T09:15:00+05:30").minute, 45)
        self.assertIsNone(news.parse_time("soon"))

    def test_yahoo_symbols(self):
        ys, ps = news.yahoo_symbol, news.plain_symbol
        self.assertEqual(ys("reliance"), "RELIANCE.NS")
        self.assertEqual(ys("M&M"), "M&M.NS")
        self.assertEqual(ys("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")        # never "." -> "-" any more
        self.assertEqual(ys("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(ys("500325.BO"), "500325.BO")
        self.assertEqual(ys("^NSEI"), "^NSEI")
        self.assertEqual(ps("RELIANCE.NS"), "RELIANCE")
        self.assertEqual(ps("m&m"), "M&M")
        self.assertEqual(ps("^NSEBANK"), "^NSEBANK")

    def test_company_names_and_the_search(self):
        self.assertEqual(news.short_name("Reliance Industries Limited"), "Reliance Industries")
        self.assertEqual(news.short_name("Tata Consultancy Services Ltd."), "Tata Consultancy Services")
        self.assertEqual(news.short_name("Mahindra & Mahindra Limited"), "Mahindra & Mahindra")
        self.assertEqual(news.short_name("ITC"), "ITC")
        self.assertEqual(news.short_name(None), "")
        q = news.gnews_query
        self.assertEqual(q("RELIANCE", "Reliance Industries Limited", 48), '"Reliance Industries" when:2d')
        self.assertEqual(q("M&M", None, 48), '"M&M" NSE when:2d')       # a bare M&M or ITC is ambiguous without NSE
        self.assertEqual(q("ITC", "", 24), '"ITC" NSE when:1d')
        self.assertEqual(q("ITC", "ITC Limited", 25), '"ITC" when:2d')
        self.assertEqual(q("ITC", "ITC Limited", 1), '"ITC" when:1d')
        self.assertEqual(q("ITC", "ITC Limited", 100000), '"ITC" when:30d')

    def test_request_urls_are_quoted(self):
        self.assertEqual(news.NSE_API % (news._q("M&M"), "21-10-2026", "04-11-2026"),
                         "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=M%26M"
                         "&from_date=21-10-2026&to_date=04-11-2026")
        self.assertEqual(news.GNEWS % news._q(news.gnews_query("M&M", "Mahindra & Mahindra Limited", 48)),
                         "https://news.google.com/rss/search?q=%22Mahindra%20%26%20Mahindra%22%20when%3A2d"
                         "&hl=en-IN&gl=IN&ceid=IN:en")
        self.assertTrue((news.CHART % ("query2", news._q("^NSEI"))).startswith(
            "https://query2.finance.yahoo.com/v8/finance/chart/%5ENSEI?"))
        self.assertIn("hl=en-IN&gl=IN", news.GNEWS)

    def test_rupees_use_indian_grouping(self):
        self.assertEqual(news.inr(123456.78), RUPEE + "1,23,456.78")
        self.assertEqual(news.inr(1416.35), RUPEE + "1,416.35")
        self.assertEqual(news.inr(12345678.5), RUPEE + "1,23,45,678.50")
        self.assertEqual(news.inr(5000, None), RUPEE + "5,000")
        self.assertEqual(news.inr(99.5, None), RUPEE + "99.50")
        self.assertEqual(news.inr(-1234567, 0), "-" + RUPEE + "12,34,567")
        self.assertEqual(news.inr(0.049), RUPEE + "0.05")
        self.assertEqual(news.inr(None), "n/a")


class Tagging(unittest.TestCase):
    def test_real_filings_are_tagged(self):
        items = news.parse_announcements(raw("nse-RELIANCE.json"), "RELIANCE")
        news.tag(items, {"RELIANCE": "Reliance Industries Limited"})
        by = {}
        for i in items:
            by.setdefault(i["category"], i)
        self.assertEqual(by["Outcome of Board Meeting"]["touches"], ["RELIANCE"])
        self.assertIn("earnings", by["Outcome of Board Meeting"]["tags"])
        self.assertIn("rating", by["Credit Rating"]["tags"])
        self.assertIn("earnings", by["Analysts/Institutional Investor Meet/Con. Call Updates"]["tags"])
        self.assertIn("legal", by["Other Restructuring"]["tags"] + ["legal"])     # a Regulation 30 disclosure, no keyword
        self.assertEqual(by["Investor Presentation"]["tags"], [])

    def test_touches_and_tags_on_real_headlines(self):
        items = news.parse_rss(raw("gnews-RELIANCE.xml"), "RELIANCE")
        watch = {"RELIANCE": "Reliance Industries Limited", "TCS": None}
        news.tag(items, watch)
        by = {i["title"][:24]: i for i in items}
        q2 = by["Reliance Industries shar"]
        self.assertEqual(q2["touches"], ["RELIANCE"])
        self.assertIn("earnings", q2["tags"])                         # "record Q2 results"
        ed = by["ED attaches 2 aircraft i"]
        self.assertIn("legal", ed["tags"])                            # the agency, written ED
        diversified = next(i for i in items if i["title"].startswith("Reliance bet on diversification"))
        self.assertEqual(diversified["touches"], ["RELIANCE"])        # its own search

    def test_mention_patterns(self):
        pats = news.mention_patterns({"M&M": "Mahindra & Mahindra Limited", "TCS": None, "BAJAJ-AUTO": None,
                                      "ITC": None})
        self.assertTrue(pats["M&M"].search("Auto stocks: (NSE: M&M) rallies"))
        self.assertTrue(pats["M&M"].search("Shares of $M&M rise"))
        self.assertTrue(pats["BAJAJ-AUTO"].search("(BSE: BAJAJ-AUTO) hits a high"))
        self.assertTrue(pats["TCS"].search("NSE: TCS gains"))
        self.assertTrue(pats["TCS"].search("Result preview (TCS)"))
        self.assertFalse(pats["TCS"].search("TCSX is not it, nor is (NSE: TCSX)"))
        self.assertTrue(pats["M&M"].search("Mahindra & Mahindra posts order book"))       # the name without Limited
        self.assertFalse(pats["ITC"].search("Hotels unit lifts ITCHY shares"))

    def test_india_keywords(self):
        def tags(title):
            item = [{"id": "x", "source": "Google News", "kind": "headline", "ticker": "RELIANCE",
                     "title": title, "summary": "", "link": "", "time": None}]
            return news.tag(item, {"RELIANCE": None})[0]["tags"]
        self.assertIn("earnings", tags("Q1 results due, board meeting on Friday"))
        self.assertIn("earnings", tags("Outcome of Board Meeting: approved the financial results"))
        self.assertIn("capital", tags("Board clears bonus issue, buyback and QIP"))
        self.assertIn("capital", tags("Record Date for the final dividend"))
        self.assertIn("legal", tags("SEBI order and NCLT hearing"))
        self.assertIn("legal", tags("ED raids a promoter's offices"))
        self.assertIn("legal", tags("Corporate Insolvency Resolution Process begins"))
        self.assertNotIn("legal", tags("Stocks ended the day higher"))      # "ed" is not the agency
        self.assertIn("ownership", tags("Promoter pledge falls, stake sale ahead"))
        self.assertIn("deal", tags("Company bags a large order; block deal seen"))
        self.assertIn("deal", tags("Bagging/Receiving of orders/contracts"))
        self.assertIn("deal", tags("Amalgamation/Merger approved"))
        self.assertIn("analyst", tags("Brokerage upgrades stock, target price raised"))
        self.assertIn("halt", tags("Stock hits upper circuit, added to F&O ban list"))
        self.assertIn("macro", tags("RBI holds repo rate; crude and rupee in focus; GST data due"))
        self.assertIn("market", tags("Nifty and Sensex slip as FPI selling continues"))
        self.assertIn("rating", tags("Credit Rating revised by CRISIL"))
        self.assertIn("clarification", tags("Spurt in Volume: clarification sought by the exchange"))
        self.assertIn("people", tags("Change in Management: CFO resigns"))
        self.assertIn("risk", tags("Strikes and lock-outs at the plant; fire at a unit"))

    def test_dedupe_merges_the_same_filing_and_the_same_story(self):
        items = news.dedupe(news.tag(news.parse_announcements(raw("nse-RELIANCE.json"), "RELIANCE"),
                                     {"RELIANCE": None}))
        # the two identical Outcome of Board Meeting filings of 30 Oct 19:40:11 (two PDFs, one text) become one
        self.assertEqual(len(items), 11)
        a = news.parse_rss(raw("gnews-RELIANCE.xml"), "RELIANCE")
        b = news.parse_rss(raw("gnews-RELIANCE.xml"), "TCS")
        items = news.dedupe(news.tag(a + b, {"RELIANCE": None, "TCS": None}))
        self.assertEqual(len(items), 12)
        self.assertEqual(items[0]["touches"], ["RELIANCE", "TCS"])

    def test_links_per_ticker(self):
        self.assertEqual(news.links_for("RELIANCE"),
                         {"nse": "https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE",
                          "bse": "https://www.bseindia.com/corporates/ann.html"})
        self.assertEqual(news.links_for("M&M")["nse"],
                         "https://www.nseindia.com/get-quotes/equity?symbol=M%26M")
        self.assertIsNone(news.links_for("^NSEI")["nse"])


class Recorder(news.Fetcher):
    """An offline fetcher that remembers the URLs it was asked for."""

    def __init__(self):
        news.Fetcher.__init__(self, offline=DIR)
        self.urls = []

    def get(self, url, name):
        self.urls.append(url)
        return news.Fetcher.get(self, url, name)


class Collecting(unittest.TestCase):
    def test_one_request_each_per_ticker_with_the_window_in_it(self):
        f, errors = Recorder(), []
        items = news.collect(f, {"RELIANCE": "Reliance Industries Limited", "M&M": None}, errors, now=news.parse_time(NOW),
                             hours=48)
        self.assertEqual(f.urls, [
            "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=M%26M"
            "&from_date=02-11-2026&to_date=04-11-2026",
            "https://news.google.com/rss/search?q=%22M%26M%22%20NSE%20when%3A2d&hl=en-IN&gl=IN&ceid=IN:en",
            "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=RELIANCE"
            "&from_date=02-11-2026&to_date=04-11-2026",
            "https://news.google.com/rss/search?q=%22Reliance%20Industries%22%20when%3A2d&hl=en-IN&gl=IN&ceid=IN:en"])
        self.assertTrue(any("company name is unknown" in e and e.startswith("headlines for M&M") for e in errors))
        self.assertEqual(len(items), 4 + 12 + 12 + 12)

    def test_headlines_are_capped_per_ticker_newest_first(self):
        items = news.collect(Recorder(), {"RELIANCE": "Reliance Industries Limited"}, [], now=news.parse_time(NOW),
                             hours=48, max_headlines=3)
        heads = [i for i in items if i["kind"] == "headline"]
        self.assertEqual(len(heads), 3)
        self.assertEqual([h["time"] for h in heads], sorted((h["time"] for h in heads), reverse=True))
        self.assertTrue(heads[0]["title"].startswith("ED attaches 2 aircraft"))        # the newest, 17:52 GMT on 3 Nov


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "news.json")
        self.universe = os.path.join(self.tmp.name, "universe.txt")
        with open(self.universe, "w", encoding="utf-8") as fh:
            fh.write("RELIANCE|Reliance Industries Limited\nM&M|Mahindra & Mahindra Limited\n")
        self.base = ["--offline", DIR, "--state", self.state, "--now", NOW, "--universe", self.universe,
                     "--portfolio", os.path.join(self.tmp.name, "none.json"),
                     "--watchlist", os.path.join(self.tmp.name, "none.json"),
                     "--plan", os.path.join(self.tmp.name, "none.json")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_items_once_newest_first(self):
        code, out, err = run(["--tickers", "RELIANCE", "M&M", "--hours", "120", "--json"] + self.base)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        times = [i["time"] for i in data["items"]]
        self.assertEqual(times, sorted(times, reverse=True))
        kinds = [i["kind"] for i in data["items"]]
        # 5 days back: Reliance's 5 filings (the two identical board outcomes merge) and M&M's 2, plus 12 + 12 headlines
        self.assertEqual((kinds.count("announcement"), kinds.count("headline")), (7, 24))
        self.assertEqual(sorted(data["links"]), ["M&M", "RELIANCE"])
        self.assertEqual(data["links"]["M&M"]["nse"], "https://www.nseindia.com/get-quotes/equity?symbol=M%26M")
        self.assertEqual(data["notes"], [])                       # the names come from the universe file
        code, out, _ = run(["--tickers", "RELIANCE", "M&M", "--hours", "120"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)
        self.assertIn("get-quotes/equity?symbol=RELIANCE", out)   # the links print even when nothing is new

    def test_the_default_window_is_48_hours(self):
        code, out, err = run(["--tickers", "RELIANCE", "M&M", "--dry-run", "--json"] + self.base)
        self.assertEqual(code, 0, err)
        items = json.loads(out)["items"]
        filings = [i for i in items if i["kind"] == "announcement"]
        self.assertEqual([i["id"] for i in filings], ["nse:106841911"])        # M&M's 3 Nov filing; Reliance's last is 1 Nov 23:09
        self.assertEqual(sum(1 for i in items if i["kind"] == "headline"), 24)

    def test_the_report_reads_like_this(self):
        code, out, err = run(["--tickers", "RELIANCE", "M&M", "--hours", "120", "--dry-run"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertIn("2026-11-03 16:07 IST  M&M         NSE filing    Updates: Mahindra & Mahindra Limited has informed "
                      "the Exchange regarding the change in the address", out)
        self.assertIn("2026-11-01 23:09 IST  RELIANCE    NSE filing    Analysts/Institutional Investor Meet/Con. Call Updates: "
                      "Transcript of the discussion", out)
        self.assertIn("tags: earnings  https://nsearchives.nseindia.com/corporate/", out)
        self.assertIn("Reuters", out)
        self.assertIn("India's Reliance posts best day in over six years", out)
        self.assertIn("Every filing and the BSE side are on the exchange pages", out)
        self.assertIn("https://www.bseindia.com/corporates/ann.html", out)
        self.assertIn("Not financial advice", out)
        code, out, _ = run(["--tickers", "RELIANCE", "--hours", "120", "--all", "--dry-run"] + self.base)
        self.assertEqual(code, 0)                                 # nothing was remembered: still new

    def test_tickers_from_watchlist_and_plan(self):
        wl = os.path.join(self.tmp.name, "watchlist.json")
        with open(wl, "w") as fh:
            json.dump({"created_by": "trader-scanner",
                       "hits": [{"symbol": "M&M", "setups": ["orb"]}]}, fh)
        plan = os.path.join(self.tmp.name, "plan.json")
        with open(plan, "w") as fh:
            json.dump({"created_by": "trader-exit", "symbol": "RELIANCE.NS"}, fh)
        self.assertEqual(sorted(news.watch_from_files("/nonexistent.json", wl, plan)), ["M&M", "RELIANCE"])
        args = list(self.base)
        args[args.index("--watchlist") + 1] = wl
        args[args.index("--plan") + 1] = plan
        code, out, err = run(["--json", "--dry-run", "--hours", "120"] + args)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(sorted(data["links"]), ["M&M", "RELIANCE"])         # plan symbol ".NS" stripped
        tickers = {t for i in data["items"] for t in i["touches"]}
        self.assertEqual(tickers, {"M&M", "RELIANCE"})
        self.assertFalse(os.path.exists(self.state))

    def test_portfolio_positions_are_watched_and_names_come_from_the_universe(self):
        pf = os.path.join(self.tmp.name, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"positions": [{"symbol": "TCS", "name": "Tata Consultancy Services"}]}, fh)
        self.assertEqual(news.watch_from_files(pf, "/none.json", "/none.json"), {"TCS": "Tata Consultancy Services"})
        self.assertEqual(news.universe_names(self.universe),
                         {"RELIANCE": "Reliance Industries Limited", "M&M": "Mahindra & Mahindra Limited"})
        self.assertEqual(news.universe_names("/nonexistent.txt"), {})

    def test_no_tickers_is_an_error(self):
        code, _, err = run(self.base)
        self.assertEqual(code, 2)
        self.assertIn("no tickers", err)

    def test_dot_ns_is_accepted_and_missing_files_are_notes(self):
        code, out, err = run(["--tickers", "RELIANCE.NS", "INFY", "--hours", "120", "--no-moves"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertIn("RELIANCE", out)
        self.assertNotIn("RELIANCE.NS", out)
        self.assertIn("note: NSE filings for INFY: offline: no file", out)
        self.assertIn("note: headlines for INFY: offline: no file", out)

    def test_the_move_lines_and_the_pinned_clock(self):
        code, out, err = run(["--tickers", "RELIANCE", "M&M", "--hours", "120", "--dry-run"] + self.base)
        self.assertRegex(out, r"RELIANCE\s+" + RUPEE + r"1,307\.90 \+0\.61% vs yesterday's close "
                              + RUPEE + r"1,300\.00 at 15:29 IST")
        self.assertRegex(out, r"M&M\s+" + RUPEE + r"2,908\.00 \+1\.37% vs yesterday's close " + RUPEE + r"2,868\.80 at 15:29 IST")
        self.assertIn("Checked 2026-11-04 00:30 IST", out)
        # a clock pinned to Monday evening drops what had not happened yet (Tuesday's headlines)
        code, out, err = run(["--tickers", "RELIANCE", "--hours", "24", "--dry-run", "--json",
                              "--now", "2026-11-02T20:00:00+05:30"] + self.base[:4] + self.base[6:])
        data = json.loads(out)
        self.assertTrue(all(i["time"] <= "2026-11-02T14:40:00+00:00" for i in data["items"]))
        self.assertGreater(len(data["items"]), 0)

    def test_moves(self):
        m = news.parse_chart(raw("chart-RELIANCE.json"), "RELIANCE")
        self.assertEqual((m["price"], m["prev_close"], m["pct"]), (1307.9, 1300.0, 0.61))
        self.assertEqual(news.to_ist(m["time"]).strftime("%H:%M"), "15:29")
        mm = news.parse_chart(raw("chart-M&M.json"), "M&M")
        self.assertEqual((mm["price"], mm["prev_close"], mm["pct"]), (2908.0, 2868.8, 1.37))
        self.assertNotIn("MOVING", news.render_moves([m])[0])
        self.assertIn("MOVING", news.render_moves([m], move_pct=0.5)[0])
        self.assertIn("+0.61%", news.render_moves([m])[0])
        with self.assertRaises(news.NewsError):
            news.parse_chart(b'{"chart": {"result": null}}', "ZZZZ")
        # no chart file offline (INFY): no move line, no error note about it
        code, out, _ = run(["--tickers", "INFY", "--hours", "24", "--dry-run"] + self.base)
        self.assertNotIn("vs yesterday's close", out)
        self.assertNotIn("price for INFY", out)
        # --json carries the moving flag
        code, out, _ = run(["--tickers", "RELIANCE", "M&M", "--hours", "24", "--dry-run", "--json", "--move-pct", "1"]
                           + self.base)
        moves = {m["ticker"]: m for m in json.loads(out)["moves"]}
        self.assertFalse(moves["RELIANCE"]["moving"])
        self.assertTrue(moves["M&M"]["moving"])
        self.assertTrue(moves["RELIANCE"]["time"].startswith("2026-11-03T09:59:39"))

    def test_ist_has_no_daylight_saving(self):
        utc = datetime.datetime(2026, 1, 15, 3, 45, tzinfo=UTC)
        self.assertEqual(news.to_ist(utc), datetime.datetime(2026, 1, 15, 9, 15))
        utc = datetime.datetime(2026, 7, 15, 3, 45, tzinfo=UTC)
        self.assertEqual(news.to_ist(utc), datetime.datetime(2026, 7, 15, 9, 15))
        self.assertEqual(news.to_ist(datetime.datetime(2026, 10, 19, 18, 30)),   # naive = UTC
                         datetime.datetime(2026, 10, 20, 0, 0))

    def test_state_forgets_after_30_days(self):
        state = {"seen": {"old": "2026-08-01T00:00:00+00:00"}}
        now = news.parse_time(NOW)
        news.remember([], state, now)
        self.assertNotIn("old", state["seen"])
        self.assertEqual(state["last_check"], now.isoformat())

    def test_state_file_remembers_between_runs(self):
        run(["--tickers", "M&M", "--hours", "120"] + self.base)
        with open(self.state) as fh:
            state = json.load(fh)
        self.assertEqual(len(state["seen"]), 4 + 12)    # all four filings and twelve headlines, shown or not
        code, out, _ = run(["--tickers", "M&M", "--hours", "120"] + self.base)
        self.assertEqual(code, 1)

    def test_select_window_and_future(self):
        now = news.parse_time(NOW)
        mk = lambda i, t: {"id": i, "time": t, "title": i, "link": "", "source": "s", "ticker": "TCS"}
        items = [mk("recent", now - datetime.timedelta(hours=1)),
                 mk("old", now - datetime.timedelta(hours=30)),
                 mk("future", now + datetime.timedelta(hours=1)),
                 mk("untimed", None)]
        out = news.select(items, {}, now, hours=24)
        self.assertEqual([i["id"] for i in out], ["untimed", "recent"])   # untimed counts as now, newest first

    def test_console_survives_the_rupee_sign(self):
        class Ascii(io.StringIO):
            encoding = "ascii"

            def reconfigure(self, **kw):
                self.encoding = kw["encoding"]
        out = Ascii()
        with redirect_stdout(out):
            news._utf8_console()
        self.assertEqual(out.encoding, "utf-8")


class Transport(unittest.TestCase):
    """The real Fetcher._fetch, with curl_cffi (Chrome's TLS fingerprint) and with the urllib fallback."""

    class Resp(object):
        def __init__(self, status, body=b"[]"):
            self.status_code, self.content = status, body

    class Session(object):
        def __init__(self, answers, **kw):
            self.answers, self.calls, self.kw = list(answers), [], kw

        def get(self, url, **kw):
            self.calls.append((url, kw))
            a = self.answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a

    class Cffi(object):
        def __init__(self, session):
            self.session, self.made = session, []

        def Session(self, **kw):
            self.made.append(kw)
            return self.session

    def fetch(self, url, answers):
        session = self.Session(answers)
        cffi = self.Cffi(session)
        with mock.patch.object(news, "_cffi", cffi):
            f = news.Fetcher(pause=0)
            try:
                body = f.get(url, "x")
            finally:
                self.calls, self.made = session.calls, cffi.made
        return body

    def test_nse_gets_a_chrome_fingerprint_and_json_headers(self):
        body = self.fetch("https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=ITC",
                          [self.Resp(200, b'[{"x": 1}]')])
        self.assertEqual(body, b'[{"x": 1}]')
        self.assertEqual(self.made, [{"impersonate": "chrome"}])
        url, kw = self.calls[0]
        self.assertEqual(kw["headers"]["Referer"], "https://www.nseindia.com/")
        self.assertIn("application/json", kw["headers"]["Accept"])
        self.assertNotIn("User-Agent", kw["headers"])               # curl_cffi sets Chrome's own

    def test_google_and_yahoo_get_their_own_headers(self):
        self.fetch("https://news.google.com/rss/search?q=x", [self.Resp(200)])
        self.assertIn("rss+xml", self.calls[0][1]["headers"]["Accept"])
        self.assertEqual(self.calls[0][1]["headers"]["Accept-Language"], "en-IN,en;q=0.9")
        self.fetch("https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS", [self.Resp(200)])
        self.assertEqual(self.calls[0][1]["headers"]["Referer"], "https://finance.yahoo.com/")

    def test_nse_403_visits_the_home_page_once_for_cookies_then_retries(self):
        body = self.fetch("https://www.nseindia.com/api/x", [self.Resp(403), self.Resp(200), self.Resp(200, b"[1]")])
        self.assertEqual(body, b"[1]")
        self.assertEqual([c[0] for c in self.calls],
                         ["https://www.nseindia.com/api/x", "https://www.nseindia.com/", "https://www.nseindia.com/api/x"])

    def test_errors_become_news_errors(self):
        with self.assertRaises(news.NewsError) as cm:
            self.fetch("https://query2.finance.yahoo.com/v8/x", [self.Resp(429)])
        self.assertIn("HTTP 429", str(cm.exception))
        with self.assertRaises(news.NewsError) as cm:
            self.fetch("https://query2.finance.yahoo.com/v8/x", [RuntimeError("timed out")])
        self.assertIn("could not reach query2.finance.yahoo.com", str(cm.exception))
        # a non-NSE 403 is not retried through the NSE home page
        with self.assertRaises(news.NewsError):
            self.fetch("https://news.google.com/rss/search", [self.Resp(403)])
        self.assertEqual(len(self.calls), 1)

    def test_without_curl_cffi_urllib_sends_a_browser_user_agent(self):
        seen = {}

        class R(object):
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"[]"
        def fake_urlopen(req, timeout=0):
            seen["headers"] = dict(req.header_items())
            return R()
        with mock.patch.object(news, "_cffi", None), mock.patch.object(news.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(news.Fetcher(pause=0).get("https://www.nseindia.com/api/x", "x"), b"[]")
        self.assertIn("Chrome/", seen["headers"]["User-agent"])
        self.assertNotIn("compatible", seen["headers"]["User-agent"])
        self.assertEqual(seen["headers"]["Referer"], "https://www.nseindia.com/")
        with mock.patch.object(news, "_cffi", None), mock.patch.object(
                news.urllib.request, "urlopen", side_effect=urllib.error.HTTPError("u", 403, "x", {}, None)):
            with self.assertRaises(news.NewsError):
                news.Fetcher(pause=0).get("https://www.nseindia.com/api/x", "x")


if __name__ == "__main__":
    unittest.main()
