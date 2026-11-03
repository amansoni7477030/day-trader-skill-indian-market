"""Generate the trader-scanner test fixtures. SYNTHETIC, not real market data.

    python tests/fixtures/make_scanner_fixtures.py

Writes into tests/fixtures/trader-scanner/ (deterministic: seeded generators, gzip with mtime 0):

    wall5m-2026-10-19.json.gz            a bundle {SYMBOL: Yahoo v8 5-minute chart} of 61 entries (58 NSE charts and
                                         3 oddities), fetched
                                         at 11:13:07 IST on Mon 2026-10-19 (bars 9:15 .. 11:10, the last one still
                                         in progress, plus a live tick off the 5-minute grid). Includes one
                                         "No data found" chart (MINDTREE), one chart with no bars (RPOWER) and one
                                         with missing bars (JPPOWER).
    daily-spark-2026-10-19.json          a Yahoo spark answer: 20 symbols, 3 months of daily closes, the last bar
                                         (Mon 19 Oct) still in progress. Hand-shaped so every setup of scan.py fires.
    spark-3mo-live-2026-10-21.json       a spark answer for 19 symbols fetched at 10:01:48 IST on Wed 2026-10-21
                                         (Tue 20 Oct was the Dussehra holiday, so there is no bar for it).
    spark-5m-live-2026-10-21-1001.json   a 5-minute spark answer for 20 symbols, same moment (closes only).
    RELIANCE-5m-live-2026-10-21-1001.json  RELIANCE's v8 chart of that morning (open/high/low/close/volume).

symdir/ (EQUITY_L.csv and ind_nifty500list.csv) is NOT generated: it is a small excerpt of the REAL files
downloaded from nsearchives.nseindia.com, with their exact headers, kept by hand.

The canonical example is RELIANCE (RELIANCE.NS) on Mon 2026-10-19, straight from _synth.reliance_history(): the
first three 5-minute bars form the 15-minute opening range (high 1413.40, low 1399.50), and the 9:30 bar closes
at 1416.30 at 9:35 AM IST. Every price sits on NSE's price-linked tick (_synth.tick_for: 0.01 under Rs 250, 0.05
to Rs 1,000, 0.10 to Rs 5,000, 0.50 to Rs 10,000, 1.00 to Rs 20,000, 5.00 above). Prices, volumes and company
levels are invented. The real Yahoo answers live in capture_real_fixtures.py's files (tests/fixtures/*/real-*).
"""

import datetime
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _synth  # noqa: E402

OUT = os.path.join(HERE, "trader-scanner")
D19 = datetime.date(2026, 10, 19)
D21 = datetime.date(2026, 10, 21)
TICK_WALL = (11, 13, 7)       # the wall was "fetched" at 11:13:07 IST
WALL_BARS = 24                # 9:15 .. 11:10 (the 11:10 bar is still in progress)
TICK_LIVE = (10, 1, 47)       # the live files were "fetched" at 10:01:47 IST on 21 Oct
LIVE_BARS = 10                # 9:15 .. 10:00 (the 10:00 bar is in progress)

# symbol, company name, price (Rs), shares per 5-minute bar (mid-day), daily tail shape
S = [
    ("RELIANCE", "Reliance Industries Limited", 1400.0, 60000, "breakout"),
    ("TCS", "Tata Consultancy Services Limited", 3050.0, 14000, "breakout"),
    ("HDFCBANK", "HDFC Bank Limited", 960.0, 90000, "breakout"),
    ("INFY", "Infosys Limited", 1480.0, 40000, "reclaim"),
    ("ICICIBANK", "ICICI Bank Limited", 1360.0, 60000, "oversold"),
    ("ITC", "ITC Limited", 410.0, 80000, "reclaim"),
    ("SBIN", "State Bank of India", 880.0, 80000, "reclaim"),
    ("BHARTIARTL", "Bharti Airtel Limited", 1900.0, 30000, None),
    ("LT", "Larsen & Toubro Limited", 3700.0, 12000, "bounce"),
    ("KOTAKBANK", "Kotak Mahindra Bank Limited", 2050.0, 25000, "none"),
    ("AXISBANK", "Axis Bank Limited", 1180.0, 40000, "none"),
    ("HINDUNILVR", "Hindustan Unilever Limited", 2450.0, 12000, "none"),
    ("M&M", "Mahindra & Mahindra Limited", 3300.0, 12000, "flag"),
    ("BAJAJ-AUTO", "Bajaj Auto Limited", 8800.0, 3500, "flag"),
    ("MARUTI", "Maruti Suzuki India Limited", 15500.0, 2000, "breakout"),
    ("SUNPHARMA", "Sun Pharmaceutical Industries Limited", 1700.0, 20000, "none"),
    ("TITAN", "Titan Company Limited", 3600.0, 10000, "none"),
    ("NTPC", "NTPC Limited", 335.0, 120000, "none"),
    ("POWERGRID", "Power Grid Corporation of India Limited", 285.0, 130000, None),
    ("ONGC", "Oil and Natural Gas Corporation Limited", 240.0, 130000, None),
    ("COALINDIA", "Coal India Limited", 385.0, 70000, None),
    ("TATASTEEL", "Tata Steel Limited", 170.0, 400000, "oversold"),
    ("JSWSTEEL", "JSW Steel Limited", 1100.0, 25000, None),
    ("WIPRO", "Wipro Limited", 255.0, 150000, None),
    ("HCLTECH", "HCL Technologies Limited", 1500.0, 25000, None),
    ("TECHM", "Tech Mahindra Limited", 1450.0, 20000, None),
    ("ULTRACEMCO", "UltraTech Cement Limited", 12000.0, 1500, None),
    ("ASIANPAINT", "Asian Paints Limited", 2400.0, 10000, None),
    ("NESTLEIND", "Nestle India Limited", 1250.0, 25000, None),
    ("BAJFINANCE", "Bajaj Finance Limited", 1010.0, 70000, None),
    ("BAJAJFINSV", "Bajaj Finserv Limited", 2000.0, 12000, None),
    ("ADANIPORTS", "Adani Ports and Special Economic Zone Limited", 1400.0, 25000, None),
    ("ADANIENT", "Adani Enterprises Limited", 2400.0, 15000, None),
    ("GRASIM", "Grasim Industries Limited", 2750.0, 6000, None),
    ("HINDALCO", "Hindalco Industries Limited", 780.0, 40000, None),
    ("CIPLA", "Cipla Limited", 1500.0, 12000, None),
    ("DRREDDY", "Dr. Reddy's Laboratories Limited", 1250.0, 12000, None),
    ("EICHERMOT", "Eicher Motors Limited", 6900.0, 3500, None),
    ("HEROMOTOCO", "Hero MotoCorp Limited", 5600.0, 3000, None),
    ("BRITANNIA", "Britannia Industries Limited", 5900.0, 2500, None),
    ("APOLLOHOSP", "Apollo Hospitals Enterprise Limited", 7800.0, 2000, None),
    ("DIVISLAB", "Divi's Laboratories Limited", 6200.0, 2000, None),
    ("TATACONSUM", "Tata Consumer Products Limited", 1150.0, 20000, None),
    ("BPCL", "Bharat Petroleum Corporation Limited", 330.0, 120000, None),
    ("INDUSINDBK", "IndusInd Bank Limited", 800.0, 40000, None),
    ("SBILIFE", "SBI Life Insurance Company Limited", 1900.0, 15000, None),
    ("HDFCLIFE", "HDFC Life Insurance Company Limited", 780.0, 40000, None),
    ("TRENT", "Trent Limited", 4500.0, 5000, None),
    ("VEDL", "Vedanta Limited", 480.0, 60000, None),
    ("PNB", "Punjab National Bank", 115.0, 300000, None),
    ("NHPC", "NHPC Limited", 85.0, 300000, None),
    ("TATAPOWER", "The Tata Power Company Limited", 390.0, 100000, None),
    ("DLF", "DLF Limited", 760.0, 40000, None),
    ("IOC", "Indian Oil Corporation Limited", 150.0, 300000, None),
    ("SUZLON", "Suzlon Energy Limited", 56.0, 500000, None),
    ("IRFC", "Indian Railway Finance Corporation Limited", 38.2, 600000, None),
    ("YESBANK", "Yes Bank Limited", 21.5, 2000000, "oversold"),
    ("IDEA", "Vodafone Idea Limited", 9.4, 6000000, "oversold"),
]
DAILY = [s[0] for s in S if s[4] is not None]          # 20 symbols with a hand-shaped daily tail
LIVE3MO = [s for s in DAILY if s != "AXISBANK"]        # 19: the 3-month live answer
INFO = {s[0]: s for s in S}
EXTRA_NO_BARS, EXTRA_MISSING, EXTRA_DELISTED = "RPOWER", "JPPOWER", "MINDTREE"
NAMES = {"RPOWER": "Reliance Power Limited", "JPPOWER": "Jaiprakash Power Ventures Limited",
         "MINDTREE": "Mindtree Limited"}


def rng_for(*parts):
    return random.Random(":".join(str(p) for p in parts))


# ---------------------------------------------------------------- daily closes

def relative_walk(rng, n):
    x = [1.0]
    for _ in range(n - 1):
        x.append(1.0 + 0.6 * (x[-1] - 1.0) + rng.gauss(0, 0.008))
    return x


DECLINE = [-0.011, -0.009, -0.014, 0.002, -0.012, -0.010, -0.016, -0.008, -0.013, 0.001, -0.011, -0.015,
           -0.009, -0.012]
WANT = {"breakout": {"breakout_20d"}, "flag": {"bull_flag"}, "reclaim": {"reclaim_ma20"},
        "oversold": {"oversold"}, "bounce": {"oversold_bounce"}, "none": set()}


def _rsi(c, n=14):
    g = [max(0.0, c[i] - c[i - 1]) for i in range(1, len(c))]
    lo = [max(0.0, c[i - 1] - c[i]) for i in range(1, len(c))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def _setups(c):
    """The same five rules as scan.find_setups (kept here so this script runs on its own)."""
    last, hi20 = c[-1], max(c[-21:-1])
    ma20, ma20_prev = sum(c[-20:]) / 20.0, sum(c[-21:-1]) / 20.0
    r, r_prev = _rsi(c), _rsi(c[:-1])
    tags = set()
    if last > hi20:
        tags.add("breakout_20d")
    if c[-6] / c[-16] - 1 > 0.10 and (max(c[-5:]) - min(c[-5:])) / last < 0.05:
        tags.add("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.add("reclaim_ma20")
    if r < 30:
        tags.add("oversold")
    if r_prev < 30 and last > c[-2]:
        tags.add("oversold_bounce")
    return tags


def shape_tail(x, kind):
    """Edit the last ~20 relative closes so scan.py's rule for `kind` fires."""
    n = len(x)
    if kind == "breakout":
        x[-2] = max(x[-2], 1.004 * sum(x[-21:-2]) / 19.0)          # yesterday is not under its average
        x[-1] = max(x[-21:-1]) * 1.012
    elif kind == "flag":
        for k in range(1, 11):
            x[n - 16 + k] = 1.0 + 0.15 * k / 10.0
        x[n - 16] = 1.0
        x[-5:] = [1.14, 1.15, 1.135, 1.145, 1.14]
        x[-6] = 1.15
    elif kind == "reclaim":
        s19 = sum(x[-21:-2])
        x[-2] = 0.99 * s19 / 19.0
        x[-1] = (s19 - x[-21] + x[-2]) / 19.0 * 1.006
    elif kind in ("oversold", "bounce"):
        f = 0.55 if kind == "bounce" else 0.8
        p = x[-16]
        for k, move in enumerate(DECLINE):
            p *= 1 + move * f
            x[n - 14 + k] = p
        if kind == "bounce":
            x[-1] = x[-2] * 1.02
    return x


def daily_series(sym, n_hist, anchor):
    """n_hist closes ending at `anchor` (Rs), on the Rs 0.05 tick, whose setups are exactly the intended
    ones (the walk is re-drawn with the next salt until they are)."""
    kind = INFO[sym][4]
    for salt in range(200):
        rng = rng_for(sym, "daily", salt)
        x = relative_walk(rng, n_hist)
        if kind != "none":
            x = shape_tail(x, kind)
        scale = anchor / x[-1]
        c = [_synth.tick(v * scale) for v in x]
        if _setups(c) == WANT[kind]:
            return c
    raise SystemExit("no daily series for %s" % sym)


# ---------------------------------------------------------------- charts

def meta(sym, last, prev_close, day_high, day_low, volume, t_epoch, interval, rng_label, hi52, lo52):
    return {
        "currency": "INR", "symbol": sym + ".NS", "exchangeName": "NSI", "fullExchangeName": "NSE",
        "instrumentType": "EQUITY", "firstTradeDate": 820468500, "regularMarketTime": t_epoch,
        "hasPrePostMarketData": False, "gmtoffset": _synth.IST_OFFSET, "timezone": "IST",
        "exchangeTimezoneName": "Asia/Kolkata", "regularMarketPrice": last, "fiftyTwoWeekHigh": hi52,
        "fiftyTwoWeekLow": lo52, "regularMarketDayHigh": day_high, "regularMarketDayLow": day_low,
        "regularMarketVolume": volume, "longName": INFO.get(sym, (0, NAMES.get(sym, sym)))[1],
        "shortName": INFO.get(sym, (0, NAMES.get(sym, sym)))[1].upper()[:20],
        "chartPreviousClose": prev_close, "priceHint": 2, "dataGranularity": interval, "range": rng_label,
        "validRanges": ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"],
    }


def at(day, hms):
    h, m, s = hms
    return _synth.epoch(day, h * 60 + m) + s


def tick_price(rng, last):
    return _synth.tick(last * (1 + rng.gauss(0, 0.0004)))


def day_for(sym, day, prev_close, kind):
    """75 five-minute bars of `day` for `sym`."""
    info = INFO.get(sym)
    base_vol = info[3] if info else 50000
    rng = rng_for(sym, "bars", day.isoformat())
    sigma = rng.uniform(0.0008, 0.0020)
    gap = rng.gauss(0, 0.35)
    return _synth.day_bars(rng, prev_close, sigma, gap, base_vol)


def live_reliance_script(ref):
    """Wed 21 Oct, RELIANCE: a 15-minute range 9:15-9:30, closes that reach the range's top close but never
    close above its high, then the 9:55 bar closes above it (a hit at 10:00 AM). The 10:00 bar is in progress."""
    t = _synth.tick

    def bar(o, h, l, c, v):
        return (t(ref + o), t(ref + h), t(ref + l), t(ref + c), v)
    return [
        bar(2.0, 6.0, -3.0, 3.0, 380000),      # 9:15  } range high 9.0 (the 9:20 bar), low -3.0
        bar(3.0, 9.0, 2.0, 6.5, 240000),       # 9:20  } top close of the range 6.5
        bar(6.5, 8.0, 3.5, 4.0, 180000),       # 9:25  }
        bar(4.0, 6.0, 3.5, 5.0, 150000),       # 9:30  not above 6.5
        bar(5.0, 8.0, 4.5, 7.0, 140000),       # 9:35  closes at/above the range's top close: a candidate, no hit
        bar(7.0, 8.5, 5.5, 6.0, 120000),       # 9:40
        bar(6.0, 8.8, 5.8, 8.0, 130000),       # 9:45
        bar(8.0, 8.9, 6.5, 7.5, 110000),       # 9:50  (highest high 8.9 stays under 9.0)
        bar(7.5, 11.0, 7.0, 10.5, 200000),     # 9:55  closes above the range high 9.0: the hit, at 10:00 AM
        bar(10.5, 11.6, 10.2, 11.2, 90000),    # 10:00 in progress
    ]


# ---------------------------------------------------------------- build

def build():
    # 1. daily closes through Fri 16 Oct for the 20 daily symbols (RELIANCE anchored on the canonical history)
    days_all = _synth.trading_days(D21, 97)
    hist_days = [d for d in days_all if d <= datetime.date(2026, 10, 16)]
    rel_hist = _synth.reliance_history()
    rel_prev = rel_hist[sorted(rel_hist)[-2]][-1]["c"]
    closes16 = {}
    for sym in DAILY:
        anchor = rel_prev if sym == "RELIANCE" else _synth.tick(INFO[sym][2])
        closes16[sym] = daily_series(sym, len(hist_days), anchor)

    # 2. Monday 19 Oct, full 75 bars, every symbol
    d19 = {}
    for s in S:
        sym = s[0]
        if sym == "RELIANCE":
            d19[sym] = rel_hist[D19]
            continue
        prev = closes16[sym][-1] if sym in closes16 else _synth.tick(s[2])
        d19[sym] = day_for(sym, D19, prev, s[4])
    prev16 = {s[0]: (closes16[s[0]][-1] if s[0] in closes16 else _synth.tick(s[2])) for s in S}
    prev16["RELIANCE"] = rel_prev

    # 3. Wednesday 21 Oct (Tue 20 Oct is the Dussehra holiday), only the morning is needed
    d21 = {}
    for s in S:
        sym = s[0]
        prev = d19[sym][-1]["c"]
        if sym == "RELIANCE":
            ref = prev
            script = live_reliance_script(ref)
            rng = rng_for(sym, "bars", D21.isoformat())
            d21[sym] = _synth.day_bars(rng, ref, 0.0011, 0.0, s[3], script)
        else:
            d21[sym] = day_for(sym, D21, prev, s[4])

    out = {}
    out["wall"] = wall_bundle(d19, prev16)
    out["daily"] = daily_spark(D19, closes16, hist_days, d19, prev16)
    out["live3"] = live_3mo(closes16, hist_days, d19, d21)
    out["live5"] = live_5m_spark(d21, d19)
    out["live_v8"] = live_v8(d21, d19)
    return out


def wall_bundle(d19, prev16):
    bundle = {}
    for s in S:
        sym = s[0]
        bars = d19[sym]
        doc = _synth.chart(sym + ".NS", {D19: bars}, interval="5m", rng_label="1d", as_of=(D19, WALL_BARS),
                           name=s[1], previous_close=prev16[sym])
        add_tick(doc, sym, rng_for(sym, "tick19"), TICK_WALL, D19)
        bundle[sym] = doc
    # a halted / thin stock with bars missing from the middle of the session
    dropped = bundle["TATAPOWER"]            # reuse the shape, then rename it JPPOWER
    doc = _clone(dropped, EXTRA_MISSING)
    res = doc["chart"]["result"][0]
    for k in ("timestamp",):
        del res[k][2:4]
    for col in res["indicators"]["quote"][0].values():
        del col[2:4]
    bundle[EXTRA_MISSING] = doc
    # a stock with no bars at all today (halted)
    doc = _clone(bundle["TATAPOWER"], EXTRA_NO_BARS)
    res = doc["chart"]["result"][0]
    del res["timestamp"]
    res["indicators"] = {"quote": [{}]}
    bundle[EXTRA_NO_BARS] = doc
    # Yahoo's answer for a symbol that no longer exists
    bundle[EXTRA_DELISTED] = {"chart": {"result": None, "error": {
        "code": "Not Found", "description": "No data found, symbol may be delisted"}}}
    return dict(sorted(bundle.items()))


def _clone(doc, sym):
    import copy
    d = copy.deepcopy(doc)
    d["chart"]["result"][0]["meta"]["symbol"] = sym + ".NS"
    d["chart"]["result"][0]["meta"]["longName"] = NAMES.get(sym, sym)
    d["chart"]["result"][0]["meta"]["shortName"] = NAMES.get(sym, sym).upper()[:20]
    return d


def add_tick(doc, sym, rng, hms, day):
    """Append Yahoo's live tick (off the 5-minute grid) and update the meta to it."""
    res = doc["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    last = q["close"][-1]
    p = tick_price(rng, last)
    t = at(day, hms)
    res["timestamp"].append(t)
    q["open"].append(last)
    q["high"].append(max(last, p))
    q["low"].append(min(last, p))
    q["close"].append(p)
    q["volume"].append(int(q["volume"][-1] * 0.4))
    m = res["meta"]
    m["regularMarketPrice"] = p
    m["regularMarketTime"] = t
    m["regularMarketDayHigh"] = max(m["regularMarketDayHigh"], p)
    m["regularMarketDayLow"] = min(m["regularMarketDayLow"], p)
    m["regularMarketVolume"] = m["regularMarketVolume"] + q["volume"][-1]
    m["previousClose"] = m["chartPreviousClose"]


def spark_item(sym, ts, closes, m):
    return {"symbol": sym + ".NS", "response": [{"meta": m, "timestamp": ts,
                                                 "indicators": {"quote": [{"close": closes}]}}]}


def spark_doc(items):
    return {"spark": {"result": items, "error": None}}


def daily_bar_ts(day):
    return _synth.epoch(day, _synth.OPEN_MIN)


def window(days, closes, end, span=92):
    keep = [(d, c) for d, c in zip(days, closes) if d >= end - datetime.timedelta(days=span)]
    return [d for d, _ in keep], [c for _, c in keep]


def daily_spark(day, closes16, hist_days, d19, prev16):
    items = []
    for sym in DAILY:
        bars = d19[sym]
        part = bars[:WALL_BARS]
        tick_p = tick_price(rng_for(sym, "tick19"), part[-1]["c"])
        vol = sum(b["v"] for b in part) + int(part[-1]["v"] * 0.4)
        days, closes = window(hist_days + [day], closes16[sym] + [tick_p], day)
        m = meta(sym, tick_p, prev16[sym], max(b["h"] for b in part), min(b["l"] for b in part), vol,
                 at(day, TICK_WALL), "1d", "3mo", max(closes) * 1.04, min(closes) * 0.93)
        items.append(spark_item(sym, [daily_bar_ts(d) for d in days], closes, m))
    return spark_doc(items)


def live_3mo(closes16, hist_days, d19, d21):
    items = []
    for sym in LIVE3MO:
        part = d21[sym][:LIVE_BARS]
        p = tick_price(rng_for(sym, "tick21"), part[-1]["c"])
        vol = sum(b["v"] for b in part) + int(part[-1]["v"] * 0.4)
        c19 = d19[sym][-1]["c"]
        days, closes = window(hist_days + [D19, D21], closes16[sym] + [c19, p], D21)
        m = meta(sym, p, c19, max(b["h"] for b in part), min(b["l"] for b in part), vol, at(D21, TICK_LIVE),
                 "1d", "3mo", max(closes) * 1.04, min(closes) * 0.93)
        items.append(spark_item(sym, [daily_bar_ts(d) for d in days], closes, m))
    return spark_doc(items)


def live_5m_series(sym, d21, d19):
    part = d21[sym][:LIVE_BARS]
    ts = [_synth.epoch(D21, _synth.OPEN_MIN + 5 * i) for i in range(LIVE_BARS)]
    p = tick_price(rng_for(sym, "tick21"), part[-1]["c"])
    ts.append(at(D21, TICK_LIVE))
    vol = sum(b["v"] for b in part) + int(part[-1]["v"] * 0.4)
    c19 = d19[sym][-1]["c"]
    m = meta(sym, p, c19, max(max(b["h"] for b in part), p), min(min(b["l"] for b in part), p), vol,
             at(D21, TICK_LIVE), "5m", "1d", 0, 0)
    for k in ("fiftyTwoWeekHigh", "fiftyTwoWeekLow"):
        m[k] = m["regularMarketPrice"] * (1.3 if k.endswith("High") else 0.7)
        m[k] = _synth.tick(m[k])
    return part, ts, p, m


def live_5m_spark(d21, d19):
    items = []
    for sym in DAILY:
        part, ts, p, m = live_5m_series(sym, d21, d19)
        items.append(spark_item(sym, ts, [b["c"] for b in part] + [p], m))
    return spark_doc(items)


def live_v8(d21, d19):
    part, ts, p, m = live_5m_series("RELIANCE", d21, d19)
    q = {"open": [b["o"] for b in part] + [part[-1]["c"]],
         "high": [b["h"] for b in part] + [max(part[-1]["c"], p)],
         "low": [b["l"] for b in part] + [min(part[-1]["c"], p)],
         "close": [b["c"] for b in part] + [p],
         "volume": [b["v"] for b in part] + [int(part[-1]["v"] * 0.4)]}
    return {"chart": {"result": [{"meta": m, "timestamp": ts, "indicators": {"quote": [q]}}], "error": None}}


def main():
    os.makedirs(OUT, exist_ok=True)
    docs = build()
    _synth.dump(docs["wall"], os.path.join(OUT, "wall5m-2026-10-19.json.gz"), gz=True)
    _synth.dump(docs["daily"], os.path.join(OUT, "daily-spark-2026-10-19.json"))
    _synth.dump(docs["live3"], os.path.join(OUT, "spark-3mo-live-2026-10-21.json"))
    _synth.dump(docs["live5"], os.path.join(OUT, "spark-5m-live-2026-10-21-1001.json"))
    _synth.dump(docs["live_v8"], os.path.join(OUT, "RELIANCE-5m-live-2026-10-21-1001.json"))
    print("wrote 5 fixtures to %s (SYNTHETIC, not real market data)" % OUT)


if __name__ == "__main__":
    main()
