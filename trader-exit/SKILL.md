---
name: trader-exit
description: >-
  Exit Planner for NSE stocks (India, IST, rupees). Sets your stop and your
  target before you enter, so you know when to get out. Reads the entry from
  trader-entry (or a price you give), puts the stop at the opening-range
  midpoint by default (or the range low, your price, a percent, or a multiple
  of the ATR), rounds it down to the NSE tick for the price (₹0.10 for a
  stock between ₹1,000 and ₹5,000, ₹0.05 from ₹250 to ₹1,000, and so on), and sets the target at
  2R (or 1R, or your price). Refuses a stop at or above the entry, a target at
  or below it, or a stop too tight to survive the spread, and says why. Writes
  trader/plan.json for the Time Machine, the Budget Manager and Price Alerts.
  Long side only. Use when the user says "where's my stop", "set my stop and
  target", "plan the exit", "when do I get out", "stop loss", "what's my R",
  "risk reward", or right after /trader-entry triggers, before
  /trader-timemachine.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|1R|price:X]"
---

# trader-exit

The Exit Planner decides how a trade ends before it starts. The stop is where
the setup has failed. The target is where you take what you came for. Both
are set BEFORE the entry, written down and handed on, so nobody has to decide
anything in the middle of a fast move. A stop is a plan, not a guarantee: a
gap or a fast market can fill past it. Day trading loses most people money,
and a neat plan does not change that.

It works on NSE equities, in Indian Standard Time (IST, UTC+5:30) and rupees.
Use the plain NSE symbol (`RELIANCE`, `M&M`, `BAJAJ-AUTO`); the tool adds
Yahoo's `.NS` suffix only when it fetches bars for an ATR stop.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
`pip install curl_cffi` once: for an `atr:K` stop the tool fetches Yahoo's bars,
and Yahoo blocks plain Python clients, so it goes through curl_cffi with
Chrome's TLS fingerprint and browser headers (urllib with the same headers is
the fallback and may be blocked).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                          # trader/entry.json: midpoint stop, 2R target
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5           # needs the session's bars (Yahoo or --offline)
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol RELIANCE --entry 1416.30 --or-high 1413.40 --or-low 1399.50
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                          # the current trader/plan.json
```

Other flags: `--flat-by 15:00`, `--tick-size 0.05`, `--json`, `--now "2026-10-19
10:15"` (IST), `--entry PRICE` with a waiting entry.json to plan ahead,
`--setup level`, `--offline FILE` (a saved Yahoo chart JSON for an ATR stop).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | the trigger from trader-entry: symbol, entry price, time, bar, opening range |
| `trader/config.json` | `flat_by` (15:00), `tick_size` (empty: NSE's tick for each price) and `min_reward_risk` (1.0: a note here, trader-risk enforces it) |
| Yahoo chart API | only for an `atr:K` stop: the session's bars up to the entry bar, free and unofficial |
| `trader/plan.json` | with `--write`: entry, stop and its rule, target and its R, risk a share, reward:risk, flat by, tick size. trader-timemachine, trader-budget and trader-alerts read it |

## Stops and targets

| stop | where it goes |
| --- | --- |
| `or-mid` | the opening-range midpoint (the default for an opening-range breakout) |
| `or-low` | the opening-range low: wider, so fewer shares for the same max loss |
| `price:X` | your price |
| `pct:X` | X percent under the entry |
| `atr:K` | K times the 14-bar ATR under the entry, on the session's bars up to the entry bar. With fewer than 14 bars by then it averages all of them and says so |

A level setup has no opening range to lean on, so it needs a stop you name.

- **Rounding to the tick.** NSE's tick depends on the price (circular
  NSE/CMTR/67133, in force since 15 April 2025 and reviewed every month on the
  last closing price): ₹0.01 under ₹250, ₹0.05 from ₹250 to ₹1,000, ₹0.10
  above ₹1,000 up to ₹5,000, ₹0.50 up to ₹10,000, ₹1.00 up to ₹20,000 and ₹5.00
  above. The tool takes the tick from the entry price (the target from its own
  price). Near a band edge your broker may still be on the neighbouring tick
  for a few weeks: when an order window shows another tick, give it
  (`tick_size` in the config, or `--tick-size`; the plan then keeps it). The
  stop rounds AWAY from the entry to the tick (down, for a long). Risk = entry
  minus stop. Target = entry + R x risk, rounded half up to the tick. `2R` by
  default; `1R`, `1.5R` or `price:X` also work.
- **Refused, with the reason.** A stop at or above the entry. A target at or
  below it. A risk under 0.05% of the price: too tight to survive the
  spread, normal wiggle would stop you out. An entry.json that has not
  triggered (unless you give `--entry`). A short: v2 plans longs only.
- **Flat by.** 15:00 IST by default (or `flat_by` in the config). Out by
  then, win or lose, no overnight hold. This is a default, not a rule of the
  exchange: since 3 August 2026 a stock with F&O contracts stops continuous
  trading at 15:15 and its stop-loss orders are cancelled then, before the
  closing auction (15:15 to 15:35), and real brokers square-off intraday (MIS)
  positions automatically, from about 15:05 to 15:20 depending on the broker
  and the stock, and charge a fee for it. So
  "flat by" sits well before all of that: a flat-by time of 15:15 or later is
  refused on a normal day, and one from 15:05 on gets a warning. Adjust it to
  your own routine. If the
  NSE ever runs a short session, it moves up by the amount the close moved.
  An entry at or after the flat-by time is refused.
- **The fine print on stops.** A stock has a daily price band (2%, 5%, 10%,
  20% or none). At its lower circuit you may not be able to sell at all, so a
  stop can fail entirely; at its upper circuit you may not be able to buy.

## Steps

1. Run it right after `/trader-entry` triggers. Before that, `--entry PRICE`
   plans ahead at the level you expect.
2. Use the default stop for an opening-range breakout unless the user names
   another. A level setup needs one: ask.
3. Run `exit.py plan --write`. Read back the entry, the stop and its rule,
   the target and its rule, the risk a share, reward:risk and flat by.
4. If it refuses, say the reason and the fix (a wider stop, a farther
   target). Never loosen anything on your own.
5. Say once that the stop is a plan, not a guarantee.
6. Hand off to `/trader-timemachine` to replay the plan, then `/trader-budget`
   to size it to the max loss per trade. Offer `/trader-alerts` for the stop,
   the target and the flat-by time. Claude can keep `entry.py watch` running
   in the background or on a `/loop` until a setup triggers, then come here.

## Example: RELIANCE on 19 October 2026

Synthetic example data, not a real market day. RELIANCE, Monday 2026-10-19.
The entry is ₹1,416.30 at 9:35 AM (the 9:30 bar, bar 3) and the opening range
is ₹1,399.50 to ₹1,413.40. `exit.py plan --stop or-mid --target 1R --write`:

```
Exit plan: RELIANCE long, Mon 2026-10-19, opening-range breakout on 5-minute bars
  Entry         ₹1,416.30 at 9:35 AM (bar 3)
  Stop          ₹1,406.40  opening-range midpoint: (₹1,413.40 + ₹1,399.50) / 2 = ₹1,406.45, rounded down to the tick
  Target        ₹1,426.20  1R (one times the risk)
  Risk a share  ₹9.90 (0.70% of the entry)
  Reward:risk   1.0
  Flat by       15:00 IST, win or lose: no overnight hold
```

The midpoint 1,406.45 sits exactly between two ticks of the ₹0.10 tick that a
stock between ₹1,000 and ₹5,000 trades on, so the stop rounds down (away from
the entry) to ₹1,406.40. The default 2R target would be 1,416.30 + 2 x 9.90 =
₹1,436.10. trader-budget then sizes it: with a ₹1,000 max loss per trade, the
loss alone would allow floor(1000 / 9.90) = 101 shares, and the position cap
and cash limits cut that down.

A stop only 70 paise under the entry (`--stop price:1415.60`) is refused: it
is under 0.05% of the price (₹0.71 on ₹1,416.30), too tight to survive the
spread. If your broker's order window still shows the old ₹0.05 tick for the
stock, pass `--tick-size 0.05`: the midpoint stop is then ₹1,406.45 and the 1R
target ₹1,426.15.

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- Plain numbers only: entry, stop, target, risk a share, reward:risk. It
  never prints a P&L or a return.
- Nothing here promises winning trades or money. A 1R target is not a
  prediction that the price gets there.
- Long side only. v2 plans longs only. Intraday short selling exists in India
  (MIS), but this pack does not plan it.
- Transaction costs (STT, brokerage, exchange charges, GST, stamp duty) are
  not in the plan. trader-budget can estimate them; verify with your broker's
  charge calculator. Talk to a chartered accountant about how intraday income
  is taxed.

## What it never does

It never places an order, never sizes a position, never widens a stop to
make a plan pass, and never calls a stop a guarantee.

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
