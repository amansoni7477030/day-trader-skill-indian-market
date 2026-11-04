---
name: trader-entry
description: >-
  Entry Timer for NSE stocks (India, IST, rupees). Watches a setup bar by bar
  and tells you the moment to get in. Reads free Yahoo 1-minute or 5-minute
  bars, builds the opening range (the first 15 minutes of the session, 9:15 to
  9:30 IST, by default) and calls the entry on the first completed bar that
  CLOSES above the range high, or above a level you give it (like a 20-day
  high from the scanner), with an optional volume check. Says waiting and how
  far, triggered with the time and the price, expired, or not open yet, and
  writes trader/entry.json for /trader-exit. Long side only. It never places
  an order. Use when the user says "tell me when to get in", "watch
  RELIANCE", "has it broken out", "did it break the opening range", "entry
  signal", "time my entry", "check my watchlist", or after /trader-scanner
  and /trader-news, before /trader-exit.
argument-hint: "[SYMBOL] [--setup orb|level] [--interval 5m|1m]"
---

# trader-entry

The Entry Timer turns a setup into one moment: the bar that confirms it. It
reads bars, 1-minute or 5-minute, and waits for each one to finish. A price
that pokes above the high in the middle of a bar is not an entry. A bar that
CLOSES above it is. Then it says the time and the price, writes them down for
the Exit Planner, and stops. It never buys anything. Day trading loses most
people money, and a clean signal does not change that.

It works on NSE equities, in Indian Standard Time (IST, UTC+5:30, no daylight
saving) and rupees. Use the plain NSE symbol: `RELIANCE`, `M&M`,
`BAJAJ-AUTO`. The tool adds Yahoo's `.NS` suffix for the request and prints
the plain symbol back.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

Setup, once: `pip install curl_cffi`. Yahoo blocks plain Python clients (HTTP
429, or a connection that hangs), so every request goes through curl_cffi with
Chrome's TLS fingerprint and the headers a browser sends. Without it the tool
falls back to urllib with the same headers and a Chrome User-Agent, and may be
blocked.

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE                  # 15-minute opening range, 5-minute bars
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --write          # also save trader/entry.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --setup level --level 1450.50
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --interval 1m --min-volume-x 1.5
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch RELIANCE --until 14:30    # one line per closed bar, stops on the trigger
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                       # every hit in trader/watchlist.json
```

Other flags: `--or-minutes N` (default 15), `--json`, `--offline FILE` (a
saved Yahoo chart JSON, `.json` or `.json.gz`, or a folder of
`<SYMBOL>.json`), `--now "2026-10-19 10:15"` (IST) to replay a day. `watch`
with `--now` or `--offline` runs on a simulated clock and replays the day bar
by bar without waiting.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | the scanner's hits (`hits[].symbol`), for `watchlist` |
| `trader/config.json` | `no_entries_before` (09:30) and `no_entries_after` (14:30), IST. Optional |
| Yahoo chart API | today's bars, `range=1d&interval=5m` (or `1m`), symbol `RELIANCE.NS`: free, unofficial, can lag |
| `trader/entry.json` | with `check --write`, or when `watch` triggers: symbol (plain NSE), setup, status, time, bar, price, level, opening range |

## The rule

- **The session.** The NSE trades 9:15 to 15:30 IST, Monday to Friday. Since
  3 August 2026 a stock with F&O contracts stops continuous trading at 15:15,
  its stop-loss orders are cancelled then, and it closes in an auction (15:15
  to 15:35). Yahoo's 5-minute bars show it: a one-trade stub bar at 15:15, no
  bar at 15:20 and the auction at 15:25. This pack's day is therefore 9:15 to
  15:15, 72 five-minute bars, and it never reads a bar from 15:15 on: the
  auction print is not an entry. The 9:00 to 9:15 pre-open auction is not
  traded by this pack. Special sessions (for example Muhurat trading) are not
  supported.
- **Opening range (orb).** The high and low of the first 15 minutes: the
  9:15, 9:20 and 9:25 five-minute bars, or fifteen 1-minute bars. The range
  is set at 9:30. The level to beat is the range high.
- **Level.** A price you give, like a 20-day high from `/trader-scanner`.
- **The trigger** is the first completed bar after the range that CLOSES
  above the level. A wick above is not a trigger. A close exactly at the
  level is not above it.
- **The time** is that bar's close time (the 9:30 bar closes at 9:35 AM).
  The price is its close, which is already on the NSE tick for its level
  (NSE's tick depends on the price: ₹0.05 from ₹250 to ₹1,000, ₹0.10 above
  ₹1,000 up to ₹5,000, and so on), so it is not rounded again.
- **Only closed bars count.** A bar is closed when the clock passes its start
  plus 5 (or 1) minutes and Yahoo has caught up to that moment (it waits a
  minute at most). Yahoo's live tick, like 10:49:45, is never a bar.
- **Volume, if you want it.** `--min-volume-x 1.5` also needs the trigger
  bar's volume at 1.5 times the range bars' average. The 9:15 bar holds the
  opening rush, so 1.0 is already strict.
- **Time limits.** No entries before 9:30 or after 14:30 IST (config). These
  are defaults: real brokers square off intraday (MIS) positions automatically,
  from about 15:05 (stocks in the closing auction) to 15:20 depending on the
  broker and the stock, and charge a fee for it. So entries stop at 14:30 and
  the pack's flat-by time is 15:00. Set `no_entries_after` to suit you. If the
  NSE ever runs a short session, the cutoff moves up by the amount the close
  moved.
- **Holidays.** Weekends and NSE trading holidays: the market is closed. The
  built-in list is NSE's own equity-segment list for 2026 (20 days, four of
  them on a weekend; Diwali Laxmi Pujan on Sunday 8 November is a Muhurat
  evening session, which is not supported). Verify it against nseindia.com
  (Market Timings & Holidays). For any other year the tool says so and asks you
  to check.
- **What it says.** Waiting (and how far the last close is from the level),
  triggered, expired, or no data. Before 9:15: not open yet. Before 9:30: the
  range is still forming.

## Steps

1. Get the symbol from the user, or run `entry.py watchlist` after
   `/trader-scanner`. Check `/trader-news` for it first.
2. Run `entry.py check SYMBOL`. Tell the user the status in one line:
   waiting and how far, triggered at what time and price, or expired.
3. To wait for it, Claude can run `entry.py watch SYMBOL --until 14:30` in
   the background, or `entry.py check SYMBOL --write` on a `/loop` every 5
   minutes. `watch` wakes just after each bar closes, prints one line per
   bar, and exits 0 on the trigger (it writes `trader/entry.json`) or 1 when
   the setup expires. Keep the computer awake while it watches: a sleeping
   laptop pauses it, and it catches up, late, when it wakes.
4. When it triggers, say the time and the price, and that nothing was placed.
   If the trigger is more than a bar old, say that too: a late entry is a
   different trade.
5. Hand off to `/trader-exit` to set the stop and the target BEFORE anything
   is bought.

## Example: RELIANCE on 19 October 2026

Synthetic example data, not a real market day: RELIANCE, Monday 2026-10-19,
5-minute bars. The opening range is ₹1,399.50 to ₹1,413.40. At 9:34 the 9:30
bar is still open, so it waits:

```
WAITING  last close ₹1,408.60 (9:25 bar), ₹4.80 under the range high ₹1,413.40 (-0.34%).
The 9:30 bar is still forming (₹1,416.30 so far, closes 9:35 AM); it does not count until it closes.
```

At 9:35 the bar has closed:

```
RELIANCE  Mon 2026-10-19 09:35 IST  opening-range breakout (15-minute range) on 5-minute bars (Yahoo, free, can lag)
Opening range 9:15-9:30: high ₹1,413.40, low ₹1,399.50 (3 of 3 bars).
TRIGGERED 9:35 AM at ₹1,416.30: the 9:30 bar (bar 3) closed above the range high ₹1,413.40 (+0.21%).
This is the moment to get in, if you take the trade. Nothing was placed.
Next: set the stop and target BEFORE you buy: /trader-exit (exit.py plan --from trader/entry.json).
```

With `--min-volume-x 1` the 9:30 bar is too thin (230,000 shares against the
range bars' average of 283,333, which is 0.81x) and it keeps waiting:

```
WAITING  the 9:30 bar closed above at ₹1,416.30 but on 0.81x the range's volume, under your 1.00x.
```

Tuesday 20 October 2026 is the Dussehra holiday:

```
MARKET CLOSED  the NSE is closed today (NSE holiday: Dussehra).
Next open: 2026-10-21 09:15 IST.
```

## Example: a real day, RELIANCE on 3 November 2026

Yahoo's own 5-minute chart, saved after the close
(`tests/fixtures/trader-entry/RELIANCE-1d5m-real-2026-11-03.json`). The range
was ₹1,284.30 to ₹1,306.60 and no 5-minute bar from 9:30 to 15:10 closed above
the high. At 9:35:

```
WAITING  last close ₹1,291.30 (9:30 bar), ₹15.30 under the range high ₹1,306.60 (-1.17%).
```

At 14:31, after the 14:30 cutoff:

```
EXPIRED  no close above the range high ₹1,306.60 by 14:30, the last time for a new entry.
No entry on this setup today. Nothing was placed.
```

Yahoo's chart for that day also holds the closing auction: a one-trade stub at
15:15, no bar at 15:20, and a 15:25 bar that closed at ₹1,307.90, above the
range high. A tool that read it would call a breakout at 3:30 PM, after the
market's continuous session. This one reads 9:15 to 15:10 only, so the day
stays EXPIRED.

## Rules

- Bar by bar, on free Yahoo 1-minute or 5-minute bars. They are unofficial
  and can lag. Say so when a minute matters.
- A signal is information, not an order, and not a promise. Nothing here
  promises winning trades or money.
- Long side only. v2 plans longs only. Intraday short selling exists in India
  (MIS), but this pack does not plan it. If the user wants a short, say that.
- One trigger per setup per day: the first close above. Later closes do not
  reset it.
- Never call an entry early because a bar looks like it will close above.
- A stock near its daily price band (circuit limit) may not let you buy at
  the upper circuit or sell at the lower one. A trigger there is not tradable
  the way it looks.
- This pack never sends an order, so SEBI's retail algo rules (in force since
  1 April 2026, for orders a program sends to a broker) do not apply to it.
  If you ever connect a real broker, read them first.

## What it never does

It never places an order, never sizes a position, never sets the stop (that
is `/trader-exit`), never moves the level after the fact, and never counts a
bar that has not closed. It never runs `/trader-broker` itself: it reports the
trigger and stops.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- An order is placed only when trader-risk passed that exact order today. A BLOCK is final: fix the order
  or your rules, never the check.
- Long only, no F&O, margin, short selling or leverage unless the config allows it. An MIS (intraday)
  order is leverage, so the pack counts it as margin when it costs more than the cash in the account.
- No logins, API keys or passwords are used, and none belong in the chat.
- Intraday gains and losses have tax consequences in India: talk to a chartered accountant.
