---
name: trader-alerts
description: >-
  Price Alerts. Rings when a price, a stop or the opening bell hits. Turns
  the trade plan into three alerts in one go (the stop, the target and "flat
  by" at your flat-by time), plus above / below / stop / target levels,
  percent moves from the previous close, any time of day, and the NSE
  opening bell, each ringing once so you are not spammed. Knows NSE hours
  (9:15 AM to 3:30 PM IST, with stocks that have F&O contracts leaving
  continuous trading at 3:15 PM for the closing auction) and NSE's own 2026
  trading holidays, works out IST itself, fetches free delayed quotes (Yahoo
  chart JSON through curl_cffi, NSE symbols as SYMBOL.NS) one symbol at a
  time, and writes trader/prices.json for sizing and the risk check. Use when
  the user says "alert me when", "tell me if RELIANCE hits", "watch my stop",
  "watch my trade", "remind me to close by 3:00", "is the market open", "when
  does the market open", "get me a price", or right after /trader-exit
  writes a plan.
argument-hint: "[NSE symbol] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. India has no bracket orders, and in paper
trading nothing sits at a broker at all: a stop-loss and a target are only a
plan, so these alerts are the only watchers. When one rings, the user records
the paper exit through trader-broker. They matter more here, not less: an
alert tells the user what happened and when it is time to act or to be flat.
This is education and tooling, not financial advice.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

Setup, once: `pip install curl_cffi`. Yahoo blocks plain Python clients (HTTP
429, or a connection that hangs), so every quote request goes through
curl_cffi with Chrome's TLS fingerprint and the headers a browser sends.
Without it the tool falls back to urllib with the same headers and a Chrome
User-Agent, and may be blocked.

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat by from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1406.40
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --target 1426.20
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:00 --label "flat by" --before-close
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol TCS --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE --write         # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan symbols
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset reliance-plan-stop
```

`--now "2026-10-19 15:00"` pins the clock (IST); `--offline DIR` reads saved
quote files (`yahoo-<SYMBOL>.json`, for example `yahoo-RELIANCE.json` or
`yahoo-M&M.json`) instead of the network. `--source` only accepts `yahoo`
(the default).

Symbols are plain NSE symbols, the way NSE lists them: `RELIANCE`,
`M&M`, `BAJAJ-AUTO`, `20MICRONS`. The tool adds the `.NS` suffix for Yahoo
and URL-quotes it (`M&M` is requested as `M%26M.NS`). A trailing `.NS` you
type is dropped, and an index such as `^NSEI` (Nifty 50) works for quotes.
Prices print in rupees with Indian grouping: `₹1,23,456.78`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes, merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the symbols held, for `quote --holdings` |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the IST clock passed `at` ("15:00") on a trading day | once per trading day |
| `opening_bell` | the NSE is open | once per trading day |

A rule with `"date"` is watched on that IST date only; `--from-plan` rules
carry the plan's date, so yesterday's stop never rings today. A `time` rule
with `"before_close": true` moves earlier by the amount the close moved on a
special short session (computed from the session's close against 15:15, never
a fixed number of hours). NSE has no early close on a normal day, so the table
of short sessions is empty today and a "flat by" alert rings at its set time.

## Quotes

- `yahoo` (the only source): `query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS`,
  an unofficial free JSON endpoint, one request per symbol, half a second
  apart. On a 429 it waits and tries once more; on a 429 or a 5xx after that
  it tries `query1`. Requests go through curl_cffi (Chrome's TLS fingerprint)
  with the headers a browser sends (`Accept`, `Accept-Language`, `Referer:
  https://finance.yahoo.com/`); without curl_cffi the urllib fallback sends the
  same headers and a Chrome User-Agent.
- Free quotes are unofficial, delayed and can lag. They are fine for alerts
  and for checking a size, not for timing an order. A real fill could be at
  another price. Each price in `trader/prices.json` keeps its quote time, and
  trader-risk only uses one under 15 minutes old.
- If Yahoo is down, read the price from your own broker app or NSE's website
  and write it into `trader/prices.json` as
  `{"as_of": "...Z", "prices": {"RELIANCE": 1419.80}}`.

## The NSE clock

Regular session 9:15 AM to 3:30 PM IST, Monday to Friday, after a pre-open
session (9:00 to 9:15, not traded by this pack). Since 3 August 2026 NSE's
Closing Auction Session covers every stock with F&O contracts: their
continuous trading ends at 3:15 PM, stop-loss orders are cancelled then, and
the closing price comes from an auction (3:15 to 3:35 PM). Other stocks trade
to 3:30 PM. This pack's trading day therefore ends at 3:15 PM: from then to
3:30 PM `clock` still says NSE OPEN, but says the pack's trading day is over
(`trading_open` is false in the JSON). IST is UTC+5:30 all year, with no
daylight saving, so the tool needs no time zone files and ignores the
computer's own zone.

Trading holidays in 2026 (equity segment): NSE's own list, 20 days, read from
nseindia.com's holiday master (Market Timings and Holidays; verify it there
before relying on a date). Four of them fall on a weekend, and the tool still
names them:

- Jan 15 (Municipal Corporation Election - Maharashtra), Jan 26 (Republic
  Day), Feb 15 (Mahashivratri, a Sunday), Mar 3 (Holi), Mar 21 (Id-Ul-Fitr,
  a Saturday), Mar 26 (Shri Ram Navami), Mar 31 (Shri Mahavir Jayanti), Apr 3
  (Good Friday), Apr 14 (Dr. Baba Saheb Ambedkar Jayanti), May 1 (Maharashtra
  Day), May 28 (Bakri Id), Jun 26 (Muharram), Aug 15 (Independence Day, a
  Saturday), Sep 14 (Ganesh Chaturthi), Oct 2 (Mahatma Gandhi Jayanti), Oct 20
  (Dussehra), Nov 8 (Diwali Laxmi Pujan, a Sunday), Nov 10
  (Diwali-Balipratipada), Nov 24 (Prakash Gurpurb Sri Guru Nanak Dev), Dec 25
  (Christmas).
- Muhurat trading (Sunday 8 Nov 2026, the Diwali Laxmi Pujan evening) and
  occasional Saturday sessions are special sessions announced by circular
  (Muhurat: NSE/CMTR/76941). This tool treats those dates as not a normal
  session (closed), so check nseindia.com for their timings.
- The 2027 list is not published yet, so for any year other than 2026 the
  clock says "Holiday list covers 2026 only: check nseindia.com".

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm
   the three alerts back to the user: the stop, the target and the flat-by
   time. The default flat-by is 3:00 PM, well ahead of the end of continuous
   trading for stocks with F&O contracts (3:15 PM) and of the automatic
   intraday (MIS) square-off that real brokers run, which starts around 3:05 PM
   and goes on to about 3:20 PM depending on the broker and the stock, and
   costs a fee. Treat 3:00 as a default and adjust it to your own routine.
2. Run `check` while the trade is open: every few minutes from a Claude
   scheduled task or `/loop`. Each run fetches only the symbols in today's
   rules and rings each alert once. A check reads the latest delayed quote;
   it is not a live feed, and a price can pass a level between two checks.
3. Tell the user what rang in one line each. A stop alert means the price
   reached the stop level: in paper trading nothing sold by itself, so check
   the paper portfolio and say plainly that the position is still open until
   the exit is recorded. A target alert means the same for the target. Either
   way the open position needs an exit ticket (trader-broker `flatten`, or the
   stop and target exit tickets), then trader-risk, then the user's yes, and
   when one exit fills the other leg is skipped (there is no automatic OCO).
   "Flat by" means close anything still open the same way. Never act for them.
4. A stock at its lower circuit may not let you sell at all, so a stop can fail
   entirely. An alert that rings at the circuit level is a reason to check the
   live quote at once: in real trading the exit may not fill.

## Example: the canonical plan

RELIANCE on Monday 2026-10-19: entry ₹1,416.30 at 9:35 AM, stop ₹1,406.40,
target ₹1,426.20, flat by 15:00. `add --from-plan` writes
`reliance-plan-stop`, `reliance-plan-target` and `reliance-flat-by` for that
day. A `check` at 10:53 reads the 10:52 quote (a SYNTHETIC test fixture, not
real data), ₹1,419.80, between the stop and the target:

```
NSE OPEN (2026-10-19 10:53 IST, regular session).
Nothing hit. 3 rule(s) watched today, RELIANCE ₹1,419.80.
```

At 3:00 PM:

```
ALERT Flat by: it is 15:00 IST (set for 15:00), continuous trading of F&O stocks ends at 15:15. If RELIANCE is still
open, close it: trader-broker flatten --symbol RELIANCE builds the sell ticket, then trader-risk and your yes.
```

It rings once. Tuesday 2026-10-20 is the Dussehra holiday: the NSE is closed
and nothing rings. On Wednesday the plan's three alerts are no longer
watched; with `add --opening-bell`, the bell rings at 9:15.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Free quotes are delayed and unofficial.
- Never say a stop or target is "in place": in paper trading it is only a plan
  and an alert until the user records the exit.
- Confirm every new rule back to the user: symbol, type, level, day.

## What it never does

It never places, cancels or changes an order, never claims a quote is
live, and never predicts where a price is going.

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
