#!/usr/bin/env python3
"""Pre-trade risk check for trader-risk (NSE, rupees, IST). Standard library only, no network.

Reads the proposed orders (trader/orders.json) and checks every one against
your rules (trader/config.json), your account (trader/portfolio.json),
prices (trader/prices.json) and what was placed today (trader/state/broker.json).
Each order gets PASS or BLOCK with the reasons. Orders are checked in file
order as if each PASS had filled, so buys that are fine one by one but break
a limit together are caught. The two exit legs of a bracket (marked exit_group)
are alternatives: only one fills, so neither uses up the other's shares.

    python3 check.py                                 # the trader/ files, writes trader/state/risk.json
    python3 check.py --now "2026-10-19 09:40"        # pin the clock (IST)
    python3 check.py --orders trader/orders.json --json
    python3 check.py --no-write                      # check without recording the verdicts

Entries and exits. An ENTRY opens or adds to a position (a buy, or a sell
that would go short). An EXIT only reduces what you hold. Getting out must
always be possible: an exit is checked for format, asset and short sales
only; every other rule turns into a warning for it.

What blocks an order (every order):

  format        missing symbol, side or qty; a notional or fractional order
                (India trades whole shares only); an unknown type, time in
                force, product or exchange; a limit or stop price that is not a
                multiple of the tick (NSE's tick depends on the price: 0.01 under
                250, 0.05 to 1,000, 0.10 to 5,000, 0.50 to 10,000, 1.00 to 20,000,
                5.00 above; or your tick_size in the config); a limit order
                without a limit price; a bracket that is not whole shares, not
                day, or misses a leg
  asset         anything that does not look like a plain NSE stock or ETF
                symbol; symbols that look like F&O contracts (ending in CE or
                PE after digits, or in FUT) unless "allow_options": true
  short         selling more than you hold, unless "allow_short": true

What blocks an entry (on top):

  blocked       a ticker on your "blocked" list
  size          under min_order_inr, over max_order_inr
  position      the stock would be over max_position_pct of the account
  cash          the buy would dip into your cash buffer, or need margin
                (MIS leverage counts as margin) unless "allow_margin": true
  duplicate     the same symbol and side twice in this batch, or already
                placed today (trader/state/broker.json)
  daily limit   max_orders_per_day orders placed today
  stop          no stop while "require_stop": true (a bracket stop_loss_stop_price
                or the order's plan stop); a stop on the wrong side of the entry
  loss          qty x (entry - stop) over max_loss_per_trade_inr. The entry is
                the limit price for a limit order, else the HIGHER of the plan's
                entry and a fresh price (under 15 minutes old), so a stale plan
                cannot understate the risk
  reward:risk   (target - entry) / (entry - stop) under min_reward_risk
  daily loss    the max losses at the stops of today's entries plus this one
                over max_daily_loss_inr (win or lose, each counts in full)
  trades        max_trades_per_day entries already placed today
  window        before no_entries_before or after no_entries_after (IST), or the
                market is closed (weekend, NSE holiday, outside 09:15 to 15:15:
                F&O stocks stop continuous trading at 15:15 and close in an
                auction, which this pack does not trade)

A product other than MIS on an entry is only a warning: flat-by and the
square-off rules in this pack assume intraday (MIS).

Exit code 0 when every order passes, 1 when any order is blocked.
The verdicts go to trader/state/risk.json with a fingerprint of each order,
so trader-broker only shows an order as ready if THIS exact order passed today.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from decimal import Decimal

DEFAULTS = {
    "budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 80000,
    "min_order_inr": 100, "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000, "max_trades_per_day": 3,
    "max_orders_per_day": 10, "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:30", "no_entries_after": "14:30", "flat_by": "15:00",
    "tick_size": None, "blocked": [], "allow_options": False, "allow_margin": False,
    "allow_short": False,
}
# Shared with trader-broker/ticket.py: the fingerprint covers exactly these fields.
ORDER_FIELDS = ("symbol", "exchange", "side", "qty", "type", "product", "time_in_force", "limit_price",
                "stop_price", "client_order_id", "order_class", "take_profit_limit_price",
                "stop_loss_stop_price", "stop_loss_limit_price")
STRING_FIELDS = ("qty", "limit_price", "stop_price", "take_profit_limit_price", "stop_loss_stop_price",
                 "stop_loss_limit_price")
PRICE_FIELDS = ("qty", "limit_price", "stop_price", "take_profit_limit_price", "stop_loss_stop_price",
                "stop_loss_limit_price")
TICK_FIELDS = ("limit_price", "stop_price", "take_profit_limit_price", "stop_loss_stop_price",
               "stop_loss_limit_price")
REMOVED_FIELDS = ("trail_price", "trail_percent", "extended_hours", "advanced_instructions")
TYPES = {"market", "limit", "stop", "stop_limit"}
TIFS = {"day", "ioc"}
PRODUCTS = {"MIS", "CNC", "NRML"}
EXCHANGES = {"NSE", "BSE"}
# A plain NSE cash-market symbol: RELIANCE, M&M, BAJAJ-AUTO, 20MICRONS, J&KBANK.
TICKER = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
# Symbols that look like F&O contracts (NFO style): NIFTY26OCT24500CE, RELIANCE26OCTFUT.
# Deliberately simple: a name ending in CE or PE right after a digit, or ending in FUT.
DERIVATIVE = re.compile(r"(\d(CE|PE)|FUT)$")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
PAISA = 0.005
FRESH_MINUTES = 15
REF_WORDS = {"plan": "the plan's entry", "price": "the latest price", "limit": "the limit price",
             "stop price": "the stop price", "stale price": "an old price"}
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)     # UTC+5:30, no daylight saving

# NSE capital-market (equity) trading holidays for 2026, exactly as NSE publishes them
# (https://www.nseindia.com/api/holiday-master?type=trading, segment CM, read 2026-11-03; it matches
# the trading days in Yahoo's ^NSEI daily bars). Four fall on a weekend (Feb 15, Mar 21, Aug 15, Nov 8).
# Nov 8 (Sunday, Diwali Laxmi Pujan) is the Muhurat trading day: NSE circular NSE/CMTR/76941 holds a
# special live session from 14:20 that day. It is not a normal session and these tools do not run on it.
NSE_HOLIDAYS = {
    "2026-01-15": "Municipal Corporation Election - Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-02-15": "Mahashivratri",
    "2026-03-03": "Holi",
    "2026-03-21": "Id-Ul-Fitr (Ramadan Eid)",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-08-15": "Independence Day",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-08": "Diwali Laxmi Pujan (Muhurat trading session only)",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
# Reserved for special short sessions (none on a normal NSE day today): {"YYYY-MM-DD": ("HH:MM close", "why")}.
# Special sessions such as Muhurat trading are NOT supported: their dates are not normal sessions.
NSE_EARLY_CLOSE = {}
CALENDAR_YEARS = (2026,)
OPEN = datetime.time(9, 15)
# Since 3 Aug 2026 the Closing Auction Session (CAS) covers every stock with F&O contracts (most liquid
# stocks): their continuous trading ends at 15:15, stop-loss orders are cancelled then, and the closing
# price comes from an auction (15:15 to 15:35). Stocks without F&O keep trading to 15:30. The pack therefore
# treats 15:15 as the end of the day it can trade in (CLOSE); MARKET_CLOSE is NSE's 15:30.
CLOSE = datetime.time(15, 15)
MARKET_CLOSE = datetime.time(15, 30)
# NSE cash-segment tick size by price (circular NSE/CMTR/67133, from 15 April 2025, reviewed monthly on the
# last close of the previous month): (highest price in the band, tick). Under 250 is 0.01; over 20,000 is 5.00.
TICK_BANDS = ((Decimal("1000"), 0.05), (Decimal("5000"), 0.10), (Decimal("10000"), 0.50),
              (Decimal("20000"), 1.00))


class RiskError(Exception):
    pass


def _utf8():
    """So the rupee sign never raises UnicodeEncodeError on a Windows console."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------- time (IST, no tz files)

def to_ist(utc):
    """Naive UTC -> naive IST (UTC+5:30, no daylight saving)."""
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    """Naive IST -> naive UTC."""
    return local.replace(tzinfo=None) - IST_OFFSET


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise RiskError("--now wants a time like \"2026-10-19 10:15\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_utc(text):
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def trading_day(d):
    """(is_trading_day, reason). The NSE holiday list first (it names the holiday even on a
    weekend), then weekends."""
    name = NSE_HOLIDAYS.get(d.isoformat())
    if name:
        return False, "NSE holiday: " + name
    if d.weekday() >= 5:
        return False, "weekend"
    return True, ""


def hhmm(value, key):
    """"09:30" -> time(9, 30); None or "" -> None (that bound is off)."""
    if value in (None, "", False):
        return None
    m = HHMM.match(str(value).strip())
    if not m:
        raise RiskError("%s must be an IST time like \"09:30\", got %r" % (key, value))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def session_close(d):
    """(close time, early-close reason or None) for a date. NSE_EARLY_CLOSE is empty today."""
    ec = NSE_EARLY_CLOSE.get(d.isoformat())
    if ec:
        return hhmm(ec[0], "NSE_EARLY_CLOSE"), ec[1]
    return CLOSE, None


def _shift(t, d, close):
    """t moved earlier by the amount the close moved (CLOSE minus close) on date d."""
    moved = datetime.datetime.combine(d, CLOSE) - datetime.datetime.combine(d, close)
    return (datetime.datetime.combine(d, t) - moved).time()


def window_check(now_ist, c):
    """Reasons an entry cannot be placed now (IST). Empty when the window is off."""
    before = hhmm(c.get("no_entries_before"), "no_entries_before")
    after = hhmm(c.get("no_entries_after"), "no_entries_after")
    if before is None and after is None:
        return [], []
    warns = []
    d, t = now_ist.date(), now_ist.time().replace(second=0, microsecond=0)
    if d.year not in CALENDAR_YEARS:
        warns.append("the holiday list covers %s only: check nseindia.com for %s"
                     % (" and ".join(map(str, CALENDAR_YEARS)), d.isoformat()))
    ok, why = trading_day(d)
    stamp = now_ist.strftime("%a %Y-%m-%d %H:%M")
    if not ok:
        return ["window: the market is closed today (%s, %s IST)" % (why, stamp)], warns
    close, early_why = session_close(d)
    if t < OPEN or now_ist.time() >= close:
        return ["window: outside the session (%s IST; entries run %s to %s, when continuous trading of F&O "
                "stocks ends and the closing auction starts)"
                % (stamp, OPEN.strftime("%H:%M"), close.strftime("%H:%M"))], warns
    if close != CLOSE and after is not None:
        after = _shift(after, d, close)
        warns.append("early close at %s today (%s): no new entries after %s"
                     % (close.strftime("%H:%M"), early_why, after.strftime("%H:%M")))
    if before is not None and t < before:
        return ["window: no new entries before %s IST (it is %s)" % (before.strftime("%H:%M"),
                                                                     t.strftime("%H:%M"))], warns
    if after is not None and t > after:
        return ["window: no new entries after %s IST (it is %s)" % (after.strftime("%H:%M"),
                                                                    t.strftime("%H:%M"))], warns
    return [], warns


# ---------------------------------------------------------------- files and helpers

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))


def fingerprint(order):
    """Same function in trader-broker/ticket.py: the exact order that was checked."""
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def inr(x):
    """Rupees with Indian digit grouping: 123456.78 -> "₹1,23,456.78"; whole amounts have no decimals."""
    x = float(x)
    s = "{:.2f}".format(abs(x))
    whole, _, frac = s.partition(".")
    if frac == "00":
        frac = ""
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    neg = x < 0 and float(s) != 0
    return ("-" if neg else "") + "₹" + whole + ("." + frac if frac else "")


def _paise(x):
    """A price to the paisa, half up (1419.475 -> 1419.48)."""
    return int(float(x) * 100 + 0.5 + 1e-9) / 100.0


def _p(x):
    return "%.2f" % float(x)


def on_tick(value, tick):
    """True when value is a whole multiple of the tick (exact decimal arithmetic)."""
    try:
        p = Decimal(str(value).strip()) * 100
    except Exception:
        return False
    t = Decimal(str(tick)) * 100
    return p == p.to_integral_value() and t > 0 and p % t == 0


def tick_for(price):
    """NSE's tick size for a price (circular NSE/CMTR/67133): 0.01 under 250; 0.05 from 250 to 1,000;
    0.10 above 1,000 to 5,000; 0.50 above 5,000 to 10,000; 1.00 above 10,000 to 20,000; 5.00 above."""
    try:
        p = Decimal(str(price).strip())
    except Exception:
        return 0.05
    if p < 250:
        return 0.01
    for top, tick in TICK_BANDS:
        if p <= top:
            return tick
    return 5.0


def tick_of(c):
    """The tick override from the config (tick_size), or None: then NSE's tick for each price applies."""
    raw = c.get("tick_size")
    if raw is None:
        return None
    t = _f(raw)
    if t is None or t <= 0 or abs(t * 100 - round(t * 100)) > 1e-6:
        raise RiskError("tick_size must be null (NSE's tick for each price) or a positive number of whole paise, "
                        "like 0.05 or 0.10")
    return t


def account(cfg, portfolio, prices):
    """Starting account: cash and position values. No portfolio = a fresh
    account funded with the budget."""
    warnings = []
    if portfolio is None:
        budget = _f(cfg.get("budget"))
        if not budget:
            raise RiskError("no trader/portfolio.json and no budget in the config: "
                            "nothing to check the orders against")
        return {"cash": budget, "shares": {}, "values": {}}, warnings
    cash = _f(portfolio.get("cash"))
    if cash is None:
        raise RiskError("portfolio.json needs a \"cash\" number")
    shares, values = {}, {}
    for p in portfolio.get("positions", []):
        t = str(p.get("ticker") or p.get("symbol") or "").upper()
        if not t:
            continue
        n = _f(p.get("shares", p.get("qty")))
        if n is None:
            n = sum(_f(l.get("shares")) or 0 for l in p.get("lots", []))
        shares[t] = shares.get(t, 0) + n
        if prices.get(t):
            values[t] = shares[t] * prices[t]
        else:
            basis = sum(_f(l.get("cost")) or 0 for l in p.get("lots", []))
            values[t] = basis
            warnings.append("no price for %s: valued at its cost basis %s" % (t, inr(basis)))
    return {"cash": cash, "shares": shares, "values": values}, warnings


def rec_day(rec):
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def rec_is_entry(rec):
    """trader-broker logs the kind trader-risk gave; older records: a buy is an entry."""
    k = rec.get("kind")
    if k:
        return k == "entry"
    return str(rec.get("side", "")).lower() == "buy"


def placed_today(state, today):
    """Orders recorded as placed today by trader-broker."""
    out = []
    for rec in (state or {}).get("orders", []):
        if rec.get("status") == "placed" and rec_day(rec) == today:
            out.append(rec)
    return out


# ---------------------------------------------------------------- the check

def check(orders, cfg, portfolio=None, prices=None, state=None, today=None, now=None, price_times=None):
    """Pure function. Returns {"results": [...], "pass": n, "block": n, "warnings": [...]}.

    now          naive UTC (default: the real clock); its IST date is "today"
    price_times  {SYM: naive UTC time of that price}; None = every price counts as fresh"""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    tick = tick_of(c)
    prices = {str(k).upper(): float(v) for k, v in (prices or {}).items() if _f(v)}
    now_utc = now or parse_now(None)
    now_ist = to_ist(now_utc)
    today = today or now_ist.date().isoformat()
    acct, top_warn = account(c, portfolio, prices)
    blocked = {str(b).upper() for b in c.get("blocked") or []}
    done_today = placed_today(state, today)
    count_today = len(done_today)
    entries_today = [r for r in done_today if rec_is_entry(r)]
    loss_today = 0.0
    for r in entries_today:
        ml = _f(r.get("max_loss"))
        if ml is None:
            top_warn.append("an entry placed today (%s %s) has no max loss in the log; not counted in "
                            "the daily loss" % (r.get("side"), r.get("symbol")))
        else:
            loss_today += ml
    placed_keys = {(str(r.get("symbol", "")).upper(), r.get("side")) for r in done_today}
    win_reasons, win_warns = window_check(now_ist, c)
    seen = set()
    results = []
    batch_entries, batch_loss = 0, 0.0
    top_warn.extend(win_warns)

    def fresh(sym):
        if sym not in prices:
            return None
        if price_times is None:
            return prices[sym]
        t = price_times.get(sym)
        if t is None:
            return None
        age = (now_utc - t).total_seconds()
        return prices[sym] if -60 <= age <= FRESH_MINUTES * 60 else None

    for i, o in enumerate(orders, 1):
        reasons, warns, info = [], [], []
        later = []                       # entry-only reasons, turned into warnings for an exit
        sym = str(o.get("symbol", "")).strip().upper()
        side = str(o.get("side", "")).strip().lower()
        typ = str(o.get("type", "market")).strip().lower()
        tif = str(o.get("time_in_force", "day")).strip().lower()
        exch = str(o.get("exchange") or "").strip().upper()
        prod = str(o.get("product") or "").strip().upper()
        qty = o.get("qty")
        plan = o.get("plan") if isinstance(o.get("plan"), dict) else {}
        oc = str(o.get("order_class") or "").strip().lower()
        legs = o.get("take_profit_limit_price") is not None or o.get("stop_loss_stop_price") is not None
        bracket = oc == "bracket" or (not oc and legs)
        value = None

        # format
        if not sym:
            reasons.append("format: no symbol")
        if side not in ("buy", "sell"):
            reasons.append("format: side must be buy or sell, got %r" % o.get("side"))
        if o.get("notional") is not None:
            reasons.append("format: India trades whole shares only (no notional / fractional orders)")
        elif qty is None:
            reasons.append("format: no qty: give a whole number of shares")
        elif _f(qty) is not None and abs(_f(qty) - round(_f(qty))) > 1e-9:
            reasons.append("format: India trades whole shares only (no notional / fractional orders)")
        for k in REMOVED_FIELDS:
            if o.get(k) is not None and o.get(k) is not False:
                reasons.append("format: %s is not supported by this pack" % k)
        for k in PRICE_FIELDS:
            if o.get(k) is not None:
                v = _f(o.get(k))
                if v is None or v <= 0:
                    reasons.append("format: %s must be a positive number, got %r" % (k, o.get(k)))
                else:
                    if not isinstance(o.get(k), str):
                        warns.append("send %s as a string (\"%s\"), the way orders.json writes it"
                                     % (k, o.get(k)))
                    if k in TICK_FIELDS:
                        tk = tick if tick is not None else tick_for(o.get(k))
                        if not on_tick(o.get(k), tk):
                            reasons.append("format: price %s is not a multiple of the ₹%s tick%s"
                                           % (str(o.get(k)).strip(), "%.2f" % tk,
                                              "" if tick is not None else " (NSE's tick for a price in its band)"))
        if typ not in TYPES:
            reasons.append("format: unknown order type %r" % typ)
        if tif not in TIFS:
            reasons.append("format: unknown time_in_force %r" % tif)
        if exch and exch not in EXCHANGES:
            reasons.append("format: unknown exchange %r (use NSE or BSE)" % exch)
        elif not exch:
            warns.append("no exchange in the order: treated as NSE")
        elif exch == "BSE":
            warns.append("exchange BSE: the data, holiday list and charges in this pack assume NSE")
        if prod and prod not in PRODUCTS:
            reasons.append("format: unknown product %r (use MIS, CNC or NRML)" % prod)
        elif not prod:
            warns.append("no product in the order: treated as MIS (intraday)")
        if typ in ("limit", "stop_limit") and o.get("limit_price") is None:
            reasons.append("format: a %s order needs a limit_price" % typ)
        if typ in ("stop", "stop_limit") and o.get("stop_price") is None:
            reasons.append("format: a %s order needs a stop_price" % typ)
        if o.get("stop_loss_limit_price") is not None and o.get("stop_loss_stop_price") is None:
            reasons.append("format: stop_loss_limit_price needs a stop_loss_stop_price")
        if bracket:
            if tif != "day":
                reasons.append("format: a bracket order needs time_in_force day, got %r" % tif)
            if o.get("take_profit_limit_price") is None or o.get("stop_loss_stop_price") is None:
                reasons.append("format: a bracket order needs both take_profit_limit_price and "
                               "stop_loss_stop_price")
            info.append("bracket = a plan: the stop-loss and target legs are placed after the entry fills, "
                        "each as its own ticket (no automatic one-cancels-the-other)")
        elif oc:
            warns.append("advanced order (%s): check every leg before you place it" % oc)

        # asset
        if sym and DERIVATIVE.search(sym):
            if not c.get("allow_options"):
                reasons.append("asset: %s looks like an F&O contract (futures or options); derivatives are off "
                               "(\"allow_options\": false)" % sym)
            else:
                warns.append("derivative order: futures and options can lose more than you expect; "
                             "this pack models equities only")
        elif sym and not TICKER.match(sym):
            reasons.append("asset: %s does not look like an NSE equity symbol (plain symbols like RELIANCE, "
                           "M&M, BAJAJ-AUTO, without .NS); this pack trades NSE stocks and ETFs only" % sym)

        # the reference price: what one share costs to get in
        plan_entry = _f(plan.get("entry"))
        live = fresh(sym)
        ref, ref_from = None, None
        if typ in ("limit", "stop_limit"):
            ref, ref_from = _f(o.get("limit_price")), "limit"
        elif typ == "stop":
            ref, ref_from = _f(o.get("stop_price")), "stop price"
        if ref is None:
            cands = [(p, w) for p, w in ((plan_entry, "plan"), (live, "price")) if p]
            if cands:
                ref, ref_from = max(cands, key=lambda x: x[0])
                ref = _paise(ref)
                if live and ref_from == "price" and plan_entry and live > plan_entry + PAISA:
                    info.append("%s last %s is above the plan's entry %s: the risk is measured from %s"
                                % (sym, _p(ref), _p(plan_entry), _p(ref)))
            elif sym in prices:
                ref, ref_from = prices[sym], "stale price"
        if plan_entry and live is None and sym in prices and price_times is not None and typ == "market":
            warns.append("the %s price in trader/prices.json is older than %d minutes: not used"
                         % (sym, FRESH_MINUTES))
        q = _f(qty)
        if q and ref:
            value = q * ref

        # entry or exit
        held = acct["shares"].get(sym, 0.0)
        held_value = acct["values"].get(sym, 0.0)
        exiting = False
        if side == "sell" and held > 1e-9 and q is not None:
            exiting = q <= held + 1e-9
        elif side == "buy" and held < -1e-9 and q is not None:
            exiting = q <= -held + 1e-9
        kind = "exit" if exiting else "entry"
        alt_exit = kind == "exit" and bool(o.get("exit_group"))    # one leg of a bracket's pair: only one fills
        if alt_exit:
            info.append("exit leg of a bracket plan: only one of its two legs fills, so it does not use up "
                        "the shares for the other")
        if kind == "entry" and value is None and q:
            later.append("price: no price for %s to size a %s-share order; add it to "
                         "trader/prices.json (trader-alerts: alerts.py quote %s --write)" % (sym, qty, sym))
        if kind == "entry" and prod and prod in PRODUCTS and prod != "MIS":
            warns.append("not an intraday order: flat-by and square-off rules in this pack assume MIS "
                         "(this order is %s)" % prod)

        # blocked list and size
        if sym in blocked:
            later.append("blocked: %s is on your blocked list" % sym)
        if value is not None:
            if value < float(c.get("min_order_inr") or 0) - PAISA:
                later.append("size: %s is under your %s minimum order"
                             % (inr(value), inr(c["min_order_inr"])))
            if c.get("max_order_inr") and value > float(c["max_order_inr"]) + PAISA:
                later.append("size: %s is over your %s max order"
                             % (inr(value), inr(c["max_order_inr"])))

        # account effects, simulated in order
        cash = acct["cash"]
        total = cash + sum(acct["values"].values())
        buffer_inr = total * float(c.get("cash_buffer_pct") or 0) / 100.0
        if value is not None and side == "buy" and kind == "entry":
            after_cash = cash - value
            if after_cash < -PAISA:
                if c.get("allow_margin"):
                    warns.append("margin: needs %s but only %s cash; the rest is MIS leverage (borrowed money)"
                                 % (inr(value), inr(cash)))
                else:
                    later.append("cash: needs %s but only %s cash; that would be margin (MIS leverage, "
                                 "borrowed money)" % (inr(value), inr(cash)))
            elif after_cash < buffer_inr - PAISA:
                later.append("cash: leaves %s cash, under your %s%% buffer of %s"
                             % (inr(max(after_cash, 0)), _pct(c["cash_buffer_pct"]), inr(buffer_inr)))
            pos_after = held_value + value
            limit = total * float(c.get("max_position_pct") or 100) / 100.0
            if total > 0 and pos_after > limit + PAISA:
                later.append("position: %s would be %s, %.1f%% of the %s account, over your %s%% cap"
                             % (sym, inr(pos_after), 100.0 * pos_after / total, inr(total),
                                _pct(c["max_position_pct"])))
        if side == "sell" and sym and kind == "entry":
            if q is not None:
                over = q > held + 1e-9
            else:
                over = held <= 0
            if over and not c.get("allow_short"):
                reasons.append("short: selling more %s than you hold (%s shares); short selling is off. "
                               "If you bought it, record the fill first (ticket.py fill)" % (sym, _num(held)))

        # day-trade checks: stop, loss, reward:risk, daily loss, trades, window
        max_loss = risk_ps = rr = stop = target = None
        if kind == "entry" and side in ("buy", "sell"):
            long = side == "buy"
            leg_stop, plan_stop = _f(o.get("stop_loss_stop_price")), _f(plan.get("stop"))
            stop = leg_stop if leg_stop else plan_stop
            target = _f(o.get("take_profit_limit_price")) or _f(plan.get("target"))
            if leg_stop and plan_stop and abs(leg_stop - plan_stop) > PAISA:
                warns.append("the bracket stop %s differs from the plan's stop %s: the bracket's is the one "
                             "placed after the entry fills" % (_p(leg_stop), _p(plan_stop)))
            if stop and not leg_stop:
                warns.append("the stop %s is in the plan only, so nothing triggers it by itself: set a stop alert "
                             "(trader-alerts: alerts.py add --from-plan)" % _p(stop))
            if not stop:
                if c.get("require_stop", True):
                    later.append("stop: no stop. Every entry needs one (\"require_stop\": true): a bracket "
                                 "stop_loss_stop_price, from trader-exit and trader-budget")
                else:
                    warns.append("no stop: the max loss is not capped, so it is not counted in the daily loss")
            elif ref:
                if (long and stop >= ref - PAISA) or (not long and stop <= ref + PAISA):
                    later.append("stop: the stop %s must be %s the entry %s for a %s"
                                 % (_p(stop), "below" if long else "above", _p(ref), side))
                else:
                    risk_c = int(round(abs(ref - stop) * 100))
                    risk_ps = risk_c / 100.0
                    shares = q if q is not None else 0
                    max_loss = round(shares * risk_ps, 2)
                    cap = _f(c.get("max_loss_per_trade_inr"))
                    if cap and max_loss > cap + PAISA:
                        later.append("loss: %s shares x %s risk a share = %s at the stop, over your %s max loss "
                                     "per trade" % (_num(shares), _p(risk_ps), inr(max_loss), inr(cap)))
                    min_rr = _f(c.get("min_reward_risk"))
                    if target:
                        if (long and target <= ref + PAISA) or (not long and target >= ref - PAISA):
                            later.append("target: the target %s must be %s the entry %s"
                                         % (_p(target), "above" if long else "below", _p(ref)))
                        else:
                            rr = round(int(round(abs(target - ref) * 100)) / float(risk_c), 2)
                            if min_rr and rr < min_rr - 1e-9:
                                later.append("reward:risk: %.2f (target %s, entry %s, stop %s) is under your "
                                             "minimum %s" % (rr, _p(target), _p(ref), _p(stop), _num(min_rr)))
                    elif min_rr:
                        later.append("reward:risk: no target to measure it against (min_reward_risk %s)"
                                     % _num(min_rr))
            daily = _f(c.get("max_daily_loss_inr"))
            if daily and max_loss is not None:
                used = loss_today + batch_loss
                if used + max_loss > daily + PAISA:
                    later.append("daily loss: %s already at risk today plus %s for this one = %s, over your %s "
                                 "daily loss limit" % (inr(used), inr(max_loss), inr(used + max_loss),
                                                       inr(daily)))
            cap_t = int(c.get("max_trades_per_day") or 0)
            if cap_t and len(entries_today) + batch_entries >= cap_t:
                later.append("trades: %d entries placed today plus %d passed earlier in this batch, your limit "
                             "is %d a day" % (len(entries_today), batch_entries, cap_t))
            later.extend(win_reasons)
            if stop and ref and risk_ps:
                info.append("entry %s (%s), stop %s, target %s: risk %s a share"
                            % (_p(ref), REF_WORDS.get(ref_from, ref_from), _p(stop),
                               _p(target) if target else "none", _p(risk_ps)))
                info.append("max loss %s%s, reward:risk %s%s"
                            % (inr(max_loss), (" (limit %s)" % inr(c["max_loss_per_trade_inr"]))
                               if _f(c.get("max_loss_per_trade_inr")) else "",
                               ("%.2f" % rr) if rr is not None else "n/a",
                               (" (min %s)" % _num(c["min_reward_risk"])) if _f(c.get("min_reward_risk")) else ""))
            if daily or cap_t:
                info.append("today before this: %d of %s trades, %s of %s daily loss"
                            % (len(entries_today) + batch_entries, cap_t or "unlimited",
                               inr(loss_today + batch_loss), inr(daily) if daily else "no limit on"))

        # duplicates and the daily order limit
        key = (sym, side)
        if key in seen and not alt_exit:
            later.append("duplicate: a second %s %s in this batch" % (side, sym))
        if key in placed_keys and not alt_exit:
            later.append("duplicate: a %s %s was already placed today" % (side, sym))
        passed_so_far = sum(1 for r in results if r["verdict"] == "PASS")
        cap_n = int(c.get("max_orders_per_day") or 0)
        if cap_n and count_today + passed_so_far >= cap_n:
            later.append("daily limit: %d placed today plus %d passed earlier in this batch, "
                         "your limit is %d a day" % (count_today, passed_so_far, cap_n))
        if not alt_exit:
            seen.add(key)

        if kind == "entry":
            reasons.extend(later)
        else:
            warns.extend("exit, not blocked: " + x for x in later)

        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS":
            if kind == "entry" and side in ("buy", "sell"):
                batch_entries += 1
                batch_loss += max_loss or 0.0
            if alt_exit:
                pass                  # nothing has filled yet, and only one of the pair will
            elif side == "buy":
                if value is not None:
                    acct["cash"] -= value
                    acct["values"][sym] = acct["values"].get(sym, 0.0) + value
                if q is not None:
                    acct["shares"][sym] = held + q
            elif side == "sell":
                if value is not None:
                    acct["cash"] += value
                    acct["values"][sym] = max(acct["values"].get(sym, 0.0) - value, 0.0)
                if q is not None:
                    acct["shares"][sym] = held - q
        results.append({
            "index": i, "symbol": sym, "side": side, "kind": kind,
            "amount": "%s shares" % qty,
            "value_inr": round(value, 2) if value is not None else None,
            "entry_ref": round(ref, 2) if ref else None, "entry_ref_from": ref_from,
            "stop": stop, "target": target, "risk_per_share": risk_ps, "max_loss": max_loss,
            "reward_risk": rr, "fingerprint": fingerprint(o), "verdict": verdict,
            "reasons": reasons, "warnings": warns, "info": info,
        })

    return {
        "date": today,
        "now_ist": now_ist.strftime("%Y-%m-%d %H:%M"),
        "results": results,
        "pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "block": sum(1 for r in results if r["verdict"] == "BLOCK"),
        "warnings": top_warn,
        "cash_after": round(acct["cash"], 2),
        "today": {"orders_placed": count_today, "entries_placed": len(entries_today),
                  "loss_at_risk": round(loss_today, 2)},
    }


def _pct(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x)


def _num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.4f" % x).rstrip("0")


def report(res):
    lines = []
    for w in res["warnings"]:
        lines.append("WARNING: " + w)
    if res["warnings"]:
        lines.append("")
    for r in res["results"]:
        lines.append("%-5s #%d %s %s %s, %s" % (r["verdict"], r["index"], r["side"], r["symbol"], r["amount"],
                                                r["kind"]))
        for x in r.get("info", []):
            lines.append("          " + x)
        for x in r["reasons"]:
            lines.append("        - " + x)
        for x in r["warnings"]:
            lines.append("        ~ " + x)
    lines.append("")
    lines.append("%d passed, %d blocked at %s IST. Cash after the passed orders: %s."
                 % (res["pass"], res["block"], res["now_ist"], inr(res["cash_after"])))
    if res["block"]:
        lines.append("Blocked orders are not placed. Fix the order or your rules, then check again.")
    else:
        lines.append("Every order passed: trader-broker can place each one.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


def load_prices(pdoc):
    """prices.json -> ({SYM: price}, {SYM: price time as naive UTC or None}).

    The price time is the quote's own time (detail.time), else when it was fetched
    (detail.fetched), else the file's as_of."""
    if not isinstance(pdoc, dict):
        return {}, {}
    prices = pdoc.get("prices", pdoc)
    if not isinstance(prices, dict):
        return {}, {}
    out, times = {}, {}
    as_of = parse_utc(pdoc.get("as_of"))
    detail = pdoc.get("detail") or {}
    for s, p in prices.items():
        if _f(p) is None:
            continue
        s = str(s).upper()
        out[s] = float(p)
        d = detail.get(s) or {}
        times[s] = parse_utc(d.get("time")) or parse_utc(d.get("fetched")) or as_of
    return out, times


def main(argv=None):
    _utf8()
    ap = argparse.ArgumentParser(description="Check proposed orders against your limits (trader-risk).")
    ap.add_argument("--orders", default="trader/orders.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; if missing, a fresh account funded with the budget")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--state", default="trader/state/broker.json", help="what trader-broker placed today")
    ap.add_argument("--now", help="pretend it is this IST time, e.g. \"2026-10-19 10:15\"")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in IST)")
    ap.add_argument("--out", default="trader/state/risk.json", help="where the verdicts are recorded")
    ap.add_argument("--no-write", action="store_true", help="do not record the verdicts")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        now = parse_now(a.now)
        if a.today and not re.match(r"^\d{4}-\d{2}-\d{2}$", a.today):
            raise RiskError("--today wants a date like 2026-10-19")
        doc = read_json(a.orders, "orders file (run trader-budget first)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("no orders in %s" % a.orders)
        cfg = read_json(a.config, "config", required=False) or {}
        portfolio = read_json(a.portfolio, "portfolio", required=False)
        pdoc = read_json(a.prices, "prices", required=False) or {}
        prices, times = load_prices(pdoc)
        state = read_json(a.state, "broker state", required=False)
        res = check(orders, cfg, portfolio, prices, state, a.today, now, times)
        if pdoc.get("as_of"):
            res["prices_as_of"] = pdoc["as_of"]
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            rec = dict(res)
            rec["checked_at"] = datetime.datetime.now(datetime.timezone.utc).replace(
                microsecond=0, tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ")
            rec["orders_file"] = a.orders
            tmp = a.out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, a.out)
    except RiskError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2) if a.json else report(res))
    return 1 if res["block"] else 0


if __name__ == "__main__":
    sys.exit(main())
