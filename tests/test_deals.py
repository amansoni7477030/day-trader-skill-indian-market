import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from _load import FIX, load

deals = load("trader-deals", "deals")
# REAL NSE files for 3 Nov 2026. DIR holds an EXCERPT: the first 30 rows of the bulk file (the file lists the buys
# first, so they are all BUY rows) and its two block deals. DIR/full-day holds both COMPLETE files, byte for byte as
# served (147 bulk rows, 2 block rows), and is what the SKILL.md example is built from.
DIR = os.path.join(FIX, "trader-deals")
FULL = os.path.join(DIR, "full-day")
BULK = os.path.join(DIR, "bulk-2026-11-03.csv")
BLOCK = os.path.join(DIR, "block-2026-11-03.csv")
RS = "₹"
DAY = "2026-11-03"

HEADER_BULK = "Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price,Remarks\n"
HEADER_BLOCK = "Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price\n"


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = deals.main(args)
    return code, out.getvalue(), err.getvalue()


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def kind_deals(kind):
    path = BULK if kind == "bulk" else BLOCK
    return deals.parse_csv(read(path), kind)


class Money(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(deals.indian_number(0), "0")
        self.assertEqual(deals.indian_number(999), "999")
        self.assertEqual(deals.indian_number(1000), "1,000")
        self.assertEqual(deals.indian_number(123456), "1,23,456")
        self.assertEqual(deals.indian_number(1347189), "13,47,189")
        self.assertEqual(deals.indian_number(30368891), "3,03,68,891")
        self.assertEqual(deals.indian_number(-5000000), "-50,00,000")
        self.assertEqual(deals.indian_number(1234567.5, 2), "12,34,567.50")

    def test_inr(self):
        self.assertEqual(deals.inr(123456.78), RS + "1,23,456.78")
        self.assertEqual(deals.inr(5000, None), RS + "5,000")
        self.assertEqual(deals.inr(263.16), RS + "263.16")
        self.assertEqual(deals.inr(1234.5, None), RS + "1,234.50")
        self.assertEqual(deals.inr(-1500, 0), "-" + RS + "1,500")

    def test_crore_lakh_and_rupees(self):
        self.assertEqual(deals.money_cr(808.595747788), RS + "808.60 cr")
        self.assertEqual(deals.money_cr(12345.6), RS + "12,345.60 cr")
        self.assertEqual(deals.money_cr(0.45), RS + "45.00 lakh")
        self.assertEqual(deals.money_cr(0.0004), RS + "4,000")
        self.assertEqual(deals.money_cr(0), RS + "0")
        self.assertEqual(deals.money_cr(-35.4526), "-" + RS + "35.45 cr")
        self.assertEqual(deals.signed_cr(35.4526), "+" + RS + "35.45 cr")
        self.assertEqual(deals.signed_cr(0), RS + "0")

    def test_value_in_crore_is_qty_times_price_over_1e7(self):
        self.assertEqual(deals.value_cr(5000000, 501.0), 250.5)      # 5e6 x 501 = 2.505e9 = 250.5 crore
        self.assertAlmostEqual(deals.value_cr(1347189, 263.16), 35.452625724, places=9)
        self.assertEqual(deals.value_cr(10000000, 1.0), 1.0)


class Parse(unittest.TestCase):
    def test_real_bulk_file(self):
        rows = kind_deals("bulk")
        self.assertEqual(len(rows), 30)
        self.assertEqual({r["date"] for r in rows}, {DAY})
        self.assertTrue(all(r["kind"] == "bulk" and r["side"] == "BUY" for r in rows))
        first = rows[0]
        self.assertEqual((first["symbol"], first["name"], first["client"]),
                         ("AGIIL", "Agi Infra Limited", "ARIHANT CAPITAL MARKETS LIMITED"))
        self.assertEqual((first["qty"], first["price"]), (1347189, 263.16))
        self.assertAlmostEqual(first["value_cr"], 35.452625724, places=9)   # 1347189 x 263.16 / 1e7
        self.assertEqual(first["remarks"], "")                              # the file's "-"
        self.assertEqual(sum(1 for r in rows if r["symbol"] == "BLSPOLY"), 28)

    def test_real_block_file(self):
        rows = kind_deals("block")
        self.assertEqual([(r["symbol"], r["side"], r["qty"], r["price"], r["value_cr"]) for r in rows],
                         [("GALLANTT", "BUY", 5000000, 501.0, 250.5),
                          ("GALLANTT", "SELL", 5000000, 501.0, 250.5)])
        self.assertEqual(rows[0]["client"], "SURYALAXMI INFRACON PRIVATE LIMITED")
        self.assertEqual(rows[1]["client"], "DINESH R AGARWAL")

    def test_dates(self):
        self.assertEqual(deals.parse_date("03-NOV-2026"), DAY)
        self.assertEqual(deals.parse_date("3-Nov-2026"), DAY)
        self.assertEqual(deals.parse_date("2026-11-03"), DAY)
        self.assertEqual(deals.parse_date("03/11/2026"), DAY)
        self.assertIsNone(deals.parse_date("31-FEB-2026"))
        self.assertIsNone(deals.parse_date("03-XYZ-2026"))
        self.assertIsNone(deals.parse_date(""))

    def test_bom_quotes_and_commas_in_names(self):
        text = ("﻿" + HEADER_BULK +
                '03-NOV-2026,ABC,"ABC Industries, Ltd","SMITH, JOHN & SONS HUF",BUY,"1,20,000","1,234.50",-\n'
                '03-NOV-2026,ABC,"ABC Industries, Ltd","Goldman Sachs (Singapore) Pte",Sell,50000,1200.05,Pledge\n')
        rows = deals.parse_csv(text, "bulk")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "ABC Industries, Ltd")
        self.assertEqual(rows[0]["client"], "SMITH, JOHN & SONS HUF")
        self.assertEqual((rows[0]["qty"], rows[0]["price"]), (120000, 1234.5))
        self.assertAlmostEqual(rows[0]["value_cr"], 14.814, places=9)        # 120000 x 1234.50 = 148,140,000
        self.assertEqual(rows[1]["side"], "SELL")
        self.assertEqual(rows[1]["remarks"], "Pledge")
        # the same bytes with a UTF-8 BOM, as the network would deliver them
        self.assertEqual(len(deals.parse_csv(text.encode("utf-8"), "bulk")), 2)

    def test_bad_rows_are_skipped_and_reported(self):
        text = (HEADER_BLOCK +
                "03-NOV-2026,AAA,Aaa Ltd,CLIENT ONE,BUY,1000,10.50\n"
                "03-NOV-2026,BBB,Bbb Ltd,CLIENT TWO,HOLD,1000,10.50\n"
                "not-a-date,CCC,Ccc Ltd,CLIENT THREE,BUY,1000,10.50\n"
                "03-NOV-2026,DDD,Ddd Ltd,CLIENT FOUR,BUY,abc,10.50\n"
                "03-NOV-2026,EEE,Eee Ltd\n"
                "\n")
        problems = []
        rows = deals.parse_csv(text, "block", problems)
        self.assertEqual([r["symbol"] for r in rows], ["AAA"])
        self.assertEqual(len(problems), 4)
        self.assertIn("row 3", problems[0])

    def test_wrong_files_are_errors(self):
        with self.assertRaises(deals.DealsError) as cm:
            deals.parse_csv("Name,Value\nA,1\n", "bulk")
        self.assertIn("does not look like an NSE deals file", str(cm.exception))
        with self.assertRaises(deals.DealsError) as cm:
            deals.parse_csv("<html><body>Access Denied</body></html>", "bulk")
        self.assertIn("web page", str(cm.exception))
        with self.assertRaises(deals.DealsError):
            deals.parse_csv("x", "options")

    def test_header_only_and_empty_files_have_no_deals(self):
        self.assertEqual(deals.parse_csv(HEADER_BLOCK, "block"), [])
        self.assertEqual(deals.parse_csv("", "block"), [])

    def test_key_is_stable_and_distinguishes_deals(self):
        rows = kind_deals("bulk")
        self.assertEqual(len(deals.deal_key(rows[0])), 40)
        self.assertEqual(deals.deal_key(rows[0]), deals.deal_key(dict(rows[0])))
        self.assertEqual(len({deals.deal_key(r) for r in rows}), 30)   # J4S's two deals differ by qty and price
        other = dict(rows[0], qty=rows[0]["qty"] + 1)
        self.assertNotEqual(deals.deal_key(rows[0]), deals.deal_key(other))


class SelectAndGroup(unittest.TestCase):
    def setUp(self):
        self.all = kind_deals("bulk") + kind_deals("block")

    def test_group_by_symbol(self):
        groups = deals.group(self.all)
        self.assertEqual([g["symbol"] for g in groups], ["BLSPOLY", "GALLANTT", "AGIIL", "COMSYN"])
        bls = groups[0]
        self.assertEqual((bls["deals"], bls["clients"], bls["kinds"]), (28, 27, "bulk"))
        self.assertEqual((bls["buy_qty"], bls["sell_qty"], bls["net_qty"]), (30368891, 0, 30368891))
        self.assertAlmostEqual(bls["buy_cr"], 808.595747788, places=6)    # checked with exact fractions
        self.assertAlmostEqual(bls["net_cr"], 808.595747788, places=6)
        gal = groups[1]
        self.assertEqual((gal["buy_qty"], gal["sell_qty"], gal["net_qty"]), (5000000, 5000000, 0))
        self.assertEqual((gal["buy_cr"], gal["sell_cr"], gal["net_cr"]), (250.5, 250.5, 0))
        self.assertEqual(gal["kinds"], "block")

    def test_net_with_sells(self):
        rows = [dict(self.all[0], side="SELL", qty=1000000, price=100.0, value_cr=10.0),
                dict(self.all[0], side="BUY", qty=300000, price=100.0, value_cr=3.0)]
        g = deals.group(rows)[0]
        self.assertEqual((g["net_qty"], g["net_cr"]), (-700000, -7.0))
        self.assertIn("-" + RS + "7.00 cr", deals.render_summary([g])[1])

    def test_kinds_mix(self):
        mix = [dict(self.all[0]), dict(self.all[-1], symbol="AGIIL")]
        self.assertEqual(deals.group(mix)[0]["kinds"], "bulk+block")

    def test_select_filters(self):
        self.assertEqual(len(deals.select(self.all, symbols=["blspoly"])), 28)
        self.assertEqual(len(deals.select(self.all, symbols=["BLSPOLY.NS", "COMSYN"])), 29)
        self.assertEqual(len(deals.select(self.all, client="arihant")), 2)
        self.assertEqual(len(deals.select(self.all, client="j4s financial")), 2)
        big = deals.select(self.all, min_value_cr=50)
        # BLSPOLY deals of at least 50 crore: 90.08, 83.44, 66.93, 62.89, 56.31 -> five, plus the two blocks
        self.assertEqual(sum(1 for d in big if d["symbol"] == "BLSPOLY"), 5)
        self.assertEqual(sum(1 for d in big if d["symbol"] == "GALLANTT"), 2)
        self.assertEqual(len(big), 7)
        self.assertEqual(deals.select(self.all, symbols=["NOPE"]), [])
        self.assertEqual(len(deals.select(self.all)), 32)

    def test_group_marks_your_list(self):
        groups = deals.group(self.all, watch={"AGIIL", "TCS"})
        self.assertEqual([g["symbol"] for g in groups if g["on_list"]], ["AGIIL"])
        lines = deals.render_summary(groups)
        self.assertTrue(any(l.startswith("AGIIL *") for l in lines))
        self.assertFalse(any(l.startswith("BLSPOLY *") for l in lines))


class YourList(unittest.TestCase):
    def test_watch_set_from_watchlist_plan_and_portfolio(self):
        with tempfile.TemporaryDirectory() as tmp:
            def put(name, doc):
                p = os.path.join(tmp, name)
                with open(p, "w") as fh:
                    json.dump(doc, fh)
                return p
            wl = put("watchlist.json", {"hits": [{"symbol": "agiil"}, {"ticker": "M&M"}]})
            pl = put("plan.json", {"symbol": "COMSYN.NS"})
            pf = put("portfolio.json", {"positions": [{"symbol": "GALLANTT"}, {"ticker": "tcs"}]})
            self.assertEqual(deals.watch_set(wl, pl, pf), {"AGIIL", "M&M", "COMSYN", "GALLANTT", "TCS"})
            self.assertEqual(deals.watch_set(os.path.join(tmp, "none.json"), None, None), set())
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w") as fh:
                fh.write("{oops")
            with self.assertRaises(deals.DealsError):
                deals.watch_set(bad)


class Fetching(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_pause = deals.PAUSE
        deals.PAUSE = 0
        self.addCleanup(setattr, deals, "PAUSE", self.old_pause)

    def test_offline_plain_names(self):
        shutil.copy(BULK, os.path.join(self.tmp.name, "bulk.csv"))
        shutil.copy(BLOCK, os.path.join(self.tmp.name, "block.csv"))
        rows, problems = deals.fetch(("bulk", "block"), self.tmp.name)
        self.assertEqual((len(rows), problems), (32, []))

    def test_offline_dated_names_use_the_latest(self):
        shutil.copy(BLOCK, os.path.join(self.tmp.name, "block-2026-11-02.csv"))
        with open(os.path.join(self.tmp.name, "block-2026-11-03.csv"), "wb") as fh:
            fh.write(read(BLOCK))
        with open(os.path.join(self.tmp.name, "block-2026-11-02.csv"), "w") as fh:
            fh.write(HEADER_BLOCK)
        rows, _ = deals.fetch(("block",), self.tmp.name)
        self.assertEqual(len(rows), 2)

    def test_offline_missing_file_and_folder(self):
        with self.assertRaises(deals.DealsError) as cm:
            deals.fetch(("bulk",), self.tmp.name)
        self.assertIn("no bulk.csv", str(cm.exception))
        with self.assertRaises(deals.DealsError):
            deals.fetch(("bulk",), os.path.join(self.tmp.name, "missing"))

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

    def test_network_download_is_a_chrome_request_through_curl_cffi(self):
        """NSE drops plain script clients (the connection just hangs): the request carries Chrome's TLS
        fingerprint (curl_cffi impersonate) and the headers a browser sends to nsearchives.nseindia.com."""
        fake = self.FakeCffi(self.Resp(200, read(BLOCK)))
        url = "https://nsearchives.nseindia.com/content/equities/block.csv"
        with mock.patch.object(deals, "_cffi", fake):
            data = deals.download(url, timeout=11)
        self.assertEqual(data, read(BLOCK))
        got_url, kw = fake.calls[0]
        self.assertEqual(got_url, url)
        self.assertEqual((kw["impersonate"], kw["timeout"]), ("chrome", 11))
        self.assertEqual(kw["headers"]["Referer"], "https://www.nseindia.com/")
        self.assertIn("text/csv", kw["headers"]["Accept"])
        self.assertIn("Accept-Language", kw["headers"])
        self.assertNotIn("User-Agent", kw["headers"])           # curl_cffi sends Chrome's own, matching its TLS
        self.assertNotIn("Cookie", kw["headers"])
        self.assertEqual(deals.URLS, {"bulk": "https://nsearchives.nseindia.com/content/equities/bulk.csv",
                                      "block": url})

    def test_without_curl_cffi_urllib_sends_the_same_headers_and_a_chrome_user_agent(self):
        seen = []

        def fake(req, timeout=0):
            seen.append((req, timeout))
            return io.BytesIO(read(BLOCK))

        with mock.patch.object(deals, "_cffi", None), mock.patch.object(deals.urllib.request, "urlopen", fake):
            data = deals.download("https://nsearchives.nseindia.com/content/equities/block.csv")
        self.assertEqual(data, read(BLOCK))
        req, timeout = seen[0]
        self.assertEqual(req.full_url, "https://nsearchives.nseindia.com/content/equities/block.csv")
        self.assertEqual((req.get_method(), timeout), ("GET", 60))
        self.assertEqual(req.get_header("User-agent"), deals.BROWSER_UA)
        self.assertIn("Chrome/", deals.BROWSER_UA)
        self.assertNotIn("compatible", deals.BROWSER_UA)         # not a bot-looking User-Agent
        self.assertEqual(req.get_header("Referer"), "https://www.nseindia.com/")
        self.assertNotIn("Cookie", req.headers)

    def test_one_file_failing_is_a_note_both_failing_is_an_error(self):
        real = deals.download

        def only_bulk(url, timeout=60):
            if url.endswith("block.csv"):
                raise deals.DealsError("HTTP 404 for " + url)
            return read(BULK)

        deals.download = only_bulk
        try:
            rows, problems = deals.fetch(("bulk", "block"))
            self.assertEqual(len(rows), 30)
            self.assertEqual(len(problems), 1)
            self.assertIn("could not read block: HTTP 404", problems[0])

            def down(url, timeout=60):
                raise deals.DealsError("could not reach %s: timed out" % url)

            deals.download = down
            with self.assertRaises(deals.DealsError) as cm:
                deals.fetch(("bulk", "block"))
            self.assertIn("bulk: could not reach", str(cm.exception))
            self.assertIn("block: could not reach", str(cm.exception))
        finally:
            deals.download = real

    def test_download_wraps_http_and_network_errors_through_curl_cffi(self):
        for code in (403, 404, 429, 503):
            with mock.patch.object(deals, "_cffi", self.FakeCffi(self.Resp(code, b"denied"))):
                with self.assertRaises(deals.DealsError) as cm:
                    deals.download("https://example.invalid/x.csv")
            self.assertIn("HTTP %d for https://example.invalid/x.csv" % code, str(cm.exception))
        with mock.patch.object(deals, "_cffi", self.FakeCffi(exc=RuntimeError("timed out"))):
            with self.assertRaises(deals.DealsError) as cm:
                deals.download("https://example.invalid/x.csv")
        self.assertIn("could not reach https://example.invalid/x.csv: timed out", str(cm.exception))

    def test_download_wraps_http_and_url_errors_through_urllib(self):
        import urllib.error

        def http(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

        def url(req, timeout=0):
            raise urllib.error.URLError("no route")

        with mock.patch.object(deals, "_cffi", None):
            with mock.patch.object(deals.urllib.request, "urlopen", http):
                with self.assertRaises(deals.DealsError) as cm:
                    deals.download("https://example.invalid/x.csv")
            self.assertIn("HTTP 403", str(cm.exception))
            with mock.patch.object(deals.urllib.request, "urlopen", url):
                with self.assertRaises(deals.DealsError) as cm:
                    deals.download("https://example.invalid/x.csv")
            self.assertIn("could not reach", str(cm.exception))


class Latest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.none = os.path.join(self.tmp.name, "none.json")
        self.base = ["--offline", DIR, "--watchlist", self.none, "--plan", self.none,
                     "--portfolio", self.none]

    def test_latest_both(self):
        code, out, _ = run(["latest"] + self.base)
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "NSE bulk and block deals for 03-Nov-2026: 30 bulk, 2 block deals")
        table = out.split("By symbol")[1].split("Every deal")[0]
        self.assertIn("BLSPOLY   BLS Polymers Limited     bulk      28       27  3,03,68,891  +" + RS + "808.60 cr  "
                      + RS + "808.60 cr          " + RS + "0", table)
        self.assertIn("GALLANTT  Gallantt Ispat Limited   block      2        2            0           " + RS + "0  "
                      + RS + "250.50 cr  " + RS + "250.50 cr", table)
        self.assertIn("AGIIL", table)
        self.assertIn("+" + RS + "35.45 cr", table)
        self.assertIn("+" + RS + "4.43 cr", table)
        self.assertIn("13,47,189         " + RS + "263.16   " + RS + "35.45 cr  ARIHANT CAPITAL", out)
        self.assertIn("50,00,000         " + RS + "501.00  " + RS + "250.50 cr  DINESH R AGARWAL", out)
        self.assertIn("not inside information", out)
        self.assertIn("Not financial advice", out)
        self.assertNotIn("2014", out)
        self.assertNotIn("*", out)   # nothing on a list

    def test_kind_filter(self):
        code, out, _ = run(["latest", "--kind", "block"] + self.base)
        self.assertEqual(code, 0)
        self.assertIn("0 bulk, 2 block deals", out)
        self.assertNotIn("BLSPOLY", out)
        code, out, _ = run(["latest", "--kind", "bulk"] + self.base)
        self.assertIn("30 bulk, 0 block deals", out)
        self.assertNotIn("GALLANTT", out)

    def test_symbol_client_and_min_value_filters(self):
        code, out, _ = run(["latest", "--symbol", "gallantt", "comsyn"] + self.base)
        self.assertEqual(code, 0)
        self.assertIn("1 bulk, 2 block deals", out)
        self.assertNotIn("BLSPOLY", out)
        self.assertIn("Filter: symbol gallantt, comsyn.", out)
        code, out, _ = run(["latest", "--client", "Arihant", "--summary"] + self.base)
        self.assertIn("2 bulk, 0 block deals", out)
        self.assertNotIn("Every deal", out)
        code, out, _ = run(["latest", "--min-value-cr", "80"] + self.base)
        self.assertIn("2 bulk, 2 block deals", out)     # BLSPOLY 90.08 and 83.44 crore; the two blocks
        self.assertEqual(out.count("BLSPOLY"), 3)       # table row + the two deals
        self.assertIn("Net figures cover only the deals shown", out)
        self.assertIn("each deal at least " + RS + "80.00 cr", out)

    def test_nothing_matches_exits_1(self):
        code, out, _ = run(["latest", "--symbol", "TCS"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("No bulk/block deals match: symbol TCS", out)
        self.assertIn("32 deal(s) in the latest file", out)

    def test_marks_deals_in_your_watchlist_plan_portfolio(self):
        wl = os.path.join(self.tmp.name, "watchlist.json")
        with open(wl, "w") as fh:
            json.dump({"hits": [{"symbol": "AGIIL"}]}, fh)
        pf = os.path.join(self.tmp.name, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"positions": [{"symbol": "GALLANTT"}]}, fh)
        code, out, _ = run(["latest", "--offline", DIR, "--watchlist", wl, "--plan", self.none,
                            "--portfolio", pf])
        self.assertEqual(code, 0)
        self.assertIn("AGIIL *", out)
        self.assertIn("GALLANTT *", out)
        self.assertNotIn("BLSPOLY *", out)
        self.assertIn("* on your watchlist, plan or portfolio", out)
        self.assertEqual(out.count("AGIIL *"), 2)         # the table and the deal line

    def test_json(self):
        code, out, _ = run(["latest", "--json", "--symbol", "GALLANTT"] + self.base)
        doc = json.loads(out)
        self.assertEqual(doc["dates"], [DAY])
        self.assertEqual(len(doc["deals"]), 2)
        self.assertEqual(doc["symbols"][0]["symbol"], "GALLANTT")
        self.assertEqual(doc["symbols"][0]["net_cr"], 0)
        self.assertEqual(doc["symbols"][0]["buy_cr"], 250.5)

    def test_bad_inputs_are_errors(self):
        code, _, err = run(["latest", "--offline", os.path.join(self.tmp.name, "missing")])
        self.assertEqual(code, 1)
        self.assertIn("deals:", err)
        self.assertEqual(run([])[0], 2)

    def test_network_errors_with_a_monkeypatched_downloader(self):
        real, pause = deals.download, deals.PAUSE
        deals.PAUSE = 0

        def down(url, timeout=60):
            raise deals.DealsError("could not reach %s: timed out" % url)

        deals.download = down
        try:
            code, out, err = run(["latest", "--watchlist", self.none, "--plan", self.none,
                                  "--portfolio", self.none])
        finally:
            deals.download, deals.PAUSE = real, pause
        self.assertEqual(code, 1)
        self.assertIn("deals: bulk: could not reach", err)
        self.assertEqual(out, "")

    def test_web_page_instead_of_csv_is_an_error(self):
        real, pause = deals.download, deals.PAUSE
        deals.PAUSE = 0
        deals.download = lambda url, timeout=60: b"<!DOCTYPE html><html>Access Denied</html>"
        try:
            code, _, err = run(["latest", "--kind", "bulk", "--watchlist", self.none, "--plan",
                                self.none, "--portfolio", self.none])
        finally:
            deals.download, deals.PAUSE = real, pause
        self.assertEqual(code, 1)
        self.assertIn("web page instead of a CSV", err)


class NewAndHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.state = os.path.join(t, "state", "deals.json")
        self.hist = os.path.join(t, "state", "deals-history.json")
        none = os.path.join(t, "none.json")
        self.base = ["new", "--offline", DIR, "--state", self.state, "--history-file", self.hist,
                     "--watchlist", none, "--plan", none, "--portfolio", none, "--today", "2026-11-03"]

    def test_new_shows_once_then_nothing_new(self):
        code, out, _ = run(self.base)
        self.assertEqual(code, 0)
        self.assertIn("NEW NSE bulk and block deals for 03-Nov-2026: 30 bulk, 2 block deals", out)
        with open(self.state) as fh:
            saved = json.load(fh)
        self.assertEqual(len(saved["seen"]), 32)
        self.assertEqual(set(saved["seen"].values()), {DAY})
        self.assertIsNotNone(saved["last_check"])
        code, out, _ = run(self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new since", out)
        self.assertIn("32 deal(s) in the file", out)
        self.assertNotIn("BLSPOLY", out)

    def test_a_deal_that_appears_later_is_new(self):
        run(self.base)
        folder = os.path.join(self.tmp.name, "off")
        os.mkdir(folder)
        shutil.copy(BLOCK, os.path.join(folder, "block.csv"))
        with open(os.path.join(folder, "block.csv"), "a") as fh:
            fh.write("03-NOV-2026,TCS,Tata Consultancy Services Limited,SOME FUND,BUY,100000,3000.00\n")
        args = list(self.base)
        args[args.index("--offline") + 1] = folder
        code, out, _ = run(args + ["--kind", "block"])
        self.assertEqual(code, 0)
        self.assertIn("0 bulk, 1 block deals", out)
        self.assertIn("TCS", out)
        self.assertNotIn("GALLANTT", out)
        self.assertIn(RS + "30.00 cr", out)   # 100000 x 3000 = 30 crore

    def test_filtered_new_only_remembers_what_it_showed(self):
        code, out, _ = run(self.base + ["--symbol", "COMSYN"])
        self.assertEqual(code, 0)
        with open(self.state) as fh:
            self.assertEqual(len(json.load(fh)["seen"]), 1)
        code, out, _ = run(self.base)           # unfiltered: everything but COMSYN is still new
        self.assertIn("29 bulk, 2 block deals", out)
        self.assertNotIn("COMSYN", out)

    def test_history_is_appended_by_day_and_kind_and_is_idempotent(self):
        run(self.base)
        with open(self.hist) as fh:
            hist = json.load(fh)
        self.assertEqual(list(hist["days"]), [DAY])
        self.assertEqual(len(hist["days"][DAY]["bulk"]), 30)
        self.assertEqual(len(hist["days"][DAY]["block"]), 2)
        self.assertEqual(hist["days"][DAY]["block"][0],
                         {"symbol": "GALLANTT", "name": "Gallantt Ispat Limited",
                          "client": "SURYALAXMI INFRACON PRIVATE LIMITED", "side": "BUY",
                          "qty": 5000000, "price": 501.0, "remarks": ""})
        run(self.base)
        with open(self.hist) as fh:
            self.assertEqual(json.load(fh)["days"][DAY], hist["days"][DAY])

        # a second day builds up next to the first
        folder = os.path.join(self.tmp.name, "next")
        os.mkdir(folder)
        with open(os.path.join(folder, "bulk.csv"), "w") as fh:
            fh.write(HEADER_BULK + "04-NOV-2026,BLSPOLY,BLS Polymers Limited,SOME FUND,SELL,100000,270.00,-\n")
        with open(os.path.join(folder, "block.csv"), "w") as fh:
            fh.write(HEADER_BLOCK)
        args = list(self.base)
        args[args.index("--offline") + 1] = folder
        code, out, _ = run(args + ["--today", "2026-11-04"])
        self.assertEqual(code, 0)
        self.assertIn("04-Nov-2026", out)
        self.assertIn("-" + RS + "2.70 cr", out)      # sold 100000 x 270 = 2.70 crore
        with open(self.hist) as fh:
            hist = json.load(fh)
        self.assertEqual(sorted(hist["days"]), [DAY, "2026-11-04"])
        self.assertEqual(len(hist["days"][DAY]["bulk"]), 30)

    def test_history_command(self):
        run(self.base)
        base = ["history", "--history-file", self.hist, "--today", "2026-11-05"]
        code, out, _ = run(base + ["--symbol", "GALLANTT"])
        self.assertEqual(code, 0)
        self.assertIn("03-Nov-2026  2 deal(s), net 0 shares, net " + RS + "0", out)
        self.assertIn("BUY", out)
        self.assertIn("SELL", out)
        self.assertIn("GALLANTT over the saved days of the last 30: 1 day(s) with deals, net 0 shares", out)
        code, out, _ = run(base + ["--symbol", "blspoly.ns"])
        self.assertIn("28 deal(s), net 3,03,68,891 shares, net +" + RS + "808.60 cr", out)
        code, out, _ = run(base + ["--symbol", "BLSPOLY", "--days", "1"])      # 2026-11-04 cutoff: the 3rd is out
        self.assertEqual(code, 1)
        self.assertIn("No saved deals for BLSPOLY in the last 1 days (1 day(s) saved)", out)
        code, out, _ = run(base + ["--symbol", "COMSYN", "--json"])
        self.assertEqual(json.loads(out)[0]["value_cr"], 4.434594)
        code, out, _ = run(base + ["--symbol", "TCS"])
        self.assertEqual(code, 1)

    def test_history_missing_file(self):
        code, _, err = run(["history", "--symbol", "X", "--history-file", os.path.join(self.tmp.name, "no.json")])
        self.assertEqual(code, 1)
        self.assertIn("run `deals.py new` once each evening", err)

    def test_dry_run_keeps_state_and_history(self):
        code, out, _ = run(self.base + ["--dry-run"])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.state))
        self.assertFalse(os.path.exists(self.hist))

    def test_state_is_pruned_after_30_days(self):
        os.makedirs(os.path.dirname(self.state))
        with open(self.state, "w") as fh:
            json.dump({"seen": {"old": "2026-09-01", "recent": "2026-10-20"}, "last_check": None}, fh)
        run(self.base)
        with open(self.state) as fh:
            seen = json.load(fh)["seen"]
        self.assertNotIn("old", seen)
        self.assertIn("recent", seen)
        self.assertEqual(len(seen), 33)
        pruned = deals.prune({"a": "2026-10-04", "b": "2026-10-03"}, datetime.date(2026, 11, 3))
        self.assertEqual(pruned, {"a": "2026-10-04"})   # exactly 30 days back is kept

    def test_corrupt_state_is_an_error(self):
        os.makedirs(os.path.dirname(self.state))
        with open(self.state, "w") as fh:
            fh.write("{broken")
        code, _, err = run(self.base)
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", err)

    def test_new_json(self):
        code, out, _ = run(self.base + ["--json", "--kind", "block"])
        doc = json.loads(out)
        self.assertEqual(len(doc["deals"]), 2)
        code, out, _ = run(self.base + ["--json", "--kind", "block"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["deals"], [])


class ParseCommand(unittest.TestCase):
    def setUp(self):
        self.none = os.path.join(tempfile.gettempdir(), "no-such-trader-file.json")

    def args(self, *a):
        return ["parse"] + list(a) + ["--watchlist", self.none, "--plan", self.none,
                                       "--portfolio", self.none]

    def test_parse_bulk_file_kind_from_name(self):
        code, out, _ = run(self.args(BULK))
        self.assertEqual(code, 0)
        self.assertIn("Deals in bulk-2026-11-03.csv for 03-Nov-2026: 30 bulk, 0 block deals", out)

    def test_parse_block_file_and_explicit_kind(self):
        code, out, _ = run(self.args(BLOCK, "--kind", "block", "--json"))
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["deals"]), 2)

    def test_kind_guessed_from_the_header_when_the_name_says_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "saved.csv")
            shutil.copy(BULK, p)
            code, out, _ = run(self.args(p))
            self.assertIn("30 bulk, 0 block", out)
            shutil.copy(BLOCK, p)
            code, out, _ = run(self.args(p))
            self.assertIn("0 bulk, 2 block", out)

    def test_parse_errors_and_empty(self):
        code, _, err = run(self.args(os.path.join(DIR, "missing.csv")))
        self.assertEqual(code, 1)
        self.assertIn("no such file", err)
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "block.csv")
            with open(p, "w") as fh:
                fh.write(HEADER_BLOCK)
            code, out, _ = run(self.args(p))
            self.assertEqual(code, 1)
            self.assertIn("No deals found", out)
            with open(p, "w") as fh:
                fh.write("hello\n")
            code, _, err = run(self.args(p))
            self.assertEqual(code, 1)
            self.assertIn("does not look like an NSE deals file", err)

    def test_parse_filters(self):
        code, out, _ = run(self.args(BULK, "--symbol", "AGIIL", "--min-value-cr", "30"))
        self.assertEqual(code, 0)
        self.assertIn("1 bulk, 0 block", out)
        code, out, _ = run(self.args(BULK, "--symbol", "AGIIL", "--min-value-cr", "40"))
        self.assertEqual(code, 1)


class Console(unittest.TestCase):
    def test_rupee_sign_survives_a_cp1252_console(self):
        # main() switches stdout to UTF-8; here we only check the helper does not raise
        deals.utf8_console()
        self.assertEqual(deals.inr(1234567), RS + "12,34,567.00")


class FullDay(unittest.TestCase):
    """NSE's two COMPLETE files for 3 Nov 2026. A block deal that is also over the bulk-deal threshold is listed in
    both files (here Gallantt Ispat: 50,00,000 shares at 501.00), and a client's buy and sell of one stock sit on
    separate rows (here every one of BLS Polymers' 27 client names is on both sides)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.none = os.path.join(self.tmp.name, "none.json")
        self.base = ["--offline", FULL, "--watchlist", self.none, "--plan", self.none, "--portfolio", self.none]

    def group_of(self, rows, symbol):
        return [g for g in deals.group(rows) if g["symbol"] == symbol][0]

    def test_the_files_as_nse_served_them(self):
        bulk = deals.parse_csv(read(os.path.join(FULL, "bulk.csv")), "bulk")
        block = deals.parse_csv(read(os.path.join(FULL, "block.csv")), "block")
        self.assertEqual((len(bulk), len(block)), (147, 2))
        self.assertEqual({d["date"] for d in bulk + block}, {DAY})
        self.assertEqual((sum(1 for d in bulk if d["side"] == "BUY"), sum(1 for d in bulk if d["side"] == "SELL")),
                         (74, 73))
        self.assertEqual(len({d["symbol"] for d in bulk}), 31)
        self.assertEqual(bulk[:30], kind_deals("bulk"))        # the excerpt in DIR is the first 30 rows of it

    def test_a_block_deal_listed_in_both_files_counts_once(self):
        both, problems = deals.fetch(("bulk", "block"), FULL)
        self.assertEqual((sum(1 for d in both if d["kind"] == "bulk"), sum(1 for d in both if d["kind"] == "block")),
                         (145, 2))
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0], "2 row(s) of the bulk file repeat a block deal (NSE lists a block deal that "
                                      "is also over the bulk threshold in both files): each trade is counted once, "
                                      "as a block deal")
        gal = self.group_of(both, "GALLANTT")
        self.assertEqual((gal["kinds"], gal["deals"], gal["clients"]), ("block", 2, 2))
        self.assertEqual((gal["buy_cr"], gal["sell_cr"], gal["net_cr"]), (250.5, 250.5, 0))   # 250.50 crore, not 501
        # one file alone has nothing to compare with: nothing is dropped
        only_bulk, problems = deals.fetch(("bulk",), FULL)
        self.assertEqual((len(only_bulk), problems), (147, []))
        gal = self.group_of(only_bulk, "GALLANTT")
        self.assertEqual((gal["kinds"], gal["deals"], gal["buy_cr"]), ("bulk", 2, 250.5))

    def test_dropping_is_one_for_one_and_only_for_the_same_trade(self):
        a = {"kind": "bulk", "date": DAY, "symbol": "X", "name": "X Ltd", "client": "C", "side": "BUY",
             "qty": 100, "price": 10.0, "value_cr": 0.0001, "remarks": ""}
        b = dict(a, kind="block")
        kept, dropped = deals.drop_double_listed([a, dict(a), b])
        self.assertEqual((dropped, [d["kind"] for d in kept]), (1, ["bulk", "block"]))   # one bulk row stays
        for other in (dict(b, qty=200), dict(b, price=10.5), dict(b, side="SELL"), dict(b, client="D"),
                      dict(b, symbol="Y"), dict(b, date="2026-11-04")):
            kept, dropped = deals.drop_double_listed([a, other])
            self.assertEqual((dropped, len(kept)), (0, 2), other)
        kept, dropped = deals.drop_double_listed([b, dict(b)])         # block rows are never dropped
        self.assertEqual((dropped, len(kept)), (0, 2))

    def test_bls_polymers_trades_both_ways(self):
        both, _ = deals.fetch(("bulk", "block"), FULL)
        bls = self.group_of(both, "BLSPOLY")
        self.assertEqual((bls["deals"], bls["clients"], bls["kinds"]), (56, 27, "bulk"))
        self.assertEqual((bls["buy_qty"], bls["sell_qty"], bls["net_qty"]), (30368891, 29397508, 971383))
        self.assertEqual((round(bls["buy_cr"], 2), round(bls["sell_cr"], 2), round(bls["net_cr"], 2)),
                         (808.60, 783.12, 25.48))
        rows = [d for d in both if d["symbol"] == "BLSPOLY"]
        buyers = {d["client"] for d in rows if d["side"] == "BUY"}
        sellers = {d["client"] for d in rows if d["side"] == "SELL"}
        self.assertEqual((len(buyers), buyers == sellers), (27, True))
        self.assertEqual((min(d["price"] for d in rows), max(d["price"] for d in rows)), (247.45, 278.57))

    def test_the_report_and_the_skill_example(self):
        from _load import ROOT
        code, out, _ = run(["latest", "--summary"] + self.base)
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "NSE bulk and block deals for 03-Nov-2026: 145 bulk, 2 block deals")
        note = [l for l in lines if l.startswith("note: ")]
        self.assertEqual(len(note), 1)
        shown = [l for l in lines if l.startswith(("SYMBOL ", "BLSPOLY ", "GALLANTT ", "TECHNOCRAF ", "RATNAVEER ",
                                                   "SFCTECH ", "TEMPSENS "))]
        self.assertEqual(len(shown), 7)                         # the header and the six biggest symbols
        table = out.split("By symbol")[1].split("\n\n")[0].splitlines()[1:]
        self.assertEqual(len([l for l in table if not l.startswith(("SYMBOL ", "note: "))]), 31)   # 31 symbols
        with open(os.path.join(ROOT, "trader-deals", "SKILL.md"), encoding="utf-8") as fh:
            skill = fh.read()
        for line in [lines[0]] + shown + note:
            self.assertIn(line, skill)

    def test_the_two_block_deal_rows_are_still_listed_once_in_every_deal(self):
        code, out, _ = run(["latest", "--symbol", "GALLANTT"] + self.base)
        self.assertEqual(code, 0)
        self.assertIn("0 bulk, 2 block deals", out)
        self.assertEqual(out.count("DINESH R AGARWAL"), 1)
        self.assertEqual(out.count("SURYALAXMI INFRACON"), 1)


class SkillDoc(unittest.TestCase):
    def test_skill_md_is_india_only(self):
        from _load import ROOT
        with open(os.path.join(ROOT, "trader-deals", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-deals\n"))
        self.assertIn("argument-hint:", text)
        self.assertGreater(len(text.splitlines()), 60)
        low = " ".join(text.lower().split())
        for need in ("bulk deal", "block deal", "not financial advice", "nsearchives.nseindia.com",
                     "not inside information", "fii", "verify"):
            self.assertIn(need, low, need)
        for old in ("pelosi", "congress", "stock act", "house clerk", "ptr.py", "sec edgar"):
            self.assertNotIn(old, low, old)
        self.assertNotIn("2014", text)
        self.assertIn("pip install curl_cffi", text)
        self.assertIn("₹25 crore", text)                        # the block-deal minimum since 8 December 2025


if __name__ == "__main__":
    unittest.main()
