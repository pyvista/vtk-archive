"""The snapshot stamp in the banner.

It comes from `/snapshot.json` rather than the page, because an item page is
reused across runs and a baked stamp would read as the archive's age when it is
only that page's. Its other job is to go red when the daily refresh stops
running, so the number and the link have to be right.
"""

from __future__ import annotations

from datetime import UTC, datetime

from builder.build import STALE_AFTER_HOURS, WORKFLOW_URL, _snapshot

NOW = datetime(2026, 10, 23, 17, 42, 58, tzinfo=UTC)


def test_the_stamp_carries_a_time_not_just_a_date():
    """A date alone cannot answer "did today's run happen"."""
    assert _snapshot(NOW)["generated"] == "2026-10-23T17:42:58+00:00"


def test_the_threshold_travels_with_the_stamp():
    """The page reads it from here; two copies would disagree."""
    assert _snapshot(NOW)["stale_after_hours"] == STALE_AFTER_HOURS
    assert STALE_AFTER_HOURS > 24, "a daily cron is not stale for missing one run"


def test_off_a_runner_the_link_goes_to_the_workflow(monkeypatch):
    for var in ("GITHUB_RUN_ID", "GITHUB_REPOSITORY", "GITHUB_SERVER_URL"):
        monkeypatch.delenv(var, raising=False)
    assert _snapshot(NOW)["run_url"] == WORKFLOW_URL


def test_on_a_runner_the_link_goes_to_the_run_that_built_it(monkeypatch):
    """The whole point is seeing *why* a refresh failed, which is the run."""
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_REPOSITORY", "pyvista/vtk-archive")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    url = _snapshot(NOW)["run_url"]
    assert url == "https://github.com/pyvista/vtk-archive/actions/runs/12345"


def test_a_partial_runner_environment_falls_back(monkeypatch):
    """`GITHUB_RUN_ID` without the repository built `.../None/actions/...`."""
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert _snapshot(NOW)["run_url"] == WORKFLOW_URL
