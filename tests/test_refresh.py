"""Staying current.

Both defects here shipped silently: the archive kept serving a page that was
right when it was first fetched and wrong afterwards, with nothing to show
which was which.
"""

from __future__ import annotations

from crawler.crawl import NOTES_FIELDS, needs_notes

FETCHED = "2026-10-01T00:00:00+00:00"


def rec(**over):
    base = {
        "iid": "1",
        "updated_at": "2026-09-30T12:00:00-04:00",
        "notes": [{"id": 1}],
        "discussions": [{"id": "abc", "notes": [{"id": 1}]}],
        "notes_fetched_at": FETCHED,
    }
    return {**base, **over}


def test_a_new_comment_is_fetched_again():
    """The whole point. An issue updated after its fetch is stale."""
    assert needs_notes(rec(updated_at="2026-10-02T09:00:00-04:00"))


def test_an_unchanged_issue_is_left_alone():
    assert not needs_notes(rec())


def test_an_issue_never_fetched_is_fetched():
    stale = rec()
    del stale["notes"]
    assert needs_notes(stale)


def test_refetch_takes_everything():
    assert needs_notes(rec(), refetch=True)


def test_the_comparison_is_by_instant_not_by_string():
    """`2026-09-30T21:00:00-04:00` is 2026-10-01T01:00Z, an hour AFTER the
    fetch, while its text sorts before it. GitLab stamps in its own offset and
    we stamp in UTC, so comparing the strings would skip this one forever."""
    changed = rec(updated_at="2026-09-30T21:00:00-04:00")
    assert changed["updated_at"] < FETCHED  # the trap, stated outright
    assert needs_notes(changed)


def test_an_unprovable_record_is_fetched():
    assert needs_notes(rec(notes_fetched_at=None))
    assert needs_notes(rec(updated_at=None))
    assert needs_notes(rec(updated_at="not a timestamp"))


def test_the_skeleton_pass_carries_every_notes_field():
    """`discussions` holds the threading the pages are built from. Carrying
    `notes` alone blanked the thread on every updated issue."""
    assert set(NOTES_FIELDS) == {"notes", "discussions", "notes_fetched_at"}
    existing = rec()
    row = {"iid": "1", "updated_at": "2026-10-02T09:00:00-04:00"}
    for field in NOTES_FIELDS:
        if field in existing:
            row[field] = existing[field]
    assert row["discussions"] == existing["discussions"]
    assert row["notes"] == existing["notes"]
