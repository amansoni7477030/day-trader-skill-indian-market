---
name: trader-timemachine
description: >-
  Time Machine. Replays every possible outcome of an NSE day trade before you
  risk a rupee: 10,000 simulated sessions plus every past time the setup
  fired. Takes the plan from trader-exit (entry, stop, target), rebuilds the
  rest of the day 10,000 times from the stock's own 5-minute bars over the
  last 60 days, and re-runs the same rule on every past session. Prints the
  odds of target first, stop first, or neither by the close, where the price
  sits at the close, the average R, the history, and one honest verdict, then
  hands off to /trader-budget. Use when the user says "run the time machine",
  "replay this trade", "what are the odds", "simulate this trade", "monte
  carlo", "backtest this setup", "how often does this hit the target", "is
  this a coin flip", or after /trader-exit writes a plan, before
  /trader-budget.
argument-hint: "[path to plan.json, or NSE symbol]"
---

# trader-timemachine

The Time Machine sits between the plan and the money. trader-exit has set
the entry, the stop and the target. Before anything is sized or sent, this
skill asks one question: if today plays out like the stock's own last 60
days, how often does this plan hit the target first, the stop first, or
neither? It answers with 10,000 replays and with the real record of the same
rule on every past session. It is a simulation built from the stock's own
recent bars, not a prediction. Day trading loses most people money, and a
good-looking replay does not change that. This is education and tooling, not
financial advice.

All times are IST (UTC+5:30, no daylight saving), all prices are in rupees
(₹), and symbols are plain NSE symbols (`RELIANCE`, `M&M`, `BAJAJ-AUTO`).

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
`pip install curl_cffi` once: Yahoo blocks plain Python clients, so the one
request the tool makes goes through curl_cffi with Chrome's TLS fingerprint and
browser headers (urllib with the same headers is the fallback and may be
blocked).

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                    # trader/plan.json, 10,000 replays, seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write            # also save trader/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:00      # end each replay at your flat-by time
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol RELIANCE --date 2026-10-19 \
    --entry 1416.30 --stop 1406.40 --target 1426.20 --entry-time "9:35 AM"
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from           # ASCII fan chart of the saved run
```

Other flags: `--replays N`, `--seed N` (default 19), `--window N` (default 2
bars), `--tick-size 0.05` (force one price step for the stops; by default the
plan's own tick rules, else NSE's tick for the price: ₹0.10 for a stock between
₹1,000 and ₹5,000), `--offline FILE`
(a saved Yahoo chart JSON, `.json` or `.json.gz`, or a folder holding
`<SYMBOL>-60d5m.json.gz`), `--now "2026-10-19 10:15"` (IST) to pin the clock.
The replay ends at `--until`, by default the plan's flat-by (15:00), else the
end of the pack's day, 3:15 PM.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the plan from trader-exit: symbol, date, entry, entry_time / entry_bar, stop, stop_rule, target, target_r, opening range |
| Yahoo chart API | one request: 60 days of 5-minute bars (`range=60d&interval=5m`) for `SYMBOL.NS`, free and unofficial, can lag |
| `trader/timemachine.json` | with `--write`: `{plan, mc, history}` plus the verdict. `mc` holds the odds, percentiles, the fan-chart bands, 160 sample paths and a 24-bin histogram of the close |

## How it works

1. **The pool.** It keeps only FULL sessions before the plan's date: all 72
   five-minute bars, 9:15 AM to 3:15 PM IST. Since 3 August 2026 a stock with
   F&O contracts stops continuous trading at 3:15 PM and closes in an auction,
   and Yahoo's bars show it: a one-trade stub bar at 3:15, a null bar at 3:20
   and the auction bar at 3:25. Those three are ignored (a 75-bar day found not
   one full session in real RELIANCE data). Short sessions, gaps and today are
   left out. Yahoo's `range=60d` answer holds the last 60 trading sessions
   (about three months); on the real RELIANCE answer 59 of the 60 were
   complete. Every bar becomes four ratios to the bar before it: open, high,
   low and close over the previous close.
2. **One replay.** Start at the entry price. For each bar left in the day,
   pick a past bar from the same time of day (plus or minus 2 bars) at
   random, and scale it to the current price. Bar by bar, it checks the stop
   first, then the target. A bar that touches both counts as the stop.
3. **10,000 replays** give four odds. Target first. Stop first. Neither,
   which means the trade was still open at `--until` (default: the plan's
   flat-by, 3:00 PM; without one, 3:15 PM, the end of the pack's day; NSE has
   no early close on a normal day, and a special short session is only handled
   if it is listed in the script's `NSE_EARLY_CLOSE` table, which is empty
   today). Green, which means target first or above the entry at `--until`.
   R is +1 at a 1R target, -1 at the stop, and otherwise the distance from
   the entry at `--until` divided by the risk. A gap through the stop still
   counts as -1R here. A real fill can be worse (and a stock at its lower
   circuit may not let you sell at all).
4. **The history.** The plan's own rule (the same opening range, stop rule
   and target R) runs again on every one of those past sessions. That is
   what happened on those bars, not a simulation. Stops are rounded to the
   tick, away from the entry.
5. **The verdict** is one line from the numbers. It looks only at the
   replays that hit one side, and asks how often the target came first. A
   bigger target needs to win less often to break even: 1 in (1 + R). That
   is 50 % at 1R, 33 % at 2R, 25 % at 3R. Less than 5 points from break-even:
   "Close to a coin flip" at 1R, "Close to break-even" at any other R. 5 to
   15 points: "Leans toward the target" or "Leans toward the stop". 15 or
   more: "Strongly toward" one of them. So a 2R plan with target first
   24.1 % and stop first 50.5 % is close to break-even (it needs 33 % of
   the 74.6 % that hit a side, and got 32 %), not a loser.
6. **Below the verdict**, when it applies: a line when more than 40 % of the
   replays hit neither (the price at `--until` decides those), and the
   average R when its sign goes against the lean. Those can only point the
   other way because of the replays that hit neither.

**The seed.** The draws come from Python's `random.Random(seed)`, seed 19 by
default. Same plan, same data, same seed: the same numbers, on any machine.
Say the seed with every result so the user can repeat it.

**5-minute bars only.** Yahoo keeps 60 days of 5-min bars but only about 7
days of 1-min bars, too few sessions for a fair pool. It refuses, in one
plain line, when there are fewer than 20 full sessions, when the entry bar is
not in the data (wrong date, an NSE holiday, not closed yet, a future day),
or when the plan does not make sense (a stop above a buy's entry, an entry
off the 5-minute grid, an entry that does not close between 9:20 AM and
3:10 PM). A live in-progress bar is never used.

## Steps

1. Make sure `trader/plan.json` is today's plan from `/trader-exit`. If there
   is none, run that first, or pass the plan as flags.
2. Run `timemachine.py run --write`. The replay ends at the plan's flat-by
   time (15:00 by default), so "neither" means still open at the time you
   would get out; `--until` changes it. Real brokers square off intraday (MIS)
   positions from about 3:05 to 3:20 PM (it varies by broker and by stock),
   and a stock with F&O contracts stops trading continuously at 3:15 PM, so
   the default flat-by of 3:00 PM is earlier than all of that: adjust it to
   your own routine.
3. Tell the user, in this order: the four odds, the median and the 5 to 95
   range of the price at the close, the average R, the history line, then the
   verdict line and any line under it exactly as printed. Say the seed.
4. If the verdict is a coin flip or close to break-even, say so plainly. Do
   not dress it up. If the history and the replays disagree, say that too.
5. Say once that this is built from the last 60 days and is not a prediction.
   Results, board meetings, news, circuit limits and halts can do things
   those 60 days never did. Check `/trader-news` for the stock before going
   on.
6. If the user still wants the trade, hand off to `/trader-budget` to size it.

## Example: the canonical plan (synthetic data)

RELIANCE, Monday 2026-10-19. The numbers below come from the SYNTHETIC test
fixture in `tests/fixtures/trader-timemachine/` (made by
`tests/fixtures/make_timemachine_fixtures.py`), not from real market data.
The 15-minute opening range is ₹1,399.50 to ₹1,413.40. The 9:30 bar closes
above it: entry ₹1,416.30 at 9:35 AM (bar 3), stop ₹1,406.40 (the range
midpoint, 1,406.45, rounded down to the ₹0.10 tick), target ₹1,426.20 (1R, risk
₹9.90 a share).

```
RELIANCE buy, Monday 2026-10-19: entry ₹1,416.30 at 9:35 AM (bar 3), stop ₹1,406.40, target ₹1,426.20 (1R, risk ₹9.90 a share).
10,000 replays of the rest of the session, 9:35 AM to 3:00 PM (65 five-minute bars), seed 19.
Each bar is drawn whole from RELIANCE's own 5-min bars at the same time of day (+-2 bars) over its last 39 full sessions (2026-08-21 to 2026-10-16).

  target first   49.0 %
  stop first     38.9 %
  neither        12.1 %   still open at 3:00 PM
  green          55.4 %   target first, or above the entry at 3:00 PM

  Price at 3:00 PM: median ₹1,417.46, 90 % of replays between ₹1,395.65 and ₹1,439.11.
  Average R: +0.102 (R = the ₹9.90 risk: +1R at the target, -1R at the stop).

History: the same rule (15-min opening range, stop at the opening-range midpoint, 1R target) on each of those 39 past sessions fired 30 times: 15 target, 13 stop, 2 neither. Average R +0.040.

Leans toward the target: target first 49.0 % vs stop first 38.9 %.
```

At 1R the target has to come first in half of the replays that hit one
side. It did in 55.7 %, so the replays lean a little toward the target, and
the history is thin (30 trades). That is the honest answer for this setup on
this data, and it is what the user should hear before `/trader-budget` sizes
52 shares. With real data the numbers will differ, and a lean is still not a
prediction.

## Example: a real day (real data)

Yahoo's own 60-session answer for RELIANCE.NS, saved after the close on
Tue 3 November 2026
(`tests/fixtures/trader-timemachine/RELIANCE-60d5m-real.json.gz`). The pool is
real; the plan is only an illustration (RELIANCE did not break its range that
day): entry ₹1,300.00 at 9:35 AM, stop ₹1,290.00, target ₹1,320.00 (2R, risk
₹10.00 a share), flat by 3:00 PM:

```
10,000 replays of the rest of the session, 9:35 AM to 3:00 PM (65 five-minute bars), seed 19.
Each bar is drawn whole from RELIANCE's own 5-min bars at the same time of day (+-2 bars) over its last 58 full sessions (2026-08-07 to 2026-11-02).

  target first   8.0 %
  stop first     40.8 %
  neither        51.2 %   still open at 3:00 PM
  green          42.4 %   target first, or above the entry at 3:00 PM

History: the same rule (15-min opening range, stop at 0.77 % from the entry, 2R target) on each of those 58 past sessions fired 28 times: 2 target, 9 stop, 17 neither. Average R +0.014.

Strongly toward the stop: at 2R the target has to come first in 33 % of the replays that hit one side; it did in 16 % (target first 8.0 % vs stop first 40.8 %).
51.2 % of the replays hit neither by 3:00 PM; the price at 3:00 PM decides those.

Left out 1 short session(s) (special short sessions or gaps): 2026-10-15.
```

Real sessions are not all full: 2026-10-15 has 70 bars, so it is left out and
said. A 2R target is reached rarely in a quiet stock, and the verdict says so
in one line.

## Rules

- Every number comes from the run. Never round a coin flip up into an edge.
- The verdict line and the lines under it are printed by the tool. Repeat
  them, do not rewrite them. Never judge a 2R or 3R plan by target first vs
  stop first alone: the verdict already weighs the target's R.
- Simulated odds are not predictions and not promises. Say it once, plainly.
- Free Yahoo data is unofficial and can lag. Bars are 5-minute bars, checked
  bar by bar, never tick by tick and never real time.
- Always say the seed, so any run can be repeated.
- One plan per run. A changed entry, stop, target or date is a new run.
- Special sessions (Muhurat trading, an occasional Saturday session) are not
  normal sessions and are not supported; check nseindia.com for the day's
  timings.

## What it never does

It never places an order, never sizes a position, never changes the plan,
and never calls a result a sure thing. It never prints a P&L or a profit.
It never uses a bar that has not closed yet, and never mixes today's bars
into the pool.

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
