"""Regenerates tests/fixtures/trader-alerts/*: Yahoo-shaped one-day quote documents and the canonical plan.

SYNTHETIC, not real market data. The quote documents are Yahoo v8 chart answers for range=1d interval=1d
(what trader-alerts reads), built with tests/fixtures/_synth.py; every price sits on NSE's price-linked tick.
File names use the plain NSE symbol: yahoo-RELIANCE.json, yahoo-M&M.json (a legal file name on Windows too).

    python tests/fixtures/make_alerts_fixtures.py
"""

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _synth  # noqa: E402

OUT = os.path.join(HERE, "trader-alerts")
D13 = datetime.date(2026, 10, 13)
D19 = _synth.RELIANCE_DAY

# symbol, name, day, (open, high, low, close = the quote), previous close, quote time in IST (hour, minute, second)
QUOTES = [
    # the canonical trade: a 10:52:13 IST quote between the stop (1406.40) and the target (1426.20)
    ("RELIANCE", "Reliance Industries Limited", D19, (1402.00, 1421.80, 1399.50, 1419.80), 1398.70, (10, 52, 13)),
    # 2026-10-13, the close (15:30 IST)
    ("HDFCBANK", "HDFC Bank Limited", D13, (1002.10, 1006.40, 999.80, 1004.30), 1003.40, (15, 29, 58)),
    ("ITC", "ITC Limited", D13, (416.50, 417.45, 411.90, 412.35), 417.10, (15, 29, 58)),
    ("TCS", "Tata Consultancy Services Limited", D13, (3174.00, 3192.80, 3168.40, 3185.50), 3170.20, (15, 29, 58)),
    ("M&M", "Mahindra & Mahindra Limited", D13, (3401.30, 3419.90, 3396.00, 3412.60), 3398.10, (15, 29, 58)),
]


def quote_doc(sym, name, day, ohlc, prev, hms):
    o, h, l, c = ohlc
    doc = _synth.chart(sym + ".NS", {day: [{"o": o, "h": h, "l": l, "c": c, "v": 4200000}]}, interval="1d",
                       rng_label="1d", name=name, previous_close=prev)
    meta = doc["chart"]["result"][0]["meta"]
    meta["regularMarketTime"] = _synth.epoch(day, hms[0] * 60 + hms[1]) + hms[2]
    meta["regularMarketDayHigh"], meta["regularMarketDayLow"] = h, l
    return doc


def main():
    os.makedirs(OUT, exist_ok=True)
    for q in QUOTES:
        _synth.dump(quote_doc(*q), os.path.join(OUT, "yahoo-%s.json" % q[0]))
    gone = {"chart": {"result": None, "error": {"code": "Not Found",
                                                "description": "No data found, symbol may be delisted"}}}
    _synth.dump(gone, os.path.join(OUT, "yahoo-ZZZZ.json"))
    plan = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-10-19", "setup": "orb",
            "entry": 1416.30, "entry_time": "9:35 AM", "entry_bar": 3, "interval": "5m",
            "opening_range": {"high": 1413.40, "low": 1399.50, "minutes": 15},
            "stop": 1406.40, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
            "target": 1426.20, "target_r": 1.0, "target_label": "1R (one times the risk)",
            "risk_per_share": 9.9, "reward_risk": 1.0, "flat_by": "15:00", "tick_size": 0.1, "tick_override": False}
    with open(os.path.join(OUT, "plan-reliance.json"), "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2)
        fh.write("\n")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
