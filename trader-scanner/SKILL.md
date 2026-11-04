---
name: trader-scanner
description: >-
  Chart Scanner for the Indian market (NSE). Scans thousands of charts for
  setups: opening-range breakouts, flags, oversold bounces. Builds the list of
  NSE stocks (series EQ from NSE's EQUITY_L.csv, or a Nifty 50 / 100 / 200 /
  500 list), runs a pre-market scan of daily closes (20-day closing-high
  breakout, bull flag, reclaimed the 20-day average, oversold, oversold bounce)
  and an intraday opening-range breakout scan on 5-minute bars (range 9:15 to
  9:30 AM IST), then writes trader/watchlist.json for trader-news and
  trader-entry. Free Yahoo data (.NS symbols), bar by bar. Use when the user
  says "scan the market", "scan NSE", "scan the Nifty 500", "find me setups",
  "what is breaking out", "opening range breakouts", "scan for bull flags",
  "oversold stocks", "pre-market scan", "build my watchlist", "what should I
  look at today", or at the start of a trading day before /trader-news.
argument-hint: "[universe | daily | orb] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner is the first step of a day trade. It reads thousands of
free charts and lists the ones that match a rule. That is all it does. A scan
is a list of charts that match a rule, not a list of stocks to buy, and a
setup on a chart is not a prediction. Most people who day trade lose money;
the scanner does not change that. It only saves you from flipping through
2,000 charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
All times are Indian Standard Time (IST, UTC+5:30, no daylight saving). The NSE
session is 9:15 AM to 3:30 PM, Monday to Friday. Since 3 August 2026 the stocks
with F&O contracts (the most liquid ones) stop continuous trading at 3:15 PM and
close in an auction, so this pack's trading day is 9:15 AM to 3:15 PM: 72
five-minute bars. The scanner never reads a bar from 3:15 PM on.

Setup, once: `pip install curl_cffi`. NSE and Yahoo block plain Python clients
(the connection hangs or answers HTTP 403 / 429), so every request goes through
curl_cffi with Chrome's TLS fingerprint and the headers a browser sends. Without
it the scanner falls back to urllib with the same headers and a Chrome
User-Agent, and may be blocked.

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                       # -> trader/universe.txt (every NSE EQ stock)
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --index nifty500      # or a Nifty list: nifty50, nifty100, nifty200, nifty500
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                          # before the open -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols "RELIANCE,TCS,M&M"  # or --watchlist FILE, --limit 200
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                            # after 9:35 AM IST -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15                 # cut the clock at 10:15 AM IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
```

(In a shell, always quote a list that contains `&`, as above.)

Flags: `--min-price 50` (rupees) and `--min-volume` (daily 5,00,000 shares, orb
0), `--or-minutes 15`, `--max-confirm 200` (orb stage 2),
`--now "2026-10-19 10:15"` (IST) to pin the clock, `--until HH:MM` (no new
entries after; default the config's `no_entries_after`, else 14:30),
`--offline PATH` to read saved Yahoo files, `--no-write`, `--show N`, `--json`.
Exit code 0 when the scan found something, 1 when it found nothing or could
not run (the reason is one plain line). Money prints in rupees with Indian
digit grouping (₹1,23,456.78).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line (plain NSE symbols: `RELIANCE`, `M&M`, `BAJAJ-AUTO`), read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 14:30) |

Files use the plain NSE symbol. Yahoo wants the `.NS` suffix (`RELIANCE.NS`);
the scanner adds it on the way out and strips it on the way back, and
URL-quotes the symbol (`M&M` is asked for as `M%26M.NS`). It never replaces a
`.` or `-` in a symbol.

## The universe

`universe` downloads NSE's list of listed equities, `EQUITY_L.csv`, from
`nsearchives.nseindia.com/content/equities/` (a plain GET, no cookies, no
login, through curl_cffi) and keeps a row when:

1. Its series is `EQ`, the normal segment where intraday trading is allowed.
   Every other series is dropped and counted by series. `BE` and `BZ` are
   trade-for-trade or restricted segments: no intraday trading there.
2. Its symbol looks like an NSE symbol: letters, digits, `&` and `-`, 1 to 20
   characters (`M&M`, `BAJAJ-AUTO`, `20MICRONS`, `J&KBANK`).

Header names and company names are trimmed (the NSE header has leading spaces
such as ` SERIES`). In the copy of the file read on 4 November 2026, 2,623
rows split into 2,391 `EQ`, 199 `BE` and 33 `BZ`, so the universe is about
2,390 stocks. NSE can change this file any day: run `universe` and read the
counts it prints.

`universe --index nifty50|nifty100|nifty200|nifty500` reads NSE's
`ind_<index>list.csv` instead (columns `Company Name, Industry, Symbol, Series,
ISIN Code`) with the same rules and the same `SYMBOL|Name` output. A Nifty list
is a good start: far fewer charts, all liquid, and a full scan takes minutes
instead of an hour. Offline, `--offline DIR` wants `EQUITY_L.csv` (and the
`ind_nifty*list.csv` file for `--index`) in the folder.

## The daily scan (before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request, so about 2,390
symbols take about 120 requests (with the polite 0.3 s pause that is roughly a
minute of waiting plus the network time: read the printed counts, not a
promise). Liquidity comes from the spark meta: last price at least
`--min-price` (₹50) and the session's volume at least `--min-volume`
(5,00,000 shares). Run it before the open or after the close: during the
session the volume is the day's so far, and today's daily bar is left out
until 4:00 PM IST (the post-close session ends then and the closing price is
final; the stocks in the closing auction settle theirs between 3:15 and 3:35 PM).

Yahoo often leaves the close of the newest finished session empty, even hours
after the closing auction (seen on real answers: the bar has its high, low and
volume but a null close). Dropping that bar would make a pre-market scan one
session old, so when the quote's time is that same day after 3:15 PM the
scanner uses the quote's last price as that day's close and says so ("Yahoo
left the newest daily close empty for 7 of those charts ..."). Before 4:00 PM
the bar is not final and stays out.

The setups are computed from daily closes only (spark has no highs or lows):

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

A closing-high breakout fires more often than an intraday-high one. Hits are
listed most traded first (price times volume).

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 9:15,
9:20 and 9:25 five-minute bars, so the range is set at 9:30 AM. A hit is the
first completed 5-minute bar to CLOSE above the range high, at that bar's
close time. The first bar after the range is the 9:30 bar, which closes at
9:35 AM, so 9:35 AM is the earliest useful run for a 15-minute range.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after the range closed at
   or above the highest close of the range. That is a necessary condition
   (the range high is at least every range close), so no breakout is lost.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one
   request each. Range high = the highest high of the range bars, range low =
   the lowest low. On a strong morning most charts are candidates, so stage 2
   checks at most `--max-confirm` (200) of them, the most traded first (price
   times volume so far), and prints PARTIAL with the count it skipped.
   `--max-confirm 0` checks them all. For a fast answer, scan a Nifty list, the
   daily watchlist, or a list of names you trade (`--watchlist`, `--symbols`).

A chart is usable only when every 5-minute bar from 9:15 to the cutoff is
there; a missing bar means a halt, no trades, or a gap in Yahoo's data (real
answers have them: on 3 November 2026 ITC lacked its 9:30, 11:00 and 12:35 bars,
M&M its 11:20 bar and BAJAJ-AUTO its 2:00 PM bar). A skipped chart is counted:
"2 charts skipped for a missing 5-minute bar between 9:15 and 10:00 AM". Null
closes are dropped, a bar off the 5-minute grid (the live tick, like 11:13:07)
is dropped, and a bar counts only once it is complete (start + 5 minutes). Bars
from 3:15 PM on are never read: F&O stocks are in the closing auction then, and
the auction print is not a breakout (RELIANCE's range on 3 November 2026 was
1,284.30 to 1,306.60; no bar to 3:10 PM closed above it, but the 3:25 auction
closed at 1,307.90). Before 9:35 AM, on a weekend or holiday, after 3:15 PM,
or after `no_entries_after` (an expired setup) it prints one plain line and
writes nothing.

Why 14:30: real brokers square off intraday (MIS) positions on their own, from
about 3:05 PM (stocks in the closing auction) to 3:20 PM depending on the
broker, and charge a fee for it. A stock with
F&O contracts stops trading continuously at 3:15 PM and its stop-loss orders
are cancelled then. So entries stop at 14:30, leaving time to manage the trade,
and the pack's flat-by time is 15:00. 14:30 is a default: set `no_entries_after`
in `trader/config.json` to suit you.

## The calendar

NSE's trading holidays for 2026 are in `NSE_HOLIDAYS` at the top of `scan.py`:
the 20 days of NSE's own equity-segment list (nseindia.com, Market Timings &
Holidays, read on 3 November 2026), Republic Day, Holi, Good Friday, Dussehra on
20 Oct, Diwali-Balipratipada on 10 Nov, and so on. Four of them fall on a
weekend (Mahashivratri Sun 15 Feb, Id-Ul-Fitr Sat 21 Mar, Independence Day Sat
15 Aug, Diwali Laxmi Pujan Sun 8 Nov), and the list is read before the weekend
check so such a day keeps its name. Diwali Laxmi Pujan is the Muhurat trading
day: a special evening session, not a normal one, so the scanner treats it as
closed (a special Saturday session, if NSE holds one, is closed to it too).
The list covers 2026 only. The 2027 list is not published yet, so for
any other year the orb scan stops and says "the holiday list in this tool
covers 2026 only: check nseindia.com". There are no normal early closes
(`NSE_EARLY_CLOSE` is empty and reserved).

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag. Bars are
  5-minute bars, bar by bar, never tick by tick. Yahoo keeps about 60 sessions
  of 5-minute bars. query2 first, query1 on a 429 or 5xx, one retry after a
  5-second wait on a 429. Requests go through curl_cffi (Chrome's TLS
  fingerprint) with browser headers: Yahoo and NSE answer plain Python clients
  with 429 or 403.
- Partial results are said as partial: "Fetched 2,310 of 2,391 charts (81
  failed or had no data)". Never round a partial scan up to "the whole market".
- NSE stocks have daily price bands (2 %, 5 %, 10 %, 20 % or none). A stock
  locked at its lower circuit may not let you sell, and at its upper circuit
  you may not be able to buy. A chart that is pinned at a band edge is a
  warning, not a setup.
- NSE only. BSE-only stocks and SME names are not in this list.

## Steps

1. Once a week or so: `scan.py universe` (or `--index nifty500`).
2. Before the open: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits) and the top few by setup.
3. After 9:35 AM IST (10:00 or 10:15 are common times to look): `scan.py orb`.
   Tell the user how many usable charts broke their range and when, and that
   a later hit is closer to the price now.
4. The user picks one chart. Hand it on: `/trader-news SYMBOL` (what is moving
   it), then `/trader-entry SYMBOL` (the entry bar by bar). Never pick for them.

## Example (synthetic data)

Monday Oct 19 2026, a scan at 9:35 AM over a 61-chart wall
(`tests/fixtures/trader-scanner/wall5m-2026-10-19.json.gz`). These charts are
SYNTHETIC: generated by `tests/fixtures/make_scanner_fixtures.py`, not real
market data. The symbols are real NSE tickers; every price and volume is
invented, and every price sits on NSE's tick for its level.

```
python3 scan.py orb --offline wall5m-2026-10-19.json.gz --now "2026-10-19 09:35"
Fetched 60 of 61 charts (1 failed or had no data); 55 usable (3 under the price / volume floor); 21 candidates, 21 confirmed with real highs and lows.
2 charts skipped for a missing 5-minute bar between 9:15 and 9:35 AM (a trading halt, no trades, or a gap in Yahoo's data).
16 of 55 charts closed above their opening-range high by 9:35 AM:
   9:35 AM  AXISBANK        ₹1,181.10  range 1,171.20 to 1,179.30  +0.15% over
  ...
   9:35 AM  RELIANCE        ₹1,416.30  range 1,399.50 to 1,413.40  +0.21% over
  ...
```

RELIANCE's range is the 9:15 to 9:25 bars, high 1,413.40, low 1,399.50; the
9:30 bar closed at ₹1,416.30, 0.21 % over the high, at 9:35 AM. The user picks
RELIANCE and Claude hands it to `/trader-news RELIANCE`, then
`/trader-entry RELIANCE`. The wall has three charts that cannot be used
(a delisted symbol with no data, a halted one with no bars, one with missing
bars) so the counts show how a partial scan reads.

The pre-market scan of the same morning (closes through Fri Oct 16, 20
synthetic daily charts, `₹50` and 5,00,000-share floors) finds 10 charts with a
setup out of 15 that pass the floors: HDFCBANK, RELIANCE and TCS a 20-day
closing-high breakout, M&M a bull flag, SBIN, INFY and ITC a reclaim of the
20-day average, ICICIBANK and TATASTEEL oversold, LT an oversold bounce.
YESBANK and IDEA (under ₹50) and BAJAJ-AUTO, MARUTI and TITAN (under
5,00,000 shares so far) are dropped by the floors.

## Example (real data)

Yahoo's own answers for Tue 3 November 2026, saved after the close
(`tests/fixtures/trader-scanner/real-spark-3mo-2026-11-03.json`, 8 large caps).
Read on the morning of Wed 4 November, the newest daily bar has no close in
Yahoo's answer, so the scanner takes the quote's last price of 3 November:

```
python3 scan.py daily --offline real-spark-3mo-2026-11-03.json --now "2026-11-04 08:45"
Daily scan, Wed 2026-11-04 08:45 IST, closes through 2026-11-03 (Yahoo spark, daily closes only).
Fetched 8 of 8 charts. 7 passed the liquidity filter (₹50+ and 5,00,000+ shares); 7 scanned.
Yahoo left the newest daily close empty for 7 of those charts; the last price of that session (its quote, taken after the closing auction) is used for it.
4 charts matched a setup: 2 20-day closing-high breakout, 1 bull flag, 1 reclaimed the 20-day average.
Most traded first (price x volume):
  RELIANCE        ₹1,307.90  RSI  66.6  20-day closing-high breakout
  HDFCBANK          ₹725.50  RSI  58.4  reclaimed the 20-day average
  TCS             ₹2,499.10  RSI  66.1  bull flag
  ITC               ₹273.90  RSI  61.3  20-day closing-high breakout
```

Check one by hand: RELIANCE's 1,307.90 is above the best of the 20 closes
before it, 1,300.00. BAJAJ-AUTO traded 4,97,022 shares, under the 5,00,000
floor, so it is not scanned.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- An order is placed only when trader-risk passed that exact order today. A BLOCK is final: fix the order
  or your rules, never the check.
- Long only, no F&O, margin, short selling or leverage unless the config allows it. An MIS (intraday)
  order is leverage, so the pack counts it as margin when it costs more than the cash in the account.
- No logins, API keys or passwords are used, and none belong in the chat.
- Intraday gains and losses have tax consequences in India: talk to a chartered accountant.
