#!/usr/bin/env python3
"""Position sizing for trader-budget (NSE, rupees, IST). Standard library only, no network.

Default mode: sizes ONE day trade from trader/plan.json (written by
trader-exit) to your budget and your max loss per trade, then writes one
"bracket" order to trader/orders.json. A bracket here is a PLAN with three
parts: the entry order, a stop-loss order and a target order. The stop-loss
and target legs are placed only AFTER the entry fills, each as its own ticket
(there is no automatic one-cancels-the-other). It never places anything:
trader-risk checks the order and trader-broker asks you yes or no.

    python3 size.py                                  # trader/plan.json + trader/config.json
    python3 size.py --write                          # write trader/orders.json
    python3 size.py --limit                          # a limit order at the plan's entry
    python3 size.py --limit 1416.30 --write          # a limit order at 1416.30
    python3 size.py --now "2026-10-19 09:40" --json  # pin the clock (IST)

How many shares. Whole shares only: India has no fractional or notional orders.

    shares = floor of the smallest of
      max loss       max_loss_per_trade_inr / risk per share (entry minus stop)
      position cap   budget x max_position_pct / entry
      max order      max_order_inr / entry
      cash           (budget minus the cash buffer) / entry
                     (and no more than the cash in trader/portfolio.json, if you have one)

The entry it sizes at: a limit order's limit price; for a market order the
plan's entry, or the price in trader/prices.json if that is fresh (under 15
minutes old) and higher, because a price that ran up since the plan means a
bigger loss at the same stop. Zero shares (the stop is too far away for your
max loss) is a plain refusal: no order is written. So is a reward:risk under
min_reward_risk (the price ran toward the target since the plan).

Prices on the order sit on NSE's tick. The tick depends on the price (NSE circular
NSE/CMTR/67133): 0.01 under 250; 0.05 from 250 to 1,000; 0.10 above 1,000 to
5,000; 0.50 above 5,000 to 10,000; 1.00 above 10,000 to 20,000; 5.00 above. Set
"tick_size" in the config (or in the plan, via trader-exit --tick-size) to force
one tick for every price. The stop is rounded DOWN to the tick, the target and a
limit price to the nearest tick, and it says so when it moves one.

Charges (informational only). It also prints the estimated round-trip charges
(brokerage, STT, stamp duty, exchange and SEBI fees, GST) for the sized trade
at the entry, at the stop and at the target. They never change the share
count. The rates are the ones documented below: verify them with your
broker's charge calculator.

Weights mode (kept from v1, no longer the headline): split a budget across a
list of stocks by weight, in whole shares.

    python3 size.py --weights RELIANCE=13,HDFCBANK=9,INFY=5 --prices trader/prices.json
    python3 size.py --targets my-weights.json --budget 100000 --write

  1. cash buffer   budget x cash_buffer_pct stays in cash, never invested
  2. weights       renormalised to 100 over the stocks you keep (blocked
                   tickers are dropped first; --top N keeps the N biggest)
  3. cap           no stock gets more than max_position_pct of the budget;
                   anything over the cap is shared out to the others by
                   weight, again and again until nobody is over
  4. whole shares  each slice is floored to whole shares at the price you
                   give (needs prices); then one more share at a time goes to
                   the stock furthest below its slice while cash lasts
  5. min order     a slice under min_order_inr is dropped and its weight
                   shared out, so you never place a tiny order

Money that cannot be invested is reported as "left idle", never hidden.
"""

import argparse
import datetime
import json
import math
import os
import sys
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

# v2 defaults: the contract's config.example.json. Missing keys fall back to these.
DEFAULTS = {
    "budget": 100000,
    "cash_buffer_pct": 10,
    "max_position_pct": 75,
    "max_order_inr": 80000,
    "min_order_inr": 100,
    "max_loss_per_trade_inr": 1000,
    "max_daily_loss_inr": 3000,
    "max_trades_per_day": 3,
    "max_orders_per_day": 10,
    "min_reward_risk": 1.0,
    "require_stop": True,
    "no_entries_before": "09:30",
    "no_entries_after": "14:30",
    "flat_by": "15:00",
    "tick_size": None,
    "fractional": False,
    "paper": True,
    "blocked": [],
    "allow_options": False,
    "allow_margin": False,
    "allow_short": False,
    "brokerage_pct": 0.03,
    "brokerage_per_order_inr": 20,
}
EPS = 1e-9
FRESH_MINUTES = 15
SETUP_WORDS = {"orb": "opening-range breakout", "flag": "flag", "bounce": "oversold bounce"}
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)     # UTC+5:30, no daylight saving

# Intraday (MIS) equity charges, percent of turnover. They change, and brokers differ in brokerage:
# verify every number with your broker's charge calculator. Only informational.
# Rates as listed on https://zerodha.com/charges for equity intraday, read 2026-11-04. Not included:
# IPFT (NSE, a fraction of a paisa a lakh), DP charges (delivery sells only), and the broker's
# auto square-off fee if it has to close the trade for you.
STT_SELL_PCT = Decimal("0.025")        # securities transaction tax, sell side
STAMP_BUY_PCT = Decimal("0.003")       # stamp duty, buy side
EXCHANGE_TXN_PCT = Decimal("0.00307")  # NSE transaction charge, each side
SEBI_PCT = Decimal("0.0001")           # SEBI turnover fee, each side (Rs 10 per crore)
GST_PCT = Decimal("18")                # on brokerage + exchange charge + SEBI fee
# NSE cash-segment tick size by price (circular NSE/CMTR/67133, from 15 April 2025, reviewed monthly on the
# last close of the previous month): (highest price in the band, tick). Under 250 is 0.01; over 20,000 is 5.00.
TICK_BANDS = ((Decimal("1000"), 0.05), (Decimal("5000"), 0.10), (Decimal("10000"), 0.50),
              (Decimal("20000"), 1.00))


class SizeError(Exception):
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
        raise SizeError("--now wants a time like \"2026-10-19 10:15\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_utc(text):
    """"2026-10-19T05:22:13Z" -> naive UTC, or None."""
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def utc_iso(now_utc=None):
    now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return now_utc.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- inputs

def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SizeError("no %s at %s" % (what, path))
    except json.JSONDecodeError as e:
        raise SizeError("%s is not valid JSON: %s" % (path, e))


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        cfg.update(read_json(path, "config"))
    if not cfg.get("paper", True):
        raise SizeError("this pack trades on paper only (no broker is connected): "
                        "set \"paper\": true in trader/config.json")
    return cfg


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_weights(data):
    """Accept a weights file, a {ticker: weight} dict or a list of rows.

    Returns [{"ticker", "name", "weight"}] with weight as a float."""
    if isinstance(data, dict) and "weights" in data:
        data = data["weights"]
    rows = []
    if isinstance(data, dict):
        for t, w in data.items():
            rows.append({"ticker": t, "name": "", "weight": w})
    elif isinstance(data, list):
        for i, r in enumerate(data):
            if not isinstance(r, dict):
                raise SizeError("weights[%d] is not an object" % i)
            t = r.get("ticker") or r.get("symbol")
            w = r.get("weight", r.get("weight_pct", r.get("pct")))
            if not t or w is None:
                raise SizeError("weights[%d] needs a ticker and a weight" % i)
            rows.append({"ticker": t, "name": r.get("name", ""), "weight": w})
    else:
        raise SizeError("weights must be a list or an object")
    out = []
    for r in rows:
        try:
            w = float(r["weight"])
        except (TypeError, ValueError):
            raise SizeError("weight for %s is not a number: %r" % (r["ticker"], r["weight"]))
        out.append({"ticker": str(r["ticker"]).strip().upper(), "name": r["name"] or "", "weight": w})
    return out


def weights_from_arg(text):
    """RELIANCE=13,HDFCBANK=9 -> {ticker: weight}"""
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SizeError("--weights wants TICKER=WEIGHT pairs, got %r" % part)
        t, w = part.split("=", 1)
        out[t.strip().upper()] = w.strip()
    return out


def load_prices(path):
    if not path:
        return {}
    data = read_json(path, "prices")
    prices = data.get("prices", data) if isinstance(data, dict) else {}
    out = {}
    for t, p in prices.items():
        try:
            out[str(t).upper()] = float(p)
        except (TypeError, ValueError):
            continue
    return out


def price_time(doc, sym):
    """When this symbol's price is from: the quote's own time (detail[sym].time), else when it
    was fetched (detail[sym].fetched), else the file's as_of."""
    det = ((doc or {}).get("detail") or {}).get(sym) or {}
    for key in ("time", "fetched"):
        t = parse_utc(det.get(key))
        if t:
            return t
    return parse_utc((doc or {}).get("as_of"))


def fresh_price(doc, sym, now_utc, minutes=FRESH_MINUTES):
    """(price, fetched_utc, fresh) for sym from a prices.json doc, or (None, None, False)."""
    if not isinstance(doc, dict):
        return None, None, False
    p = _f((doc.get("prices") or {}).get(sym))
    if p is None or p <= 0:
        return None, None, False
    t = price_time(doc, sym)
    if t is None:
        return p, None, False
    age = (now_utc - t).total_seconds()
    return p, t, -60 <= age <= minutes * 60


# ---------------------------------------------------------------- money and tick helpers

def inr(x, dec=None):
    """Rupees with Indian digit grouping: 123456.78 -> "₹1,23,456.78". dec=None: no decimals
    when the amount is whole, else two."""
    x = float(x)
    if dec is None:
        dec = 0 if abs(x - round(x)) < 0.005 else 2
    s = "%.*f" % (dec, abs(x))
    whole, _, frac = s.partition(".")
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


def _dec(x):
    return Decimal(str(x))


def to_paise(x):
    """A price as whole paise (an int), half up: 1416.35 -> 141635."""
    return int((_dec(x) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def tick_for(price):
    """NSE's tick size for a price (circular NSE/CMTR/67133): 0.01 under 250; 0.05 from 250 to 1,000;
    0.10 above 1,000 to 5,000; 0.50 above 5,000 to 10,000; 1.00 above 10,000 to 20,000; 5.00 above."""
    p = _dec(price)
    if p < 250:
        return 0.01
    for top, tick in TICK_BANDS:
        if p <= top:
            return tick
    return 5.0


def floor_tick(x, tick=None):
    """Round DOWN to the tick (a stop goes away from the entry): 1406.475 -> 1406.40 on the 0.10 tick.
    tick None: NSE's tick for x."""
    t = _dec(tick if tick is not None else tick_for(x))
    return float((_dec(x) / t).to_integral_value(rounding=ROUND_FLOOR) * t)


def round_tick(x, tick=None):
    """Round to the NEAREST tick, half up (a target or a limit price): 1416.35 -> 1416.40 on the 0.10 tick.
    tick None: NSE's tick for x."""
    t = _dec(tick if tick is not None else tick_for(x))
    return float((_dec(x) / t).to_integral_value(rounding=ROUND_HALF_UP) * t)


def tick_of(cfg):
    """The tick override from the config (tick_size), or None: then NSE's tick for each price applies."""
    raw = (cfg or {}).get("tick_size")
    if raw is None:
        return None
    t = _f(raw)
    if t is None or t <= 0 or abs(t * 100 - round(t * 100)) > 1e-6:
        raise SizeError("tick_size must be null (NSE's tick for each price) or a positive number of whole paise, "
                        "like 0.05 or 0.10")
    return t


def shares_for(rupees, per_share):
    """Whole shares that fit in `rupees` at per_share, in exact integer paise."""
    u, p = to_paise(rupees), to_paise(per_share)
    if p <= 0 or u <= 0:
        return 0
    return u // p


def _price(x):
    """1406.45 -> "1406.45": orders carry prices as strings with two decimals."""
    return "%.2f" % (to_paise(x) / 100.0)


def _num(x):
    """1250 -> "1250", 1118.13 -> "1118.13"."""
    x = round(float(x), 2)
    if abs(x - round(x)) < 0.005:
        return str(int(round(x)))
    return "%.2f" % x


def _raw(x):
    """The number as given, without float noise: 1426.225 -> "1426.225"."""
    return str(_dec(x))


def _q2(x):
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- estimated charges (informational)

def charges_for(qty, entry, exit_price, cfg=None):
    """Estimated round-trip charges for buying `qty` at `entry` and selling at `exit_price`
    (intraday equity, MIS). Informational only: it never changes the share count.

      brokerage      per order min(brokerage_pct % of turnover, brokerage_per_order_inr), two orders
      STT            0.025% of the sell turnover
      stamp duty     0.003% of the buy turnover
      exchange txn   0.00307% of the buy plus sell turnover
      SEBI fee       0.0001% of the buy plus sell turnover
      GST            18% of (brokerage + exchange txn + SEBI fee)

    Each part is rounded to the paisa and the total is their sum."""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    q = Decimal(int(qty))
    buy, sell = q * _dec(entry), q * _dec(exit_price)
    both = buy + sell
    pct, cap = _f(c.get("brokerage_pct")), _f(c.get("brokerage_per_order_inr"))

    def order_brokerage(turnover):
        opts = []
        if pct is not None:
            opts.append(turnover * _dec(pct) / 100)
        if cap is not None:
            opts.append(_dec(cap))
        return min(opts) if opts else Decimal(0)

    brokerage = order_brokerage(buy) + order_brokerage(sell)
    stt = sell * STT_SELL_PCT / 100
    stamp = buy * STAMP_BUY_PCT / 100
    txn = both * EXCHANGE_TXN_PCT / 100
    sebi = both * SEBI_PCT / 100
    gst = (brokerage + txn + sebi) * GST_PCT / 100
    parts = {"brokerage": _q2(brokerage), "stt": _q2(stt), "stamp_duty": _q2(stamp),
             "exchange_txn": _q2(txn), "sebi_fee": _q2(sebi), "gst": _q2(gst)}
    out = {"exit_price": float(_dec(exit_price))}
    out.update({k: float(v) for k, v in parts.items()})
    out["total"] = float(sum(parts.values()))
    return out


# ---------------------------------------------------------------- plan mode (the default)

def size_trade(plan, cfg, budget=None, limit=None, price=None, cash=None, today=None):
    """Pure function: one plan + your rules -> the sizing (a dict). qty may be 0.

    limit  the limit price for a limit order (None = market)
    price  a FRESH latest price; a market order is sized at the higher of it and the plan's entry
    cash   the cash in trader/portfolio.json, if there is one
    today  IST date (YYYY-MM-DD); a plan for another day is refused"""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    tick = tick_of(c)               # None: NSE's tick for each price
    if not isinstance(plan, dict):
        raise SizeError("the plan is not a JSON object")
    if tick is None and plan.get("tick_override") is True:      # trader-exit was given a tick
        tick = tick_of({"tick_size": plan.get("tick_size")})
    sym = str(plan.get("symbol") or "").strip().upper()
    if not sym:
        raise SizeError("the plan has no symbol (run trader-exit)")
    side = str(plan.get("side") or "buy").strip().lower()
    if side != "buy":
        raise SizeError("this pack sizes long (buy) day trades only; the plan says %r" % side)
    if today and plan.get("date") and str(plan["date"]) != today:
        raise SizeError("the plan is for %s and today is %s in IST: make a new plan with trader-exit"
                        % (plan["date"], today))
    entry_plan, stop0, target0 = _f(plan.get("entry")), _f(plan.get("stop")), _f(plan.get("target"))
    for name, v in (("entry", entry_plan), ("stop", stop0), ("target", target0)):
        if v is None or v <= 0:
            raise SizeError("the plan needs a positive %s (run trader-exit); a bracket order needs entry, "
                            "stop and target" % name)
    blocked = {str(b).upper() for b in (c.get("blocked") or [])}
    if sym in blocked:
        raise SizeError("%s is on your blocked list" % sym)

    budget = float(budget if budget is not None else (c.get("budget") or 0))
    if budget <= 0:
        raise SizeError("no budget: pass --budget or set \"budget\" in trader/config.json")
    buf_pct = float(c.get("cash_buffer_pct") or 0)
    if not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")

    def tk(x):
        return tick if tick is not None else tick_for(x)

    tick_notes = []
    entry_plan = to_paise(entry_plan) / 100.0
    if limit is not None:
        ref, ref_from = round_tick(limit, tick), "limit"
        if _dec(ref) != _dec(limit):
            tick_notes.append("limit price %s is not on the %s tick: rounded to %s"
                              % (_raw(limit), inr(tk(limit), 2), _price(ref)))
    elif price is not None and to_paise(price) > to_paise(entry_plan):
        ref, ref_from = to_paise(price) / 100.0, "price"
    else:
        ref, ref_from = entry_plan, "plan"
    stop, target = floor_tick(stop0, tick), round_tick(target0, tick)
    if _dec(stop) != _dec(stop0):
        tick_notes.append("stop %s is not on the %s tick: rounded DOWN to %s (away from the entry)"
                          % (_raw(stop0), inr(tk(stop0), 2), _price(stop)))
    if _dec(target) != _dec(target0):
        tick_notes.append("target %s is not on the %s tick: rounded to %s"
                          % (_raw(target0), inr(tk(target0), 2), _price(target)))
    if stop >= ref:
        raise SizeError("the stop %s is not below the entry %s; a long trade needs its stop underneath"
                        % (_price(stop), _price(ref)))
    if target <= ref:
        raise SizeError("the target %s is not above the entry %s: the move has already happened. "
                        "Skip it or make a new plan with trader-exit" % (_price(target), _price(ref)))
    risk_c = to_paise(ref) - to_paise(stop)
    reward_c = to_paise(target) - to_paise(ref)
    risk = risk_c / 100.0

    cash_buffer = round(budget * buf_pct / 100.0, 2)
    available = budget - cash_buffer
    cash_note = "budget %s minus the %s cash buffer" % (inr(budget), inr(cash_buffer))
    if cash is not None and cash - cash_buffer < available - 0.005:
        available = cash - cash_buffer
        cash_note = "cash %s in trader/portfolio.json minus the %s buffer" % (inr(cash), inr(cash_buffer))
    available = max(round(available, 2), 0.0)

    limits = []
    max_loss = _f(c.get("max_loss_per_trade_inr"))
    if max_loss:
        limits.append({"name": "max loss", "inr": max_loss, "per": risk, "shares": shares_for(max_loss, risk),
                       "how": "%s max loss / %s risk a share" % (inr(max_loss), _price(risk))})
    cap_pct = _f(c.get("max_position_pct"))
    if cap_pct:
        cap = round(budget * cap_pct / 100.0, 2)
        limits.append({"name": "position cap", "inr": cap, "per": ref, "shares": shares_for(cap, ref),
                       "how": "%s%% of %s = %s / %s" % (_num(cap_pct), inr(budget), inr(cap), _price(ref))})
    max_order = _f(c.get("max_order_inr"))
    if max_order:
        limits.append({"name": "max order", "inr": max_order, "per": ref, "shares": shares_for(max_order, ref),
                       "how": "%s / %s" % (inr(max_order), _price(ref))})
    limits.append({"name": "cash", "inr": available, "per": ref, "shares": shares_for(available, ref),
                   "how": "%s = %s / %s" % (cash_note, inr(available), _price(ref))})

    qty = max(0, min(l["shares"] for l in limits))
    bound = [l["name"] for l in limits if l["shares"] == qty]
    for l in limits:
        l["binds"] = l["name"] == bound[0]
    cost = round(qty * to_paise(ref) / 100.0, 2)
    loss = round(qty * risk_c / 100.0, 2)
    refusal = None
    if qty == 0:
        if bound[0] == "max loss":
            refusal = ("one share risks %s (entry %s minus stop %s), more than your %s max loss per trade. "
                       "No order. Skip this trade, or plan a closer stop with trader-exit."
                       % (inr(risk), _price(ref), _price(stop), inr(max_loss)))
        else:
            refusal = ("one share of %s costs %s, more than your %s limit (%s). No order."
                       % (sym, inr(ref), bound[0], limits[[l["name"] for l in limits].index(bound[0])]["how"]))
    elif _f(c.get("min_reward_risk")) and reward_c < _f(c["min_reward_risk"]) * risk_c - EPS:
        refusal = ("reward:risk %.2f (target %s, entry %s, stop %s) is under your minimum %s, so trader-risk "
                   "would block it. No order. Skip it, or make a new plan with trader-exit."
                   % (reward_c / float(risk_c), _price(target), _price(ref), _price(stop), _num(c["min_reward_risk"])))
    elif cost < float(c.get("min_order_inr") or 0) - 0.005:
        refusal = ("%d share%s of %s is %s, under your %s minimum order. No order."
                   % (qty, "" if qty == 1 else "s", sym, inr(cost), inr(c["min_order_inr"])))
    charges = None
    if qty > 0:
        charges = {"at_entry": charges_for(qty, ref, ref, c),
                   "at_stop": charges_for(qty, ref, stop, c),
                   "at_target": charges_for(qty, ref, target, c),
                   "note": ("informational; they do not change the share count; "
                            "verify with your broker's charge calculator")}
    return {
        "symbol": sym, "exchange": "NSE", "product": "MIS", "side": "buy", "setup": plan.get("setup", ""),
        "date": plan.get("date"), "entry_time": plan.get("entry_time", ""), "entry_plan": entry_plan,
        "entry": ref, "entry_from": ref_from, "stop": stop, "target": target, "tick_size": tk(ref),
        "risk_per_share": risk, "reward_risk": round(reward_c / float(risk_c), 2),
        "stop_label": plan.get("stop_label", ""), "target_label": plan.get("target_label", ""),
        "budget": budget, "cash_buffer_pct": buf_pct, "cash_buffer": cash_buffer, "cash_available": available,
        "max_loss_per_trade_inr": max_loss, "limits": limits, "qty": qty, "bound_by": bound[0],
        "cost": cost, "max_loss": loss, "flat_by": plan.get("flat_by") or c.get("flat_by"),
        "order_type": "limit" if limit is not None else "market", "refusal": refusal,
        "tick_notes": tick_notes, "charges": charges,
    }


def bracket_order(s):
    """The one proposed order in trader-budget's order shape (strings for numbers), plus why and plan.

    order_class "bracket" = entry + stop-loss leg + target leg. The legs are placed after the
    entry fills (trader-broker), each as its own ticket."""
    if s["refusal"] or s["qty"] <= 0:
        return None
    o = {"symbol": s["symbol"], "exchange": "NSE", "side": "buy", "qty": str(int(s["qty"])),
         "type": s["order_type"], "product": "MIS", "time_in_force": "day"}
    if s["order_type"] == "limit":
        o["limit_price"] = _price(s["entry"])
    setup = SETUP_WORDS.get(s["setup"], s["setup"] or "day trade")
    why = ("%s on %s: %d shares, bound by your %s (%d x %s = %s at the stop)"
           % (setup, s["symbol"], s["qty"], {"max loss": "%s max loss per trade" % inr(s["max_loss_per_trade_inr"] or 0),
                                             "position cap": "position cap", "max order": "max order",
                                             "cash": "cash after the buffer"}[s["bound_by"]],
              s["qty"], _price(s["risk_per_share"]), inr(s["max_loss"])))
    o.update({
        "order_class": "bracket",
        "take_profit_limit_price": _price(s["target"]),
        "stop_loss_stop_price": _price(s["stop"]),
        "why": why,
        "plan": {"entry": s["entry"], "stop": s["stop"], "target": s["target"],
                 "risk_per_share": s["risk_per_share"], "max_loss": s["max_loss"], "setup": s["setup"],
                 "entry_time": s["entry_time"], "flat_by": s["flat_by"]},
    })
    return o


def plan_report(s, order, notes=()):
    lines = []
    setup = SETUP_WORDS.get(s["setup"], s["setup"] or "setup")
    lines.append("%s buy, %s, entry %s%s" % (s["symbol"], setup, _price(s["entry_plan"]),
                                            (" at " + s["entry_time"]) if s["entry_time"] else ""))
    lines.append("Stop %s%s. Target %s%s." % (_price(s["stop"]), (": " + s["stop_label"]) if s["stop_label"] else "",
                                             _price(s["target"]),
                                             (": " + s["target_label"]) if s["target_label"] else ""))
    for n in notes:
        lines.append(n)
    src = {"plan": "the plan's entry", "price": "the latest price, above the plan's entry",
           "limit": "your limit price"}[s["entry_from"]]
    lines.append("Sized at %s (%s). Risk %s a share (entry %s minus stop %s). Reward:risk %.2f."
                 % (_price(s["entry"]), src, _price(s["risk_per_share"]), _price(s["entry"]), _price(s["stop"]),
                    s["reward_risk"]))
    lines.append("")
    lines.append("Limits (whole shares; the smallest one wins):")
    for l in s["limits"]:
        lines.append("  %-13s %-58s %3d shares%s" % (l["name"], l["how"], l["shares"],
                                                      "  <- binds" if l["binds"] else ""))
    lines.append("")
    if order is None:
        lines.append("No order: " + s["refusal"])
        lines.append("Education and tooling, not financial advice.")
        return "\n".join(lines)
    lines.append("Size: %d shares of %s = %s at %s. Max loss at the stop: %d x %s = %s."
                 % (s["qty"], s["symbol"], inr(s["cost"]), _price(s["entry"]), s["qty"],
                    _price(s["risk_per_share"]), inr(s["max_loss"])))
    ch = s["charges"]
    if ch:
        lines.append("Estimated charges at the stop %s, at the target %s: verify with your broker's calculator."
                     % (inr(ch["at_stop"]["total"], 2), inr(ch["at_target"]["total"], 2)))
        lines.append("  (Closed flat at the entry: %s. Brokerage, STT, stamp duty, exchange and SEBI fees, GST. "
                     "They are NOT in the max loss above and never change the share count: at the stop, "
                     "loss plus charges is about %s.)"
                     % (inr(ch["at_entry"]["total"], 2), inr(s["max_loss"] + ch["at_stop"]["total"], 2)))
    lines.append("A stop is an order, not a promise: in a fast market it can fill below %s, and a stock at its "
                 "lower circuit may not let you sell at all." % _price(s["stop"]))
    lines.append("")
    lines.append("Proposed bracket plan (nothing has been placed):")
    how = "limit %s" % order["limit_price"] if order["type"] == "limit" else "market"
    lines.append("  1. ENTRY: buy %s %s on NSE, %s, MIS (intraday), day" % (order["qty"], order["symbol"], how))
    lines.append("  2. STOP-LOSS: sell %s at stop %s  (placed after the entry fills)"
                 % (order["qty"], order["stop_loss_stop_price"]))
    lines.append("  3. TARGET: sell %s at limit %s  (placed after the entry fills)"
                 % (order["qty"], order["take_profit_limit_price"]))
    lines.append("  There is no automatic one-cancels-the-other: when one leg fills, record that sell and skip "
                 "the other leg.")
    if s["flat_by"]:
        lines.append("Flat by %s IST: anything still open then gets closed (trader-alerts rings, "
                     "trader-broker flatten). Real brokers also auto square-off MIS positions, from about 15:05 "
                     "(F&O stocks) to 15:20, and charge a fee: keep flat_by before that." % s["flat_by"])
    lines.append("")
    lines.append("Next: trader-risk checks it, then trader-broker asks you yes or no.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


# ---------------------------------------------------------------- weights mode (v1)

def normalise(rows, blocked=(), top=None):
    """Drop blocked and non-positive weights, keep the top N, renormalise to 100."""
    blocked = {str(b).upper() for b in (blocked or [])}
    dropped, keep, seen = [], [], set()
    for r in rows:
        if r["ticker"] in seen:
            raise SizeError("%s is listed twice in the weights" % r["ticker"])
        seen.add(r["ticker"])
        if r["ticker"] in blocked:
            dropped.append({"ticker": r["ticker"], "reason": "on your blocked list"})
        elif r["weight"] <= 0:
            dropped.append({"ticker": r["ticker"], "reason": "weight is zero or negative"})
        else:
            keep.append(dict(r))
    keep.sort(key=lambda r: -r["weight"])
    if top:
        for r in keep[top:]:
            dropped.append({"ticker": r["ticker"], "reason": "outside the top %d" % top})
        keep = keep[:top]
    total = sum(r["weight"] for r in keep)
    for r in keep:
        r["source_weight"] = r["weight"]
        r["weight"] = 100.0 * r["weight"] / total if total else 0.0
    return keep, dropped


def allocate(rows, investable, cap):
    """Exact (unrounded) rupees per ticker with the cap enforced.

    Proportional to weight; any stock over the cap is pinned at the cap and
    the rest of the money is shared over the others, repeated until no stock
    is over. Returns ({ticker: rupees}, set of capped tickers)."""
    alloc, capped = {}, set()
    active = [r for r in rows]
    money_left = investable
    while active:
        wsum = sum(r["weight"] for r in active)
        trial = {r["ticker"]: money_left * r["weight"] / wsum for r in active}
        over = [r for r in active if trial[r["ticker"]] > cap + EPS]
        if not over:
            alloc.update(trial)
            break
        for r in over:
            alloc[r["ticker"]] = cap
            capped.add(r["ticker"])
            money_left -= cap
        active = [r for r in active if r["ticker"] not in capped]
        if money_left <= EPS:
            for r in active:
                alloc[r["ticker"]] = 0.0
            break
    return alloc, capped


def round_to_unit(exact, capped, order, cap, unit):
    """Round to a unit (0.01 = paise) and keep the total.

    The total placed is the exact total rounded DOWN to the unit, so it never
    spends money you do not have. Each slice is rounded to the nearest unit,
    then the difference (usually one unit) goes to the biggest stock that is
    not capped, then the next, never pushing anyone over the cap."""
    def units(x):
        return int(math.floor(x / unit + EPS))

    cap_u = units(cap)
    total_u = units(sum(exact.values()))
    out = {}
    for t, v in exact.items():
        out[t] = cap_u if t in capped else int(math.floor(v / unit + 0.5 + EPS))
    diff = total_u - sum(out.values())
    takers = [t for t in order if t not in capped]
    guard = 0
    while diff != 0 and takers and guard < 100000:
        moved = False
        for t in takers:
            if diff == 0:
                break
            if diff > 0 and out[t] + 1 <= cap_u:
                out[t] += 1
                diff -= 1
                moved = True
            elif diff < 0 and out[t] > 0:
                out[t] -= 1
                diff += 1
                moved = True
        if not moved:
            break
        guard += 1
    return {t: round(u * unit, 2) for t, u in out.items()}


def whole_shares(rupees, prices, order, cap, cash):
    """Whole shares: floor each slice, then buy one more share at a time for the
    stock furthest below its slice while cash lasts and the cap holds."""
    shares = {}
    for t in order:
        shares[t] = int(math.floor(rupees[t] / prices[t] + EPS)) if rupees[t] > 0 else 0
    spent = sum(shares[t] * prices[t] for t in order)
    left = cash - spent
    while True:
        best, gap = None, None
        for t in order:
            p = prices[t]
            if p > left + EPS or (shares[t] + 1) * p > cap + EPS:
                continue
            g = rupees[t] - shares[t] * p
            if best is None or g > gap + EPS:
                best, gap = t, g
        if best is None:
            break
        shares[best] += 1
        left -= prices[best]
    return shares


def size(rows, cfg, budget=None, prices=None, top=None):
    """Weights mode. Pure function: weights + rules -> the full sizing result (a dict).

    Whole shares only (India has no fractional shares), so a price is needed for every stock."""
    budget = float(budget if budget is not None else (cfg.get("budget") or 0))
    if budget <= 0:
        raise SizeError("no budget: pass --budget or set \"budget\" in trader/config.json")
    buf_pct = float(cfg.get("cash_buffer_pct", 10))
    cap_pct = float(cfg.get("max_position_pct", 25))
    min_order = float(cfg.get("min_order_inr", 100) or 0)
    if not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")
    if not 0 < cap_pct <= 100:
        raise SizeError("max_position_pct must be above 0 and at most 100")
    prices = prices or {}

    keep, dropped = normalise(rows, cfg.get("blocked"), top)
    if not keep:
        raise SizeError("nothing to size: no weights left after the blocked list")

    cash_buffer = round(budget * buf_pct / 100.0, 2)
    investable = round(budget - cash_buffer, 2)
    cap = round(budget * cap_pct / 100.0, 2)
    unit = 0.01

    missing = [r["ticker"] for r in keep if not prices.get(r["ticker"])]
    if missing:
        raise SizeError("whole shares need a price for: %s (add them to trader/prices.json, "
                        "e.g. with trader-alerts: alerts.py quote %s --write)"
                        % (", ".join(missing), " ".join(missing)))

    while True:
        live = [dict(r) for r in keep]
        tot = sum(r["source_weight"] for r in live)
        for r in live:
            r["weight"] = 100.0 * r["source_weight"] / tot
        exact, capped = allocate(live, investable, cap)
        order = [r["ticker"] for r in sorted(live, key=lambda r: -exact[r["ticker"]])]
        slices = round_to_unit(exact, capped, order, cap, unit)
        shares = whole_shares(slices, prices, order, cap, sum(slices.values()))
        rupees = {t: round(shares[t] * prices[t], 2) for t in order}
        small = [r for r in live if rupees[r["ticker"]] < min_order - EPS]
        if not small:
            break
        worst = min(small, key=lambda r: (r["source_weight"], r["ticker"]))
        if shares[worst["ticker"]] == 0 and prices.get(worst["ticker"], 0) > 0:
            reason = ("one share costs %s, more than its slice; it cannot be bought in whole shares"
                      % inr(prices[worst["ticker"]]))
        else:
            reason = "its slice (%s) is under your %s minimum order" % (
                inr(rupees[worst["ticker"]]), inr(min_order))
        dropped.append({"ticker": worst["ticker"], "reason": reason})
        keep = [r for r in keep if r["ticker"] != worst["ticker"]]
        if not keep:
            raise SizeError("every slice is under your minimum order; raise the budget or hold fewer stocks")

    positions = []
    for r in sorted(live, key=lambda r: -r["weight"]):
        t = r["ticker"]
        positions.append({"ticker": t, "name": r["name"], "weight": round(r["weight"], 2),
                          "source_weight": r["source_weight"], "rupees": rupees[t],
                          "capped": t in capped, "price": prices[t], "shares": shares[t]})
    invested = round(sum(p["rupees"] for p in positions), 2)
    return {
        "budget": budget, "cash_buffer_pct": buf_pct, "cash_buffer": cash_buffer,
        "investable": investable, "max_position_pct": cap_pct, "cap": cap,
        "min_order_inr": min_order, "whole_shares": True,
        "positions": positions, "invested": invested,
        "idle": round(investable - invested, 2), "dropped": dropped,
    }


def orders_from(result, source="", product="MIS"):
    """Weights mode: proposed buy orders, one per position, in the order shape (whole shares)."""
    out = []
    for p in result["positions"]:
        if p["shares"] <= 0:
            continue
        why = "%.2f%% of the split" % p["weight"]
        if source:
            why = "%s, weights from %s" % (why, source)
        if p["capped"]:
            why += "; capped at %s%% of the %s budget" % (_num(result["max_position_pct"]), inr(result["budget"]))
        out.append({"symbol": p["ticker"], "exchange": "NSE", "side": "buy", "qty": str(int(p["shares"])),
                    "type": "market", "product": product, "time_in_force": "day", "why": why})
    return out


def report(result, orders, source=""):
    """Weights mode table."""
    r = result
    lines = []
    if source:
        lines.append("Weights from: %s" % source)
    lines.append("Budget %s, cash buffer %s%% = %s, investing %s, cap %s%% = %s per stock"
                 % (inr(r["budget"]), _num(r["cash_buffer_pct"]), inr(r["cash_buffer"]),
                    inr(r["investable"]), _num(r["max_position_pct"]), inr(r["cap"])))
    lines.append("")
    lines.append("%-12s %-22s %8s %13s %11s %8s  note" % ("ticker", "name", "weight", "rupees", "price", "shares"))
    for p in r["positions"]:
        row = "%-12s %-22s %7.2f%% %13s %11s %8s" % (p["ticker"], (p["name"] or "")[:22], p["weight"],
                                                      inr(p["rupees"]), inr(p["price"]), p["shares"])
        if p["capped"]:
            row += "  capped at %s%%" % _num(r["max_position_pct"])
        lines.append(row)
    lines.append("%-12s %-22s %8s %13s" % ("", "invested", "", inr(r["invested"])))
    lines.append("%-12s %-22s %8s %13s" % ("", "cash buffer", "", inr(r["cash_buffer"])))
    lines.append("%-12s %-22s %8s %13s" % ("", "left idle", "", inr(r["idle"])))
    if r["idle"] >= 1:
        lines.append("")
        lines.append("Left idle %s: %s" % (inr(r["idle"]), (
            "every stock is at its cap; add stocks or raise max_position_pct"
            if all(p["capped"] for p in r["positions"]) else "whole shares do not divide evenly")))
    for d in r["dropped"]:
        lines.append("Dropped %s: %s" % (d["ticker"], d["reason"]))
    lines.append("")
    lines.append("Proposed orders (nothing has been placed):")
    for o in orders:
        lines.append("  %s %s qty \"%s\" on %s, %s, %s, %s"
                     % (o["side"], o["symbol"], o["qty"], o["exchange"], o["type"], o["product"],
                        o["time_in_force"]))
    lines.append("")
    lines.append("These buys have no stop. trader-risk blocks them while \"require_stop\" is true and applies "
                 "your day-trade limits to every buy.")
    lines.append("Next: trader-risk checks every order, then trader-broker asks you yes or no for each one.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


# ---------------------------------------------------------------- orders file

def write_orders(path, orders, cfg, source="", now_utc=None):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    doc = {
        "created_by": "trader-budget",
        "created_at": utc_iso(now_utc),
        "status": "proposed",
        "paper": True,
        "note": ("Proposals only. Nothing is recorded until trader-risk passes each order "
                 "and you say yes to it in the chat. This pack trades on paper only: "
                 "nothing is ever sent to a broker."),
        "source": source,
        "orders": orders,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- main

def run_plan(a, cfg):
    now = parse_now(a.now)
    today = to_ist(now).date().isoformat()
    plan = read_json(a.plan, "plan (run trader-exit first, or use --weights for a split)")
    limit = None
    if a.limit is not None:
        if a.limit == "plan":
            limit = _f((plan or {}).get("entry"))
        else:
            limit = _f(a.limit)
            if limit is None or limit <= 0:
                raise SizeError("--limit wants a price, like --limit 1416.30")
    sym = str((plan or {}).get("symbol") or "").upper()
    notes, price = [], None
    ppath = a.prices or ("trader/prices.json" if os.path.exists("trader/prices.json") else None)
    if ppath and limit is None and sym:
        pdoc = read_json(ppath, "prices")
        p, t, fresh = fresh_price(pdoc, sym, now)
        if p is not None and fresh:
            price = p
            if to_paise(p) > to_paise(_f(plan.get("entry")) or 0):
                notes.append("%s last %s (as of %s IST) is above the plan's entry %s, so the risk "
                             "is bigger: sized at %s." % (sym, _price(p), to_ist(t).strftime("%H:%M"),
                                                          _price(plan.get("entry")), _price(p)))
        elif p is not None:
            notes.append("%s in %s is %s: not used (older than %d minutes). Sized at the plan's entry."
                         % (sym, ppath, ("from " + to_ist(t).strftime("%Y-%m-%d %H:%M") + " IST")
                            if t else "undated", FRESH_MINUTES))
    cash = None
    if a.portfolio and os.path.exists(a.portfolio):
        pf = read_json(a.portfolio, "portfolio")
        cash = _f(pf.get("cash")) if isinstance(pf, dict) else None
    s = size_trade(plan, cfg, a.budget, limit, price, cash, today)
    notes.extend(s["tick_notes"])
    order = bracket_order(s)
    if order and a.write:
        src = "trader/plan.json (%s %s %s)" % (s["symbol"], s["setup"] or "plan", s["date"] or today)
        write_orders(a.write, [order], cfg, src, now)
    if a.json:
        print(json.dumps({"sizing": s, "orders": [order] if order else [], "notes": notes}, indent=2))
    else:
        print(plan_report(s, order, notes))
        if order and a.write:
            print("Wrote 1 proposed bracket order to %s" % a.write)
    return 0 if order else 1


def run_weights(a, cfg):
    source = ""
    if a.weights:
        rows = parse_weights(weights_from_arg(a.weights))
    else:
        data = read_json(a.targets, "weights file")
        rows = parse_weights(data)
        if isinstance(data, dict):
            source = data.get("source", "")
    prices = load_prices(a.prices)
    if not prices and os.path.exists("trader/prices.json") and not a.prices:
        prices = load_prices("trader/prices.json")
    result = size(rows, cfg, a.budget, prices, a.top)
    orders = orders_from(result, source, a.product)
    if a.write:
        write_orders(a.write, orders, cfg, source, parse_now(a.now))
    if a.json:
        print(json.dumps({"result": result, "orders": orders}, indent=2))
    else:
        print(report(result, orders, source))
        if a.write:
            print("Wrote %d proposed orders to %s" % (len(orders), a.write))
    return 0


def main(argv=None):
    _utf8()
    ap = argparse.ArgumentParser(description="Size a day trade to your budget and max loss (trader-budget).")
    ap.add_argument("--plan", default="trader/plan.json", help="the trade plan (default trader/plan.json)")
    ap.add_argument("--config", default="trader/config.json", help="rules (default trader/config.json)")
    ap.add_argument("--budget", type=float, help="override the budget in the config (rupees)")
    ap.add_argument("--limit", nargs="?", const="plan", metavar="PRICE",
                    help="a limit order instead of market, at the plan's entry or at PRICE (rounded to the tick)")
    ap.add_argument("--prices", help="prices JSON (default trader/prices.json if it exists)")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; its cash caps the size if it exists")
    ap.add_argument("--now", help="pretend it is this IST time, e.g. \"2026-10-19 10:15\"")
    ap.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH",
                    help="write the proposed order (default trader/orders.json)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the report")
    g = ap.add_argument_group("weights mode (split a budget across a list, whole shares)")
    g.add_argument("--weights", help="inline weights: RELIANCE=13,HDFCBANK=9")
    g.add_argument("--targets", help="a weights file: {\"weights\": [{\"ticker\", \"weight\"}]}")
    g.add_argument("--top", type=int, help="keep only the N biggest weights")
    g.add_argument("--whole-shares", action="store_true", help="accepted and ignored: India is always whole shares")
    g.add_argument("--product", default="MIS", choices=["MIS", "CNC", "NRML"],
                   help="product written into each order (default MIS = intraday; CNC = delivery)")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
        if a.weights or a.targets:
            return run_weights(a, cfg)
        if a.top or a.whole_shares:
            raise SizeError("--top and --whole-shares work with --weights or --targets")
        return run_plan(a, cfg)
    except SizeError as e:
        print("size.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
