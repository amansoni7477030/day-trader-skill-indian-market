---
name: trader-news
description: >-
  News Monitor for Indian stocks (NSE). Tracks what is new on every stock you
  watch and flags what moves it: the company's own NSE corporate
  announcements (results and board-meeting outcomes, orders won, dividends and
  record dates, pledges, clarifications, with the PDF), Google News India
  headlines searched by company name, how far each stock is from yesterday's
  close in rupees, and plain links to the NSE and BSE announcement pages.
  Shows only what is new since the last check, newest first, in IST, tags each
  item (earnings, analyst, legal, deal, capital, ownership, people, rating,
  risk, halt, clarification, regulatory, macro, market) and never repeats one.
  Use when the user says "any news on RELIANCE", "why is this moving", "what's
  the news on my watchlist", "morning briefing", "pre-open news", "monitor the
  news", "did anything break", "any board meeting or results today", or after
  /trader-scanner finds setups.
argument-hint: "[symbols]"
---

# trader-news

A chart can look perfect and still be about to gap on a results date, a
circuit-filter hit or a SEBI order. Before a setup becomes a trade, this skill
reads the exchange filings and the headline feed for every stock on the
watchlist and tells you only what is new. It also prints how far each stock has
moved today, so a headline and a move sit side by side.

Setup, once: `pip install curl_cffi`. NSE and Yahoo block plain Python clients
(HTTP 401, 403 or 429, or a connection that hangs), so every request goes
through curl_cffi with Chrome's TLS fingerprint and the headers a browser sends.
Without it the tool falls back to urllib with the same headers and a Chrome
User-Agent, and may be blocked.

Tool in this folder (standard library Python plus curl_cffi, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                         # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE TCS --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers "M&M" --all --hours 72
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-moves --json
```

Symbols are plain NSE symbols (`RELIANCE`, `M&M`, `BAJAJ-AUTO`); quote a symbol
with `&` in a shell. A trailing `.NS` is accepted and stripped. Times print in
IST (UTC+5:30, no daylight saving). The NSE session is 09:15 to 15:30 IST; for
a stock with F&O contracts continuous trading ends at 15:15 and a closing
auction follows, and this pack's trading day ends at 15:15.

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json` | the trade you are planning (trader-exit) |
| `trader/portfolio.json` | what you hold (trader-broker records fills there) |
| `trader/universe.txt` | company names (`scan.py universe`), used to search the headlines |
| NSE corporate announcements | `https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=RELIANCE&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY`: the company's own filings, with the PDF link |
| Google News India (RSS) | `https://news.google.com/rss/search?q="Reliance Industries" when:2d&hl=en-IN&gl=IN&ceid=IN:en`: headlines found by the company name, newest first, at most `--max-headlines` (12) a stock |
| Yahoo Finance chart endpoint | the last price against yesterday's close, in rupees (unofficial, can lag) |
| `trader/state/news.json` | what has been shown (kept 30 days) |

One request per source per ticker, with a pause between calls. If NSE answers
401 or 403 the tool visits NSE's home page once for its cookies and asks again.
Per ticker it also prints two plain links (and returns them under `links` with
`--json`): the NSE quote page,
`https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE`, and the BSE
announcements page, `https://www.bseindia.com/corporates/ann.html`. BSE's own
feed is not fetched: if a BSE-only filing matters, open that page.

Yahoo's own news feed for `.NS` symbols is sparse and noisy (for RELIANCE.NS it
returned nothing), which is why the headlines come from Google News searched by
company name instead.

## The rules

- **Filings are the company's words; headlines are leads, not facts.** Before
  telling the user something happened, open the filing or the story and read
  it. Quote the source and link it. Never summarise a paywalled story you
  could not read: say so.
- **Announcements beat headlines.** If an exchange filing and a headline
  disagree, the filing is what the company told NSE.
- **Say what it touches.** Every item names its ticker and carries tags:
  earnings (results, Q1 to Q4, board meeting, investor presentation), analyst
  (upgrade, target price), legal (SEBI, ED, CBI, NCLT, court), deal
  (acquisition, order win, block or bulk deal), capital (dividend, bonus,
  split, buyback, rights, QIP, OFS, IPO), ownership (promoter, pledge, stake),
  people, rating (CRISIL, ICRA), risk, halt (circuit, ASM, GSM, F&O ban),
  clarification (NSE asked the company about a price or volume move),
  regulatory, macro (RBI, repo, inflation, GST, crude, rupee) and market
  (Nifty, Sensex, FII, FPI).
- **MOVING** marks a stock 3 % or more from yesterday's close (`--move-pct`).
  A move with no headline is worth saying out loud too. NSE stocks have daily
  price bands (2 %, 5 %, 10 %, 20 % or none): a stock stuck at its upper or
  lower circuit may not trade, and a stop cannot fill there.
- **No predictions.** Report what was published and how far the price is from
  yesterday's close. Never say what the stock will do next.
- **Numbers only, never buy or sell.** This skill reports; it does not tell
  the user to buy or sell because of a headline.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves the state alone, `--offline DIR` reads
   saved files (`nse-SYMBOL.json`, `gnews-SYMBOL.xml`, `chart-SYMBOL.json`).
2. Read the items that matter: anything tagged earnings, halt, legal,
   ownership, regulatory, clarification or risk, and every MOVING ticker first.
3. Open the PDF of an NSE filing that matters (the link under the line) and read
   what the company filed (results, board meeting outcome, pledge, block deal).
4. Write a short brief, per ticker: what happened, the source and link, the
   move, and one line on why it matters to the setup (for example "results
   are today after the close: an overnight gap can jump past any stop").
5. If a story changes the picture, say so before `/trader-entry` and
   `/trader-exit`: a results date inside the trade, a circuit limit, a board
   meeting on a fund raise. Never act on a headline alone.

During the session: `/loop 15m /trader-news`, or a scheduled task each
morning before the 09:15 open (the pre-open call auction runs 09:00 to 09:15
and this pack does not trade it).

## Example (real data)

Yahoo's chart, NSE's announcements API and Google News, all saved on the night
of Tue 3 November 2026 (`tests/fixtures/trader-news/`: `nse-RELIANCE.json`,
`gnews-RELIANCE.xml`, `chart-RELIANCE.json`), read with
`news.py --offline tests/fixtures/trader-news --tickers RELIANCE --hours 200`:

```
RELIANCE   ₹1,307.90 +0.61% vs yesterday's close ₹1,300.00 at 15:29 IST

  2026-11-03 23:22 IST  RELIANCE    Business Stan ED attaches 2 aircraft in probe against Reliance Anil Ambani Group
      tags: legal  https://news.google.com/rss/articles/...
  2026-11-01 23:09 IST  RELIANCE    NSE filing    Analysts/Institutional Investor Meet/Con. Call Updates: Transcript of the discussion ...
      tags: earnings  https://nsearchives.nseindia.com/corporate/kavinavora_01112026230907_SE_01112026.pdf
  2026-10-30 19:40 IST  RELIANCE    NSE filing    Outcome of Board Meeting: ... approved the unaudited Financial Results ...
      tags: earnings  https://nsearchives.nseindia.com/corporate/kavinavora_30102026193935_SE_30102026_Results.pdf
```

The brief: "Results came out on 30 October after the close (NSE filing, board
meeting outcome); the stock jumped about 12 % on 2 November on them. The ED
headline is about the Anil Ambani Group, not Reliance Industries: read it
before assuming it touches the stock." Headlines found by company name can be
about another company, so the tool says when it searched by the symbol alone
and may bring noise. When a ticker has no news, say so rather than padding
the brief.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never fetches BSE pages: it prints their link only.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: one request per source per ticker and a pause
  between calls.

## Safety

Education and tooling, not financial advice. This skill only reads and
reports. Nothing records a paper trade except `/trader-broker`, after
`/trader-risk` passes and the user types an explicit yes for that order.
