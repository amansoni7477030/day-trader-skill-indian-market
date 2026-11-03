"""Regenerates tests/fixtures/trader-timemachine/RELIANCE-60d5m.json.gz.

SYNTHETIC, not real market data. The chart is built by tests/fixtures/_synth.py (seeded random bars on the
NSE price-linked tick, IST timestamps, 75 five-minute bars a session) in Yahoo's v8 chart shape, range=60d
interval=5m. The time machine reads the first 72 bars of each session (9:15 to 3:15 PM).

It looks like a fetch made at 10:41:45 IST on Monday 2026-10-19 (the canonical scenario, see _synth.py):
the 60 calendar days of sessions before it are complete, the plan day holds 17 complete bars (9:15 to 10:35
starts), the 10:40 bar is still in progress, and Yahoo's live tick sits off the 5-minute grid at 10:41:45.

    python tests/fixtures/make_timemachine_fixtures.py
"""

import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _synth  # noqa: E402

OUT = os.path.join(HERE, "trader-timemachine", "RELIANCE-60d5m.json.gz")


def build():
    hist = _synth.reliance_history()
    # 18 bars on the plan day: slots 0..16 complete, slot 17 (10:40 start) in progress
    doc = _synth.chart("RELIANCE.NS", hist, name="Reliance Industries Limited", as_of=(_synth.RELIANCE_DAY, 18))
    res = doc["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    tick_ts = _synth.epoch(_synth.RELIANCE_DAY, 10 * 60 + 41) + 45          # 10:41:45 IST, off the 5-minute grid
    res["timestamp"].append(tick_ts)
    for k in ("open", "high", "low", "close"):
        q[k].append(q["close"][-1])
    q["volume"].append(q["volume"][-1])
    res["meta"]["regularMarketTime"] = tick_ts
    return doc


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    _synth.dump(build(), OUT, gz=True)
    print("wrote", OUT)
