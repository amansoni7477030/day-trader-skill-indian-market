---
name: trader-risk
description: >-
  Risk Manager. Checks every trade against your limits and stops anything
  over them, before it can be placed, for Indian (NSE) day trades in
  rupees. For a day trade: a stop is required, the loss at the stop must fit
  your max loss per trade (measured from the higher of the plan's entry and a
  fresh price), reward:risk must meet your minimum, the day's total risk must
  fit your daily loss limit, no more than your trades per day, and no new
  entries outside your entry window (IST) or when the market is closed. It
  also keeps the basics: blocked tickers, order size, position cap, cash
  buffer, margin, short sales, F&O symbols, whole shares only, prices on the
  NSE tick for their level (₹0.10 for a stock between ₹1,000 and ₹5,000, ₹0.05
  from ₹250 to ₹1,000, and so on), valid product and exchange, malformed or bad
  bracket orders,
  duplicates and a daily order limit. Getting out is never blocked by a day
  limit. Prints PASS or BLOCK with the reason and records the verdicts so
  trader-broker only places an order that passed. Use when the user says
  "check my order", "is this safe", "risk check", "does this break my
  rules", "can I take this trade", "how much have I risked today", or before
  /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager is the gate between a proposal and the account. Every order
written by trader-budget or trader-broker's flatten passes through it. A
BLOCK is final for that order: it is never placed. A PASS is all trader-broker
needs to place it. Most people who day trade lose money. These limits
keep each loss and each day's losses small. They do not make a trade win.
This is education and tooling, not financial advice.

Everything is in rupees (₹) on the NSE, with times in IST (UTC+5:30, no
daylight saving), worked out inside the tool and never from the computer's
clock zone.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                           # trader/ files; writes trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-19 09:40"  # pin the clock (IST)
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget, or trader-broker's flatten) |
| `trader/config.json` | the limits (trader-budget ships `config.example.json`) |
| `trader/portfolio.json` | cash and holdings; if missing, a fresh account funded with the budget |
| `trader/prices.json` | a fresh price raises the entry it measures risk from; also values holdings |
| `trader/state/broker.json` | what was placed today: duplicates, trades per day, daily loss |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |

## Entries and exits

An **entry** opens or adds to a position: a buy, or a sell that would go
short. An **exit** only reduces what you already hold. Getting out must
always be possible, so an exit is checked for format, asset and short sales
only. Every other rule (day limits, entry window, loss, reward:risk, blocked
list, size, duplicates) turns into a warning for an exit, never a BLOCK. An
exit after the entry cutoff, after flat-by, on a holiday or at the weekend
still passes.

The two exit legs that trader-broker's `ticket.py exits` writes (a stop-loss
leg and a target leg, both marked `exit_group`) are alternatives: only one of
them will fill, so each is checked against the shares you hold and neither uses
them up for the other. Two ordinary sells in one batch still count against each
other.

## The order shape it checks

Every order is the plain order shape trader-budget writes: `symbol` (the
plain NSE symbol, like `RELIANCE`, `M&M` or `BAJAJ-AUTO`, without `.NS`),
`exchange` (`NSE` or `BSE`), `side`, `qty` (whole shares, as a string),
`type` (`market`, `limit`, `stop`, `stop_limit`), `product` (`MIS` intraday,
`CNC` delivery, `NRML` carry), `time_in_force` (`day` or `ioc`), the price
fields, and for a bracket `take_profit_limit_price` and
`stop_loss_stop_price`. A missing exchange or product is read as NSE and MIS,
with a warning. The fingerprint covers exactly these fields and is the same
function trader-broker uses.

A **bracket** (`"order_class": "bracket"`) is a plan, not one order:
the entry, then a stop-loss leg and a target leg that trader-broker records
after the entry fills, each as its own ticket (there is no automatic
one-cancels-the-other). The risk check still demands both legs, whole
shares and `day`, and it measures the max loss from the stop-loss leg.

## The checks

Orders are checked in file order, as if each passed order had filled, so a
batch that is fine order by order but breaks a limit together is caught.

| check | blocks when |
| --- | --- |
| format | no symbol, side not buy or sell, no qty; a notional or fractional order ("India trades whole shares only"); an unknown type, `time_in_force`, `product` or `exchange`; a limit, stop or leg price that is not a multiple of the tick ("price X is not a multiple of the ₹0.10 tick (NSE's tick for a price in its band)"); a limit order with no limit price; a bracket that is not whole shares, not `day`, or misses its stop-loss or take-profit |
| asset | a symbol that does not look like a plain NSE stock or ETF symbol; a symbol that looks like an F&O contract (ends in `CE` or `PE` after a digit, or in `FUT`) while `allow_options` is false |
| short | a sell of more than you hold (`allow_short` false) |
| blocked | (entries) the ticker is on the `blocked` list |
| size | (entries) under `min_order_inr` or over `max_order_inr` |
| position | (entries) the stock would be over `max_position_pct` of the account |
| cash | (entries) the buy would leave less than the cash buffer, or need margin (MIS leverage counts as margin) unless `allow_margin` is true |
| stop | (entries) no stop while `require_stop` is true (a bracket `stop_loss_stop_price`, or the order's plan stop); a stop on the wrong side of the entry |
| loss | (entries) qty x (entry minus stop) over `max_loss_per_trade_inr` |
| reward:risk | (entries) (target minus entry) / (entry minus stop) under `min_reward_risk` |
| daily loss | (entries) the max losses at the stops of today's entries, plus this one, over `max_daily_loss_inr` |
| trades | (entries) `max_trades_per_day` entries already placed today |
| window | (entries) before `no_entries_before` or after `no_entries_after`, or the market is closed (weekend, NSE holiday, outside 09:15 to 15:15: the pack's day ends when continuous trading of F&O stocks does) |
| duplicate | (entries) the same symbol and side twice in one batch, or already placed today |
| daily limit | (entries) `max_orders_per_day` orders already placed today |

How the numbers work:

- **The entry it measures from**: the limit price for a limit order. For a
  market order, the HIGHER of the plan's entry and a fresh price from
  `trader/prices.json` (under 15 minutes old). A plan written at 09:35 cannot
  understate the risk at 10:55 after the price ran up. An older price is not
  used, and it says so.
- **Daily loss** counts the full max loss of every entry placed today, win or
  lose, as trader-broker logged it. Three full-size ₹1,000 trades use up a
  ₹3,000 day.
- **The window** is IST. The defaults are `no_entries_before` 09:30 (the
  15-minute opening range ends then) and `no_entries_after` 14:30. Those two
  and `flat_by` 15:00 are defaults you can adjust. Since 3 August 2026 a stock
  with F&O contracts stops continuous trading at 15:15 and its stop-loss orders
  are cancelled then, before the closing auction, and real brokers square off
  intraday (MIS) positions from about 15:05 to 15:20 (it varies by broker and
  by stock) and charge a fee for it. So entries stop at 14:30 and "flat by" is
  well before all of that. Set both `no_entries_*` to null to switch the window
  off.
- **Holidays**: NSE's own equity-segment list for 2026 (20 days, four of them
  on a weekend: Mahashivratri, Id-Ul-Fitr, Independence Day and Diwali Laxmi
  Pujan), read before the weekend check so such a day keeps its name. Verify
  it against nseindia.com (Market Timings and Holidays). For any other year
  the tool warns that the list covers 2026 only. Special sessions (Muhurat
  trading on the Diwali Laxmi Pujan evening, occasional Saturday sessions) are
  not supported: their dates count as not a normal session. There are no early
  closes on normal days; the early-close table (`NSE_EARLY_CLOSE`) is empty and
  reserved for special short sessions, and when it holds a date the entry
  cutoff moves earlier by the same amount the close moved.
- **Tick**: NSE's tick depends on the price (circular NSE/CMTR/67133, reviewed
  monthly): ₹0.01 under ₹250, ₹0.05 from ₹250 to ₹1,000, ₹0.10 above ₹1,000 up
  to ₹5,000, ₹0.50 up to ₹10,000, ₹1.00 up to ₹20,000 and ₹5.00 above. Each
  limit, stop or leg price is judged on its own level, and one that is not a
  multiple of it is blocked, as a real exchange would reject it. `tick_size`
  in the config forces one tick for every price (for a broker window that
  shows a neighbouring tick near a band edge).
- **MIS**: an entry with a product other than MIS (CNC, NRML) is allowed but
  warned: "not an intraday order: flat-by and square-off rules in this pack
  assume MIS".
- **F&O**: `allow_options` is a simple off switch that looks only at the
  symbol. This pack models cash-market equities, not derivatives, and
  derivative orders would need the NFO segment, which is not an allowed
  exchange here.

Warnings do not block, but say them to the user: a number
sent as a number instead of a string, a stop that lives only in the plan
(nothing triggers it by itself), a price too old to use, a missing exchange or
product, a product other than MIS, a holiday list that does not cover the year.

## Steps

1. If the price may have moved since the plan, refresh it first
   (trader-alerts: `alerts.py quote RELIANCE --write`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, order by order: the max loss at
   the stop, reward:risk, and how much of today's limits it uses. For a
   BLOCK, give the reason and the fix (fewer shares, a closer stop from
   trader-exit, wait for the window, or skip the trade). Never change a
   limit yourself.
4. If the order changes in any way, run the check again. trader-broker matches
   each order to its verdict by fingerprint, so an edited order is not ready
   until it passes again, and a verdict from yesterday does not count today.
5. Every order that passed goes on to `/trader-broker`, which places it. No
   confirmation is needed.

## Example: the reliance trade (synthetic data)

The RELIANCE bracket from trader-budget at 09:40 on Monday 2026-10-19 (built
from synthetic bars, not real market data), on a fresh ₹1,00,000
account with the example config:

```
PASS  #1 buy RELIANCE 52 shares, entry
          bracket = a plan: the stop-loss and target legs are placed after the entry fills, each as its own ticket (no automatic one-cancels-the-other)
          entry 1416.30 (the plan's entry), stop 1406.40, target 1426.20: risk 9.90 a share
          max loss ₹514.80 (limit ₹1,000), reward:risk 1.00 (min 1)
          today before this: 0 of 3 trades, ₹0 of ₹3,000 daily loss

1 passed, 0 blocked at 2026-10-19 09:40 IST. Cash after the passed orders: ₹26,352.40.
```

One more share and it is blocked, by the position cap (75% of ₹1,00,000 is
₹75,000, and 53 shares are ₹75,063.90):

```
BLOCK #1 buy RELIANCE 53 shares, entry
          entry 1416.30 (the plan's entry), stop 1406.40, target 1426.20: risk 9.90 a share
          max loss ₹524.70 (limit ₹1,000), reward:risk 1.00 (min 1)
          today before this: 0 of 3 trades, ₹0 of ₹3,000 daily loss
        - position: RELIANCE would be ₹75,063.90, 75.1% of the ₹1,00,000 account, over your 75% cap
```

The same 52 shares checked at 10:55 with a 10:52 quote of 1419.50 in
`trader/prices.json` risk 13.10 a share (₹681.20 at the stop) and reward:risk
falls to 0.51: blocked. At 09:20 they are blocked by the window (no new
entries before 09:30), and at 14:31 by the cutoff (no new entries after
14:30). At 15:20 they are outside the session, which ends when continuous
trading of F&O stocks does, at 15:15. On Tuesday 2026-10-20 they are blocked
because the market is closed (Dussehra, an NSE holiday). A stop of 1406.45 is
blocked because it is not a multiple of the ₹0.10 tick that RELIANCE trades
on. The 15:00 sell that closes the position passes, even though it is after
the 14:30 entry cutoff: it is an exit.

## Rules

- A PASS only means the order is allowed to be placed. It is a rules check, not a recommendation.
- Every number comes from the config, the order, the portfolio, the prices or the trade log.
- Free Yahoo prices are unofficial and can lag; a real fill can differ, and a
  stop can fill worse than its price in a fast market. A stock at its lower circuit may not let
  you sell at all, so a stop can fail entirely.

## What it never does

It never records a trade, never edits an order to make it pass, never
loosens a limit, never blocks a sell that only gets the user out, and never
treats a PASS as a recommendation.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- An order is placed only when trader-risk passed that exact order today. A BLOCK is final: fix the order
  or your rules, never the check.
- Long only, no F&O, margin, short selling or leverage unless the config allows it. An MIS (intraday)
  order is leverage, so the pack counts it as margin when it costs more than the cash in the account.
- No logins, API keys or passwords are used, and none belong in the chat.
- Intraday gains and losses have tax consequences in India: talk to a chartered accountant.
