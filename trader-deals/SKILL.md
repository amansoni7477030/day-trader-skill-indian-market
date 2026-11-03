---
name: trader-deals
description: >-
  The Deals Tracker. Reads the two public files NSE publishes after every
  trading day, the BULK DEALS and BLOCK DEALS files, and turns them into a
  clear table: which stocks had big deals, how much was bought and sold, the
  net quantity and net value in rupees crore, and which client names were on
  each side. Marks deals in stocks on your watchlist, plan or portfolio,
  filters by symbol, client name or minimum value, alerts once per new deal,
  and saves each evening's file so a history builds up. Use when the user
  says "bulk deals", "block deals", "who bought", "who sold", "big buyer",
  "big buyers in RELIANCE", "any bulk deal in my stocks", "deals today",
  "NSE deals", "alert me on new block deals", or "run the deals skill".
  Covers the NSE deal files only: FII/DII flows, mutual fund portfolios and
  insider (SEBI PIT) filings are NOT covered, see the manual route in the
  skill.
argument-hint: "[symbol ...] [--kind bulk|block] [--min-value-cr N]"
---

# trader-deals

After every trading day NSE publishes two small CSV files: the bulk deals of
the day and the block deals of the day. They name the stock, the client, buy
or sell, the quantity and the price. This skill reads them, adds up what they
say per stock, and tells you which of them touch the stocks you follow.

It is fast. It is not early. These are public files published after the
close, so you read a deal after it happened. There is no inside information
here, only public data read quickly. Say that every time. One big buyer is
not a signal.

Tool in this folder (standard library Python plus curl_cffi, no keys, no login):

```bash
python3 "${CLAUDE_SKILL_DIR}/deals.py" --help
```

Setup, once: `pip install curl_cffi`. NSE drops requests that look like
scripts (the connection hangs or is refused), so the download goes through
curl_cffi with Chrome's TLS fingerprint and the headers a browser sends.
Without it the tool falls back to urllib with the same headers and a Chrome
User-Agent, and may time out.

## What a bulk deal and a block deal are

Verify the current thresholds and windows with the exchange: the rules change.

- A **bulk deal** is a trade where the total quantity a client bought or sold
  in one stock during the day is more than 0.5% of the company's listed equity
  shares. It can be built from many small trades across the day.
- A **block deal** is one large trade, with a minimum order size of ₹25 crore
  (since 8 December 2025, NSE circular NSE/CMTR/71394; it was ₹10 crore
  before), done in a separate trading window of the exchange (08:45 to 09:00
  and 14:05 to 14:20 IST) within 3% of the reference price, instead of on the
  normal order book.
- Both are published by the exchange after the close. They are public data,
  not inside information, and you read them after the fact.
- The price in the file is a weighted average price, not a single fill.
- A block deal has a buyer and a seller on the same price, so the two lines
  net to zero. That tells you a big parcel changed hands, not which way the
  stock will go.

## What it reads and writes

- Reads `https://nsearchives.nseindia.com/content/equities/bulk.csv` and
  `.../block.csv`: a plain download through curl_cffi with the headers a
  browser sends (`Referer: https://www.nseindia.com/`). No login, no cookies,
  and nothing from `www.nseindia.com/api`.
- **Each file holds the latest trading day only.** Yesterday's file is gone
  when today's appears. To keep a record, run `new` once each evening after
  the market closes.
- Writes `trader/state/deals.json` (the deals already shown, kept 30 days, so
  each deal alerts once) and `trader/state/deals-history.json` (every fetched
  day's rows, keyed by date, kept until you delete them).
- Reads `trader/watchlist.json`, `trader/plan.json` and `trader/portfolio.json`
  (if they exist) to mark deals in stocks you follow with a `*`.
- Money is in rupees: values in crore (1 crore = 1,00,00,000), lakh below
  that, Indian digit grouping (13,47,189 shares).

## The rules

- **Quote the file.** Every number comes from the NSE file, with its date. If
  a number is not in the file, do not make it up.
- **Say what the client names are.** They are the names on the exchange
  record: often brokers, trading firms, LLPs and HUFs, not the end investor.
  Do not guess who is "really" behind a name.
- **Net across clients is a sum of what is shown.** With `--client`,
  `--symbol` or `--min-value-cr` the table covers only the deals that passed
  the filter, and the report says so.
- **Value is quantity times weighted average price**, divided by 1,00,00,000
  for crore. A block deal's buy and sell lines each count in full. A block
  deal that NSE also lists in the bulk file (it is over the bulk threshold) is
  counted once, as a block deal, and the report says so in a note.
- **No return claims.** Never say that following deals makes money, or that a
  big buyer means the price will rise.

## Step 1: what happened on the latest day

```bash
python3 "${CLAUDE_SKILL_DIR}/deals.py" latest
python3 "${CLAUDE_SKILL_DIR}/deals.py" latest --kind block
python3 "${CLAUDE_SKILL_DIR}/deals.py" latest --symbol RELIANCE TCS
python3 "${CLAUDE_SKILL_DIR}/deals.py" latest --client "goldman" --min-value-cr 25
python3 "${CLAUDE_SKILL_DIR}/deals.py" latest --summary --json
```

`--symbol` takes plain NSE symbols (`M&M`, `BAJAJ-AUTO`), several at once.
`--client` matches part of the client name, any case. `--min-value-cr N` keeps
deals worth at least N crore each. `--summary` prints only the per-stock table.
Exit code 0 means there is something to show, 1 means nothing matched or the
download failed.

## Step 2: only what is new, and build a history

```bash
python3 "${CLAUDE_SKILL_DIR}/deals.py" new
python3 "${CLAUDE_SKILL_DIR}/deals.py" new --symbol RELIANCE --kind block
```

Shows only the deals not shown at a previous run (a deal is identified by its
date, kind, symbol, client, side, quantity and price), and appends the day's
rows to the history file. Exit code 1 means nothing new. NSE publishes the
files after the close, so schedule it once in the evening, for example with
`/loop 1d` or a scheduled task in the evening on trading days (for example 19:00 IST; check when the files actually appear). If you run
it at lunchtime it will simply find yesterday's file again and say "Nothing
new".

## Step 3: the saved days for one stock

```bash
python3 "${CLAUDE_SKILL_DIR}/deals.py" history --symbol BLSPOLY --days 30
```

Lists each saved day with the deals in that stock, the day's net quantity and
value, and the total over the window. It can only show the days you saved with
`new`: a fresh install has none.

## Step 4: read a saved file

```bash
python3 "${CLAUDE_SKILL_DIR}/deals.py" parse downloaded-bulk.csv --kind bulk
```

For a CSV the user saved from NSE's website, or an older day. The tests use
`--offline DIR` on `latest` and `new` to read `bulk.csv` and `block.csv` from a
folder.

## Example: the files of 3 November 2026

Real data: the two complete files NSE served for that day, kept as
`tests/fixtures/trader-deals/full-day/bulk.csv` and `block.csv` (147 bulk rows,
2 block rows). `deals.py latest --summary` prints this (the first six of its
31 symbols, biggest turnover first):

```
NSE bulk and block deals for 03-Nov-2026: 145 bulk, 2 block deals

By symbol (net = bought minus sold, across the clients shown):
SYMBOL      COMPANY                    KIND   DEALS  CLIENTS     NET QTY     NET VALUE       BOUGHT         SOLD
BLSPOLY     BLS Polymers Limited       bulk      56       27    9,71,383    +₹25.48 cr   ₹808.60 cr   ₹783.12 cr
GALLANTT    Gallantt Ispat Limited     block      2        2           0            ₹0   ₹250.50 cr   ₹250.50 cr
TECHNOCRAF  Technocraft Ventures Ltd   bulk      12        6    1,08,325     +₹4.71 cr   ₹170.52 cr   ₹165.82 cr
RATNAVEER   Ratnaveer Precision Eng L  bulk       4        3  -15,30,701    -₹58.21 cr    ₹63.61 cr   ₹121.82 cr
SFCTECH     SFC Environmental Tech L   bulk       5        5   27,70,566   +₹110.54 cr   ₹136.02 cr    ₹25.48 cr
TEMPSENS    Tempsens Inst (India) Ltd  bulk       4        2     -30,273     -₹1.70 cr    ₹58.90 cr    ₹60.61 cr
note: 2 row(s) of the bulk file repeat a block deal (NSE lists a block deal that is also over the bulk threshold in both files): each trade is counted once, as a block deal
```

A reading of it: "BLS Polymers had 56 bulk deals on 3 November from 27 client
names, and every one of the 27 is on both sides: ₹808.60 crore bought and
₹783.12 crore sold, at weighted average prices between ₹247 and ₹279, a net of
only 9,71,383 shares (₹25.48 crore). That is the same names buying and selling
the same stock in one day, so the gross figures say little and the net says
more. SFC Environmental Tech is lopsided instead: ₹136.02 crore bought and
₹25.48 crore sold by five names, a net of ₹110.54 crore. This is what the
exchange published after the close; it does not say why they traded or what
happens next." Gallantt Ispat shows the usual shape of a block deal:
50,00,000 shares at ₹501.00 (₹250.50 crore), one buyer and one seller, net
zero. NSE also lists that block deal in the bulk file (it is over the bulk
threshold); the tool counts it once, which is what the note says. Read only
the bulk file (`--kind bulk`) and it appears there, as a bulk deal, with
nothing to compare it with.

If the user wants to act on a stock that shows up, it goes the long way: a
deal is a reason to look at a chart, not a setup. `/trader-scanner` and
`/trader-entry` decide if there is a trade today; then shares only (the config
has `allow_options: false`), sized by `/trader-budget`, checked by
`/trader-risk`, recorded as a paper trade by `/trader-broker` only after their yes.

## Other routes (manual, not in this tool)

- **Insider-trading (SEBI PIT) disclosures.** Promoters, directors and other
  insiders disclose their own trades. NSE lists them at
  `https://www.nseindia.com/companies-listing/corporate-filings-insider-trading`
  and BSE has an equivalent page. Verify the link: exchange sites move pages.
  The page is built for a browser and the JSON behind it was out of date when
  this pack was checked, so this skill does not read it. Claude may
  open the page for the user, who can pick the symbol and date range, and paste
  the table into the chat. Then summarise it the same way, quote the filing
  and its date, and note that these filings can be days after the trade.
- **FII and DII flows, mutual fund holdings and shareholding patterns** are
  not covered. The daily FII/DII figures are an aggregate, not per stock.

## What it never does

- Never says or implies a client has inside information, or that copying a
  big buyer beats the market. No return claims.
- Never presents a weighted average price as a single execution price.
- Never names who is "behind" a client name.
- Never places an order, and never touches a login.
- Never uses the NSE website's `/api` endpoints, logs in, or keeps cookies: it
  reads only the two public CSV files, fetched the way a browser fetches them
  (Chrome's TLS fingerprint and browser headers). If NSE still refuses the
  download, say so and use `parse` on a file the user saved.

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
- Bulk and block deals are PUBLIC data published after the close. This skill reads them fast; it has
  no inside information, and it says so.
