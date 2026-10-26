# Incident: a stop-loss fired on a price that never traded

**Session:** [`2026-10-25T23-33-51Z`](../data/sessions/2026-10-25T23-33-51Z.json) (the overnight run) · **When:** between 2026-10-25 23:38 and 2026-10-26 00:01 UTC · **Status:** fixed in the app on 2026-10-26

## Summary

The app's stop-loss sold a position because its price feed reported $0.006759. The coin's only trading pool never went below $0.00833 in that window. The sale itself went through at the real price, about $0.0086, for a $0.14 loss. Within two hours the coin rose past the position's take-profit. The trade would most likely have closed around **+$0.30** instead of **−$0.14**.

The cause was the app, not Grok: Grok's entry and its stop were reasonable. Since 2026-10-26, a stop sells only if the price on the real sell quote agrees with the price that set it off.

## The trade

At 23:33 UTC Grok bought $3 of a memecoin called BTC on Solana: 331.4 tokens at about $0.00905 each. The token is `C5xph2yL6gMdxmuLGxJBkGpmuDFfyi5fqkBNhvL6pump`, traded on a PumpSwap pool with about $347,000 of liquidity. It set two orders in the same check-in:

- **Stop-loss** at $0.00724 (20% below the entry): sell everything.
- **Take-profit** at $0.01086 (20% above the entry): sell half.

## What the app saw, and what the market did

The app checks stops every 20 seconds. Its price for a Solana token comes from DexScreener (the most liquid pool) or, if DexScreener has none, from Jupiter's price API. Sometime between 23:38 and 00:01 UTC, a check read **$0.006759**, below the stop, and the stop sold.

The pool's one-minute candles for that window show nothing near that price. They come from [GeckoTerminal](https://www.geckoterminal.com/solana/pools/3M9sVgL4u6ATKaiExf3XhQqNfMB6ijvzk3meMcQaen1w)'s public API, saved in [`evidence/2026-10-26-btc-pool-minutes.csv`](evidence/2026-10-26-btc-pool-minutes.csv):

| UTC | Low | | UTC | Low | | UTC | Low |
|---|---:|---|---|---:|---|---|---:|
| 23:38 | 0.008735 | | 23:46 | 0.008445 | | 23:54 | 0.008432 |
| 23:39 | 0.008604 | | 23:47 | 0.008407 | | 23:55 | 0.008361 |
| 23:40 | 0.008621 | | 23:48 | 0.008351 | | 23:56 | 0.008343 |
| 23:41 | 0.008534 | | 23:49 | 0.008340 | | 23:57 | 0.008390 |
| 23:42 | 0.008609 | | 23:50 | **0.008331** | | 23:58 | 0.008435 |
| 23:43 | 0.008451 | | 23:51 | 0.008349 | | 23:59 | 0.008457 |
| 23:44 | 0.008477 | | 23:52 | 0.008744 | | 00:00 | 0.008445 |
| 23:45 | 0.008488 | | 23:53 | 0.008486 | | 00:01 | 0.008405 |

The lowest trade in the window was $0.008331. The price the stop acted on was 19% lower than that. DexScreener lists only this one pool for the token, so the bad print came either from a momentary DexScreener error or from the Jupiter fallback. Which of the two isn't recorded.

The paper sale filled at a live Jupiter quote, so it got the real price: about $0.0086, for −$0.14.

## What the coin did next

Hourly highs and lows from the same pool ([`evidence/2026-10-26-btc-pool-hours.csv`](evidence/2026-10-26-btc-pool-hours.csv)):

| Hour (UTC) | High | Low | |
|---|---:|---:|---|
| 00:00 | 0.009077 | 0.008267 | |
| 01:00 | **0.011027** | 0.008382 | above the $0.01086 take-profit |
| 02:00 | 0.010158 | 0.009107 | |
| 03:00 | 0.010302 | 0.009011 | |
| 04:00 | 0.010253 | 0.008988 | |
| 05:00 | 0.009452 | 0.008095 | |
| 06:00 | 0.008407 | 0.007280 | |
| 07:00 | 0.007873 | **0.006812** | first time below the $0.00724 stop |

Without the false stop:

1. **01:00 hour:** the take-profit sells half at $0.01086, for about +$0.30.
2. **Then:** the app's breakeven rule moves the stop on the other half to the entry price.
3. **02:00 to 04:00:** the rest is sold near the entry, as the lows dip to around $0.0090.

Net: about +$0.30, against the −$0.14 that happened. Over the night, that would have turned the paper result from −$0.29 to roughly +$0.15. The estimate assumes the take-profit filled when the price touched it. The 01:00 high cleared it by only 1.5%, so that isn't certain.

## The fix

Applied in Gambit on 2026-10-26, after this session. It doesn't affect any earlier session in the data.

- **Before any stop, take-profit or trailing stop sells,** the app reads the price on the quote for that exact sale. That is the same quote the sale would execute.
- **If that price is more than 5% on the safe side of the trigger,** nothing is sold. The 5% covers fees and the usual gap between a quote and the feed. The trigger stays armed and is asked again with a fresh quote at most every 15 seconds. The activity log notes "Stop loss held" once, with both prices.
- **A trailing stop's peak** follows the feed in steps of up to 5% per reading. A larger jump counts only as far as a real sell quote goes, so a bad *high* print can't lift the peak and make normal prices look like a crash.

A test in the app replays this incident on a real paper position with live quotes. With the check switched off, a fake low print sells the position. With it, the print is held, and a stop the market really reached still sells.

## For anyone building trading agents

- **A stop is only as good as the price it watches.** Third-party price APIs print bad values now and then; an agent that trades many small positions will hit them.
- **The executable quote is the one price that can't be wrong about what a sale would get.** Checking it takes one request at the moment of selling.
- **The model was not the weak point here.** Grok's position sizing, stop and take-profit were what the playbook asked for. The infrastructure around it failed.
