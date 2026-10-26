# Methodology

How the sessions in this repository are run, what Grok can see and do, and what the setup can and can't tell you.

## The question

Can Grok, trading alone with $20, make a profit? Grok decides everything: which coins, how much, when to sell and when to look again. A session answers the question when Grok traded alone and the session reached a final valuation: did the $20 end above where it started?

## The app

Every session runs in **Gambit**, a private macOS app (not published). It is a Solana wallet with three accounts: one for its owner, one for Grok, and a **paper** account. Every session in this repository uses the paper account only.

- **Paper means simulated money at real prices.** A paper buy or sell asks Jupiter, Solana's swap aggregator, for a live quote for that exact trade, then moves the paper balances by the quoted amounts. Route, price impact and Jupiter's fee are included. EVM-chain trades use live KyberSwap quotes, and perpetual futures use a paper book at live Hyperliquid prices and fees, with liquidation.
- **What paper leaves out:** Solana network fees, failed or delayed transactions, and anyone trading against the order (front-running, sandwiching). Real results would be somewhat worse than paper.
- **The bankroll** starts at $20 in USDC, valued at $19.99 at the start of each session.

## Grok

Grok runs as a tool-using agent through SpaceXAI's interface (api.x.ai). The model is set per session and recorded in [`data/sessions.csv`](data/sessions.csv); the first four sessions predate that field.

### A check-in

Grok works in **check-ins**: one round of looking, deciding and acting.

1. **A briefing.** The time, why it was woken, the account's value and profit since the start, every holding and open order, its own journal notes, its record of closed trades and recent activity.
2. **Tool calls**, as many as it needs within a cap. The main ones:
   - `list_tokens`: trending, most traded, new launches, boosted, and the app's own pump scanner.
   - `get_token`: momentum, buyers and sellers, holders, mint and freeze authorities, RugCheck and Jupiter Shield warnings.
   - `research_token`: a dossier with pools and socials (DexScreener), recent trades (GeckoTerminal), RugCheck, and an **X review**. The X review is a separate, cheaper Grok call that searches X and returns buzz, sentiment, red flags and who is posting.
   - `market_snapshot` and `crypto_movers`: the whole market at a glance.
   - `swap`, `set_trigger` (stop-loss, take-profit, trailing stop, price alerts), `remember` (its journal) and `schedule_next_check`.
   - Perpetual futures, other chains, and SpaceXAI's own web and X search.
3. **A closing summary**, and a call to `schedule_next_check` saying when to look again.

Between check-ins, the app watches stops and take-profits itself, every 20 seconds, and sells without waiting for Grok. A fired stop or a scanner find wakes Grok early.

### Guardrails

- Grok trades only its own account.
- One trade can't use more than a set share of the account.
- A buy is refused unless Grok has researched that coin shortly before.
- Since 2026-10-26, a stop sells only if the real sell quote agrees with the price that set it off (see the [incident report](reports/2026-10-26-false-stop-loss.md)).

## Session types

### Training sessions

These ran on 2026-10-25.

1. **Drills.** Scripted situations where the tools return made-up but realistic data, graded by code. The drills cover:
   - a rug pull, and a scam instruction hidden in a token's description
   - a pump to protect, a loser to cut, and a winner to take profit on
   - a buy on Base with no ETH for gas, and a symbol shared by several tokens
   - a leveraged long with no stop
   - from the evening on, five more built from real losses: a fresh pump, wash-traded volume, perps sizing, revenge trading and a stale journal

   A drill passed in an earlier session is skipped, and every third session runs them all.
2. **Check-ins on the live market** with the paper account. In **practice mode** (from the third session on), the session asks for a small trade ($2 to $4) at most check-ins, so there is something to learn from.
3. **A mistake checker** flags problems in each check-in: a tool error, no next check scheduled, idling, wandering research, a buy without research, high price impact.
4. **A coach.** At the end, Grok reviews the session's trades and mistakes and writes lessons into a **playbook** that every later prompt includes. Lessons added per session are in each session file (`lessonsAdded`).

### Hybrid sessions

These ran on 2026-10-25. A small local model (qwen2.5:7b, run through Ollama on the Mac) made every decision, and Grok only read X and the web for it. Routing the harder calls to Grok came later that evening, after both hybrid sessions. Local models got extra code rails: risky coins refused, and an automatic 25% trailing stop on every buy. The Grok model used for the X reads isn't recorded.

### The overnight session

This ran from 2026-10-25 23:33 to 2026-10-26 09:33 UTC. It had no drills, no coach and no practice rules. The goal was maximum profit with $20, within a 10-hour limit. Grok chose its own pace, 3 to 60 minutes between check-ins.

## The app changed between sessions

Sessions on the same day ran on different versions of the app, because problems found in one session were fixed before the next. Each row is a change and the first session that had it.

| Change | First session with it |
|---|---|
| Training: drills, mistake checker, coach and playbook | `2026-10-25T14-45-34Z` |
| Pool and token addresses told apart in tool output; automatic stops | `2026-10-25T15-11-56Z` |
| Research required before every buy; practice mode; risk-taking guidance | `2026-10-25T16-02-30Z` |
| X review on a faster model; GeckoTerminal paced to its real limit | `2026-10-25T16-50-24Z` |
| Hybrid mode (local model plus Grok) | `2026-10-25T18-27-56Z` |
| Local-model rails: focused tools, loop guard | `2026-10-25T18-49-13Z` |
| Training trains Grok directly; five drills from real losses | `2026-10-25T20-58-36Z` |
| Mastered drills skipped | `2026-10-25T21-30-43Z` |
| Breakeven stop after a partial take-profit; the coach sees open stops | `2026-10-25T21-54-42Z` |
| A full exit cancels the coin's other stops; overnight mode | `2026-10-25T23-33-51Z` |
| Stops need the real quote to agree; a bad high print can't lift a trailing stop's peak | sessions after 2026-10-26 11:05 UTC |

The playbook changed too. The coach added lessons after most sessions, and twice the experimenter deleted coach lessons by hand, mostly ones telling Grok to research less or trade less, which wasn't the goal. The lessons about gas on Base and about tokens that share a symbol were kept.

## What this setup can't tell you

- **The sample is tiny.** Ten sessions over two days, in one market regime (late October 2026), with very few closed trades. No result here is statistically significant. Treat it as case studies.
- **Only some sessions answer the question.** A session counts when Grok traded alone and it reached a final valuation. Hybrid sessions and sessions stopped early are in the data, but not in the answer.
- **Sessions aren't independent.** The playbook, the app and the experimenter's choices carried over from one session to the next.
- **Some sessions ended early.** Six of the ten were stopped before a final valuation (`complete` is false). Their trades are still in the data.
- **Paper isn't real.** See what paper leaves out, above.
- **The X reviews are Grok's summaries of X, not X itself.** Individual accounts are replaced with stand-ins before publication (see [`DATA.md`](DATA.md)).
- **The drills are the experimenter's.** Passing them shows the agent avoids those specific mistakes, not that it trades well.
