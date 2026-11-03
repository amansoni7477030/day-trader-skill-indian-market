---
name: trader-budget
description: >-
  Budget Manager. Sizes every position to your budget and your max loss per
  trade, in rupees, for Indian (NSE) day trades. Reads the trade plan from
  trader-exit (entry, stop, target), finds the whole shares that fit inside
  four limits (max loss per trade, position cap, max order, cash after your
  buffer), says which limit decided it, and writes ONE bracket order to
  trader/orders.json: an entry plus a stop-loss leg and a target leg, as an
  intraday (MIS) NSE order. Prices are rounded to the exchange tick for their
  level (₹0.10 for a stock between ₹1,000 and ₹5,000, ₹0.05 from ₹250 to
  ₹1,000, and so on).
  It also prints the estimated round-trip charges (brokerage, STT, stamp
  duty, exchange fees, GST) for the trade, for information only. A stop too
  far away for your max loss is a plain refusal, not a smaller order. Nothing
  is placed. Use when the user says "how many shares", "size this trade",
  "how much should I buy", "I have ₹1,00,000", "I only want to risk ₹1,000 a
  trade", "set my rules", "keep 10% in cash", or after /trader-exit writes a
  plan.
argument-hint: "[budget in rupees]"
---

# trader-budget

The Budget Manager turns a plan ("buy RELIANCE at 1416.30, stop 1406.40") into
an exact order ("buy 52 shares"), inside rules the user sets once. The stop
decides the size: the further away the stop, the fewer shares. It writes a
proposal only. trader-risk checks it and trader-broker asks the user yes or
no. Day trading loses most people money. Sizing to a max loss keeps each
loss small; it does not make a trade win. This is education and tooling,
not financial advice.

Everything is in Indian rupees (₹, written with lakh/crore grouping like
₹1,00,000), on the NSE, in IST (UTC+5:30, no daylight saving).

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                        # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit --write        # a limit order at the plan's entry
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 1416.30 --json # a limit order at 1416.30 (rounded to the tick)
python3 "${CLAUDE_SKILL_DIR}/size.py" --weights HDFCBANK=60,ITC=40 --prices trader/prices.json   # weights mode, below
```

`--now "2026-10-19 09:40"` pins the clock (IST). A plan for another day is
refused: make a new one with trader-exit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat-by time (trader-exit writes it) |
| `trader/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (trader-alerts writes it) |
| `trader/portfolio.json` | optional: the paper account; its cash caps the size (trader-broker keeps it) |
| `trader/orders.json` | the one proposed bracket order it writes with `--write` |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: the paper account's starting cash, in rupees (default ₹1,00,000). Even on paper, use an
  amount the user could afford to lose if they ever trade for real.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at the stop (default ₹1,000 on a ₹1,00,000 budget).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest position (75% of the
  budget), the biggest order (₹80,000) and the smallest order (₹100).
- The day limits trader-risk enforces: **max_daily_loss_inr** (₹3,000), **max_trades_per_day** (3),
  **min_reward_risk** (1.0), **require_stop** (true), **no_entries_before** / **no_entries_after**
  ("09:30" / "14:30") and **flat_by** ("15:00"), all IST.
- **tick_size**: leave it empty (`null`) and every price uses NSE's tick for its level (0.01 under
  ₹250, 0.05 to ₹1,000, 0.10 to ₹5,000, 0.50 to ₹10,000, 1.00 to ₹20,000, 5.00 above). Set a number
  only to force one tick for every price, for example when your broker's order window shows a
  neighbouring tick near a band edge.
- **blocked**: tickers they never want to trade.
- **brokerage_pct** (0.03) and **brokerage_per_order_inr** (20): only used for the charges estimate.
- **paper**: keep it `true` (the default). This pack trades on paper only, so `false` is refused with
  "this pack trades on paper only". There is no broker setting.

The times 14:30 and 15:00 are defaults, not rules of the exchange. Since
3 August 2026 a stock with F&O contracts stops trading continuously at 15:15
and its stop-loss orders are cancelled then, before the closing auction, and
real brokers auto square-off intraday (MIS) positions from about 15:05 to 15:20
(it varies by broker and by stock) and charge a fee for it. So entries stop at
14:30 and "flat by" comes well before all of that. If the user trades for real
later, ask for their broker's square-off time and adjust `no_entries_after`
and `flat_by`.

## Step 2: size the trade

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
```

Whole shares only: India has no fractional or notional orders. The size is
the smallest of four limits:

| limit | shares |
| --- | --- |
| max loss | `max_loss_per_trade_inr` / risk a share (entry minus stop) |
| position cap | budget x `max_position_pct` / entry |
| max order | `max_order_inr` / entry |
| cash | (budget minus the cash buffer) / entry, never more than the cash in `trader/portfolio.json` |

The entry it sizes at: the limit price with `--limit`. For a market order, the
plan's entry, or the price in `trader/prices.json` if it is fresh (under 15
minutes old) and higher. A price that ran up since the plan means a bigger
loss at the same stop, so fewer shares. A lower price never shrinks the risk.

Prices on the order sit on the tick of their own level: the stop is rounded
DOWN (away from the entry), the target and a `--limit` price to the nearest
tick (half up), and the report says so whenever it moves one. A plan made with
`trader-exit --tick-size` keeps its tick, and `tick_size` in the config wins
over both. A market order has no price of its own.

It refuses, and writes no order, when: the size is zero shares, the order is
under the minimum, reward:risk is under `min_reward_risk`, the stop is not
below the entry, the target is not above it (the move already happened), the
plan is for another day, the ticker is blocked, or the plan is a short. Say
the reason to the user in one line.

## Step 3: show the user

Show the four limits, which one decided the size, the shares, the rupees and
the max loss at the stop. Say plainly that a stop is an order, not a promise:
in a fast market it can fill below the stop, and a stock at its lower circuit
(NSE stocks have daily price bands of 2%, 5%, 10% or 20%, or none) may not let
you sell at all; at its upper circuit you may not be able to buy.

Also read out the estimated charges line (below) and tell the user it is an
estimate to check against their broker's charge calculator. Then hand over to
`/trader-risk`.

## The bracket is a plan, not one order

A stop-loss and a target are two separate orders with no
one-cancels-the-other between them, and this pack records them that way. So
`"order_class": "bracket"` in `orders.json` means a plan with three parts: the
ENTRY order (what the user says yes to), then a STOP-LOSS order (sell, trigger
at `stop_loss_stop_price`) and a TARGET order (sell, limit at
`take_profit_limit_price`). trader-broker records the two legs only after the
entry fills, each as its own ticket with its own yes. When one leg fills, the
other is skipped: nothing does it automatically. The max loss at the stop is
measured from the stop-loss leg.

## Estimated charges (information only)

For the sized trade it prints "Estimated charges at the stop ₹X, at the
target ₹Y: verify with your broker's calculator" and stores the entry, stop
and target cases in the sizing JSON. They cover brokerage (the smaller of
`brokerage_pct` of turnover and `brokerage_per_order_inr`, per order), STT
(0.025% of the sell side), stamp duty (0.003% of the buy side), the NSE
transaction charge (0.00307% of turnover, each side, as Zerodha lists it on its
charges page, read on 4 November 2026), the SEBI turnover fee (0.0001%, ₹10 a
crore) and 18% GST on brokerage plus exchange and SEBI fees. Not included: the
exchange's investor-protection fund fee (a fraction of a paisa a lakh), DP
charges (they apply to delivery sells only) and the broker's auto square-off
fee if it has to close the trade for you. Rates change and brokers differ:
treat the figure as an estimate and say so. The charges are not in the max loss
and they never change the share count.

## Example: the reliance trade (synthetic data)

RELIANCE on Monday 2026-10-19, built from synthetic bars (not real market
data). Opening-range breakout: the range 09:15 to 09:30 is 1399.50 to
1413.40, the 09:30 bar closes at 09:35 at 1416.30, stop 1406.40 (the
opening-range midpoint 1406.45, rounded down to the ₹0.10 tick), target 1426.20
(1R). Risk 9.90 a share. The example config: ₹1,00,000 budget, 10% cash
buffer, ₹1,000 max loss.

| limit | the math | shares |
| --- | --- | --- |
| max loss | ₹1,000 / 9.90 | 101 |
| position cap | 75% of ₹1,00,000 = ₹75,000 / 1416.30 | **52 (decides it)** |
| max order | ₹80,000 / 1416.30 | 56 |
| cash | ₹1,00,000 minus ₹10,000 = ₹90,000 / 1416.30 | 63 |

52 shares = ₹73,647.60. Max loss at the stop: 52 x 9.90 = ₹514.80.
Estimated charges: ₹73.19 if it stops out, ₹73.48 at the target (₹73.33 if it
is closed flat at the entry). Hand check at the target: buy 52 x 1416.30 =
₹73,647.60 and sell 52 x 1426.20 = ₹74,162.40; brokerage is ₹20 an order (0.03%
would be more than ₹20), so ₹40; STT 0.025% of the sell side = ₹18.54; stamp
duty 0.003% of the buy side = ₹2.21; NSE charge 0.00307% of ₹1,47,810.00 =
₹4.54; SEBI fee ₹0.15; GST 18% of (40 + 4.54 + 0.15) = ₹8.04; total ₹73.48.
It writes:

```json
{"symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "qty": "52", "type": "market",
 "product": "MIS", "time_in_force": "day",
 "order_class": "bracket", "take_profit_limit_price": "1426.20", "stop_loss_stop_price": "1406.40",
 "why": "opening-range breakout on RELIANCE: 52 shares, bound by your position cap (52 x 9.90 = ₹514.80 at the stop)",
 "plan": {"entry": 1416.3, "stop": 1406.4, "target": 1426.2, "risk_per_share": 9.9, "max_loss": 514.8,
          "setup": "orb", "entry_time": "9:35 AM", "flat_by": "15:00"}}
```

`why` and `plan` are notes for trader-risk and trader-broker. They are only
notes in the file. With the stop at 300.00 instead, one share would risk
1116.30, more than the ₹1,000 max loss: no order. If the price had already
run to 1419.50 by 10:52, the same plan would risk 13.10 a share and
reward:risk would fall to 0.51: refused, because the move it was waiting for
has mostly happened.

## Weights mode (kept from v1)

`--weights HDFCBANK=37.88,ITC=9.09` or `--targets FILE` (`targets.example.json`
here, an ILLUSTRATIVE five-name file: not a recommendation and not real
index weights) splits a budget across a list by weight, in whole shares, so
it needs a price for each name (`--prices trader/prices.json`). The cash
buffer comes off the top, weights are renormalised to 100, any stock over
`max_position_pct` is pinned at the cap and its excess shared out, slices
under the minimum order are dropped, and each slice is floored to whole
shares with the leftover shared out one share at a time, so a little money
may be left idle (the report says how much). Orders are written as `MIS`
(intraday) unless you pass `--product CNC` (delivery) for a long-term split.
These are plain buys with no stop: trader-risk blocks them while
`require_stop` is true and counts each one against `max_trades_per_day`. Use
a separate config (`--config`) for a long-term split.

## Taxes

Intraday equity profit in India is usually treated as speculative business
income, not capital gains. Talk to a chartered accountant. This skill gives
no tax advice.

## Rules

- Proposals only. This skill never records a trade.
- Never size money the user has not said is for this. Ask for the budget and the max loss.
- Every number comes from the config, the plan or the prices. Nothing is guessed.
- No projections: never say what a trade will make. A target is a plan, not a forecast.
- Free Yahoo prices are unofficial and can lag. Paper fills are simulated: a real fill can differ.
- Holiday and fee figures are from public sources and can change: verify them on nseindia.com and
  with your broker.

## What it never does

It never places, approves or schedules an order, never raises a limit on
its own, never shrinks a stop to fit more shares, never changes the share
count for charges, and never sizes options, margin or short positions.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading only: no broker is connected and nothing in this pack can place a real order. Every
  trade is a simulation in a paper account that starts with ₹1,00,000.
- A paper trade is recorded only when two things hold: trader-risk passed that exact order today, and
  the user typed an explicit yes for that one order in the chat.
- Long only, no F&O, margin, short selling or leverage unless the config allows it and the user confirms.
  An MIS (intraday) order is leverage in real trading, so the pack counts it as margin when it costs more
  than the cash in the paper account.
- No logins, API keys or passwords are used, and none belong in the chat.
- Intraday gains and losses have tax consequences in India: talk to a chartered accountant if you ever
  trade for real.
