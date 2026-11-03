"""Writes the fixtures of tests/test_budget.py and tests/test_risk.py.

SYNTHETIC / ILLUSTRATIVE, not real market data and not a recommendation. The
RELIANCE numbers come from tests/fixtures/_synth.py (RELIANCE_SCRIPT: the
15-minute opening range 09:15-09:30 and the 09:30 bar that closes at 09:35
above the range high). The weights and prices of the five-name split are
made up for the example. Deterministic: running it twice gives identical files.

    python tests/fixtures/make_budget_risk_fixtures.py

Files:
  trader-budget/plan-reliance.json                what trader-exit would write for the trigger bar
  trader-budget/nifty-illustrative-weights.json   raw (not renormalised) weights, ILLUSTRATIVE
  trader-budget/prices-illustrative.json          made-up prices for the five names
  trader-risk/orders-reliance.json                what trader-budget writes for that plan (52 shares)
  trader-risk/orders-weights.json                 what weights mode writes for the five names
"""

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import _synth  # noqa: E402

NOTE = "SYNTHETIC / ILLUSTRATIVE: not real market data, not a recommendation."
NOW_UTC = "2026-10-19T04:10:00Z"          # 09:40 IST on Monday 2026-10-19
WEIGHTS = [("HDFCBANK", "HDFC Bank", 12.5), ("RELIANCE", "Reliance Industries", 8.5), ("INFY", "Infosys", 5.0),
           ("TCS", "Tata Consultancy Services", 4.0), ("ITC", "ITC", 3.0)]
PRICES = {"RELIANCE": 1416.30, "HDFCBANK": 950.40, "TCS": 3150.30, "INFY": 1580.10, "ITC": 410.55}


def load_size():
    path = os.path.join(ROOT, "trader-budget", "size.py")
    spec = importlib.util.spec_from_file_location("trader_size", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def main():
    size = load_size()
    budget_dir = os.path.join(HERE, "trader-budget")
    risk_dir = os.path.join(HERE, "trader-risk")
    script = _synth.RELIANCE_SCRIPT
    rng = script[:3]
    hi, lo = max(b[1] for b in rng), min(b[2] for b in rng)
    trigger = script[3]
    entry = trigger[3]
    stop = size.floor_tick((hi + lo) / 2.0)                    # opening-range midpoint, floored to the tick
    risk = round(entry - stop, 2)
    target = size.round_tick(entry + risk)                     # 1R
    plan = {
        "_note": NOTE,
        "created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": _synth.RELIANCE_DAY.isoformat(),
        "setup": "orb", "entry": entry, "entry_time": "9:35 AM", "entry_bar": 3, "interval": "5m",
        "opening_range": {"high": hi, "low": lo, "minutes": 15},
        "stop": stop, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": target, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": risk, "reward_risk": 1.0, "flat_by": "15:00", "tick_size": 0.1, "tick_override": False,
    }
    write(os.path.join(budget_dir, "plan-reliance.json"), plan)

    write(os.path.join(budget_dir, "nifty-illustrative-weights.json"), {
        "_note": NOTE + " Five large Nifty 50 names; the weights are made up, not real index weights.",
        "source": "ILLUSTRATIVE raw weights, not renormalised",
        "weights": [{"ticker": t, "name": n, "weight": w} for t, n, w in WEIGHTS]})
    write(os.path.join(budget_dir, "prices-illustrative.json"), {
        "_note": NOTE + " Made-up prices for the example.",
        "as_of": NOW_UTC, "source": "illustrative", "prices": PRICES})

    cfg = size.load_config(os.path.join(ROOT, "trader-budget", "config.example.json"))
    s = size.size_trade(plan, cfg, today=plan["date"])
    order = size.bracket_order(s)
    path = os.path.join(risk_dir, "orders-reliance.json")
    size.write_orders(path, [order], cfg, "trader/plan.json (RELIANCE orb 2026-10-19)",
                      size.parse_utc(NOW_UTC))
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["fixture_note"] = NOTE
    write(path, doc)

    rows = size.parse_weights({"weights": [{"ticker": t, "name": n, "weight": w} for t, n, w in WEIGHTS]})
    wcfg = dict(cfg, max_position_pct=25, max_order_inr=40000)
    result = size.size(rows, wcfg, prices=PRICES)
    orders = size.orders_from(result, "ILLUSTRATIVE raw weights, not renormalised")
    path = os.path.join(risk_dir, "orders-weights.json")
    size.write_orders(path, orders, wcfg, "illustrative weights", size.parse_utc(NOW_UTC))
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["fixture_note"] = NOTE
    write(path, doc)
    print("wrote 5 fixture files; RELIANCE: %d shares, max loss %s" % (s["qty"], size.inr(s["max_loss"])))


if __name__ == "__main__":
    main()
