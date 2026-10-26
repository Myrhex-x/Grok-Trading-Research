# The data

Everything in [`data/`](data) comes from the session reports Gambit writes, cleaned (see below) by [`scripts/research.py`](scripts/research.py). The CSV tables are derived from the session files and rebuilt on every update, so they always agree with them.

## Files

| File | Contents |
|---|---|
| `data/sessions/<id>.json` | One session in full. The source of everything else. |
| `data/sessions.csv` | One row per session. |
| `data/checkins.csv` | One row per check-in. |
| `data/tool_calls.csv` | One row per tool call (drills included). Full outputs are in the session files. |
| `data/trades.csv` | Buys and sells Grok made in check-ins. Drill trades are simulated, so they're left out. |
| `data/closed_trades.csv` | Positions closed during a session, by Grok or by a stop, with the realized result. |
| `data/drills.csv` | Drill results. |
| `data/mistakes.csv` | Mistakes the app's checker flagged. |
| `data/datapackage.json` | A [Frictionless Data](https://specs.frictionlessdata.io/data-package/) descriptor: every CSV column with its type and meaning. |

A session's **id** is its start time in UTC (`2026-10-25T23-33-51Z`). All times are UTC. Money is in US dollars.

## A session file

These fields come from Gambit's report as it wrote them:

| Field | Meaning |
|---|---|
| `started`, `finished` | Start and end. `finished` is missing when the session was stopped early. |
| `title` | "Overnight paper test" for the overnight session; absent for training. |
| `brain` | Who did the thinking, as the app labels it ("Grok: grok-4.3", "Hybrid: qwen2.5:7b"). |
| `config` | Session settings: check-in count, minutes between check-ins (0 = Grok decides), practice mode, drills on or off. |
| `startValue`, `endValue` | The paper bankroll at the start and the end, with open positions at market value. |
| `stoppedReason` | Why it ended, when recorded. |
| `runs` | Every drill and check-in, in order. Each run has a `kind` (`check-in` or `drill:<name>`), a `date`, the minutes Grok scheduled until the next check (`scheduledMinutes`, missing if it didn't), its closing summary (`finalText`), and its `calls`. |
| `runs[].calls` | Every tool call: `name`, `arguments` and `output`, as JSON text. Grok saw each output in full, but the app keeps only the first 4,000 characters in its reports, which cut 41 of the first 1,143 outputs (mostly `market_snapshot` and `research_token`). Those few aren't complete JSON. |
| `drills`, `drillTitles`, `drillsSkipped` | Drill verdicts (`passed`, `detail`), what each drill sets up, and drills skipped because they were passed before. |
| `mistakes` | What the checker flagged: `kind`, `detail`, `date`. |
| `lessonsAdded` | Lessons the coach wrote into the playbook at the end. |
| `closedTrades`, `openPositions` | Closed trades with their result, and positions still open at the end with their stops. |

These fields are added at publication:

| Field | Meaning |
|---|---|
| `id` | The session id. |
| `schema` | The version of this layout (currently 2). |
| `kind` | `training` or `overnight`. |
| `setup` | `grok` or `hybrid`. |
| `model` | The model, from `brain`; null when not recorded. |
| `model_note` | Why the model is missing, when it is. |
| `complete` | Whether the session has a final valuation. |

## Cleaning before publication

The raw reports stay on the experimenter's Mac. Before a session is published:

- **X accounts.** Grok's X reviews name accounts next to labels such as "bullish" or "bots and shills", and even a token's own "project account" is often a person. So every X account becomes a stable stand-in such as `@user_655d7001`. That covers names written with an @, x.com links, `from:` searches, `handle` fields and names in Grok's X search queries. The same account always gets the same stand-in, and the stand-ins come from a secret key, so they can't be reversed by guessing names. Links to X posts lose their post IDs, since an ID leads to its author. Before anything is written, the cleaned session is compared with the raw report, and any account still readable stops the publication.
- **Local file paths, email addresses**, and anything shaped like an API key or private key are removed.
- **Fetched web pages** (`fetch_url`) are cut to a 1,000-character excerpt, since the text belongs to someone else.
- **Simulated sessions** (the app's built-in pretend Grok, used for testing) are never published.
- **Sessions still running** are not published. A session goes out once it has finished, or once its report has been left alone for six hours.

- **Outside the question.** The question is whether Grok's trading makes money, so records about running the sessions themselves are left out. Grok's remarks about the experimenter's session limits are cut from its text and marked […], and the coach's lessons, the checker's flags and the one drill about those limits are dropped.

Apart from that, Grok's reasoning, its summaries and every tool's output are as they were.

## Gaps to know about

- The first four sessions (`2026-10-25T14-45-34Z` to `2026-10-25T16-50-24Z`) ran on Grok, but the model wasn't recorded: `model` is null.
- Six sessions were stopped before a final valuation (`complete: false`), so their paper result is unknown. Their trades are complete.
- `closedTrades` and `openPositions` are the app's text summaries. `closed_trades.csv` parses them into columns, and the original line is kept in `text`.
- A trade in `trades.csv` is a successful call to a trading tool. Stops and take-profits sell without a tool call, so their sells appear only in `closed_trades.csv`.

## Checking it yourself

```sh
python3 scripts/research.py check
```

This rebuilds every table and chart from the session files and compares them with the ones in the repository. It also scans every file for anything that looks like personal data or a secret. GitHub runs the same check on every push.
