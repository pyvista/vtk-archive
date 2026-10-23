"""Paging a list that is changing under you.

The skeleton pass walks every issue and merge request ordered by `updated_at`,
which is the column the walk itself is racing. Offset pagination over that list
drops a row, and the dropped row's `updated_at` stays below the watermark, so
no later incremental run revisits it either: the record is gone for good.
"""

from __future__ import annotations

from types import SimpleNamespace

from crawler.rest import Rest
from crawler.times import instant

ROWS = [
    {"iid": 1, "updated_at": "2026-01-01T00:00:00+00:00"},
    {"iid": 2, "updated_at": "2026-01-02T00:00:00+00:00"},
    {"iid": 3, "updated_at": "2026-01-03T00:00:00+00:00"},
    {"iid": 4, "updated_at": "2026-01-04T00:00:00+00:00"},
    {"iid": 5, "updated_at": "2026-01-05T00:00:00+00:00"},
]
BUMPED = "2026-01-06T00:00:00+00:00"


class Host:
    """A list endpoint where one row is edited after the first page is served.

    This is the ordinary case, not a contrived one: VTK's merge requests are
    touched constantly and a full walk takes minutes.
    """

    walk_updated = Rest.walk_updated

    def __init__(self, rows=ROWS, *, per_page=2, bump=True):
        self.rows = [dict(r) for r in rows]
        self.served = 0
        self.per_page = per_page
        self.bump = bump
        self.seen: set[int] = set()

    def get(self, path, *, per_page, page, updated_after=None, **params):
        rows = sorted(self.rows, key=lambda r: instant(r["updated_at"]))
        if updated_after:
            rows = [r for r in rows if instant(r["updated_at"]) >= instant(updated_after)]
        # Copied, because a real response is a snapshot: an edit that lands
        # after it was sent does not reach back into it.
        chunk = [dict(r) for r in rows[(page - 1) * per_page : page * per_page]]
        self.served += 1
        if self.bump and self.served == 1:
            # Someone comments on issue 1 while we are on page two.
            self.rows[0]["updated_at"] = BUMPED
        for row in chunk:
            self.seen.add(row["iid"])
        return SimpleNamespace(json=lambda: chunk)

    def offset_walk(self):
        """What the pass used to do: hold the filter, step the page number."""
        page = 1
        while True:
            chunk = self.get("x", per_page=self.per_page, page=page)
            if not chunk.json():
                return
            page += 1


def test_offset_paging_drops_a_row_when_one_is_edited_mid_walk():
    host = Host()
    host.offset_walk()
    assert host.seen == {1, 2, 4, 5}, "issue 3 slid across the page boundary"


def test_the_timestamp_cursor_drops_nothing():
    host = Host()
    for _ in host.walk_updated("x", per_page=2):
        pass
    assert host.seen == {1, 2, 3, 4, 5}


def test_a_page_that_cannot_advance_the_cursor_steps_the_offset():
    """Every row sharing one timestamp would otherwise re-serve page one
    forever, because `updated_after` is inclusive."""
    same = [{"iid": r["iid"], "updated_at": "2026-01-01T00:00:00+00:00"} for r in ROWS]
    host = Host(same, bump=False)
    # Terminates, and covers everything. A re-anchor re-serves the rows
    # carrying the cursor's own stamp, so duplicates are expected.
    for _ in host.walk_updated("x", per_page=2):
        pass
    assert host.seen == {1, 2, 3, 4, 5}


def test_the_cursor_is_the_stamp_gitlab_wrote_not_a_reformatted_one():
    """It goes straight back as `updated_after`, so it stays verbatim."""
    host = Host([{"iid": 9, "updated_at": "2026-09-30T21:00:00-04:00"}], bump=False)
    seen: list[str | None] = []
    real = host.get

    def spy(path, **kw):
        seen.append(kw.get("updated_after"))
        return real(path, **kw)

    host.get = spy
    for _ in host.walk_updated("x", per_page=2):
        pass
    assert "2026-09-30T21:00:00-04:00" in seen
