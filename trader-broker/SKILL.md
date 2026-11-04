---
name: trader-broker
description: >-
  Order Executor for NSE (rupees, IST). Places the orders that passed
  trader-risk and keeps the account, which starts with ₹1,00,000. Shows each
  order as a plain ticket (a bracket plan shows the entry, then the stop-loss
  leg and the target leg as separate follow-up tickets, and the max loss at the
  stop), places it with the price it filled at, and records the fill in
  trader/portfolio.json. Builds the sell ticket that closes a day trade by your
  flat-by time, and logs each trade's max loss so trader-risk can hold the
  daily limit. Fills are simulated at the price it is given: no order is sent
  to any broker. Use when the user says "place the order", "buy it", "take the
  trade", "execute", "close my position", "flatten", "get me out", "how is my
  account", or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

The Order Executor is the only skill in this pack that records a trade, so it
checks the most. **No broker is connected and nothing is sent anywhere: a fill
is simulated at the price it is given.** (The folder keeps its old name,
`trader-broker`.) An order is placed only when **trader-risk passed this exact
order today** (matched by fingerprint). That is the one gate. A BLOCK is final,
and a PASS needs nobody's reply: the chain from the plan to the fill can run on
its own.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                              # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" place --index 1 --price 1416.30   # place it, filled at 1416.30
python3 "${CLAUDE_SKILL_DIR}/ticket.py" exits --index 1                   # the stop-loss and target tickets, after the entry filled
python3 "${CLAUDE_SKILL_DIR}/ticket.py" place --index 1                   # a stop-loss or target leg: no price, it rests
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol RELIANCE --side sell --qty 52 --price 1426.20
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol RELIANCE --qty 52
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` sends nothing anywhere. It renders tickets, places the READY ones,
records the fills, builds the exit and flatten tickets and keeps the log in
`trader/state/broker.json` (the file keeps its old name). `--now "2026-10-19
10:15"` pins the clock (IST, no daylight saving). Amounts are in rupees with
Indian grouping (₹1,23,456.78).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `exits` and `flatten` replace it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every trade placed and filled, with each entry's max loss |
| `trader/portfolio.json` | the account: cash and shares (cost-basis lots, no tax claims), updated by `place --price` and `fill` |
| `trader/plan.json` | the flat-by time for `flatten` |
| `trader/config.json` | `budget` (the account's starting cash, default 100000) and `flat_by` (default "15:00") |

## The account (read this first)

- **It starts with ₹1,00,000** (1 lakh): the `budget` in `trader/config.json`, or
  100000 when there is no config or no `budget`. The first fill creates
  `trader/portfolio.json` with that cash, and trader-budget and trader-risk read
  the same account.
- **Fills are simulated.** Place an order with the price it filled at: the limit
  price for a limit order, or the latest quote (`alerts.py quote RELIANCE`) for
  a market order. A quote is delayed and unofficial, so say which price you used.
  A real fill can be worse: a market order slips, a stop gaps, and a stock at its
  circuit limit may not trade at all. No charges come out of the cash;
  trader-budget prints an estimate of them.
- **Nothing connects to anything.** No broker, no login, no key, no password.

## Rules of the Indian market that change the tickets

The pack follows the structure of the real market, so a ticket here is shaped
like an order on the exchange.

- **Product.** Every order from this pack is `MIS` (intraday). `CNC` is
  delivery, `NRML` is carry; they are allowed values but a day-trading pack
  does not use them.
- **MIS auto square-off.** A broker squares off open intraday (MIS) positions
  on its own, from about 15:05 to 15:20 depending on the broker and the stock,
  and charges a fee for it. Since 3 August 2026 a stock with F&O contracts also
  stops continuous trading at 15:15 and its stop-loss orders are cancelled
  then, before the closing auction (15:15 to 15:35). So the defaults stop new
  entries at 14:30 and set `flat_by` to 15:00, well before all of that. They
  are defaults: adjust `no_entries_after` and `flat_by` in `trader/config.json`.
- **Whole shares only.** Notional or fractional orders do not exist in India.
  A ticket for one still renders, but trader-risk blocks it.
- **No bracket order.** A stop-loss and a target are two separate orders with
  no OCO (one cancels other) between them. See the next section.
- **Price bands.** NSE stocks have daily price bands. A stock at its lower
  circuit may not let you sell, so a stop-loss can fail to fill at all.
  At its upper circuit you may not be able to buy.
- **Tax.** The account makes no tax claims. Intraday equity profit is
  speculative business income in India: talk to a chartered accountant.

## The bracket plan

trader-budget writes `"order_class": "bracket"` for an order that has a
stop-loss and a target. In India that is a **plan with three parts**, not one
order:

1. **The entry.** Placing the ticket records it, with its fill price.
2. **The stop-loss leg.** A SELL stop order (SL-M at the stop-loss price, or SL
   when a stop-loss limit price is given), placed **only after the entry fills**.
3. **The target leg.** A SELL limit order at the target price, placed **only
   after the entry fills**.

Each exit leg is its own ticket: `ticket.py exits --index N` writes them to
`trader/orders.json`, and they go through trader-risk (an exit is never blocked
by the entry-only rules). Both legs carry an `exit_group` marker, so trader-risk
treats them as alternatives: only one will fill, and neither uses up the shares
for the other. **There is no automatic OCO: when one leg fills, record that
sell and skip the other.** A leg placed without a price rests: nothing triggers
it by itself, so watch both levels with trader-alerts. The max loss on the
ticket is at the stop-loss price; a stop-loss can fill worse in a fast market.

## Steps: the entry

1. Make sure trader-risk checked the orders today (`check.py`), then
   `ticket.py show`. A ticket that is NOT READY or BLOCKED goes back to
   `/trader-risk`: never place it, and never edit an order to make it pass.
2. Place each READY entry ticket: `ticket.py place --index N --price P`. No
   confirmation is needed. A market order needs the price it fills at; a limit
   order that has not reached its limit can be placed without one and rests.
3. `place` logs the entry's max loss for trader-risk's daily limit and adds the
   fill to `trader/portfolio.json`. Tell the user what was placed, in plain
   words: the shares, the fill price, the stop, the target and the max loss.
4. Once the entry filled, `ticket.py exits --index N` (add `--qty` if only part
   filled), then `/trader-risk`, then `ticket.py show` and
   `ticket.py place --index 1` (the stop-loss leg) and `ticket.py place --index 2`
   (the target leg).

## Steps: while it is open, and out before the square-off

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`). Nothing moves a stop for you, and a leg that
  was never placed protects nothing.
- When the stop or the target is hit, record the exit:
  `ticket.py fill --symbol RELIANCE --side sell --qty 52 --price 1426.20`, and
  skip the other leg.
- By `flat_by` (15:00 IST by default), anything still open gets closed:
  1. Read the portfolio (`trader/portfolio.json`) for the shares held.
  2. `ticket.py flatten --symbol RELIANCE --qty 52` writes a market SELL (MIS,
     NSE) for those whole shares to `trader/orders.json`.
  3. `/trader-risk`. A sell that only closes what the account holds is never
     blocked by the daily limits or the entry window.
  4. `ticket.py show`, then `ticket.py place --index 1 --price P` (P: the latest
     quote) records the sell and brings the cash back.

## Example: the reel

The canonical RELIANCE trade, with no `budget` in the config (so the account
starts with ₹1,00,000). `ticket.py show`:

```
ORDER 1 of 1
  BUY 52 RELIANCE (NSE, MIS), market, good for today
  stop-loss 1406.40
  target 1426.20
  max loss about ₹514.80 if the stop-loss fills at 1406.40 (it can fill worse in a fast market)
  why: opening-range breakout on RELIANCE: 52 shares, bound by your position cap (52 x 9.90 = ₹514.80 at the stop)
  READY: trader-risk PASS
  This is a bracket PLAN in three parts, not one order. Placing it records the entry.
  After the entry fills, the exit legs are written as two SEPARATE follow-up
  tickets (ticket.py exits), each with its own trader-risk check:
    1. stop-loss leg: SELL 52 shares of RELIANCE (NSE, MIS), stop order at ₹1,406.40, good for today
    2. target leg: SELL 52 shares of RELIANCE (NSE, MIS), limit order at ₹1,426.20, good for today
  There is no automatic OCO here: when one of the two exit legs fills, record that sell
  (ticket.py fill) and skip the other leg.
  Place it: ticket.py place --index 1 --price P   (P is the price it fills at; leave --price
  out for a limit or stop order that has not filled yet)
```

The latest quote is 1416.30, so `ticket.py place --index 1 --price 1416.30`:

```
Placed order 1: BUY 52 RELIANCE (NSE, MIS), market, good for today / stop-loss 1406.40 / target 1426.20 / max loss about ₹514.80 if the stop-loss fills at 1406.40 (it can fill worse in a fast market).
Filled 52 RELIANCE at ₹1,416.30 (simulated: nothing is sent to a broker). Cash now ₹26,352.40.
Next: ticket.py exits --index 1 (the stop-loss leg and the target leg, each its own ticket).
```

52 x 1416.30 = ₹73,647.60 leaves the cash, so `trader/portfolio.json` holds
₹26,352.40 and 52 RELIANCE shares in one cost-basis lot. `ticket.py log`:

```
2026-10-19T09:41:00+05:30  #1 placed    BUY 52 RELIANCE (NSE, MIS), market, good for today / ... max loss ₹514.80
2026-10-19T09:41:00+05:30     filled    BUY 52 RELIANCE at 1416.30
1 placed today.
```

`ticket.py exits --index 1` then writes the stop-loss leg (SELL 52, stop order
at ₹1,406.40) and the target leg (SELL 52, limit order at ₹1,426.20) as two new
tickets. After `/trader-risk` passes both, `ticket.py place --index 1` and
`ticket.py place --index 2` place them without a price, so both rest. If the
target is hit, `ticket.py fill --symbol RELIANCE --side sell --qty 52
--price 1426.20` brings ₹74,162.40 back (cash ₹1,00,514.80) and the stop-loss
leg is simply skipped.

## What it never does

- Never places an order that trader-risk has not passed, and never edits an
  order to make it pass.
- Never sends anything to a broker or an exchange: none is connected, and it
  never logs in to anything or asks for a key, a password, a PIN or a code.
- Never places a market order without the price it fills at.
- Never promises a fill price: a market order and a stop fill at whatever the
  market gives.
- Never treats the stop-loss leg as in place until it is placed and watched.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- An order is placed only when trader-risk passed that exact order today. A BLOCK is final: fix the order
  or your rules, never the check.
- Long only, no F&O, margin, short selling or leverage unless the config allows it. An MIS (intraday)
  order is leverage, so the pack counts it as margin when it costs more than the cash in the account.
- No logins, API keys or passwords are used, and none belong in the chat.
- Intraday gains and losses have tax consequences in India: talk to a chartered accountant.
