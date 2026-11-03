"""Writes the trader-entry (and trader-exit) test fixtures. SYNTHETIC, not real market data.

    python tests/fixtures/make_entry_fixtures.py

Files, in tests/fixtures/trader-entry/:

RELIANCE-2026-10-19-5m.json  The canonical scenario: RELIANCE.NS, Monday 2026-10-19, one full session
                             (75 five-minute bars from 9:15 IST; the tools read the first 72), shaped like a
                             Yahoo v8 range=1d chart. Built by _synth.reliance_history(). The first three bars
                             form the 15-minute opening range (high 1413.40, low 1399.50); the 9:30 bar closes
                             at 1416.30 (9:35 AM IST), above the range high. The later bars are seeded
                             random noise. The tests cut this file down to replay a live session.
DEMO-1m-synthetic.json       SYNTHETIC one-minute bars for an invented symbol "DEMO" (DEMO.NS): a wick
                             above the range high, a close AT the high, a null bar, a live tick.
                             Prices sit on the Rs 0.01 tick (a stock under 250).

Deterministic: the numbers come from a seeded random generator, so every machine writes the same bytes.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _synth  # noqa: E402

OUT = os.path.join(HERE, "trader-entry")
NOTE = "SYNTHETIC test bars for trader-entry, not market data"


def reliance():
    hist = _synth.reliance_history()
    day = _synth.RELIANCE_DAY
    days = sorted(hist)
    prev_close = hist[days[days.index(day) - 1]][-1]["c"]
    doc = _synth.chart("RELIANCE.NS", {day: hist[day]}, interval="5m", rng_label="1d",
                       name="Reliance Industries Limited", previous_close=prev_close)
    meta = doc["chart"]["result"][0]["meta"]
    meta["previousClose"] = prev_close
    meta["note"] = NOTE
    return doc


# (open, high, low, close, volume) per 1-minute bar from 9:15 IST; None = Yahoo sent nulls
DEMO_BARS = [
    (99.80, 100.20, 99.40, 100.00, 60000), (100.00, 100.10, 99.00, 99.20, 15000),
    (99.20, 99.90, 99.10, 99.85, 9000), (99.85, 100.50, 99.80, 100.30, 12000),
    (100.30, 100.40, 99.90, 100.00, 8000), (100.00, 100.20, 99.70, 99.75, 7000),
    (99.75, 99.95, 99.50, 99.60, 6000), (99.60, 99.90, 99.55, 99.85, 6000),
    (99.85, 100.10, 99.80, 100.05, 7000), (100.05, 100.25, 100.00, 100.20, 6000),
    (100.20, 100.35, 100.10, 100.15, 5000), (100.15, 100.30, 100.00, 100.10, 5000),
    (100.10, 100.20, 99.95, 100.00, 4000), (100.00, 100.15, 99.90, 100.05, 5000),
    (100.05, 100.30, 100.00, 100.25, 5000),                                  # bar 14 ends the range
    (100.25, 100.45, 100.20, 100.40, 6000), (100.40, 100.45, 100.30, 100.35, 5000),
    (100.35, 100.40, 100.10, 100.20, 4000), (100.20, 100.30, 100.05, 100.10, 4000),
    None,                                                                    # bar 19 (9:34): nulls
    (100.10, 100.25, 100.00, 100.20, 4000), (100.20, 100.35, 100.15, 100.30, 5000),
    (100.30, 100.70, 100.25, 100.30, 15000),                                 # bar 22: a wick above the high
    (100.30, 100.40, 100.20, 100.35, 5000), (100.35, 100.45, 100.30, 100.40, 5000),
    (100.40, 100.50, 100.35, 100.45, 6000), (100.45, 100.50, 100.30, 100.35, 5000),
    (100.35, 100.50, 100.30, 100.50, 6000),                                  # bar 27: closes AT the high
    (100.50, 100.70, 100.45, 100.60, 9000),                                  # bar 28: the first close above
    (100.60, 100.75, 100.55, 100.70, 8000), (100.70, 100.80, 100.60, 100.65, 7000),
    (100.65, 100.85, 100.60, 100.80, 30000),                                 # bar 31: the high-volume close
    (100.80, 100.90, 100.70, 100.85, 9000), (100.85, 100.95, 100.75, 100.90, 8000),
    (100.90, 101.00, 100.80, 100.95, 8000), (100.95, 101.05, 100.85, 100.90, 7000),
    (100.90, 100.95, 100.85, 100.90, 2000),                                  # bar 36: still forming at 9:51:30
]


def demo():
    day = _synth.RELIANCE_DAY
    ts, o, h, l, c, v = [], [], [], [], [], []
    for i, bar in enumerate(DEMO_BARS):
        ts.append(_synth.epoch(day, _synth.OPEN_MIN + i))
        if bar is None:
            for arr in (o, h, l, c, v):
                arr.append(None)
        else:
            for arr, x in zip((o, h, l, c, v), bar):
                arr.append(x)
    tick_ts = ts[-1] + 30                                   # Yahoo's live tick, 9:51:30, off the grid
    ts.append(tick_ts)
    for arr in (o, h, l, c):
        arr.append(100.90)
    v.append(0)
    for x in (o, h, l, c):
        for val in x:
            assert val is None or abs(val / 0.01 - round(val / 0.01)) < 1e-6, val
    meta = {"currency": "INR", "symbol": "DEMO.NS", "exchangeName": "NSI", "fullExchangeName": "NSE",
            "instrumentType": "EQUITY", "regularMarketTime": tick_ts, "gmtoffset": _synth.IST_OFFSET,
            "timezone": "IST", "exchangeTimezoneName": "Asia/Kolkata", "regularMarketPrice": 100.90,
            "chartPreviousClose": 99.50, "previousClose": 99.50, "priceHint": 2, "dataGranularity": "1m",
            "range": "1d", "note": NOTE}
    return {"chart": {"result": [{"meta": meta, "timestamp": ts,
                                  "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c, "volume": v}]}}],
                      "error": None}}


def main():
    os.makedirs(OUT, exist_ok=True)
    _synth.dump(reliance(), os.path.join(OUT, "RELIANCE-2026-10-19-5m.json"))
    _synth.dump(demo(), os.path.join(OUT, "DEMO-1m-synthetic.json"))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
