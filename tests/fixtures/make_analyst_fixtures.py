"""Generate the trader-analyst fixtures. Run from the repo root:

    python tests/fixtures/make_analyst_fixtures.py

README: EVERYTHING WRITTEN HERE IS SYNTHETIC. The numbers are round, made-up
placeholders (in rupee crore, 1 crore = 1e7) that only reproduce the SHAPE of
Yahoo Finance's fundamentals-timeseries answer. They are not any company's
reported results. The shape has since been checked against a live answer: the
REAL one is fundamentals-RELIANCE-real.json (captured by capture_real_fixtures.py,
never touched by this script) and the tests read both.

Files written to tests/fixtures/trader-analyst/ (plain NSE symbols in the names):
    fundamentals-RELIANCE.json   8 quarters, all five types (a 12M entry and a gap included)
    fundamentals-HDFCBANK.json   8 quarters, bank-like: revenue, net income and EPS only;
                                 gross profit absent, operating income present but empty
    fundamentals-NODATA.json     Yahoo's error answer (no data found)
    fundamentals-EMPTY.json      an answer with an empty result list
"""

import calendar
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "trader-analyst")
FILES = ("fundamentals-RELIANCE.json", "fundamentals-HDFCBANK.json", "fundamentals-NODATA.json",
         "fundamentals-EMPTY.json")

# (period end, revenue, gross profit, operating income, net income) in rupee crore, EPS in rupees
RELIANCE = [
    ("2024-09-30", 220000, 70000, 40000, 17000, 12.6),
    ("2024-12-31", 230000, 72000, 42000, 18500, 13.7),
    ("2025-03-31", 240000, 75000, 43000, 19000, 14.1),
    ("2025-06-30", 225000, 71000, 40500, 17500, 12.9),
    ("2025-09-30", 240000, 76000, 44000, 18800, 13.9),
    ("2025-12-31", 250000, 79000, 46000, 20500, 15.1),
    ("2026-03-31", 262000, 84000, 48000, 21000, 15.5),
    ("2026-06-30", 250000, 82000, 47500, 20000, 14.7),
]
# bank-like: (period end, revenue, net income, EPS); no gross or operating profit
HDFCBANK = [
    ("2024-09-30", 80000, 16000, 20.9),
    ("2024-12-31", 82000, 16500, 21.5),
    ("2025-03-31", 85000, 17000, 22.1),
    ("2025-06-30", 84000, 17200, 22.4),
    ("2025-09-30", 88000, 18000, 23.4),
    ("2025-12-31", 90000, 18500, 24.0),
    ("2026-03-31", 93000, 19000, 24.7),
    ("2026-06-30", 95000, 19600, 25.4),
]


def crore_fmt(raw):
    """Yahoo-style short label, e.g. 2.5T (only decoration; the tool reads 'raw')."""
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(raw) >= div:
            return "%.3g%s" % (raw / div, suffix)
    return "%.4g" % raw


def entry(data_id, end, raw, ptype="3M", ccy="INR", fmt=None):
    return {"dataId": data_id, "asOfDate": end, "periodType": ptype, "currencyCode": ccy,
            "reportedValue": {"raw": raw, "fmt": fmt or crore_fmt(raw)}}


def result(symbol, typ, entries):
    """One Yahoo result object: meta, timestamp (unix, UTC midnight of each asOfDate), the data list."""
    ts = [calendar.timegm(datetime.date.fromisoformat(e["asOfDate"]).timetuple()) for e in entries]
    return {"meta": {"symbol": [symbol], "type": [typ]}, "timestamp": ts, typ: entries}


def money(crore):
    return float(crore) * 1e7


def doc(results):
    return {"timeseries": {"result": results, "error": None}}


def reliance():
    sym = "RELIANCE.NS"
    rev = [entry(1000 + i, r[0], money(r[1])) for i, r in enumerate(RELIANCE)]
    # a 12-month figure on the same date as a quarter: must never replace the 3M value
    rev.insert(3, entry(1100, "2025-03-31", money(990000), ptype="12M"))
    # a gap: an entry without a number (Yahoo does this)
    gp = [entry(2000 + i, r[0], money(r[2])) for i, r in enumerate(RELIANCE)]
    gp.append({"dataId": 2999, "asOfDate": "2026-09-30", "periodType": "3M", "currencyCode": "INR"})
    oi = [entry(3000 + i, r[0], money(r[3])) for i, r in enumerate(RELIANCE)]
    ni = [entry(4000 + i, r[0], money(r[4])) for i, r in enumerate(RELIANCE)]
    eps = [entry(5000 + i, r[0], r[5], fmt="%.2f" % r[5]) for i, r in enumerate(RELIANCE)]
    return doc([result(sym, "quarterlyTotalRevenue", rev), result(sym, "quarterlyGrossProfit", gp),
                result(sym, "quarterlyOperatingIncome", oi), result(sym, "quarterlyNetIncome", ni),
                result(sym, "quarterlyDilutedEPS", eps)])


def hdfcbank():
    sym = "HDFCBANK.NS"
    rev = [entry(6000 + i, r[0], money(r[1])) for i, r in enumerate(HDFCBANK)]
    ni = [entry(7000 + i, r[0], money(r[2])) for i, r in enumerate(HDFCBANK)]
    eps = [entry(8000 + i, r[0], r[3], fmt="%.2f" % r[3]) for i, r in enumerate(HDFCBANK)]
    empty_oi = {"meta": {"symbol": [sym], "type": ["quarterlyOperatingIncome"]}}   # no data key at all
    return doc([result(sym, "quarterlyTotalRevenue", rev), empty_oi,
                result(sym, "quarterlyNetIncome", ni), result(sym, "quarterlyDilutedEPS", eps)])


def main():
    os.makedirs(OUT, exist_ok=True)
    for name in FILES:                    # replace our own files, never the real captures next to them
        path = os.path.join(OUT, name)
        if os.path.exists(path):
            os.remove(path)
    files = {
        "fundamentals-RELIANCE.json": reliance(),
        "fundamentals-HDFCBANK.json": hdfcbank(),
        "fundamentals-NODATA.json": {"timeseries": {"result": None, "error": {
            "code": "Not Found", "description": "No fundamentals data found for symbol: NODATA.NS"}}},
        "fundamentals-EMPTY.json": {"timeseries": {"result": [], "error": None}},
    }
    for name, document in files.items():
        with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(document, fh, indent=1)
            fh.write("\n")
    print("wrote", sorted(os.listdir(OUT)))


if __name__ == "__main__":
    main()
