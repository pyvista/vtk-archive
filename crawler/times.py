"""Comparing GitLab's timestamps.

Every staleness decision and every watermark in the crawler turns on one
comparison, and getting it wrong loses content silently. GitLab stamps in its
own local offset and crosses a DST boundary twice a year, so the corpus holds
both `-04:00` and `-05:00`, and we stamp in UTC. Text order and time order
disagree:

    2026-09-30T21:00:00-04:00   is LATER than
    2026-10-01T00:00:00+00:00   and sorts EARLIER as a string.

So nothing here compares stamps as strings.
"""

from __future__ import annotations

from datetime import UTC, datetime

EPOCH = datetime.min.replace(tzinfo=UTC)


def instant(stamp: str | None) -> datetime:
    """A comparable instant, or the epoch when there is nothing to compare."""
    if not stamp:
        return EPOCH
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return EPOCH


def is_stale(updated_at: str | None, fetched_at: str | None) -> bool:
    """Whether a record changed after we last fetched the thing beside it.

    Anything unprovable resolves toward fetching: a wrongly re-fetched record
    costs one request, and a wrongly skipped one loses the content
    permanently, because nothing later revisits it.
    """
    if not fetched_at or not updated_at:
        return True
    try:
        return datetime.fromisoformat(updated_at) > datetime.fromisoformat(fetched_at)
    except ValueError:
        return True


def newest(stamps) -> str | None:
    """The latest of some stamps, by instant, keeping the text as written.

    A watermark is handed back to GitLab as `updated_after`, so it has to stay
    the exact string GitLab gave us rather than a reformatted one.
    """
    kept: str | None = None
    for stamp in stamps:
        if not stamp:
            continue
        if kept is None or instant(stamp) > instant(kept):
            kept = stamp
    return kept
