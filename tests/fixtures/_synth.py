"""Deterministic SYNTHETIC Yahoo-shaped chart JSON for NSE stocks.

This is NOT real market data. The tests need Yahoo Finance v8 chart / spark
documents for Indian stocks (NSE, Yahoo suffix .NS) in the shape Yahoo returns
them, with Indian timestamps (IST, UTC+5:30, 75 five-minute bars from 9:15), and
with prices on NSE's price-linked tick (0.01 under 250, 0.05 to 1,000, 0.10 to
5,000, 0.50 to 10,000, 1.00 to 20,000, 5.00 above). The numbers are drawn from a
seeded random generator, so every machine builds the same files.

The bars from 15:15 on are ordinary bars here. Real Yahoo data for stocks with F&O
contracts looks different since the Closing Auction Session began (3 Aug 2026):
a stub bar at 15:15, a null bar at 15:20 and the auction at 15:25. That shape is
covered by the REAL fixtures captured from Yahoo (tests/fixtures/*/real-*), and
the tools read only the bars from 9:15 to 15:10 either way.

    python tests/fixtures/_synth.py          # prints a short self-check

Use from a generator script:

    import sys; sys.path.insert(0, "tests/fixtures"); import _synth
    days = _synth.trading_days(datetime.date(2026, 10, 19), calendar_days=60)
    chart = _synth.chart("RELIANCE.NS", series, ...)
"""

import calendar
import datetime
import json
import random

IST_OFFSET = 19800            # seconds, UTC+5:30, no daylight saving
BARS = 75                     # 9:15 to 15:30 in 5-minute bars (the tools read the first 72)
OPEN_MIN = 9 * 60 + 15

# NSE capital-market trading holidays, 2026, exactly as NSE publishes them (same list as the tools)
HOLIDAYS = {
    "2026-01-15", "2026-01-26", "2026-02-15", "2026-03-03", "2026-03-21", "2026-03-26", "2026-03-31",
    "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-08-15", "2026-09-14",
    "2026-10-02", "2026-10-20", "2026-11-08", "2026-11-10", "2026-11-24", "2026-12-25",
}


def is_trading_day(d):
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


def trading_days(end, calendar_days=60):
    """Trading days in the `calendar_days` days up to and including `end`, oldest first."""
    out = []
    d = end - datetime.timedelta(days=calendar_days - 1)
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def tick_for(price):
    """NSE's tick size for a price (circular NSE/CMTR/67133)."""
    if price < 250:
        return 0.01
    if price <= 1000:
        return 0.05
    if price <= 5000:
        return 0.10
    if price <= 10000:
        return 0.50
    if price <= 20000:
        return 1.00
    return 5.00


def tick(x):
    """Round to NSE's tick for that price."""
    t = tick_for(x)
    return round(round(x / t) * t + 1e-9, 2)


def epoch(day, minutes_after_midnight):
    """IST wall-clock -> Unix seconds."""
    naive = datetime.datetime.combine(day, datetime.time(0, 0)) + datetime.timedelta(minutes=minutes_after_midnight)
    return calendar.timegm(naive.timetuple()) - IST_OFFSET


def day_bars(rng, prev_close, sigma=0.0011, gap_pct=0.0, base_volume=60000, script=None):
    """75 five-minute bars as dicts {o,h,l,c,v}. `script` = list of (o,h,l,c,v)
    tuples that replace the first bars exactly (a designed opening range)."""
    bars = []
    p = tick(prev_close * (1 + gap_pct / 100.0))
    for i in range(BARS):
        if script and i < len(script):
            o, h, l, c, v = script[i]
            bars.append({"o": o, "h": h, "l": l, "c": c, "v": int(v)})
            p = c
            continue
        o = p
        ret = rng.gauss(0, sigma * (1.8 if i < 6 else 1.0))
        c = tick(o * (1 + ret))
        wick_hi = abs(rng.gauss(0, sigma * 0.6))
        wick_lo = abs(rng.gauss(0, sigma * 0.6))
        h = tick(max(o, c) * (1 + wick_hi))
        l = tick(min(o, c) * (1 - wick_lo))
        h = max(h, o, c)
        l = min(l, o, c)
        u = 1.0 + 2.2 * (1 - min(i, BARS - 1 - i) / 37.0) ** 2     # U-shaped intraday volume
        v = int(base_volume * u * rng.uniform(0.6, 1.4))
        bars.append({"o": o, "h": h, "l": l, "c": c, "v": v})
        p = c
    return bars


def build_history(symbol, days, start_price, seed, sigma=0.0011, base_volume=60000, scripts=None):
    """{date: bars} for each day, chained so each day opens near the last close."""
    rng = random.Random("%s:%d" % (symbol, seed))
    out, prev = {}, start_price
    for d in days:
        gap = rng.gauss(0, 0.35)
        script = (scripts or {}).get(d.isoformat())
        bars = day_bars(rng, prev, sigma, gap, base_volume, script)
        out[d] = bars
        prev = bars[-1]["c"]
    return out


def chart(symbol, history, interval="5m", rng_label="60d", as_of=None, name=None, previous_close=None):
    """A Yahoo v8 chart document from {date: bars}. `as_of` = (date, n_bars) keeps
    only the first n_bars of that last day (a session still in progress)."""
    ts, o, h, l, c, v = [], [], [], [], [], []
    days = sorted(history)
    for d in days:
        bars = history[d]
        if as_of and d == as_of[0]:
            bars = bars[:as_of[1]]
        for i, b in enumerate(bars):
            ts.append(epoch(d, OPEN_MIN + 5 * i))
            o.append(b["o"]); h.append(b["h"]); l.append(b["l"]); c.append(b["c"]); v.append(b["v"])
    last_day = days[-1]
    last_bars = [b for b in history[last_day]][:as_of[1]] if as_of and as_of[0] == last_day else history[last_day]
    prev_close = previous_close
    if prev_close is None and len(days) > 1:
        prev_close = history[days[-2]][-1]["c"]
    meta = {
        "currency": "INR", "symbol": symbol, "exchangeName": "NSI", "fullExchangeName": "NSE",
        "instrumentType": "EQUITY", "firstTradeDate": 820468500, "regularMarketTime": ts[-1] + 300 if as_of is None else ts[-1] + 300,
        "hasPrePostMarketData": False, "gmtoffset": IST_OFFSET, "timezone": "IST",
        "exchangeTimezoneName": "Asia/Kolkata", "regularMarketPrice": last_bars[-1]["c"],
        "fiftyTwoWeekHigh": max(b["h"] for bs in history.values() for b in bs),
        "fiftyTwoWeekLow": min(b["l"] for bs in history.values() for b in bs),
        "regularMarketDayHigh": max(b["h"] for b in last_bars),
        "regularMarketDayLow": min(b["l"] for b in last_bars),
        "regularMarketVolume": sum(b["v"] for b in last_bars),
        "longName": name or symbol, "shortName": (name or symbol).upper()[:20],
        "chartPreviousClose": prev_close, "priceHint": 2, "dataGranularity": interval, "range": rng_label,
        "validRanges": ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"],
    }
    return {"chart": {"result": [{"meta": meta, "timestamp": ts,
                                  "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c, "volume": v}]}}],
                      "error": None}}


def dump(doc, path, gz=False):
    text = json.dumps(doc, separators=(",", ":"))
    if gz:
        import gzip
        with gzip.GzipFile(path, "wb", mtime=0) as fh:      # mtime=0: byte-identical on every run
            fh.write(text.encode("utf-8"))
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


# The canonical example used by every skill's tests and by the README:
# RELIANCE (RELIANCE.NS), Monday 2026-10-19 (a normal NSE session; Tue 20 Oct is the Dussehra holiday).
# The first four bars are scripted: three bars form the 15-minute opening range
# (high 1413.40, low 1399.50, midpoint 1406.45), and the 09:30 bar CLOSES at 1416.30 (at 09:35), above the range high.
# Every price is a multiple of 0.10, the tick of a stock between 1,000 and 5,000.
RELIANCE_SCRIPT = [
    (1402.00, 1409.50, 1399.50, 1407.30, 410000),   # 09:15
    (1407.30, 1412.00, 1405.10, 1410.00, 250000),   # 09:20
    (1410.00, 1413.40, 1406.80, 1408.60, 190000),   # 09:25
    (1408.60, 1418.20, 1408.20, 1416.30, 230000),   # 09:30 bar, closes 09:35: the trigger
]
RELIANCE_DAY = datetime.date(2026, 10, 19)


def reliance_history(calendar_days=60, seed=7, start_price=1400.0):
    """The canonical RELIANCE.NS history: 60 calendar days of sessions ending 2026-10-19."""
    days = trading_days(RELIANCE_DAY, calendar_days)
    return build_history("RELIANCE.NS", days, start_price, seed,
                         scripts={RELIANCE_DAY.isoformat(): RELIANCE_SCRIPT})


if __name__ == "__main__":
    end = datetime.date(2026, 10, 19)
    days = trading_days(end)
    hist = reliance_history()
    doc = chart("RELIANCE.NS", hist)
    n = len(doc["chart"]["result"][0]["timestamp"])
    print("sessions %d, bars %d, first day %s, last %s" % (len(days), n, days[0], days[-1]))
    assert n == len(days) * BARS
    assert all(abs(b["c"] / tick_for(b["c"]) - round(b["c"] / tick_for(b["c"]))) < 1e-6 for bs in hist.values() for b in bs)
    print("ok")
