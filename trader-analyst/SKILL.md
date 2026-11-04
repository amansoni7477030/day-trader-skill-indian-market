---
name: trader-analyst
description: >-
  The Earnings Analyst for Indian stocks (NSE). Reads a company's quarterly
  numbers from Yahoo Finance's fundamentals-timeseries endpoint (unofficial
  and keyless, fetched through curl_cffi: verify it against the filed result)
  and sums up what changed: revenue, gross, operating and net margins, net
  income and diluted EPS in rupee crore, quarter by quarter with fiscal
  labels (Q1 FY27 is April to June 2026), each against the same quarter a
  year earlier when Yahoo has it, with the last four quarters (TTM). Points
  to the NSE and BSE financial-results pages to read the filed result, and
  reads an earnings call transcript when you paste one. Use when the user
  says "how did Reliance's results look", "analyze the quarterly results",
  "what changed this quarter", "is revenue growing", "compare margins", "sum
  up the call", "does it report this week", or before trading a stock around
  its results.
argument-hint: "[symbols]"
---

# trader-analyst

Results move Indian stocks more than anything else on the calendar, and the
press release is written to put the best number first. This skill lines up the
last quarters of a company's reported numbers and says plainly what changed.
For a day trader it answers two questions before a trade: what did the last
result say, and is the next one inside this trade?

Tool in this folder (standard library Python plus curl_cffi, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/facts.py" --help
```

Setup, once: `pip install curl_cffi`. Yahoo blocks plain Python clients (HTTP
429, or a connection that hangs), so the request goes through curl_cffi with
Chrome's TLS fingerprint and the headers a browser sends. Without it the tool
falls back to urllib with the same headers and a Chrome User-Agent, and may be
blocked.

## Where the numbers come from (and how far to trust them)

- One request per symbol to Yahoo Finance's fundamentals-timeseries endpoint,
  `https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/RELIANCE.NS`
  with `type=quarterlyTotalRevenue,quarterlyGrossProfit,quarterlyOperatingIncome,quarterlyNetIncome,quarterlyDilutedEPS`.
  It is unofficial and keyless, and Yahoo can change it without notice. The
  response shape in this tool was checked against a live answer for
  `RELIANCE.NS` on 3 November 2026 (the test suite keeps that answer as a
  fixture). Compare one quarter with the filed result on first use, and treat
  the tool as broken if Yahoo changes the format.
- **Yahoo's quarters are few and have gaps.** The live answer for RELIANCE
  held five quarters and no 30 September 2025 quarter, so the Q2 FY27 row has
  no year-earlier comparison (its growth cells print `-`) and the older rows
  have none either. "Last 4 quarters" is printed only when the four newest
  quarters in the answer are consecutive. A gap is shown as a gap, never
  filled in.
- These are an aggregator's numbers, usually **consolidated**. Companies also
  file **standalone** figures, and one-offs (exceptional items) sit inside net
  income. Before summarising, open the filed result and check consolidated
  against standalone and any exceptional items.
- Banks and NBFCs report revenue differently and have no gross or operating
  profit in this data: those margins are blank, never zero.
- When Yahoo does not answer (an error, an empty reply, a changed format) the
  tool says that Yahoo's unofficial endpoint did not answer and points to the
  filed results. It never guesses a number.

Read the filed result here:

- NSE: `https://www.nseindia.com/companies-listing/corporate-filings-financial-results`
- BSE: `https://www.bseindia.com/corporates/Comp_Resultsnew.aspx`

This tool does not fetch exchange filings: those pages are interactive, and
the unofficial JSON behind NSE's results page was out of date when this pack
was checked, so nothing here relies on it. Open the pages, read the result,
and quote it. `/trader-news` lists NSE's corporate announcements for a symbol
(including board-meeting intimations and outcomes), which is the quickest way
to see that a result was filed.

## What it reads and writes

- Reads the Yahoo answer above (or `fundamentals-<SYMBOL>.json` in a folder
  with `--offline DIR`, which the tests use).
- Writes `trader/metrics.json` with `--metrics`: one row per company (TTM
  revenue and net income in rupee crore, TTM EPS in rupees, the latest margins
  and growth), for your notes or a spreadsheet. `metrics.example.json` in this
  folder is ILLUSTRATIVE: round made-up numbers that show the file's shape for
  RELIANCE, TCS, HDFCBANK, INFY and ITC. They are not real results.
- Earnings calls: the tool does not fetch transcripts. If the user pastes one
  (or the company's investor page has it), read it and sum up what management
  said changed: guidance, margins, demand, anything they declined to answer.
- The next results date is not in this data. Look in `/trader-news` (NSE
  filings include the board-meeting intimation), the NSE or BSE announcements
  page, or the company's investor page, and say where it came from.

## The rules

- **Filed numbers first.** The table is an aggregator's copy; the filed result
  is the source. Use the press release for context (guidance, segment colour)
  and say which numbers came from where.
- **Money in rupee crore.** 1 crore is 10,000,000 (1e7), 1 lakh is 100,000.
  EPS is in rupees. Numbers use Indian grouping (2,50,000 crore).
- **Fiscal labels.** The year runs 1 April to 31 March and is named for the
  year it ends: the quarter ending 30 June 2026 is Q1 FY27, 30 September Q2,
  31 December Q3 and 31 March 2027 Q4 FY27. A quarter ending in January to
  March is Q4 of the year ending that March.
- **Compare like with like.** Growth is against the same quarter a year
  earlier (the period ending 365 days before, give or take 10 days).
  Quarter-on-quarter changes are shown but many businesses are seasonal.
- **No derived fourth quarter.** Indian companies file their own Q4 result, so
  Q4 is read as reported, never computed from a full year.
- **Say when the data lags.** When the newest quarter is more than about 135
  days old, the tool prints a NOTE: Yahoo may lag, so read the filed result
  before summarising.
- **Flag one-offs.** When net income is above operating income, other income
  or exceptional and tax items are inside it. The tool says so.

## Steps

1. Run it for the companies in question (or the symbols on
   `trader/watchlist.json`):

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE
   python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE TCS INFY --metrics trader/metrics.json
   ```

   `--quarters 8` asks for eight quarters (Yahoo's answer may hold fewer:
   RELIANCE's held five) and `--json` gives everything machine readable.
   Symbols are plain NSE symbols (`M&M`, `BAJAJ-AUTO`); a trailing `.NS` is
   stripped.
2. Open the filed result (the NSE and BSE links above) and read it when the
   numbers need context, or when the tool printed a NOTE.
3. Write the summary: one line on the quarter (revenue, EPS, growth), one on
   margins, one on anything unusual (a one-off, consolidated against
   standalone, data that lags), and the links. Plain words, real numbers, no
   adjectives the numbers do not support.
4. If the next result falls inside the planned trade (an overnight hold, or a
   result before the 09:15 open), say it before `/trader-exit` sets the stop:
   a stop does not protect against a gap, the stock can open past it.

## Example

A real answer: `facts.py RELIANCE` on the Yahoo data captured on 3 November
2026 (kept as `tests/fixtures/trader-analyst/fundamentals-RELIANCE-real.json`;
these are Yahoo's numbers, not checked here against the filed result):

```
RELIANCE (RELIANCE.NS). ₹ crore except EPS (₹). Source: Yahoo Finance fundamentals-timeseries (unofficial, an aggregator's numbers).

quarter end fiscal       revenue  rev yoy   gross    oper     net    net inc  EPS dil EPS yoy
2026-09-30  Q2 FY27     3,30,601        -   27.1%   12.5%    7.7%     25,548    18.88       -
2026-06-30  Q1 FY27     3,09,468   +27.0%   26.8%   10.5%    6.8%     20,946    15.48  -22.4%
2026-03-31  Q4 FY26     2,94,059        -   26.6%   10.0%    5.8%     16,971    12.54       -
2025-12-31  Q3 FY26     2,64,905        -   29.7%   11.9%    7.0%     18,645    13.78       -
2025-06-30  Q1 FY26     2,43,632        -   31.7%   11.9%   11.1%     26,994    19.95       -

Last 4 quarters: revenue ₹11,99,033 crore, net income ₹82,110 crore, diluted EPS ₹60.68.
```

How to read it: the newest quarter (Q2 FY27, ended 30 September 2026) has
revenue ₹3,30,601 crore, 6.8% above the quarter before, with gross margin
27.1% (+0.3 points), operating margin 12.5% (+2.0) and net margin 7.7% (+0.9).
There is no year-on-year figure for it because Yahoo has no 30 September 2025
quarter. Q1 FY27 is compared with Q1 FY26: revenue +27.0% but EPS -22.4%, a
swing to check in the filed result for a one-off before calling it a trend.
The four newest quarters are consecutive (December 2025 to September 2026), so
the last-four-quarters line is printed. A summary ends with where the next
result stands. RELIANCE filed its Q2 FY27 result on 30 October 2026 (the NSE
filing shows in `/trader-news`), so the next one is months away and a day
trade today is not exposed to it. For a company that reports this week the
line is: "The next result is days away. A day trade that is closed before the
broker's square-off is not exposed to it; anything held overnight is."

The tests also use made-up round numbers in Yahoo's shape (see
`tests/fixtures/make_analyst_fixtures.py`; not a real company's result),
including a bank-like symbol whose gross and operating margin columns print `-`.

## What it never does

- Never says a stock is cheap, expensive, a buy or a sell. It reports numbers.
- Never invents a number Yahoo did not give, or estimates a quarter it cannot
  read.
- Never fetches exchange filings: it prints where to read them.
- Never sends more than one request per symbol, with a pause between calls.

## Safety

Education and tooling, not financial advice. This skill only reads numbers.
Nothing is placed except by `/trader-broker`, after `/trader-risk` passes.
