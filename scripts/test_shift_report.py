import io
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import shift_report


def metadata(repo, *, private=False, archived=False, fork=False):
    return {
        "name": repo,
        "full_name": f"{shift_report.USER}/{repo}",
        "owner": {"login": shift_report.USER},
        "private": private,
        "visibility": "private" if private else "public",
        "archived": archived,
        "fork": fork,
        "default_branch": "main",
        "html_url": f"https://github.com/{shift_report.USER}/{repo}",
    }


def commit_payload(repo, sha="a" * 40, date="2026-10-08T01:02:03Z"):
    return [
        {
            "sha": sha,
            "html_url": f"https://github.com/{shift_report.USER}/{repo}/commit/{sha}",
            "commit": {"committer": {"date": date}},
        }
    ]


def release_payload(repo, tag="v1.2.3", date="2026-09-28T01:02:03Z"):
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "published_at": date,
        "html_url": f"https://github.com/{shift_report.USER}/{repo}/releases/tag/{tag}",
    }


def valid_fetcher(*, missing_release=None, calls=None):
    missing_release = missing_release or set()

    def fetch(url, *, allow_release_404=False):
        if calls is not None:
            calls.append((url, allow_release_404))
        for repo in shift_report.REPOSITORIES:
            base = f"{shift_report.API_ROOT}/repos/{shift_report.USER}/{repo}"
            if url == base:
                return metadata(repo)
            if url.startswith(f"{base}/commits?"):
                return commit_payload(repo)
            if url == f"{base}/releases/latest":
                if repo in missing_release:
                    return None
                return release_payload(repo)
        raise AssertionError(f"unexpected URL: {url}")

    return fetch


class ShiftReportTests(unittest.TestCase):
    def test_private_repository_is_rejected_before_commit_fetches(self):
        calls = []

        def fetch(url, *, allow_release_404=False):
            calls.append(url)
            if url == f"{shift_report.API_ROOT}/repos/{shift_report.USER}/llm-prompt-guard":
                return metadata("llm-prompt-guard", private=True)
            raise AssertionError("metadata validation should stop before another request")

        with patch.object(shift_report, "_fetch_json", side_effect=fetch):
            with self.assertRaises(shift_report.UpdateError):
                shift_report.fetch_portfolio()
        self.assertEqual(
            calls,
            [f"{shift_report.API_ROOT}/repos/{shift_report.USER}/llm-prompt-guard"],
        )

    def test_api_failure_leaves_readme_unchanged(self):
        original = (
            "before\n"
            f"{shift_report.START_MARK}\nold report\n{shift_report.END_MARK}\n"
            "after\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(original, encoding="utf-8")
            with patch.object(shift_report, "README", readme), patch.object(
                shift_report,
                "_fetch_json",
                side_effect=shift_report.UpdateError("github_api_failure"),
            ):
                with patch("sys.stderr", new_callable=io.StringIO):
                    self.assertEqual(shift_report.main(), 1)
            self.assertEqual(readme.read_text(encoding="utf-8"), original)

    def test_release_404_is_treated_as_missing_release(self):
        error = urllib.error.HTTPError(
            "https://api.github.com/example", 404, "not found", {}, None
        )
        with patch.object(shift_report.urllib.request, "urlopen", side_effect=error):
            self.assertIsNone(
                shift_report._fetch_json(
                    "https://api.github.com/repos/shanemhamilton/baton/releases/latest",
                    allow_release_404=True,
                )
            )

    def test_markers_preserve_surrounding_readme_text(self):
        original = (
            "prefix\n"
            f"{shift_report.START_MARK}\nold report\n{shift_report.END_MARK}\n"
            "suffix\n"
        )
        updated = shift_report.replace_shift_report(original, "new report")
        self.assertEqual(
            updated,
            "prefix\n"
            f"{shift_report.START_MARK}\nnew report\n{shift_report.END_MARK}\n"
            "suffix\n",
        )
        self.assertEqual(updated.count(shift_report.START_MARK), 1)
        self.assertEqual(updated.count(shift_report.END_MARK), 1)

    def test_missing_duplicate_and_reversed_markers_fail(self):
        cases = (
            "prefix\nno markers\nsuffix\n",
            f"{shift_report.START_MARK}\n{shift_report.START_MARK}\n{shift_report.END_MARK}",
            f"{shift_report.END_MARK}\n{shift_report.START_MARK}",
        )
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(shift_report.UpdateError):
                    shift_report.validate_markers(text)

    def test_rendering_is_factual_ordered_and_uses_release_dash(self):
        snapshots = []
        for index, repo in enumerate(shift_report.REPOSITORIES):
            sha = f"{index + 1:x}" * 40
            snapshots.append(
                {
                    "repo": repo,
                    "repo_url": f"https://github.com/{shift_report.USER}/{repo}",
                    "commit": {
                        "sha": sha[:7],
                        "date": "2026-10-08",
                        "url": f"https://github.com/{shift_report.USER}/{repo}/commit/{sha}",
                    },
                    "release": None if repo == "baton" else {
                        "tag": "v1.2.3",
                        "date": "2026-09-28",
                        "url": f"https://github.com/{shift_report.USER}/{repo}/releases/tag/v1.2.3",
                    },
                }
            )

        report = shift_report.render_report(
            snapshots, datetime(2026, 10, 8, 4, 5, tzinfo=timezone.utc)
        )
        self.assertIn("**Portfolio snapshot — checked 2026-10-08 04:05 UTC**", report)
        self.assertIn("[1111111](https://github.com/shanemhamilton/llm-prompt-guard/commit/", report)
        self.assertIn("[v1.2.3](https://github.com/shanemhamilton/bugsweep/releases/tag/v1.2.3)", report)
        self.assertIn("| [baton](https://github.com/shanemhamilton/baton) |", report)
        self.assertIn("| — |", report)
        positions = [report.index(f"| [{repo}]") for repo in shift_report.REPOSITORIES]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("pull request", report.lower())
        self.assertNotIn("precision", report.lower())
        self.assertNotIn("recall", report.lower())

    def test_missing_stable_release_renders_an_em_dash(self):
        with patch.object(
            shift_report,
            "_fetch_json",
            side_effect=valid_fetcher(missing_release={"baton"}),
        ):
            snapshots = shift_report.fetch_portfolio()
        report = shift_report.render_report(
            snapshots, datetime(2026, 10, 8, 4, 5, tzinfo=timezone.utc)
        )
        baton_row = next(line for line in report.splitlines() if "[baton](" in line)
        self.assertTrue(baton_row.rstrip().endswith("| — |"))

    def test_metadata_is_fetched_before_commit_and_release_data(self):
        calls = []
        with patch.object(
            shift_report, "_fetch_json", side_effect=valid_fetcher(calls=calls)
        ):
            shift_report.fetch_portfolio()
        metadata_urls = [
            f"{shift_report.API_ROOT}/repos/{shift_report.USER}/{repo}"
            for repo in shift_report.REPOSITORIES
        ]
        self.assertEqual(calls[: len(metadata_urls)], [(url, False) for url in metadata_urls])
        self.assertTrue(any("/commits?" in url for url, _ in calls[len(metadata_urls) :]))
        self.assertTrue(any("/releases/latest" in url for url, _ in calls[len(metadata_urls) :]))


if __name__ == "__main__":
    unittest.main()
