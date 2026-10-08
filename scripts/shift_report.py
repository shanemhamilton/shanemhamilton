#!/usr/bin/env python3
"""Update the profile README with factual public GitHub portfolio state."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


USER = "shanemhamilton"
REPOSITORIES = (
    "llm-prompt-guard",
    "bugsweep",
    "nightshift",
    "baton",
    "unify-agent-docs",
    "smokejumper",
)
README = Path(__file__).resolve().parent.parent / "README.md"
START_MARK = "<!-- SHIFT-REPORT:START -->"
END_MARK = "<!-- SHIFT-REPORT:END -->"
API_ROOT = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 2_000_000
HEX_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


class UpdateError(Exception):
    """A safe, non-sensitive updater failure code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _api_url(repo: str, suffix: str = "") -> str:
    return f"{API_ROOT}/repos/{USER}/{repo}{suffix}"


def _request_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "shanemhamilton-profile-shift-report",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_json(url: str, *, allow_release_404: bool = False) -> Any:
    """Fetch a bounded JSON response without exposing response or exception data."""
    request = urllib.request.Request(url, headers=_request_headers())
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        if allow_release_404 and error.code == 404:
            return None
        raise UpdateError("github_api_failure") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UpdateError("github_api_failure") from None
    except Exception:
        raise UpdateError("github_api_failure") from None

    if len(body) > MAX_RESPONSE_BYTES:
        raise UpdateError("github_response_too_large")
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        raise UpdateError("github_schema_failure") from None


def _require(condition: bool, code: str = "github_schema_failure") -> None:
    if not condition:
        raise UpdateError(code)


def _parse_utc(value: Any) -> datetime:
    _require(isinstance(value, str) and value and "\n" not in value and "\r" not in value)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise UpdateError("github_schema_failure") from None
    _require(parsed.tzinfo is not None)
    return parsed.astimezone(timezone.utc)


def _validate_https_github_url(value: Any, expected_path: str | None = None) -> str:
    _require(isinstance(value, str) and value)
    try:
        parsed = urllib.parse.urlparse(value)
    except ValueError:
        raise UpdateError("github_schema_failure") from None
    _require(
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
        and not parsed.params
    )
    if expected_path is not None:
        _require(parsed.path == expected_path)
    return value


def _validate_metadata(payload: Any, repo: str) -> dict[str, str]:
    _require(isinstance(payload, dict))
    owner = payload.get("owner")
    _require(isinstance(owner, dict) and owner.get("login") == USER)
    _require(payload.get("name") == repo)
    _require(payload.get("full_name") == f"{USER}/{repo}")
    _require(payload.get("private") is False)
    _require(payload.get("visibility") == "public")
    _require(payload.get("archived") is False)
    _require(payload.get("fork") is False)
    repo_url = _validate_https_github_url(payload.get("html_url"), f"/{USER}/{repo}")
    default_branch = payload.get("default_branch")
    _require(
        isinstance(default_branch, str)
        and bool(default_branch)
        and len(default_branch) <= 255
        and not any(ord(char) < 32 for char in default_branch)
    )
    return {"branch": default_branch, "url": repo_url}


def _validate_commit(payload: Any, repo: str) -> dict[str, str]:
    _require(isinstance(payload, list) and len(payload) >= 1)
    commit = payload[0]
    _require(isinstance(commit, dict))
    sha = commit.get("sha")
    _require(isinstance(sha, str) and HEX_SHA_RE.fullmatch(sha) is not None)
    commit_details = commit.get("commit")
    _require(isinstance(commit_details, dict))
    committer = commit_details.get("committer")
    _require(isinstance(committer, dict))
    date = _parse_utc(committer.get("date"))
    url = _validate_https_github_url(
        commit.get("html_url"), f"/{USER}/{repo}/commit/{sha}"
    )
    return {"sha": sha[:7], "date": date.strftime("%Y-%m-%d"), "url": url}


def _escape_table_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _validate_release(payload: Any, repo: str) -> dict[str, str] | None:
    if payload is None:
        return None
    _require(isinstance(payload, dict))
    _require(payload.get("draft") is False and payload.get("prerelease") is False)
    tag = payload.get("tag_name")
    _require(
        isinstance(tag, str)
        and bool(tag)
        and len(tag) <= 128
        and not any(ord(char) < 32 for char in tag)
    )
    published = _parse_utc(payload.get("published_at"))
    url = _validate_https_github_url(payload.get("html_url"))
    path_prefix = f"/{USER}/{repo}/releases/tag/"
    parsed_path = urllib.parse.urlsplit(url).path
    _require(parsed_path.startswith(path_prefix))
    encoded_tag = parsed_path[len(path_prefix) :]
    _require(bool(encoded_tag) and urllib.parse.unquote(encoded_tag) == tag)
    return {
        "tag": tag,
        "date": published.strftime("%Y-%m-%d"),
        "url": url,
    }


def fetch_portfolio() -> list[dict[str, Any]]:
    """Fetch and validate the six repositories in their display order."""
    repositories: dict[str, dict[str, str]] = {}

    # Validate every repository before requesting any commit or release data.
    for repo in REPOSITORIES:
        repositories[repo] = _validate_metadata(_fetch_json(_api_url(repo)), repo)

    snapshots: list[dict[str, Any]] = []
    for repo in REPOSITORIES:
        branch = repositories[repo]["branch"]
        commit_query = urllib.parse.urlencode({"sha": branch, "per_page": "1"})
        commit_payload = _fetch_json(_api_url(repo, f"/commits?{commit_query}"))
        release_payload = _fetch_json(
            _api_url(repo, "/releases/latest"), allow_release_404=True
        )
        snapshots.append(
            {
                "repo": repo,
                "repo_url": repositories[repo]["url"],
                "commit": _validate_commit(commit_payload, repo),
                "release": _validate_release(release_payload, repo),
            }
        )
    return snapshots


def validate_markers(text: str) -> None:
    if text.count(START_MARK) != 1 or text.count(END_MARK) != 1:
        raise UpdateError("invalid_shift_markers")
    if text.index(START_MARK) > text.index(END_MARK):
        raise UpdateError("reversed_shift_markers")


def render_report(snapshots: list[dict[str, Any]], checked_at: datetime) -> str:
    _require(len(snapshots) == len(REPOSITORIES), "render_failure")
    checked_at = checked_at.astimezone(timezone.utc)
    lines = [
        f"**Portfolio snapshot — checked {checked_at:%Y-%m-%d %H:%M} UTC**",
        "",
        "| Project | Latest default-branch commit (UTC) | Latest stable release (UTC) |",
        "|---|---|---|",
    ]
    for snapshot in snapshots:
        repo = snapshot["repo"]
        commit = snapshot["commit"]
        release = snapshot["release"]
        repo_link = f"[{repo}]({snapshot['repo_url']})"
        commit_link = f"[{commit['sha']}]({commit['url']}) — {commit['date']}"
        if release is None:
            release_cell = "—"
        else:
            release_cell = (
                f"[{_escape_table_text(release['tag'])}]({release['url']}) — "
                f"{release['date']}"
            )
        lines.append(f"| {repo_link} | {commit_link} | {release_cell} |")
    lines.extend(
        [
            "",
            "_Source: public GitHub repository data. Commits may include unreleased work; stable releases exclude prereleases._",
        ]
    )
    return "\n".join(lines)


def replace_shift_report(text: str, report: str) -> str:
    validate_markers(text)
    start = text.index(START_MARK)
    end = text.index(END_MARK)
    return f"{text[:start]}{START_MARK}\n{report}\n{END_MARK}{text[end + len(END_MARK):]}"


def main() -> int:
    try:
        existing = README.read_text(encoding="utf-8")
        validate_markers(existing)
        snapshots = fetch_portfolio()
        report = render_report(snapshots, datetime.now(timezone.utc))
        updated = replace_shift_report(existing, report)
        README.write_text(updated, encoding="utf-8")
    except UpdateError as error:
        print(f"shift-report: update skipped ({error.code})", file=sys.stderr)
        return 1
    except OSError:
        print("shift-report: update skipped (readme_io)", file=sys.stderr)
        return 1

    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
