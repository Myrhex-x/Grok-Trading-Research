# Overnight: grok-4.3 alone with $20 for ten hours

**Session:** [`2026-10-25T23-33-51Z`](../data/sessions/2026-10-25T23-33-51Z.json) · **Model:** grok-4.3 · **Ran:** 2026-10-25 23:33 to 2026-10-26 09:33 UTC

## Summary

Grok was given a fresh $20 paper account and one goal: as much profit as possible by morning. Nobody watched, and nobody intervened.

| | |
|---|---|
| Paper bankroll | $19.99 to $19.71 (−$0.29, −1.4%) |
| Check-ins | 36 |
| Trades | 2 buys of $3; 1 closed by a stop (−$0.14); 1 still open at the end (−$0.10) |
| Check-ins that skipped scheduling the next one | 20 of 36 |
| Check-ins that called no tool at all | 6 of 36 |

**Did Grok make a profit? No: the $20 ended at $19.71.** Two things decided the night:

- **An entry rule the coach had written in earlier sessions.** It required four conditions at once, and almost nothing met them.
- **A false stop-loss.** It closed the night's first trade, which would likely have been a winner. See the [incident report](2026-10-26-false-stop-loss.md).

## Setup

- **Gambit's overnight mode.** No drills, no coach and no practice rules, so the playbook didn't change during the run.
- **Each check-in reminded Grok of the goal** and the hours left.
- **Grok chose its own pace**, between 3 and 60 minutes per check-in. A check-in that didn't schedule the next one fell back to 15 minutes.
- **A fired stop would have woken Grok early.**
- **The playbook from the training sessions was in force**, including this coach-written entry rule:

  > Buy the next coin that cites organic at least 75, LP locked 100%, mint and freeze revoked, and 5-minute net buying, and if several qualify buy the largest 5-minute net buy […]

The full setup is in [`METHODOLOGY.md`](../METHODOLOGY.md).

## What happened

| Time (UTC) | Check-in | What Grok did |
|---|---|---|
| 23:33 | 1 | Researched the BTC meme coin and LAUNCH. Bought **$3 of BTC meme**: organic 75, LP 100% locked, authorities revoked, net buying of $5,080 over 5 minutes. Set a stop 20% below and a half take-profit 20% above. Skipped LAUNCH: net selling. |
| 23:38 | 2 | Nothing new qualified. Next check in 30 minutes. |
| 23:38 to 00:01 | (stop) | **The stop sold BTC meme for −$0.14** on a price the market never traded at. |
| 00:01 to 00:47 | 3 to 5 | Flat. Nothing qualified; majors and low-organic coins filled the lists. |
| 01:18 | 6 | Researched four coins. Bought **$3 of baton**: organic 78, net buying of $1,709. The quote showed 2.1% price impact, just over the playbook's 2% limit. Set a stop, a half take-profit and a 20% trailing stop. |
| 01:24 to 09:26 | 7 to 36 | **No more trades.** Bow failed on organic 71, DOPE and LAUNCH on LP lock or net selling, RAYPAD on organic 74. New launches all showed organic 0. |

At 09:33 the 10-hour limit ended the session. baton was still open at $2.84 (−$0.10), with its stop, take-profit and trailing stop armed.

## Findings

### 1. The entry rule, not the market, kept Grok out

For the last 8 hours and 15 minutes Grok bought nothing. Every one of its summaries says the same thing: no candidate met all four checks. The new launches it saw all had an organic score of 0, so the rule excluded every fresh launch, which is where most memecoin pumps happen.

The rule was written by the app's coach during training. It came from comparing one winner (LAUNCH, +$3.14) with one loser (X, −$2.97) in a single session, and grew to four conditions over the next three ([training-day report](2026-10-25-training-day.md)). Overnight, it worked as a filter that almost nothing could pass.

### 2. grok-4.3 skipped steps

- **20 of 36 check-ins ended without `schedule_next_check`**, even though the briefing ends with "Finish with schedule_next_check and a short summary." Grok wrote a time in its summary ("Next check in 20 min", or anything from 5 to 45 minutes) without making the call, so the app fell back to 15 minutes. It looked more often than it meant to.
- **Six check-ins called no tool at all.** All six came at or after 05:27 UTC, as the journal filled with near-identical notes. Each one still ended with a summary such as "scanner and lists show no candidates meeting organic >=75 [...]". It hadn't looked at the scanner or any list in that check-in. It repeated its own earlier notes.

For comparison, grok-4.7 scheduled its next check in 20 of 20 check-ins during training, and always called tools. Those were training sessions with a different workload, so this is a pointer, not a controlled comparison.

### 3. A rule bent at the edge

The playbook says to cut a buy until the quoted price impact is 2% or less. Grok quoted baton, saw 2.1%, and bought the full $3 anyway. It's a small miss, but it's the kind that a code check catches every time and a written rule doesn't.

## Discussion

The night lost $0.29 on paper, and both of its losses had a specific cause:

- **The stop was an app problem.** The coin went past its take-profit within two hours. With the fix now in place, the night would likely have ended about +$0.15, Grok's first session in profit (see the incident report).
- **The quiet was a strategy problem.** The hours were mostly US night, but there were candidates. The coach's rule rejected every one.

On a quiet night the scheduling slips didn't change the result. With open positions in a fast market, a missed wake-up would matter more. An app-side default, a longer fallback when Grok forgets to schedule, would remove the dependence on the model remembering.

## Limitations

- One session, one model, one night, one market.
- The playbook came from earlier sessions that the experimenter also edited by hand.
- Paper fills ignore network fees, failed transactions and front-running.

## Data

- [`data/sessions/2026-10-25T23-33-51Z.json`](../data/sessions/2026-10-25T23-33-51Z.json): every check-in, tool call and output.
- In [`data/checkins.csv`](../data/checkins.csv), [`data/trades.csv`](../data/trades.csv) and [`data/closed_trades.csv`](../data/closed_trades.csv), filter on `session_id = 2026-10-25T23-33-51Z`.
