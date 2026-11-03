#!/usr/bin/env python3
"""Paper-trading tickets and the decision log for trader-broker (NSE, rupees, IST). Standard library only.

This pack trades on paper only. No broker is connected, so this tool never
sends an order anywhere: every order is a simulated trade in a paper account
that starts with 1,00,000 rupees (the "budget" in trader/config.json). It does
its work around the one moment that matters, your yes:

  show      renders each proposed order as a plain-English ticket. A bracket
            plan shows the ENTRY, then the stop-loss leg and the target leg as
            follow-up tickets, and the max loss at the stop. There is no
            automatic OCO: when one leg fills, record that sell and skip the
            other. An order is READY only if trader-risk passed THIS exact
            order today (matched by fingerprint) and nobody has decided on it yet.
  approve   records your explicit yes for ONE order, with your reply word for
            word. It refuses "yes to all", "sure", "ok", a yes that names a
            different stock or amount, and any order that is not READY.
  decline   records a no.
  result    records the paper trade: placed (with its simulated fill) or failed.
  fill      records a SIMULATED fill in trader/portfolio.json (cash and shares).
  exits     after the entry filled: writes the stop-loss and target exit
            orders of a bracket plan to trader/orders.json (each needs its own
            trader-risk check and its own yes).
  flatten   builds a sell ticket that closes a symbol's open position by
            flat_by. It still goes through trader-risk and your yes.
  log       prints today's decisions.

    python3 ticket.py show                    # every order, READY or not
    python3 ticket.py show --index 1 --json   # one ticket as JSON
    python3 ticket.py approve --index 1 --reply "yes"
    python3 ticket.py result --index 1 --status placed --filled-qty 52 --filled-price 1416.30
    python3 ticket.py exits --index 1
    python3 ticket.py fill --symbol RELIANCE --side buy --qty 52 --price 1416.30
    python3 ticket.py flatten --symbol RELIANCE --qty 52
    python3 ticket.py log

"paper": true (the default in trader/config.json) is the only mode: "paper":
false is refused. The paper account starts with the config's "budget", or
1,00,000 rupees when there is none. Fills are simulated at the price you give:
a real fill can be worse, and no charges are taken from the paper cash.

Everything is written to trader/state/broker.json (append only; the file keeps
its old name). Dates are IST dates; --now "2026-10-19 10:15" pins the clock (IST).
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import textwrap

ORDER_FIELDS = ("symbol", "exchange", "side", "qty", "type", "product", "time_in_force", "limit_price",
                "stop_price", "client_order_id", "order_class", "take_profit_limit_price",
                "stop_loss_stop_price", "stop_loss_limit_price")
STRING_FIELDS = ("qty", "limit_price", "stop_price", "take_profit_limit_price", "stop_loss_stop_price",
                 "stop_loss_limit_price")
PRICE_FIELDS = ("qty", "limit_price", "stop_price", "take_profit_limit_price", "stop_loss_stop_price",
                "stop_loss_limit_price")
TYPES = {"market", "limit", "stop", "stop_limit"}
TIFS = {"day", "ioc"}
PRODUCTS = {"MIS", "CNC", "NRML"}
EXCHANGES = {"NSE", "BSE"}
TIF_WORDS = {"day": "good for today", "ioc": "immediate or cancel"}
YES_WORDS = ("yes", "y")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if"}
SYMBOL_RE = r"[A-Z0-9][A-Z0-9&\-]{0,19}"

DEFAULT_PAPER_BUDGET = 100000    # rupees in a new paper account when the config has no "budget"

NO_OCO = ("There is no automatic OCO here: when one of the two exit legs fills, record that sell "
          "(ticket.py fill) and skip the other leg.")


class TicketError(Exception):
    pass


def paper_only(cfg):
    """This pack trades on paper only: no broker is connected, so "paper": false is refused."""
    if not cfg.get("paper", True):
        raise TicketError("this pack trades on paper only (no broker is connected): "
                          "set \"paper\": true in trader/config.json")


def _utf8_stdio():
    """So the rupee sign never raises UnicodeEncodeError on a Windows console."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------- time (IST, UTC+5:30, no daylight saving)

IST_DELTA = datetime.timedelta(hours=5, minutes=30)


def to_ist(utc):
    return utc.replace(tzinfo=None) + IST_DELTA


def ist_to_utc(local):
    return local - IST_DELTA


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise TicketError("--now wants a time like \"2026-10-19 10:15\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def ist_iso(now_utc):
    """naive UTC -> "2026-10-19T10:15:00+05:30" (IST wall clock with its offset)."""
    return "%s+05:30" % to_ist(now_utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def rec_day(rec):
    """The trading day a decision belongs to: its "date" (the day it was made under), else the day of "at"."""
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


# ---------------------------------------------------------------- the order

def fingerprint(order):
    """Same function as trader-risk/check.py: identifies the exact order."""
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _str_num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f == int(f) else repr(f)
    return str(v)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _group_indian(digits):
    """"1234567" -> "12,34,567" (last three digits, then pairs)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    if head:
        pairs.insert(0, head)
    return ",".join(pairs + [tail])


def inr(v, dec=None):
    """Rupees with Indian digit grouping: inr(123456.78) -> "₹1,23,456.78". dec=None drops ".00"
    from a whole amount ("₹5,000"); dec=2 always shows paise."""
    f = float(v)
    neg = f < 0
    s = "%.2f" % abs(f)
    whole, frac = s.split(".")
    if dec is None:
        frac = "" if frac == "00" else "." + frac
    elif dec == 0:
        frac = ""
    else:
        frac = "." + frac[:dec].ljust(dec, "0")
    return ("-" if neg else "") + "₹" + _group_indian(whole) + frac


def _p(v):
    return "%.2f" % float(v)


def _venue(order):
    return "%s, %s" % (str(order.get("exchange") or "NSE").upper(), str(order.get("product") or "MIS").upper())


def is_bracket(order):
    oc = str(order.get("order_class") or "").lower()
    legs = order.get("take_profit_limit_price") is not None or order.get("stop_loss_stop_price") is not None
    return oc == "bracket" or (not oc and legs)


def exit_legs(order, qty=None):
    """The two exit orders of a bracket plan: [stop-loss leg, target leg] (a leg is left out when the
    plan does not give its price). Recorded only AFTER the entry fills, each as its own ticket."""
    if not is_bracket(order):
        return []
    side = "sell" if str(order.get("side", "")).lower() == "buy" else "buy"
    base = {"symbol": str(order.get("symbol", "")).upper(), "exchange": str(order.get("exchange") or "NSE").upper(),
            "side": side, "qty": _str_num(qty if qty is not None else order.get("qty")),
            "product": str(order.get("product") or "MIS").upper(), "time_in_force": "day"}
    base["exit_group"] = "%s bracket exit" % base["symbol"]      # trader-risk: only one of the two legs fills
    legs = []
    if order.get("stop_loss_stop_price") is not None:
        leg = dict(base, stop_price=_str_num(order["stop_loss_stop_price"]), type="stop",
                   why="stop-loss leg of the %s bracket plan (only after the entry filled)" % base["symbol"])
        if order.get("stop_loss_limit_price") is not None:
            leg["type"] = "stop_limit"
            leg["limit_price"] = _str_num(order["stop_loss_limit_price"])
        legs.append(leg)
    if order.get("take_profit_limit_price") is not None:
        legs.append(dict(base, type="limit", limit_price=_str_num(order["take_profit_limit_price"]),
                         why="target leg of the %s bracket plan (only after the entry filled)" % base["symbol"]))
    return legs


def max_loss(order):
    """(rupees, stop) lost if the stop fills exactly at the stop, or (None, stop)."""
    stop = _f(order.get("stop_loss_stop_price")) or _f((order.get("plan") or {}).get("stop"))
    q = _f(order.get("qty"))
    if not stop or not q:
        return None, stop
    plan = order.get("plan") or {}
    if str(order.get("type", "market")) == "limit" and _f(order.get("limit_price")):
        entry = _f(order["limit_price"])
    elif _f(plan.get("max_loss")) is not None:
        return round(_f(plan["max_loss"]), 2), stop
    else:
        entry = _f(plan.get("entry"))
    if not entry:
        return None, stop
    risk = abs(entry - stop)
    return round(q * int(round(risk * 100)) / 100.0, 2), stop


def plain_parts(order):
    """The ticket in plain words, one part per line."""
    side = str(order.get("side", "")).upper()
    sym = order.get("symbol", "?")
    typ = str(order.get("type", "market"))
    tif = TIF_WORDS.get(str(order.get("time_in_force", "day")), order.get("time_in_force"))
    if is_bracket(order):
        how = "limit %s" % _p(order["limit_price"]) if typ == "limit" and order.get("limit_price") else typ
        parts = ["%s %s %s (%s), %s, %s" % (side, _str_num(order.get("qty")), sym, _venue(order), how, tif)]
        if order.get("stop_loss_stop_price") is not None:
            leg = "stop-loss %s" % _p(order["stop_loss_stop_price"])
            if order.get("stop_loss_limit_price") is not None:
                leg += " (limit %s)" % _p(order["stop_loss_limit_price"])
            parts.append(leg)
        if order.get("take_profit_limit_price") is not None:
            parts.append("target %s" % _p(order["take_profit_limit_price"]))
        loss, stop = max_loss(order)
        if loss is not None:
            parts.append("max loss about %s if the stop-loss fills at %s (it can fill worse in a fast market)"
                         % (inr(loss), _p(stop)))
        return parts
    if order.get("notional") is not None:
        return ["%s %s of %s (%s) as a notional order: India trades whole shares only, trader-risk blocks this"
                % (side, inr(order["notional"]), sym, _venue(order))]
    q = _str_num(order.get("qty"))
    what = "%s share%s of %s (%s)" % (q, "" if q == "1" else "s", sym, _venue(order))
    if typ == "limit":
        how = "limit order at %s" % inr(order.get("limit_price"))
    elif typ == "stop":
        how = "stop order at %s" % inr(order.get("stop_price"))
    elif typ == "stop_limit":
        how = "stop %s, limit %s" % (inr(order.get("stop_price")), inr(order.get("limit_price")))
    else:
        how = "%s order" % typ.replace("_", " ")
    return ["%s %s, %s, %s" % (side, what, how, tif)]


def plain(order):
    """BUY 52 RELIANCE (NSE, MIS), market, good for today / stop-loss 1406.40 / target 1426.20 / max loss about ₹514.80 ..."""
    return " / ".join(plain_parts(order))


def amount_numbers(order):
    nums = []
    for k in ("notional", "qty", "limit_price", "stop_price", "stop_loss_stop_price", "take_profit_limit_price",
              "stop_loss_limit_price"):
        if order.get(k) is not None:
            v = _f(order[k])
            if v is not None:
                nums.append(v)
    loss, _ = max_loss(order)
    if loss is not None:
        nums.append(loss)
    return nums


def parse_yes(reply, order, batch_symbols=()):
    """Is this reply an explicit yes for THIS one order? Returns (ok, why)."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, go, do it)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    sym = str(order.get("symbol", "")).upper()
    tokens = set(re.findall(SYMBOL_RE, raw.upper()))
    others = {s.upper() for s in batch_symbols} - {sym}
    named = tokens & others
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    # the order's own symbol may contain digits (20MICRONS): take it out before reading amounts
    numtext = re.sub(re.escape(sym), " ", raw, flags=re.I) if sym else raw
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", numtext)]
    if nums:
        want = amount_numbers(order)
        if not any(abs(n - w) < 0.005 for n in nums for w in want):
            return False, "the reply says %s, but this order is %s" % (
                ", ".join(_str_num(n) for n in nums), plain(order))
    return True, "explicit yes for %s" % plain(order)


# ---------------------------------------------------------------- state

def load_state(path):
    return read_json(path, "broker state", required=False) or {"orders": []}


def latest(state, fp, today):
    """The latest decision recorded today for this fingerprint, or None."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "trader-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    """(ready, label, risk_result)"""
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by trader-risk: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            return False, ("APPROVED by you at %s, waiting for the simulated fill to be recorded"
                           % rec.get("at")), rv
        return False, "DONE: %s at %s" % (st, rec.get("at")), rv
    return True, "READY: trader-risk PASS, waiting for your yes", rv


def kind_of(order, rv):
    """entry or exit, as trader-risk saw it (older checks: a buy is an entry)."""
    if rv and rv.get("kind"):
        return rv["kind"]
    return "entry" if str(order.get("side", "")).lower() == "buy" else "exit"


# ---------------------------------------------------------------- rendering

def render(order, idx, total, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    lines = ["ORDER %d of %d, PAPER TRADE (simulated: no broker, no real money)" % (idx, total)]
    for part in plain_parts(order):
        lines.append("  " + part)
    if order.get("why"):
        lines.append("  why: " + order["why"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    lines.append("  Paper trade: nothing is sent anywhere. After your yes it is recorded and the fill is")
    lines.append("  simulated at the price you give (ticket.py result / fill).")
    if is_bracket(order):
        lines.append("  This is a bracket PLAN in three parts, not one order. Only the entry above is")
        lines.append("  asked for now. After the entry fills, the exit legs are recorded as two SEPARATE follow-up")
        lines.append("  tickets (ticket.py exits), each with its own trader-risk check and its own yes:")
        for n, leg in enumerate(exit_legs(order), 1):
            kind = "stop-loss leg" if "stop_price" in leg else "target leg"
            lines.append("    %d. %s: %s" % (n, kind, plain(leg)))
        lines.extend(textwrap.wrap(NO_OCO, width=92, initial_indent="  ", subsequent_indent="  "))
    if ready:
        lines.append("  Reply \"yes\" to record this one paper trade, or \"no\" to skip it.")
    return "\n".join(lines), ready


def ticket_json(order, idx, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    loss, stop = max_loss(order)
    return {"index": idx, "ready": ready, "status": label, "plain": plain(order),
            "fingerprint": fingerprint(order), "paper": True, "why": order.get("why", ""),
            "bracket": is_bracket(order),
            "exit_legs": [{"plain": plain(l)} for l in exit_legs(order)],
            "max_loss": loss, "stop": stop}


# ---------------------------------------------------------------- fills -> portfolio

def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date):
    """Record a filled order in trader/portfolio.json: cash and shares, with a
    cost-basis lot per buy (oldest lots are relieved first on a sell). Cost basis
    only: no tax-lot or tax claim. A new paper account starts with the config's
    budget, or DEFAULT_PAPER_BUDGET (1,00,000 rupees)."""
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        pf = {"cash": float(cfg.get("budget") or DEFAULT_PAPER_BUDGET), "positions": []}
    symbol = symbol.upper()
    qty, price = float(qty), float(price)
    if qty <= 0 or price <= 0:
        raise TicketError("a fill needs a positive qty and price")
    cost = round(qty * price, 2)
    pos = None
    for p in pf.setdefault("positions", []):
        if str(p.get("ticker", "")).upper() == symbol:
            pos = p
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "shares": 0.0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": qty, "cost": cost})
        pos["shares"] = round(float(pos.get("shares", 0)) + qty, 6)
        pf["cash"] = round(float(pf.get("cash", 0)) - cost, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("cannot record selling %s %s: the portfolio holds %s"
                              % (_str_num(qty), symbol, pos.get("shares", 0) if pos else 0))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 1e-9:
                break
            take = min(left, float(lot["shares"]))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = round(float(lot["shares"]) - take, 6)
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 1e-9]
        pos["shares"] = round(float(pos["shares"]) - qty, 6)
        if pos["shares"] <= 1e-9:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + cost, 2)
    write_json(portfolio_path, pf)
    return pf


def log_fill(state_path, now, today, symbol, side, qty, price):
    """A fill in the log (a buy or a sell recorded in the portfolio)."""
    state = load_state(state_path)
    state.setdefault("orders", []).append({
        "at": ist_iso(now), "date": today, "status": "filled", "symbol": symbol.upper(), "side": side,
        "qty": _str_num(float(qty)), "price": float(price),
        "plain": "%s %s %s at %s" % (side.upper(), _str_num(float(qty)), symbol.upper(), _p(price))})
    write_json(state_path, state)


# ---------------------------------------------------------------- commands

def _clock(a):
    now = parse_now(getattr(a, "now", None))
    today = getattr(a, "today", None) or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", today):
        raise TicketError("--today wants a date like 2026-10-19")
    return now, today


def _load(a):
    doc = read_json(a.orders, "orders file (run trader-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    paper_only(cfg)
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    now, today = _clock(a)
    return orders, cfg, risk, state, today, now


def _pick(orders, index):
    if index is None:
        raise TicketError("say which order: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("there is no order %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, risk, state, today) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), risk, state, today)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nOne order at a time: a yes covers one ticket, never the batch. Education and tooling, "
          "not financial advice.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    ready, label, rv = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("symbol", "") for o in orders])
    if not ok:
        print("Not recorded: %s. Ask the user again with the ticket for order %d." % (why, a.index))
        return 1
    loss, stop = max_loss(order)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("symbol", "")).upper(), "side": order.get("side"),
           "qty": order.get("qty"), "kind": kind_of(order, rv), "max_loss": loss, "stop": stop,
           "plain": plain(order), "status": "approved", "reply": a.reply, "paper": True}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded your yes for order %d: %s." % (a.index, plain(order)))
    sym, side = rec["symbol"], str(order.get("side", "buy")).lower()
    print("PAPER TRADE: nothing is sent to a broker. Record the simulated fill (use the limit price, or the")
    print("current price you see, as the price):")
    print("  ticket.py result --index %d --status placed --filled-qty %s --filled-price P "
          "(logs it as placed so the daily limits count it, and adds the fill)" % (a.index, _str_num(order.get("qty"))))
    print("  or only the portfolio: ticket.py fill --symbol %s --side %s --qty %s --price P"
          % (sym, side, _str_num(order.get("qty"))))
    if is_bracket(order):
        print("After the entry fills: ticket.py exits --index %d writes the stop-loss and target exit "
              "tickets (each needs its own risk check and its own yes)." % a.index)
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("symbol", "")).upper(), "side": order.get("side"),
           "plain": plain(order), "status": "declined", "reply": a.reply or "no"}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded: you said no to order %d (%s). Nothing was recorded as a trade." % (a.index, plain(order)))
    return 0


def cmd_result(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise TicketError("order %d has no yes recorded today; a result can only follow an approval" % a.index)
    new = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": rec["side"], "plain": rec["plain"], "status": a.status, "paper": True}
    for k in ("qty", "kind", "max_loss", "stop"):
        if rec.get(k) is not None:
            new[k] = rec[k]
    if a.note:
        new["note"] = a.note
    elif a.status == "placed":
        new["note"] = "paper trade: simulated, nothing was sent to a broker"
    state["orders"].append(new)
    write_json(a.state, state)
    print("Recorded order %d as %s%s." % (a.index, a.status, " (paper trade)" if a.status == "placed" else ""))
    if a.status == "placed" and a.filled_qty and a.filled_price:
        apply_fill(a.portfolio, cfg, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, today)
        log_fill(a.state, now, today, rec["symbol"], rec["side"], a.filled_qty, a.filled_price)
        print("Added the fill to %s." % a.portfolio)
        if rec.get("kind") == "entry" and is_bracket(order):
            print("Next: ticket.py exits --index %d (stop-loss leg and target leg, each its own ticket)." % a.index)
    elif a.status == "placed":
        print("When it fills, record it: ticket.py fill --symbol %s --side %s --qty Q --price P"
              % (rec["symbol"], rec["side"]))
        if rec.get("kind") == "entry" and is_bracket(order):
            print("Then: ticket.py exits --index %d. When the stop-loss or the target is hit later, record that "
                  "sell the same way, and skip the other leg." % a.index)
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    paper_only(cfg)
    now = parse_now(a.now)
    date = a.date or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise TicketError("--date wants a date like 2026-10-19")
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date)
    log_fill(a.state, now, date, a.symbol, a.side, a.qty, a.price)
    print("Recorded %s %s %s at %s on %s in %s. Cash now %s." % (a.side, a.qty, a.symbol.upper(),
                                                                inr(a.price), date, a.portfolio,
                                                                inr(pf["cash"])))
    return 0


def _write_tickets(a, now, orders, note, source):
    old = read_json(a.orders, "orders", required=False)
    replaced = len(old.get("orders", [])) if isinstance(old, dict) else 0
    doc = {"created_by": "trader-broker", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "proposed",
           "paper": True, "note": note, "source": source, "orders": orders}
    write_json(a.orders, doc)
    return replaced


def _replaced_text(replaced):
    return (" (it replaced %d earlier proposal%s)" % (replaced, "" if replaced == 1 else "s")) if replaced else ""


def cmd_exits(a):
    """Write the stop-loss and target exit tickets of a bracket plan, once its entry is placed."""
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    if not is_bracket(order):
        raise TicketError("order %d is not a bracket plan: there are no exit legs to write" % a.index)
    rec = latest(state, fingerprint(order), today)
    if not rec or rec.get("status") != "placed":
        raise TicketError("order %d is not recorded as placed today: record the paper trade first "
                          "(ticket.py result --status placed)" % a.index)
    qty = a.qty if a.qty is not None else order.get("qty")
    q = _f(qty)
    if q is None or q <= 0 or q != int(q):
        raise TicketError("--qty wants the whole shares the entry filled, like --qty 52")
    legs = exit_legs(order, qty=int(q))
    if not legs:
        raise TicketError("order %d has no stop-loss or target price" % a.index)
    for leg in legs:
        leg["why"] += " (entry: %s)" % plain_parts(order)[0]
    replaced = _write_tickets(a, now, legs,
                              "Exit legs of a bracket plan. Recorded only after the entry filled; each needs "
                              "trader-risk and its own yes. No automatic OCO: when one leg fills, skip the other.",
                              "ticket.py exits --index %d on %s" % (a.index, today))
    print("Exit legs for %s (%s shares):" % (order.get("symbol"), int(q)))
    for n, leg in enumerate(legs, 1):
        print("  %d. %s" % (n, plain(leg)))
    print("Wrote them to %s%s." % (a.orders, _replaced_text(replaced)))
    print("Next:")
    print("  1. Record the entry's fill first (result --filled-qty/--filled-price, or ticket.py fill) so")
    print("     trader-risk sees that you hold the shares, then run trader-risk: check.py.")
    print("  2. ticket.py show, then the user's yes for each leg, one at a time.")
    print("  3. " + NO_OCO)
    print("     Watch the stop and the target with trader-alerts: a paper stop does not trigger by itself.")
    return 0


def cmd_flatten(a):
    cfg = read_json(a.config, "config", required=False) or {}
    paper_only(cfg)
    sym = a.symbol.strip().upper()
    now, today = _clock(a)
    source = "your paper portfolio"
    if a.qty is not None:
        qty = _f(a.qty)
        if qty is None or qty <= 0:
            raise TicketError("--qty wants the number of shares you hold, like --qty 52")
    else:
        pf = read_json(a.portfolio, "portfolio", required=False) or {}
        qty = sum(_f(p.get("shares", p.get("qty"))) or 0 for p in pf.get("positions", [])
                  if str(p.get("ticker") or p.get("symbol") or "").upper() == sym)
        source = a.portfolio
        if qty <= 1e-9:
            raise TicketError("nothing to flatten: no %s shares in %s. Pass --qty if the paper account "
                              "does hold some" % (sym, a.portfolio))
    if qty != int(qty):
        raise TicketError("India trades whole shares only: --qty %s is not a whole number" % _str_num(qty))
    plan = read_json(a.plan, "plan", required=False) or {}
    flat_by = (plan.get("flat_by") if str(plan.get("symbol", "")).upper() == sym else None) \
        or cfg.get("flat_by") or "15:00"
    order = {"symbol": sym, "exchange": "NSE", "side": "sell", "qty": _str_num(qty), "type": "market",
             "product": "MIS", "time_in_force": "day",
             "why": "flat by %s IST: closes the %s day trade before the MIS auto square-off window "
                    "(qty from %s)" % (flat_by, sym, source)}
    replaced = _write_tickets(a, now, [order],
                              "Flatten ticket. Nothing is recorded until trader-risk passes it and you say yes in the chat.",
                              "ticket.py flatten %s on %s" % (sym, today))
    print("Flatten %s: %s (flat by %s IST)." % (sym, plain(order), flat_by))
    print("Wrote it to %s%s." % (a.orders, _replaced_text(replaced)))
    if a.qty is None:
        print("The qty comes from %s: check it before you say yes." % source)
    print("Next:")
    print("  1. trader-risk: check.py. A sell that only closes what you hold is never blocked by the daily")
    print("     limits or the entry window.")
    print("  2. ticket.py show, then the user's yes for this one order.")
    print("  3. After the yes, record the simulated sell: ticket.py result --index 1 --status placed "
          "--filled-qty %s --filled-price P." % _str_num(qty))
    return 0


def cmd_log(a):
    state = load_state(a.state)
    now, today = _clock(a)
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") and r["status"] in ("approved", "declined") else ""
        loss = (" max loss %s" % inr(r["max_loss"])) if r.get("max_loss") is not None and r["status"] == "placed" else ""
        paper = " [paper]" if r.get("paper") and r["status"] == "placed" else ""
        idx = ("#%s" % r["index"]) if r.get("index") is not None else "  "
        print("%s  %s %-9s %s%s%s%s" % (r.get("at"), idx, r.get("status"), r.get("plain"), reply, loss, paper))
    placed = sum(1 for r in rows if r.get("status") == "placed")
    print("%d placed%s." % (placed, "" if a.all else " today"))
    return 0


def main(argv=None):
    _utf8_stdio()
    ap = argparse.ArgumentParser(description="Paper-trading tickets and your yes (trader-broker). "
                                             "No broker is connected: it never places a real order.")
    sub = ap.add_subparsers(dest="cmd")

    def clock(p):
        p.add_argument("--now", help="pretend it is this IST time, e.g. \"2026-10-19 10:15\"")
        p.add_argument("--today", help="YYYY-MM-DD (default: today in IST)")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        clock(p)

    p = sub.add_parser("show", help="render the tickets")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve", help="record the user's explicit yes for one order")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p = sub.add_parser("decline", help="record a no")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p = sub.add_parser("result", help="record the paper trade: placed (with its simulated fill) or failed")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--status", required=True, choices=["placed", "failed"])
    p.add_argument("--note")
    p.add_argument("--filled-qty")
    p.add_argument("--filled-price")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p = sub.add_parser("exits", help="write the stop-loss and target exit tickets of a placed bracket plan")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--qty", help="whole shares the entry filled (default: the plan's qty)")
    p = sub.add_parser("fill", help="record a simulated fill in trader/portfolio.json")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--date", help="YYYY-MM-DD (default: today in IST)")
    p.add_argument("--now")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")
    p = sub.add_parser("flatten", help="a sell ticket that closes a symbol's position by flat_by")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", help="shares you hold (default: trader/portfolio.json)")
    p.add_argument("--orders", default="trader/orders.json", help="where the ticket is written")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json")
    clock(p)
    p = sub.add_parser("log", help="today's decisions")
    p.add_argument("--state", default="trader/state/broker.json")
    clock(p)
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result,
                "fill": cmd_fill, "exits": cmd_exits, "flatten": cmd_flatten, "log": cmd_log}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
