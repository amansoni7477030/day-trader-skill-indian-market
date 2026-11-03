---
name: trader-broker
description: >-
  Paper Trading Desk for NSE (rupees, IST). Records approved orders as simulated
  paper trades in a paper account that starts with ₹1,00,000, never without your
  yes, and never sends anything to a broker: no broker is connected. Shows each
  order as a plain ticket (a bracket plan shows the entry, then the stop-loss leg
  and the target leg as separate follow-up tickets, and the max loss at the
  stop), records your yes word for word, then records the simulated fill in
  trader/portfolio.json. Builds the sell ticket that closes a day trade by your
  flat-by time, and logs each trade's max loss so trader-risk can hold the daily
  limit. Use when the user says "place the order", "buy it", "paper trade it",
  "execute", "close my position", "flatten", "get me out", "how is my paper
  account", "set up paper trading", or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

The Paper Trading Desk is the only skill in this pack that records a trade, so it
is the strictest. **This pack trades on paper only: no broker is connected and
nothing is ever sent anywhere.** (The folder keeps its old name, `trader-broker`.)
A paper trade is recorded only after two gates, every time, for every order:

1. **trader-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.

One PASS from the rules, one yes from the user. No yes, no paper trade.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --filled-qty 52 --filled-price 1416.30
python3 "${CLAUDE_SKILL_DIR}/ticket.py" exits --index 1          # the stop-loss and target tickets, after the entry filled
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol RELIANCE --side sell --qty 52 --price 1426.20
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol RELIANCE --qty 52
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` sends nothing anywhere. It renders tickets, checks that a reply is a
real yes for that order, builds the exit and flatten tickets, records the
simulated fills and keeps the log in `trader/state/broker.json` (the file keeps
its old name). `--now "2026-10-19 10:15"` pins the clock (IST, no daylight
saving). Amounts are in rupees with Indian grouping (₹1,23,456.78).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `exits` and `flatten` replace it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed, failed and filled, with each entry's max loss |
| `trader/portfolio.json` | the paper account: cash and shares (cost-basis lots, no tax claims), updated by `fill` and by `result --filled-qty` |
| `trader/plan.json` | the flat-by time for `flatten` |
| `trader/config.json` | `budget` (the paper account's starting cash, default 100000), `paper` (true), `flat_by` (default "15:00") |

## The paper account (read this first)

- **It starts with ₹1,00,000** (1 lakh): the `budget` in `trader/config.json`, or
  100000 when there is no config or no `budget`. The first recorded fill creates
  `trader/portfolio.json` with that cash, and trader-budget and trader-risk read
  the same account.
- **Fills are simulated.** After the yes, record the fill at a price you can
  defend: the limit price for a limit order, or the latest quote
  (`alerts.py quote RELIANCE`) for a market order, and say it is a simulation.
  A real fill can be worse: a market order slips, a stop gaps, and a stock at its
  circuit limit may not trade at all. No charges come out of the paper cash;
  trader-budget prints an estimate of them.
- **`"paper": true` is the only mode.** With `"paper": false` in the config,
  `ticket.py`, trader-risk and trader-budget stop with "this pack trades on paper
  only". There is no live trading in this pack, and nothing here logs in to
  anything.

## Rules of the Indian market that change the tickets

The pack follows the real market's structure so that what you practise matches
what happens when you trade for real.

- **Product.** Every order from this pack is `MIS` (intraday). `CNC` is
  delivery, `NRML` is carry; they are allowed values but a day-trading pack
  does not use them.
- **MIS auto square-off.** In real trading, a broker squares off open intraday
  (MIS) positions on its own, from about 15:05 to 15:20 depending on the broker
  and the stock, and charges a fee for it. Since 3 August 2026 a stock with F&O
  contracts also stops continuous trading at 15:15 and its stop-loss orders are
  cancelled then, before the closing auction (15:15 to 15:35). So the defaults
  stop new entries at 14:30 and set `flat_by` to 15:00, well before all of that.
  They are defaults: adjust `no_entries_after` and `flat_by` in
  `trader/config.json`.
- **Whole shares only.** Notional or fractional orders do not exist in India.
  A ticket for one still renders, but trader-risk blocks it.
- **No bracket order.** A stop-loss and a target are two separate orders with
  no OCO (one cancels other) between them. See the next section.
- **Price bands.** NSE stocks have daily price bands. A stock at its lower
  circuit may not let you sell, so a stop-loss can fail to fill at all.
  At its upper circuit you may not be able to buy.
- **Tax.** Paper trades have no tax. Real intraday equity profit is speculative
  business income: talk to a chartered accountant. This pack makes no tax claims.

## The bracket plan (paper version)

trader-budget writes `"order_class": "bracket"` for an order that has a
stop-loss and a target. In India that is a **plan with three parts**, not one
order:

1. **The entry.** This is what the ticket asks yes for, and the only thing
   recorded first.
2. **The stop-loss leg.** A SELL stop order (SL-M at the stop-loss price, or SL
   when a stop-loss limit price is given), recorded **only after the entry fills**.
3. **The target leg.** A SELL limit order at the target price, recorded **only
   after the entry fills**.

Each exit leg is its own ticket: `ticket.py exits --index N` writes them to
`trader/orders.json`, they go through trader-risk (an exit is never blocked by
the entry-only rules), and each needs its own yes. Both legs carry an
`exit_group` marker, so trader-risk treats them as alternatives: only one will
fill, and neither uses up the shares for the other. **There is no automatic
OCO: when one leg fills, record that sell and skip the other.** Nothing
triggers a paper stop by itself, so watch both levels with trader-alerts. The
max loss on the ticket is at the stop-loss price; a stop-loss can fill worse in
a fast market.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket and ask: "Record this paper trade? Reply yes or no." One
   order per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", the reply was not an explicit yes for this
   order (for example "sure", "yes to all", or a yes that names a different
   stock or amount). Ask again. Never turn a reply into a yes yourself.
4. After "Recorded your yes", record the simulated fill:
   `ticket.py result --index N --status placed --filled-qty Q --filled-price P`
   (it logs the order as placed, so trader-risk's daily limits count it, and adds
   the fill to `trader/portfolio.json`). If the paper order would not have
   filled (a limit price never reached, say), record
   `--status failed --note "why"`: nothing is added to the account, and a failed
   order is not retried without a new check and a new yes.
5. The log keeps the entry's max loss for trader-risk's daily limit.
6. Once the entry filled, `ticket.py exits --index N` (add `--qty` if only part
   filled), then `/trader-risk`, then `ticket.py show` and one yes per leg,
   stop-loss leg first, as in steps 2 to 4.

## Steps: while it is open, and out before the square-off

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`). Nothing moves a paper stop for you, and a leg
  that was never recorded protects nothing.
- When the stop or the target is hit, record the exit:
  `ticket.py fill --symbol RELIANCE --side sell --qty 52 --price 1426.20`, and
  skip the other leg.
- By `flat_by` (15:00 IST by default), anything still open gets closed:
  1. Read the paper portfolio (`trader/portfolio.json`) for the shares held.
  2. `ticket.py flatten --symbol RELIANCE --qty 52` writes a market SELL (MIS,
     NSE) for those whole shares to `trader/orders.json`.
  3. `/trader-risk`. A sell that only closes what the user holds is never
     blocked by the daily limits or the entry window.
  4. `ticket.py show`, then the user's yes for that one order, as above.
  5. `ticket.py result --index 1 --status placed --filled-qty 52 --filled-price P`
     records the simulated sell and brings the cash back.

## Example: the reel

The canonical RELIANCE trade, with no `budget` in the config (so the paper
account starts with ₹1,00,000). `ticket.py show`:

```
ORDER 1 of 1, PAPER TRADE (simulated: no broker, no real money)
  BUY 52 RELIANCE (NSE, MIS), market, good for today
  stop-loss 1406.40
  target 1426.20
  max loss about ₹514.80 if the stop-loss fills at 1406.40 (it can fill worse in a fast market)
  why: opening-range breakout on RELIANCE: 52 shares, bound by your 75% position cap
  READY: trader-risk PASS, waiting for your yes
  Paper trade: nothing is sent anywhere. After your yes it is recorded and the fill is
  simulated at the price you give (ticket.py result / fill).
  This is a bracket PLAN in three parts, not one order. Only the entry above is
  asked for now. After the entry fills, the exit legs are recorded as two SEPARATE follow-up
  tickets (ticket.py exits), each with its own trader-risk check and its own yes:
    1. stop-loss leg: SELL 52 shares of RELIANCE (NSE, MIS), stop order at ₹1,406.40, good for today
    2. target leg: SELL 52 shares of RELIANCE (NSE, MIS), limit order at ₹1,426.20, good for today
  There is no automatic OCO here: when one of the two exit legs fills, record that sell
  (ticket.py fill) and skip the other leg.
  Reply "yes" to record this one paper trade, or "no" to skip it.
```

The user replies "yes". `approve` records it, word for word, with the order's max
loss, and says what to do next:

```
Recorded your yes for order 1: BUY 52 RELIANCE (NSE, MIS), market, good for today / stop-loss 1406.40 / ...
PAPER TRADE: nothing is sent to a broker. Record the simulated fill (use the limit price, or the
current price you see, as the price):
  ticket.py result --index 1 --status placed --filled-qty 52 --filled-price P (logs it as placed so the daily limits count it, and adds the fill)
```

`result ... --filled-qty 52 --filled-price 1416.30` then records the paper
trade: 52 x 1416.30 = ₹73,647.60 leaves the cash, so `trader/portfolio.json`
holds ₹26,352.40 and 52 RELIANCE shares in one cost-basis lot. `ticket.py log`:

```
2026-10-19T10:15:00+05:30  #1 approved  BUY 52 RELIANCE (NSE, MIS), ... reply "yes"
2026-10-19T10:16:00+05:30  #1 placed    BUY 52 RELIANCE (NSE, MIS), ... max loss ₹514.80 [paper]
2026-10-19T10:16:00+05:30     filled    BUY 52 RELIANCE at 1416.30
1 placed today.
```

`ticket.py exits --index 1` then writes the stop-loss leg (SELL 52, stop order at
₹1,406.40) and the target leg (SELL 52, limit order at ₹1,426.20) as two new
tickets. If the target is hit, `ticket.py fill --symbol RELIANCE --side sell
--qty 52 --price 1426.20` brings ₹74,162.40 back (cash ₹1,00,514.80) and the
stop-loss leg is simply never recorded.

## What it never does

- Never records a paper trade without both gates, and never two orders on one yes.
- Never sends anything to a broker or an exchange: there is no broker, and it
  never logs in to anything or asks for a key, a password, a PIN or a code.
- Never records orders from a scheduled or background task: nobody is there to say yes.
- Never switches `"paper"` to false: with `"paper": false` the pack refuses to run.
- Never promises a fill price: a market order and a stop fill at whatever the market gives.
- Never treats the stop-loss leg as in place until it is recorded and watched.

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
