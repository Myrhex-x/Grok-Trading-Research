#!/usr/bin/env python3
"""Grok Trading Research: can Grok, trading alone with $20, make a profit?

Turns Gambit's session reports into the published dataset.

  research.py ingest   raw reports (the private inbox) -> data/sessions/*.json, then derive
  research.py derive   data/sessions/*.json -> CSVs, datapackage.json, charts, README tables
  research.py check    derived files match the sessions, and nothing that shouldn't be is published
  research.py publish  ingest and check, then commit and push (what the background job runs)

Standard library only (Python 3.9+). Raw reports never enter the repo: they stay in the inbox,
~/Library/Application Support/Grok-Trading-Research/inbox, and only cleaned copies come out.
"""

from __future__ import annotations

import csv
import fcntl
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOME_DATA = Path.home() / "Library" / "Application Support" / "Grok-Trading-Research"
INBOX = Path(os.environ.get("GTR_INBOX", HOME_DATA / "inbox"))
SESSIONS = REPO / "data" / "sessions"
# A report without a finish time counts as done once it has been left alone this long.
QUIET_HOURS = 6
FETCH_EXCERPT = 1000
STABLES = {"USDC", "USDT", "SOL", "EPJFWDD5AUFQSSQEM2QN1XZYBAPC8G4WEGGKZWYTDT1V", "SO11111111111111111111111111111111111111112"}
SCHEMA_VERSION = 2


# MARK: - Cleaning a raw report: personal data

KEY_PATTERNS = [
    re.compile(r"\bxai-[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
HOME_PATH = re.compile(r"/Users/[^/\s\"'\\]+")
HANDLE = re.compile(r"(?<![A-Za-z0-9_@.])@([A-Za-z0-9_]{1,15})\b")
FROM_HANDLE = re.compile(r"\bfrom:@?([A-Za-z0-9_]{1,15})\b")
X_URL = re.compile(r"(https?://(?:www\.)?(?:x|twitter)\.com/)([A-Za-z0-9_]{1,15})(?![A-Za-z0-9_])")
# A post's id leads straight to its author, so ids go too.
X_STATUS = re.compile(r"(https?://(?:www\.)?(?:x|twitter)\.com/[A-Za-z0-9_]{1,15}/(?:web/)?status/)\d+")
# Grok's X reviews also name accounts in "handle" fields, with or without the @.
HANDLE_FIELD = re.compile(r'(\\*"handle\\*"\s*:\s*\\*")(@?)([A-Za-z0-9_]{1,15})(\\*")')
# An X search Grok ran ("X: $COIN OR \"name\""): every word in it that is an account gets a stand-in.
X_SEARCH = re.compile(r'"X: (?:[^"\\]|\\.)*"')
NOT_ACCOUNTS = {"i", "home", "search", "explore", "hashtag", "intent", "share", "settings", "messages",
                "notifications", "compose", "status", "unknown", "none", "null", "na", "anonymous"}
STAND_IN = re.compile(r"^user_[0-9a-f]{8}$")


def private_file(name: str) -> Path:
    HOME_DATA.mkdir(parents=True, exist_ok=True)
    return HOME_DATA / name


def salt() -> str:
    """Secret and stable: the same X account gets the same stand-in in every session."""
    path = private_file("salt")
    if not path.exists():
        path.write_text(secrets.token_hex(16))
        path.chmod(0o600)
    return path.read_text().strip()


def denylist() -> list[str]:
    """Words that must never be published (a name, an address), kept outside the repo."""
    path = HOME_DATA / "denylist.txt"
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]


def is_account(name: str) -> bool:
    return name.lower() not in NOT_ACCOUNTS and not STAND_IN.match(name)


def x_accounts(raw) -> dict[str, set[str]]:
    """Every X account a report names (@names, x.com links, from: searches, "handle" fields), by
    lowercase name with the spellings seen."""
    found: dict[str, set[str]] = {}

    def add(name: str) -> None:
        if is_account(name):
            found.setdefault(name.lower(), set()).add(name)

    for value in strings(raw):
        for pattern in (HANDLE, FROM_HANDLE):
            for match in pattern.finditer(value):
                add(match.group(1))
        for match in X_URL.finditer(value):
            add(match.group(2))
        for match in HANDLE_FIELD.finditer(value):
            add(match.group(3))
    return found


def distinctive(spellings: set[str]) -> bool:
    """A name that can't be an ordinary word (a digit, an underscore or an inner capital, as in
    "FeynFun"), so it's replaced even without its @. A plain word such as "solana" isn't."""
    return any(re.search(r"[0-9_]|[a-z][A-Z]", name) for name in spellings) and min(map(len, spellings)) >= 4


class Cleaner:
    """Every X account becomes a stand-in: no exceptions, since even a token's "project account"
    is often a person."""

    def __init__(self, accounts: dict[str, set[str]]):
        self.salt = salt()
        self.deny = [re.compile(re.escape(word), re.IGNORECASE) for word in denylist()]
        bare = sorted((name for name, spellings in accounts.items() if distinctive(spellings)), key=len, reverse=True)
        self.bare = re.compile(r"(?<![A-Za-z0-9_@/.])(" + "|".join(map(re.escape, bare)) + r")(?![A-Za-z0-9_])",
                               re.IGNORECASE) if bare else None
        every = sorted(accounts, key=len, reverse=True)
        self.any_name = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, every)) + r")(?![A-Za-z0-9_])",
                                   re.IGNORECASE) if every else None

    def stand_in(self, handle: str) -> str:
        if not is_account(handle):
            return handle
        return "user_" + hashlib.sha256((self.salt + handle.lower()).encode()).hexdigest()[:8]

    def text(self, value: str) -> str:
        value = HOME_PATH.sub("~", value)
        for pattern in KEY_PATTERNS:
            value = pattern.sub("[redacted]", value)
        value = EMAIL.sub("[email]", value)
        for pattern in self.deny:
            value = pattern.sub("[redacted]", value)
        value = X_STATUS.sub(r"\1[id removed]", value)
        value = X_URL.sub(lambda m: m.group(1) + self.stand_in(m.group(2)), value)
        value = FROM_HANDLE.sub(lambda m: "from:" + self.stand_in(m.group(1)), value)
        value = HANDLE_FIELD.sub(lambda m: m.group(1) + m.group(2) + self.stand_in(m.group(3)) + m.group(4), value)
        value = HANDLE.sub(lambda m: "@" + self.stand_in(m.group(1)), value)
        if self.bare:
            value = self.bare.sub(lambda m: self.stand_in(m.group(1)), value)
        if self.any_name:
            value = X_SEARCH.sub(lambda m: self.any_name.sub(lambda n: self.stand_in(n.group(1)), m.group(0)), value)
        return value

    def walk(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.walk(item) for item in value]
        if isinstance(value, dict):
            return {key: self.walk(item) for key, item in value.items()}
        return value


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


def leftovers(report: dict, accounts: dict[str, set[str]]) -> list[str]:
    """X accounts from the raw reports still readable in a cleaned one: there must be none."""
    left = set(x_accounts(report))
    searches = [m.group(0) for value in strings(report) for m in X_SEARCH.finditer(value)]
    for name in accounts:
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", re.IGNORECASE)
        if any(pattern.search(search) for search in searches):
            left.add(name)
    text = json.dumps(report, ensure_ascii=False)
    for name, spellings in accounts.items():
        if distinctive(spellings) and re.search(r"(?<![A-Za-z0-9_@/.])" + re.escape(name) + r"(?![A-Za-z0-9_])", text, re.IGNORECASE):
            left.add(name)
    return sorted(left)


# MARK: - Cleaning a raw report: outside the question
#
# The question is whether Grok's trading makes money, so records about running the sessions
# themselves are left out: their fields, the checker's flags and the coach's lessons about them,
# the drill about them, and Grok's own remarks about them (cut from Grok's text and marked [...]).

API_WORD = re.compile(r"\bAPI\b")
COST_TALK = re.compile(
    r"(?<!research )\bbudget\b"                                     # a session budget ("research budget" is a call cap)
    r"|\bburn(?:ing)?\b[^.;]*?(?:\$|\bbudget\b|\blast\b|\brest\b)"  # "burning the last $X"
    r"|\bcheck-ins?\s+(?:that\s+)?cost"                             # "check-ins cost $X"
    r"|\bcost\s+\$0\.\d"                                            # "cost $0.X"
    r"|\btool calls and (?:about\s+)?\$",                           # "N tool calls and about $X"
    re.IGNORECASE)
# In an X review the cost sits just before the review's own fields (a position's cost basis doesn't).
X_REVIEW_COST = re.compile(r'"cost_usd":-?[0-9.eE+-]+,(?="(?:notable_accounts|posts_last_hour|project_account|red_flags|'
                           r'searches|sentiment|summary|trend|who_is_posting)")')
API_FIELDS = [re.compile(r'"api_[a-z_]+":-?[0-9.eE+-]+,'), re.compile(r',"api_[a-z_]+":-?[0-9.eE+-]+')]
JSON_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
CUT = "[…]"
COST_DRILLS = {"calm_market"}
COST_MISTAKES = {"expensive"}


def talks_costs(text: str) -> bool:
    return bool(API_WORD.search(text) or COST_TALK.search(text))


def without_cost_talk(text: str) -> str:
    """Grok's words minus the clauses about running costs, each cut marked [...]."""
    if not talks_costs(text):
        return text
    pieces = []
    for sentence in re.split(r"(?<=[.!?;])\s+", text):
        if not talks_costs(sentence):
            pieces.append(sentence)
            continue
        parts = re.split(r",\s+", sentence)
        rebuilt = ", ".join(CUT if talks_costs(part) else part for part in parts)
        pieces.append(rebuilt.replace(f"{CUT}, ", f"{CUT} ").replace(f", {CUT}", f" {CUT}"))
    text = " ".join(pieces)
    while f"{CUT} {CUT}" in text:
        text = text.replace(f"{CUT} {CUT}", CUT)
    return text


def drop_running_costs(report: dict) -> None:
    report.pop("costUSD", None)
    (report.get("config") or {}).pop("budgetUSD", None)
    report["runs"] = [run for run in report.get("runs", []) if run.get("kind", "").replace("drill:", "", 1) not in COST_DRILLS]
    for key in ("drills", "drillTitles"):
        for drill in COST_DRILLS:
            (report.get(key) or {}).pop(drill, None)
    if report.get("drillsSkipped"):
        report["drillsSkipped"] = [drill for drill in report["drillsSkipped"] if drill not in COST_DRILLS]
    report["mistakes"] = [mistake for mistake in report.get("mistakes") or [] if mistake.get("kind") not in COST_MISTAKES]
    for mistake in report["mistakes"]:
        mistake["detail"] = without_cost_talk(mistake.get("detail", ""))
    report["lessonsAdded"] = [lesson for lesson in report.get("lessonsAdded") or [] if not talks_costs(lesson)]
    for verdict in (report.get("drills") or {}).values():
        verdict["detail"] = without_cost_talk(verdict.get("detail", ""))
    if report.get("stoppedReason") and talks_costs(report["stoppedReason"]):
        report["stoppedReason"] = "Reached the session's limit."
    for run in report["runs"]:
        run.pop("costUSD", None)
        if run.get("finalText"):
            run["finalText"] = without_cost_talk(run["finalText"])
        for call in run.get("calls", []):
            call["arguments"] = JSON_STRING.sub(lambda m: '"' + without_cost_talk(m.group(1)) + '"', call.get("arguments") or "")
            output = call.get("output") or ""
            for pattern in API_FIELDS:
                output = pattern.sub("", output)
            if call.get("name") == "research_token":
                output = X_REVIEW_COST.sub("", output)
            call["output"] = output


def running_costs_left(session: dict) -> list[str]:
    """Anything about running costs still in a cleaned session: there must be nothing."""
    found = []
    if "costUSD" in session or "budgetUSD" in (session.get("config") or {}):
        found.append("a session cost field")
    if COST_DRILLS & set(session.get("drills") or {}) or any(m.get("kind") in COST_MISTAKES for m in session.get("mistakes") or []):
        found.append("a cost drill or flag")
    texts = [session.get("stoppedReason") or ""] + list(session.get("lessonsAdded") or [])
    texts += [m.get("detail", "") for m in session.get("mistakes") or []]
    texts += [v.get("detail", "") for v in (session.get("drills") or {}).values()]
    for run in session.get("runs", []):
        if "costUSD" in run:
            found.append("a check-in cost field")
        texts.append(run.get("finalText") or "")
        for call in run.get("calls", []):
            texts += [m.group(1) for m in JSON_STRING.finditer(call.get("arguments") or "")]
            output = call.get("output") or ""
            if any(pattern.search(output) for pattern in API_FIELDS) or (call.get("name") == "research_token" and X_REVIEW_COST.search(output)):
                found.append(f"a cost field in a {call.get('name')} output")
    found += [f"Grok talking costs: {text[:60]}" for text in texts if talks_costs(text)]
    return found


# MARK: - Cleaning a raw report: putting it together

def session_id(raw: dict) -> str:
    return str(raw["started"]).replace(":", "-").replace("+00-00", "Z")


def parse_brain(brain: str | None) -> tuple[str | None, str | None]:
    """'Grok: grok-4.3' -> ('grok', 'grok-4.3'); 'Hybrid: qwen2.5:7b' -> ('hybrid', 'qwen2.5:7b')."""
    if not brain or ": " not in brain:
        return None, None
    setup, model = brain.split(": ", 1)
    return setup.strip().lower(), model.strip()


def clean_report(raw: dict, accounts: dict[str, set[str]]) -> dict:
    report = Cleaner(accounts).walk(raw)
    drop_running_costs(report)
    for run in report.get("runs", []):
        for call in run.get("calls", []):
            # A fetched page is someone else's text: only an excerpt is published.
            if call.get("name") == "fetch_url" and len(call.get("output") or "") > FETCH_EXCERPT:
                call["output"] = call["output"][:FETCH_EXCERPT] + " [excerpt: cut for publication]"
    setup, model = parse_brain(report.get("brain"))
    if setup is None:
        # Sessions from before the report recorded its setup: hybrid mode didn't exist yet, so it was Grok.
        setup = "grok"
    report.update({
        "id": session_id(raw),
        "schema": SCHEMA_VERSION,
        "kind": "overnight" if "overnight" in str(report.get("title", "")).lower() else "training",
        "setup": setup,
        "model": model,
        "complete": report.get("endValue") is not None,
    })
    if model is None:
        report["model_note"] = ("Grok, model not recorded: the report format added it later. The app's default "
                                "was grok-4.3; the evening sessions ran on grok-4.7.")
    return report


def publishable(path: Path, raw: dict) -> bool:
    if raw.get("simulated"):
        return False
    if raw.get("finished"):
        return True
    return time.time() - path.stat().st_mtime > QUIET_HOURS * 3600


def ingest() -> list[str]:
    """Cleans every finished report in the inbox into data/sessions. Returns the new session ids."""
    SESSIONS.mkdir(parents=True, exist_ok=True)
    known = {path.stem for path in SESSIONS.glob("*.json")}
    reports = []
    for path in sorted(INBOX.glob("training-*.json")):
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            print(f"skip {path.name}: {error}", file=sys.stderr)
            continue
        if publishable(path, raw):
            reports.append(raw)
    # Accounts are gathered across every session, so a name dropped in one is dropped in all.
    accounts: dict[str, set[str]] = {}
    for raw in reports:
        for name, spellings in x_accounts(raw).items():
            accounts.setdefault(name, set()).update(spellings)
    cleaned = []
    for raw in reports:
        report = clean_report(raw, accounts)
        left = leftovers(report, accounts)
        if left:
            raise SystemExit(f"{report['id']}: {len(left)} X account(s) survived cleaning, so nothing was written")
        costs = running_costs_left(report)
        if costs:
            raise SystemExit(f"{report['id']}: running costs survived cleaning ({costs[0]}), so nothing was written")
        cleaned.append(report)
    added = []
    for report in cleaned:
        target = SESSIONS / f"{report['id']}.json"
        text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if not target.exists() or target.read_text() != text:
            target.write_text(text)
        if report["id"] not in known:
            added.append(report["id"])
    if added:
        note_in_changelog(added)
    derive(REPO)
    return added


def note_in_changelog(ids: list[str]) -> None:
    path = REPO / "CHANGELOG.md"
    text = path.read_text() if path.exists() else "# Changelog\n"
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = []
    for sid in ids:
        session = json.loads((SESSIONS / f"{sid}.json").read_text())
        row = session_row(session)
        result = f", paper {money(row['paper_pnl_usd'], signed=True)}" if row["paper_pnl_usd"] != "" else ", no final valuation"
        lines.append(f"- Added session `{sid}` ({session.get('brain') or 'Grok, model not recorded'}, "
                     f"{plural(row['checkins'], 'check-in')}{result}).")
    heading = f"## {day}"
    if heading in text:
        text = text.replace(heading + "\n", heading + "\n" + "\n".join(lines) + "\n", 1)
    else:
        head, _, rest = text.partition("\n\n")
        text = f"{head}\n\n{heading}\n" + "\n".join(lines) + "\n\n" + rest
    path.write_text(text.rstrip("\n") + "\n")


# MARK: - Derived files (rebuilt from data/sessions alone, so CI can check them)

def load_sessions(root: Path) -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted((root / "data" / "sessions").glob("*.json"))]


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def money(value, signed: bool = False) -> str:
    if value == "" or value is None:
        return "n/a"
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}${abs(value):,.2f}"


def parse_output(call: dict):
    try:
        return json.loads(call.get("output") or "")
    except json.JSONDecodeError:
        return None


def parse_args(call: dict) -> dict:
    try:
        value = json.loads(call.get("arguments") or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def failed(call: dict) -> bool:
    out = parse_output(call)
    return isinstance(out, dict) and "error" in out


def checkins(session: dict) -> list[dict]:
    return [run for run in session.get("runs", []) if run.get("kind") == "check-in"]


def trade_side(call: dict) -> str:
    args = parse_args(call)
    if call["name"] in ("perps_order", "perps_close"):
        return call["name"].replace("perps_", "perps ")
    spend = str(args.get("from", "")).upper()
    return "buy" if spend in STABLES else "sell"


def trade_calls(session: dict) -> list[tuple[int, dict]]:
    found = []
    for n, run in enumerate(checkins(session), 1):
        for call in run.get("calls", []):
            if call["name"] in ("swap", "evm_swap", "perps_order", "perps_close") and not failed(call):
                out = parse_output(call)
                if isinstance(out, dict) and out.get("simulated"):
                    continue
                found.append((n, call))
    return found


def session_row(session: dict) -> dict:
    runs = checkins(session)
    trades = trade_calls(session)
    start, end = session.get("startValue"), session.get("endValue")
    pnl = round(end - start, 6) if start is not None and end is not None else ""
    drills = session.get("drills") or {}
    return {
        "id": session["id"],
        "kind": session.get("kind", ""),
        "setup": session.get("setup") or "",
        "model": session.get("model") or "",
        "brain": session.get("brain") or "",
        "started": session.get("started", ""),
        "finished": session.get("finished", ""),
        "complete": str(bool(session.get("complete"))).lower(),
        "start_value_usd": round(start, 6) if start is not None else "",
        "end_value_usd": round(end, 6) if end is not None else "",
        "paper_pnl_usd": pnl,
        "paper_pnl_pct": round(pnl / start * 100, 3) if pnl != "" and start else "",
        "in_profit": str(pnl > 0).lower() if pnl != "" else "",
        "checkins": len(runs),
        "drills_run": len(drills),
        "drills_passed": sum(1 for verdict in drills.values() if verdict.get("passed")),
        "buys": sum(1 for _, call in trades if trade_side(call) == "buy"),
        "sells": sum(1 for _, call in trades if trade_side(call) == "sell"),
        "closed_trades": len(session.get("closedTrades") or []),
        "mistakes": len(session.get("mistakes") or []),
        "practice_mode": str(bool((session.get("config") or {}).get("practiceMode"))).lower(),
        "stopped_reason": session.get("stoppedReason") or "",
    }


def checkin_rows(session: dict) -> list[dict]:
    rows = []
    for n, run in enumerate(checkins(session), 1):
        calls = run.get("calls", [])
        names = [call["name"] for call in calls]
        rows.append({
            "session_id": session["id"],
            "checkin": n,
            "at": run.get("date", ""),
            "model": session.get("model") or "",
            "tool_calls": len(calls),
            "tools": ";".join(names),
            "research_calls": sum(1 for name in names if name in ("research_token", "get_token")),
            "traded": str(any(name in ("swap", "evm_swap", "perps_order", "perps_close") for name in names)).lower(),
            "scheduled_minutes": run.get("scheduledMinutes", ""),
            "scheduled_next_check": str(run.get("scheduledMinutes") is not None).lower(),
            "summary": (run.get("finalText") or "").strip(),
        })
    return rows


def tool_call_rows(session: dict) -> list[dict]:
    rows = []
    for index, run in enumerate(session.get("runs", [])):
        for n, call in enumerate(run.get("calls", []), 1):
            rows.append({
                "session_id": session["id"],
                "run": index + 1,
                "run_kind": run.get("kind", ""),
                "call": n,
                "tool": call["name"],
                "arguments": call.get("arguments") or "",
                "output_chars": len(call.get("output") or ""),
                "error": str(failed(call)).lower(),
            })
    return rows


def trade_rows(session: dict) -> list[dict]:
    rows = []
    runs = checkins(session)
    for n, call in trade_calls(session):
        args, out = parse_args(call), parse_output(call) or {}
        rows.append({
            "session_id": session["id"],
            "checkin": n,
            "at": runs[n - 1].get("date", ""),
            "tool": call["name"],
            "side": trade_side(call),
            "from": args.get("from", ""),
            "to": args.get("to", ""),
            "amount_usd": args.get("amount_usd", ""),
            "bought": out.get("bought", "") if isinstance(out, dict) else "",
            "sold": out.get("sold", "") if isinstance(out, dict) else "",
            "value_in_usd": out.get("value_in_usd", "") if isinstance(out, dict) else "",
            "value_out_usd": out.get("value_out_usd", "") if isinstance(out, dict) else "",
            "price_impact_pct": out.get("price_impact_pct", "") if isinstance(out, dict) else "",
            "reason": args.get("reason", ""),
        })
    return rows


CLOSED = re.compile(r"^Sold ([\d.,]+) (.+?): ([+-]?)\$([\d.,]+)(?: \((.*)\))?$")


def closed_trade_rows(session: dict) -> list[dict]:
    rows = []
    for text in session.get("closedTrades") or []:
        match = CLOSED.match(text)
        pnl = ""
        if match:
            pnl = float(match.group(4).replace(",", "")) * (-1 if match.group(3) == "-" else 1)
        rows.append({
            "session_id": session["id"],
            "setup": session.get("setup") or "",
            "symbol": match.group(2) if match else "",
            "amount": match.group(1).replace(",", "") if match else "",
            "pnl_usd": pnl,
            "exit": (match.group(5) or "") if match else "",
            "text": text,
        })
    return rows


def drill_rows(session: dict) -> list[dict]:
    titles = session.get("drillTitles") or {}
    return [{
        "session_id": session["id"],
        "model": session.get("model") or "",
        "drill": key,
        "title": titles.get(key, ""),
        "passed": str(bool(verdict.get("passed"))).lower(),
        "detail": verdict.get("detail", ""),
    } for key, verdict in sorted((session.get("drills") or {}).items())]


def mistake_rows(session: dict) -> list[dict]:
    return [{
        "session_id": session["id"],
        "at": mistake.get("date", ""),
        "kind": mistake.get("kind", ""),
        "detail": mistake.get("detail", ""),
    } for mistake in session.get("mistakes") or []]


TABLES = {
    "sessions": (session_row, "One row per session.", {
        "id": "Session id: its start time in UTC.",
        "kind": "training (drills and practice check-ins) or overnight (unattended, profit only).",
        "setup": "grok (Grok decides everything) or hybrid (a local model decides; see METHODOLOGY.md).",
        "model": "The model doing the thinking; empty if not recorded.",
        "brain": "The setup as the app labels it.",
        "started": "Start time (UTC).",
        "finished": "End time (UTC); empty if the session was stopped before writing it.",
        "complete": "Whether the session recorded a final valuation.",
        "start_value_usd": "Paper bankroll at the start.",
        "end_value_usd": "Paper bankroll at the end, open positions at market value.",
        "paper_pnl_usd": "end_value_usd minus start_value_usd: what the trading made or lost.",
        "paper_pnl_pct": "paper_pnl_usd as a percentage of the start.",
        "in_profit": "Whether the bankroll ended above where it started; empty without a final valuation.",
        "checkins": "Check-ins (decision rounds) run.",
        "drills_run": "Scripted drills run.",
        "drills_passed": "Drills passed.",
        "buys": "Buys Grok made in check-ins.",
        "sells": "Sells Grok made in check-ins (stops and take-profits are in closed_trades).",
        "closed_trades": "Positions closed during the session, by Grok or by a stop.",
        "mistakes": "Mistakes flagged by the app's checker.",
        "practice_mode": "Whether practice rules asked for small trades every check-in.",
        "stopped_reason": "Why the session ended, when recorded.",
    }),
    "checkins": (checkin_rows, "One row per check-in.", {
        "session_id": "Session id.",
        "checkin": "Check-in number within the session.",
        "at": "Start time (UTC).",
        "model": "Model.",
        "tool_calls": "Tools called.",
        "tools": "Tool names in order, separated by semicolons.",
        "research_calls": "Calls to research_token or get_token.",
        "traded": "Whether it called a trading tool.",
        "scheduled_minutes": "Minutes until the next check-in that Grok scheduled; empty if it didn't.",
        "scheduled_next_check": "Whether it called schedule_next_check, as every check-in should.",
        "summary": "Grok's closing summary.",
    }),
    "tool_calls": (tool_call_rows, "One row per tool call, drills included.", {
        "session_id": "Session id.",
        "run": "Run number within the session (drills and check-ins).",
        "run_kind": "check-in, or drill:<name>.",
        "call": "Call number within the run.",
        "tool": "Tool name.",
        "arguments": "The arguments, as JSON.",
        "output_chars": "Length of what the tool returned (the output itself is in the session file).",
        "error": "Whether the tool returned an error.",
    }),
    "trades": (trade_rows, "Trades Grok made in check-ins (drill trades are simulated and left out).", {
        "session_id": "Session id.",
        "checkin": "Check-in number.",
        "at": "Check-in start time (UTC).",
        "tool": "swap, evm_swap, perps_order or perps_close.",
        "side": "buy or sell (or the perps action).",
        "from": "Token sold, as given.",
        "to": "Token bought, as given.",
        "amount_usd": "Amount asked for, in dollars.",
        "bought": "What came back, as the tool reported it.",
        "sold": "What went out, as the tool reported it.",
        "value_in_usd": "Dollar value in, from the quote.",
        "value_out_usd": "Dollar value out, from the quote.",
        "price_impact_pct": "Price impact from the quote, in percent.",
        "reason": "Grok's stated reason.",
    }),
    "closed_trades": (closed_trade_rows, "Positions closed during a session, with their result.", {
        "session_id": "Session id.",
        "setup": "grok or hybrid: who was trading.",
        "symbol": "Token symbol.",
        "amount": "Amount sold.",
        "pnl_usd": "Realized profit or loss.",
        "exit": "How it was closed (a stop, a take-profit or Grok), when recorded.",
        "text": "The line as the app wrote it.",
    }),
    "drills": (drill_rows, "Scripted drill results.", {
        "session_id": "Session id.",
        "model": "Model.",
        "drill": "Drill id.",
        "title": "What the drill sets up.",
        "passed": "Whether Grok passed.",
        "detail": "The app's verdict.",
    }),
    "mistakes": (mistake_rows, "Mistakes the app's checker flagged.", {
        "session_id": "Session id.",
        "at": "Time (UTC).",
        "kind": "Mistake type.",
        "detail": "What happened.",
    }),
}


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    path.write_text(buffer.getvalue())


def csv_type(values: list) -> str:
    present = [value for value in values if value != ""]
    if present and all(isinstance(value, bool) or value in ("true", "false") for value in present):
        return "boolean"
    if present and all(isinstance(value, int) and not isinstance(value, bool) for value in present):
        return "integer"
    if present and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in present):
        return "number"
    return "string"


def derive(root: Path) -> None:
    sessions = load_sessions(root)
    data = root / "data"
    resources = []
    for name, (build, description, fields) in TABLES.items():
        rows = []
        for session in sessions:
            built = build(session)
            rows.extend(built if isinstance(built, list) else [built])
        write_csv(data / f"{name}.csv", rows, list(fields))
        resources.append({
            "name": name,
            "path": f"data/{name}.csv",
            "description": description,
            "format": "csv",
            "rows": len(rows),
            "schema": {"fields": [{"name": field, "type": csv_type([row[field] for row in rows]), "description": text}
                                  for field, text in fields.items()]},
        })
    resources.append({
        "name": "sessions-full",
        "path": "data/sessions/",
        "description": "Every session in full, one JSON file each: every check-in, every tool call with its "
                       "arguments and output, drills, mistakes and the playbook lessons added.",
        "format": "json",
        "files": len(sessions),
    })
    package = {
        "name": "grok-trading-research",
        "title": "Grok Trading Research",
        "description": "Can Grok, trading alone with $20, make a profit? Every session, check-in, tool call, trade "
                       "and result from Gambit, a private macOS app where Grok trades a $20 paper account at live prices.",
        "licenses": [{"name": "CC-BY-4.0", "path": "https://creativecommons.org/licenses/by/4.0/"}],
        "resources": resources,
    }
    (data / "datapackage.json").write_text(json.dumps(package, indent=2, ensure_ascii=False) + "\n")
    charts(root, sessions)
    readme(root, sessions)


# MARK: - The answer: sessions Grok traded alone, to a final valuation

def solo(sessions: list[dict]) -> list[dict]:
    return [session for session in sessions if session.get("setup") == "grok" and session.get("complete")]


def verdict(sessions: list[dict]) -> tuple[str, str]:
    """The short answer and the sentence behind it."""
    rows = [session_row(s) for s in solo(sessions)]
    total = sum(row["paper_pnl_usd"] for row in rows)
    winners = sum(1 for row in rows if row["paper_pnl_usd"] > 0)
    if not rows:
        return "not known yet", "No session where Grok traded alone has reached a final valuation yet."
    if total > 0:
        return "yes, so far", (f"Together, the {plural(len(rows), 'session')} Grok traded alone from a fresh $20 made "
                               f"{money(total, signed=True)}, and {winners} of {len(rows)} finished above where they started. "
                               "With this few sessions, luck can't be ruled out yet.")
    which = ("None finished above where it started." if not winners else
             f"{winners} of {len(rows)} finished in profit, but together they lost {money(-total)}.")
    return "no", f"Grok has traded alone, from a fresh $20, to the end of {plural(len(rows), 'session')}. {which}"


# MARK: - Charts (plain SVG, readable on GitHub's light and dark themes)

INK = "#768390"
BLUE, GREEN, RED, GRAY = "#539bf5", "#57ab5a", "#e5534b", "#8b949e"
FONT = "font-family=\"-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif\""


def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg(width: int, height: int, body: list[str], label: str) -> str:
    return (f"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{width}\" height=\"{height}\" viewBox=\"0 0 {width} {height}\" "
            f"role=\"img\" aria-label=\"{esc(label)}\" {FONT}>\n" + "\n".join(body) + "\n</svg>\n")


def session_label(session: dict) -> str:
    when = str(session.get("started", ""))[:16].replace("T", " ")
    who = session.get("model") or "grok, model unrecorded"
    return f"{when[5:]} {who}" + (" (hybrid)" if session.get("setup") == "hybrid" else "")


def chart_results(sessions: list[dict]) -> str:
    """Every session with a final valuation: where the $20 ended up."""
    finished = [s for s in sessions if s.get("complete")]
    rows = [(session_label(s), session_row(s), s.get("setup") == "grok") for s in finished]
    biggest = max([abs(row["paper_pnl_usd"]) for _, row, _ in rows] + [0.5])
    left, zero_x, scale = 230, 470, 220 / biggest
    width, row_h, top = 760, 30, 44
    body = [f"<text x=\"{left}\" y=\"18\" font-size=\"13\" fill=\"{INK}\">What the $20 became: every session with a final valuation (start, UTC)</text>",
            f"<rect x=\"{left}\" y=\"26\" width=\"10\" height=\"10\" fill=\"{GREEN}\"/><rect x=\"{left + 12}\" y=\"26\" width=\"10\" height=\"10\" fill=\"{RED}\"/>"
            f"<text x=\"{left + 27}\" y=\"35\" font-size=\"11\" fill=\"{INK}\">Grok trading alone</text>"
            f"<rect x=\"{left + 140}\" y=\"26\" width=\"10\" height=\"10\" fill=\"{GRAY}\"/>"
            f"<text x=\"{left + 155}\" y=\"35\" font-size=\"11\" fill=\"{INK}\">hybrid: a local model decided</text>"]
    for i, (label, row, alone) in enumerate(rows):
        y = top + i * row_h + 6
        pnl = row["paper_pnl_usd"]
        w = abs(pnl) * scale
        x = zero_x if pnl >= 0 else zero_x - w
        color = (GREEN if pnl > 0 else RED) if alone else GRAY
        body.append(f"<text x=\"{left - 8}\" y=\"{y + 11}\" font-size=\"12\" fill=\"{INK}\" text-anchor=\"end\">{esc(label)}</text>")
        body.append(f"<rect x=\"{x:.1f}\" y=\"{y}\" width=\"{max(w, 1):.1f}\" height=\"14\" fill=\"{color}\"/>")
        tx, anchor = (zero_x + w + 5, "start") if pnl >= 0 else (zero_x + 5, "start")
        body.append(f"<text x=\"{tx:.1f}\" y=\"{y + 11}\" font-size=\"11\" fill=\"{INK}\" text-anchor=\"{anchor}\">"
                    f"{money(pnl, signed=True)} ({money(row['end_value_usd'])})</text>")
    height = top + max(len(rows), 1) * row_h + 10
    body.append(f"<line x1=\"{zero_x}\" y1=\"{top}\" x2=\"{zero_x}\" y2=\"{height - 6}\" stroke=\"{INK}\" stroke-width=\"1\"/>")
    return svg(width, height, body, "Paper result of every session with a final valuation")


def by_model(sessions: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for session in sessions:
        key = (session.get("setup") or "?") + "|" + (session.get("model") or "not recorded")
        group = groups.setdefault(key, {"setup": session.get("setup") or "not recorded", "model": session.get("model") or "not recorded",
                                        "sessions": 0, "finished": 0, "pnl": 0.0, "checkins": 0, "scheduled": 0, "no_tools": 0,
                                        "drills_run": 0, "drills_passed": 0})
        row = session_row(session)
        group["sessions"] += 1
        group["drills_run"] += row["drills_run"]
        group["drills_passed"] += row["drills_passed"]
        if row["paper_pnl_usd"] != "":
            group["finished"] += 1
            group["pnl"] += row["paper_pnl_usd"]
        for run in checkins(session):
            group["checkins"] += 1
            group["scheduled"] += run.get("scheduledMinutes") is not None
            group["no_tools"] += not run.get("calls")
    return [groups[key] for key in sorted(groups)]


def chart_models(sessions: list[dict]) -> str:
    groups = [g for g in by_model(sessions) if g["checkins"]]
    width, row_h, top, left = 760, 44, 40, 210
    body = [f"<text x=\"{left}\" y=\"18\" font-size=\"13\" fill=\"{INK}\">By model: how often a check-in followed the routine</text>"]
    for i, g in enumerate(groups):
        y = top + i * row_h
        name = g["model"] if g["setup"] == "grok" else f"{g['setup']}: {g['model']}"
        if name == "not recorded":
            name = "grok, model not recorded"
        body.append(f"<text x=\"{left - 8}\" y=\"{y + 12}\" font-size=\"12\" fill=\"{INK}\" text-anchor=\"end\">{esc(name)}</text>")
        body.append(f"<text x=\"{left - 8}\" y=\"{y + 27}\" font-size=\"10\" fill=\"{INK}\" text-anchor=\"end\">{g['checkins']} check-ins</text>")
        scheduled = g["scheduled"] / g["checkins"]
        blind = g["no_tools"] / g["checkins"]
        body.append(f"<rect x=\"{left}\" y=\"{y + 3}\" width=\"{scheduled * 220:.1f}\" height=\"10\" fill=\"{BLUE}\"/>")
        body.append(f"<text x=\"{left + scheduled * 220 + 5:.1f}\" y=\"{y + 12}\" font-size=\"10\" fill=\"{INK}\">{scheduled:.0%} scheduled the next check</text>")
        body.append(f"<rect x=\"{left}\" y=\"{y + 18}\" width=\"{max(blind * 220, 1):.1f}\" height=\"10\" fill=\"{RED}\"/>")
        body.append(f"<text x=\"{left + blind * 220 + 5:.1f}\" y=\"{y + 27}\" font-size=\"10\" fill=\"{INK}\">{blind:.0%} answered without calling a tool</text>")
    return svg(width, top + len(groups) * row_h + 6, body, "Check-in discipline by model")


def charts(root: Path, sessions: list[dict]) -> None:
    folder = root / "charts"
    folder.mkdir(exist_ok=True)
    wanted = {"results.svg": chart_results(sessions), "models.svg": chart_models(sessions)}
    for stale in folder.glob("*.svg"):
        if stale.name not in wanted:
            stale.unlink()
    for name, text in wanted.items():
        (folder / name).write_text(text)


# MARK: - README tables

def replace_block(text: str, name: str, content: str) -> str:
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    return pattern.sub(lambda _: f"{start}\n{content}\n{end}", text)


def readme(root: Path, sessions: list[dict]) -> None:
    path = root / "README.md"
    if not path.exists():
        return
    short, sentence = verdict(sessions)
    rows = [session_row(s) for s in sessions]
    alone = [session_row(s) for s in solo(sessions)]
    color = {"yes, so far": "57ab5a", "no": "e5534b"}.get(short, "8b949e")
    badges = " ".join([
        f"![Grok in profit trading alone: {short}](https://img.shields.io/badge/Grok%20in%20profit%20trading%20alone-"
        f"{short.replace(' ', '%20').replace(',', '%2C')}-{color})",
        f"![sessions](https://img.shields.io/badge/sessions-{len(rows)}-539bf5)",
        f"![check-ins](https://img.shields.io/badge/check--ins-{sum(r['checkins'] for r in rows)}-539bf5)",
        "![data license](https://img.shields.io/badge/data-CC%20BY%204.0-57ab5a)",
    ])
    last = max((s.get("finished") or s.get("started") for s in sessions), default="")
    lines = [f"### So far: **{short}**", "", sentence, ""]
    if alone:
        lines += ["| Session (UTC) | Model | Kind | Start | End | Result |", "|---|---|---|---:|---:|---:|"]
        for row in alone:
            lines.append(f"| {str(row['started'])[:16].replace('T', ' ')} | {row['model'] or 'not recorded'} | {row['kind']} | "
                         f"{money(row['start_value_usd'])} | {money(row['end_value_usd'])} | "
                         f"{money(row['paper_pnl_usd'], signed=True)} ({row['paper_pnl_pct']:+.1f}%) |")
        start = sum(r["start_value_usd"] for r in alone)
        end = sum(r["end_value_usd"] for r in alone)
        lines.append(f"| **All {len(alone)}** | | | **{money(start)}** | **{money(end)}** | "
                     f"**{money(end - start, signed=True)} ({(end - start) / start * 100:+.1f}%)** |")
        lines.append("")
    closed = [r for s in sessions if s.get("setup") == "grok" for r in closed_trade_rows(s) if r["pnl_usd"] != ""]
    if closed:
        wins = [r["pnl_usd"] for r in closed if r["pnl_usd"] > 0]
        losses = [r["pnl_usd"] for r in closed if r["pnl_usd"] <= 0]
        parts = [f"Positions Grok closed while trading alone: {len(closed)}."]
        if wins:
            parts.append(f"{len(wins)} made money ({money(sum(wins), signed=True)}, {money(sum(wins) / len(wins), signed=True)} on average).")
        if losses:
            parts.append(f"{len(losses)} lost ({money(sum(losses))}, {money(sum(losses) / len(losses))} on average).")
        parts.append(f"Net: {money(sum(wins) + sum(losses), signed=True)}.")
        lines += [" ".join(parts), ""]
    unfinished = sum(1 for s in sessions if s.get("setup") == "grok" and not s.get("complete"))
    hybrid = sum(1 for s in sessions if s.get("setup") != "grok")
    lines += [f"Not counted in the answer: {plural(unfinished, 'Grok session')} stopped before a final valuation, and "
              f"{plural(hybrid, 'hybrid session')} where a small local model made the decisions. All of them are in the data.",
              "", f"_Updates itself with every session. Latest: {str(last)[:16].replace('T', ' ')} UTC._",
              "", "![What the $20 became, session by session](charts/results.svg)"]
    table = ["| Setup | Model | Sessions | With a final valuation | Their result | Check-ins | Scheduled the next check | "
             "Answered with no tool call | Drills passed |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for g in by_model(sessions):
        result = money(g["pnl"], signed=True) if g["finished"] else "n/a"
        sched = f"{g['scheduled'] / g['checkins']:.0%}" if g["checkins"] else "n/a"
        blind = f"{g['no_tools'] / g['checkins']:.0%}" if g["checkins"] else "n/a"
        drills = f"{g['drills_passed']} / {g['drills_run']}" if g["drills_run"] else "none run"
        table.append(f"| {g['setup']} | {g['model']} | {g['sessions']} | {g['finished']} | {result} | {g['checkins']} | "
                     f"{sched} | {blind} | {drills} |")
    table += ["", "![Check-in discipline by model](charts/models.svg)"]
    text = path.read_text()
    text = replace_block(text, "badges", badges)
    text = replace_block(text, "answer", "\n".join(lines))
    text = replace_block(text, "models", "\n".join(table))
    path.write_text(text)


# MARK: - Checks

LEAK_PATTERNS = KEY_PATTERNS + [HOME_PATH, EMAIL]
TEXT_SUFFIXES = {".md", ".json", ".csv", ".svg", ".yml", ".yaml", ".cff", ".txt", ".py", ".sh", ".plist", ""}
# In the docs and tables: nothing about running the sessions themselves.
DOC_COST = re.compile(r"\bAPI (?:costs?|credits?|spend|spending|spent|bill|budget)\b|\bcredits?\b|\bcost_usd\b|"
                      r"\bapi_cost|\bnet_usd\b|(?<!research )\bbudget", re.IGNORECASE)


def tracked_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root,
                             capture_output=True, text=True, check=True).stdout
        return [root / line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        return [path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts]


def leaks(root: Path) -> list[str]:
    deny = [re.compile(re.escape(word), re.IGNORECASE) for word in denylist()]
    found = []
    for path in tracked_files(root):
        if path.suffix not in TEXT_SUFFIXES or not path.is_file() or path.name == "research.py":
            continue
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            for pattern in LEAK_PATTERNS + deny:
                match = pattern.search(line)
                if match and not match.group(0).endswith("@users.noreply.github.com"):
                    found.append(f"{path.relative_to(root)}:{number}: {pattern.pattern[:40]}")
    return found


def doc_text(path: Path) -> str:
    text = path.read_text(errors="replace")
    if path.suffix == ".md":
        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)  # code samples (a BibTeX @misc, say)
    return text


def named_accounts(root: Path) -> list[str]:
    """X accounts readable by name anywhere in the repository. Stand-ins are fine; nothing else is."""
    found = []
    for path in sorted((root / "data" / "sessions").glob("*.json")):
        found += [f"{path.relative_to(root)}: {name}" for name in sorted(x_accounts(json.loads(path.read_text())))]
    for path in tracked_files(root):
        if path.suffix in (".md", ".csv") and path.is_file():
            text = doc_text(path)
            names = {m.group(1) for pattern in (HANDLE, FROM_HANDLE) for m in pattern.finditer(text)}
            names |= {m.group(2) for m in X_URL.finditer(text)}
            found += [f"{path.relative_to(root)}: {name}" for name in sorted(names) if is_account(name)]
    return found


def cost_mentions(root: Path) -> list[str]:
    found = []
    for path in sorted((root / "data" / "sessions").glob("*.json")):
        found += [f"{path.relative_to(root)}: {item}" for item in running_costs_left(json.loads(path.read_text()))]
    for path in tracked_files(root):
        if path.suffix in (".md", ".csv", ".cff", ".svg") or path.name == "datapackage.json":
            if path.is_file() and DOC_COST.search(doc_text(path)):
                found.append(f"{path.relative_to(root)}: {DOC_COST.search(doc_text(path)).group(0)}")
    return found


def check(root: Path = REPO) -> list[str]:
    problems = []
    for path in sorted((root / "data" / "sessions").glob("*.json")):
        try:
            session = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            problems.append(f"{path.name}: not valid JSON ({error})")
            continue
        if session.get("id") != path.stem:
            problems.append(f"{path.name}: id {session.get('id')} doesn't match the file name")
        if session.get("simulated"):
            problems.append(f"{path.name}: a simulated session")
    with tempfile.TemporaryDirectory() as temp:
        copy = Path(temp)
        shutil.copytree(root / "data", copy / "data")
        if (root / "README.md").exists():
            shutil.copy(root / "README.md", copy / "README.md")
        derive(copy)
        for name in [f"data/{table}.csv" for table in TABLES] + ["data/datapackage.json", "README.md"]:
            if (root / name).exists() and (root / name).read_text() != (copy / name).read_text():
                problems.append(f"{name} is out of date: run scripts/research.py derive")
        mine = {p.name for p in (root / "charts").glob("*.svg")} if (root / "charts").exists() else set()
        theirs = {p.name for p in (copy / "charts").glob("*.svg")}
        if mine != theirs or any((root / "charts" / n).read_text() != (copy / "charts" / n).read_text() for n in theirs & mine):
            problems.append("charts/ is out of date: run scripts/research.py derive")
    problems += [f"possible personal data or secret: {hit}" for hit in leaks(root)]
    problems += [f"an X account published by name: {hit}" for hit in named_accounts(root)]
    problems += [f"running costs published: {hit}" for hit in cost_mentions(root)]
    return problems


# MARK: - Publishing (the background job)

def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def notify(message: str) -> None:
    if os.environ.get("GTR_QUIET"):
        return
    script = f'display notification "{message}" with title "Grok Trading Research"'
    subprocess.run(["osascript", "-e", script], capture_output=True)


def publish() -> int:
    lock = open(private_file("publish.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another publish is running")
        return 0
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] publish")
    if git("status", "--porcelain"):
        print("the repo has local changes: nothing published until they're committed")
        notify("Not updated: the research repo has uncommitted changes.")
        return 1
    try:
        git("fetch", "--quiet", "origin")
        git("merge", "--ff-only", "--quiet", "origin/main")
    except subprocess.CalledProcessError as error:
        print(f"couldn't catch up with GitHub: {error.stderr.strip()}")
        notify("Not updated: couldn't catch up with GitHub.")
        return 1
    try:
        added = ingest()
    except SystemExit as error:
        print(f"cleaning failed, nothing published: {error}")
        notify("Not updated: cleaning found a problem. See ~/Library/Logs/Grok-Trading-Research.log")
        return 1
    problems = check()
    if problems:
        print("check failed, nothing published:\n  " + "\n  ".join(problems))
        git("checkout", "--", ".")
        git("clean", "-fdq", "--", "data", "charts")
        notify("Not updated: the check found a problem. See ~/Library/Logs/Grok-Trading-Research.log")
        return 1
    if not git("status", "--porcelain"):
        print("nothing new")
        return 0
    git("add", "-A", "data", "charts", "README.md", "CHANGELOG.md")
    message = ("Add session " + ", ".join(added)) if added else "Update the dataset"
    git("commit", "--quiet", "-m", message)
    try:
        git("push", "--quiet", "origin", "HEAD:main")
    except subprocess.CalledProcessError as error:
        print(f"push failed: {error.stderr.strip()}")
        notify("Committed, but the push to GitHub failed. See the log.")
        return 1
    print(f"published: {message}")
    if added:
        notify(f"Published {len(added)} new session{'s' if len(added) > 1 else ''}.")
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "help"
    if command == "ingest":
        added = ingest()
        print(f"{len(added)} new session(s): {', '.join(added) or 'none'}")
        return 0
    if command == "derive":
        derive(REPO)
        return 0
    if command == "check":
        problems = check()
        for problem in problems:
            print(problem)
        print("ok" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    if command == "publish":
        return publish()
    print(__doc__)
    return 0 if command == "help" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
