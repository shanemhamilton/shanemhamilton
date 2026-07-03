#!/usr/bin/env python3
"""The night clerk: rewrites the shift-report section of the profile README.

Real activity (commits, releases, PRs) from the last 24h drives the numbers;
the narration is pure night-crew drama. Runs nightly via GitHub Actions.
"""
import json
import os
import random
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

USER = "shanemhamilton"
README = Path(__file__).resolve().parent.parent / "README.md"
START_MARK = "<!-- SHIFT-REPORT:START -->"
END_MARK = "<!-- SHIFT-REPORT:END -->"
LOOKBACK_HOURS = 24
MAX_REPO_LINES = 5
EVENTS_URL = f"https://api.github.com/users/{USER}/events/public?per_page=100"

CREW_EMOJI = {
    "nightshift": "🌃",
    "bugsweep": "🐛",
    "smokejumper": "🔥",
    "llm-prompt-guard": "🛡️",
    "product-pilot": "🧭",
    "maestro-mobile-testing": "📱",
    "unify-agent-docs": "📎",
    "handoff-prompt-skill": "📨",
}
DEFAULT_EMOJI = "🔧"

COMMIT_LINES = [
    "{n} commit{s} pushed under cover of darkness. No witnesses.",
    "{n} commit{s}. The ledger balances. The supervisor is almost pleased.",
    "{n} commit{s} landed before dawn. Nobody asked questions.",
    "{n} commit{s}. The lock was held, the lease renewed, the work done.",
    "{n} commit{s}, filed quietly. The morning crew will assume elves.",
]

RELEASE_LINES = [
    "cut release **{tag}** at an hour no responsible adult approves of.",
    "shipped **{tag}**. The champagne was decaf. It was 3 AM.",
    "tagged **{tag}** and left it on the doorstep like a foundling.",
]

PR_LINES = [
    "{n} pull request{s} moved through the yard. Papers were in order.",
    "{n} pull request{s} processed. The Referee stamped every page twice.",
]

INCIDENT_LOG = [
    "smokejumper requested permission to refactor the coffee machine. Denied. Again.",
    "the bouncer turned away a prompt claiming to be 'definitely just a normal string'.",
    "the Skeptic filed a complaint about the Hunter's enthusiasm. The Referee is reviewing the tape.",
    "nightshift renewed its own lock lease. Trust the lease, never the pid.",
    "product-pilot updated the map and insists we were never lost. Officially.",
    "maestro ran the tap-test suite on a simulator that swears it was awake the whole time.",
    "someone left a TODO in the codebase. The night crew does not do 'later'.",
    "the run ledger shows a five-minute gap. Nobody talks about the five-minute gap.",
    "the Hunter claims it saw a race condition. The Skeptic says it was just the wind.",
    "an approval request sat in the queue all night, staring. It's in the digest now.",
]

QUIET_LINES = [
    "No commits. No incidents. The Skeptic finds the silence suspicious and has opened an investigation.",
    "A quiet night. The Hunter sharpened its greps. The bouncer checked IDs on zero visitors, thoroughly.",
    "Nothing to report. The supervisor wrote that down anyway. The ledger must balance.",
    "All quiet. The crew played cards. The Referee won. The Skeptic demanded a recount.",
]


def fetch_events():
    """Return recent public events, or [] if the API is unreachable."""
    req = urllib.request.Request(EVENTS_URL, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except Exception:
        return []


def summarize(events):
    """Aggregate last-24h activity into {repo: {commits, prs, release}}."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    activity = {}
    for event in events:
        created = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
        if created < cutoff:
            continue
        repo = event["repo"]["name"].split("/")[-1]
        if repo == USER:
            continue  # the clerk does not report on the clerk
        entry = activity.setdefault(repo, {"commits": 0, "prs": 0, "release": None})
        kind = event["type"]
        if kind == "PushEvent":
            entry["commits"] += event["payload"].get("distinct_size", 0)
        elif kind == "PullRequestEvent":
            entry["prs"] += 1
        elif kind == "ReleaseEvent":
            entry["release"] = event["payload"]["release"].get("tag_name")
    return {repo: stats for repo, stats in activity.items() if any(stats.values())}


def bugsweep_drama(commits, rng):
    filed = commits
    if filed == 1:
        return "the Hunter filed 1 finding. The Skeptic is still circling it."
    killed = max(1, filed // 2)
    survived = filed - killed
    verdict = rng.choice([
        f"the Referee let {survived} through",
        f"the Referee upheld {survived} on appeal",
    ])
    return f"the Hunter filed {filed}; the Skeptic killed {killed}; {verdict}."


def repo_line(repo, stats, rng):
    emoji = CREW_EMOJI.get(repo, DEFAULT_EMOJI)
    parts = []
    if repo == "bugsweep" and stats["commits"]:
        parts.append(bugsweep_drama(stats["commits"], rng))
    elif stats["commits"]:
        n = stats["commits"]
        parts.append(rng.choice(COMMIT_LINES).format(n=n, s="s" if n != 1 else ""))
    if stats["release"]:
        parts.append(rng.choice(RELEASE_LINES).format(tag=stats["release"]))
    if stats["prs"]:
        n = stats["prs"]
        parts.append(rng.choice(PR_LINES).format(n=n, s="s" if n != 1 else ""))
    return f"- {emoji} **{repo}** — {' '.join(parts)}"


def build_report(activity, now):
    rng = random.Random(now.strftime("%Y-%m-%d"))  # same night, same drama
    header = f"**Shift of {now:%Y-%m-%d} — filed {now:%H:%M} UTC by the night clerk**"
    lines = [header, ""]
    if activity:
        ranked = sorted(activity.items(), key=lambda kv: kv[1]["commits"], reverse=True)
        for repo, stats in ranked[:MAX_REPO_LINES]:
            lines.append(repo_line(repo, stats, rng))
        overflow = len(ranked) - MAX_REPO_LINES
        if overflow > 0:
            lines.append(f"- …and {overflow} more. The clerk's hand cramped.")
    else:
        lines.append(f"- {rng.choice(QUIET_LINES)}")
    incident_minute = rng.randrange(60)
    lines += ["", f"*03:{incident_minute:02d} incident log: {rng.choice(INCIDENT_LOG)}*"]
    return "\n".join(lines)


def main():
    now = datetime.now(timezone.utc)
    report = build_report(summarize(fetch_events()), now)
    text = README.read_text(encoding="utf-8")
    head, rest = text.split(START_MARK, 1)
    _, tail = rest.split(END_MARK, 1)
    README.write_text(
        f"{head}{START_MARK}\n{report}\n{END_MARK}{tail}", encoding="utf-8"
    )
    print(report)


if __name__ == "__main__":
    main()
