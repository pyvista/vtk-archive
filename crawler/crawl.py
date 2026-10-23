"""Crawl drivers.

Two passes, because the host splits access two ways:

- `skeleton` needs no credential: every issue and merge request with its body,
  labels, author and dates.
- `notes` needs a `read_api` token: the comment threads, which are the whole
  reason this archive exists.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Any

from crawler import store, times
from crawler.rest import Rest, Unauthorized
from crawler.times import is_stale  # re-exported: every pass asks staleness here

KINDS = {
    "issues": {"path": "issues", "notes_path": "issues/{iid}"},
    "merge_requests": {"path": "merge_requests", "notes_path": "merge_requests/{iid}"},
}

# Items between corpus rewrites. An interrupted run loses at most this many.
CHECKPOINT = 1000

# Everything the authenticated pass attaches. Named once so the skeleton pass
# cannot carry some of it forward and drop the rest: `discussions` holds the
# threading the pages are built from, and losing it blanks a comment thread.
NOTES_FIELDS = ("notes", "discussions", "notes_fetched_at")


def needs_notes(rec: dict[str, Any], *, refetch: bool = False) -> bool:
    """Whether this record's comment threads must be fetched again."""
    if refetch or "notes" not in rec:
        return True
    return is_stale(rec.get("updated_at"), rec.get("notes_fetched_at"))


def _progress(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr, flush=True)


def skeleton(
    kind: str,
    *,
    updated_after: str | None = None,
    limit: int | None = None,
    delay: float = 0.2,
) -> dict[str, Any]:
    """Fetch every issue or MR with its body. Works unauthenticated."""
    spec = KINDS[kind]
    with store.lock(kind, who=f"skeleton {kind}"):
        records = store.load(kind)
        state = store.load_state()
        slot = state.setdefault(kind, {})

        seen = 0
        newest = slot.get("skeleton_through")

        with Rest(delay=delay) as api:
            for rows in api.walk_updated(spec["path"], since=updated_after, state="all"):
                for row in rows:
                    iid = str(row["iid"])
                    existing = records.get(iid, {})
                    # Carry forward everything the authenticated pass attached.
                    # The row GitLab just returned has none of it, so a field
                    # missed here is deleted from the corpus.
                    for field in NOTES_FIELDS:
                        if field in existing:
                            row[field] = existing[field]
                    records[iid] = row
                    seen += 1
                # The watermark is handed back to GitLab as `updated_after`, so
                # a string compare across the DST boundary would advance it too
                # far and skip every record in between. Compare instants.
                newest = times.newest([newest, *(r.get("updated_at") for r in rows)])
                store.save(kind, records)
                slot["skeleton_through"] = newest
                slot["count"] = len(records)
                store.save_state(state)
                _progress(
                    f"{kind}: {seen} fetched, {len(records)} held, "
                    f"{api.stats.requests} req, {api.stats.seconds:.0f}s"
                )
                if limit is not None and seen >= limit:
                    break
            stats = api.stats

    return {
        "pass": "skeleton",
        "kind": kind,
        "fetched": seen,
        "total_held": len(records),
        "requests": stats.requests,
        "retries": stats.retries,
        "seconds": round(stats.seconds, 1),
        "mib_in": round(stats.bytes_in / 1048576, 1),
        "skeleton_through": newest,
    }


def notes(
    kind: str,
    *,
    limit: int | None = None,
    delay: float = 0.2,
    refetch: bool = False,
) -> dict[str, Any]:
    """Attach comment threads to records already on disk. Needs a token."""
    spec = KINDS[kind]
    with store.lock(kind, who=f"notes {kind}"):
        return _notes(kind, spec, limit=limit, delay=delay, refetch=refetch)


def _notes(
    kind: str,
    spec: dict[str, str],
    *,
    limit: int | None,
    delay: float,
    refetch: bool,
) -> dict[str, Any]:
    records = store.load(kind)
    if not records:
        raise RuntimeError(f"no {kind} on disk; run the skeleton pass first")

    with Rest(delay=delay) as api:
        who = api.whoami()
        if not who:
            raise Unauthorized(
                "the notes pass needs a gitlab.kitware.com token with read_api scope. "
                "Put GITLAB_TOKEN in ~/.config/vtk-archive/env. See README 'Access'."
            )
        _progress(f"authenticated as {who['username']} ({who.get('name')})")

        # Prefer the oldest-updated first so an interrupted run resumes sanely.
        pending = [
            iid for iid in sorted(records, key=int) if needs_notes(records[iid], refetch=refetch)
        ]
        if limit is not None:
            pending = pending[:limit]
        fresh = sum(1 for iid in pending if "notes" not in records[iid])
        _progress(
            f"{kind}: {len(pending)} of {len(records)} need notes "
            f"({fresh} never fetched, {len(pending) - fresh} changed since)"
        )

        done = 0
        failed = 0
        total_notes = 0
        for iid in pending:
            rec = records[iid]
            base = spec["notes_path"].format(iid=iid)
            # Stamped BEFORE the first request: a comment posted inside a
            # multi-page fetch would sort below an after-the-fact stamp and
            # never be seen again. docs/MAINTENANCE.md "Staying current".
            started = _now()
            try:
                # /discussions carries every note /notes does, plus threading.
                discussions = api.all_rows(f"{base}/discussions")
            except Exception as exc:  # noqa: BLE001 — one bad item is data, not a crash
                # A deleted or confidential item otherwise raises at the same
                # deterministic point on every run, starving everything after it.
                failed += 1
                rec["notes_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                _progress(f"{kind} {iid}: {rec['notes_error']}")
                continue
            rec.pop("notes_error", None)
            rec["discussions"] = discussions
            rec["notes"] = sorted(
                (n for d in discussions for n in d["notes"]),
                key=lambda n: (_instant(n.get("created_at")), n.get("id") or 0),
            )
            rec["notes_fetched_at"] = started
            total_notes += len(rec["notes"])
            done += 1
            # A checkpoint rewrites the whole corpus, which reaches ~200 MB once
            # notes land, so checkpoint rarely. Progress is reported either way.
            last = done == len(pending)
            if done % CHECKPOINT == 0 or last:
                store.save(kind, records)
            if done % 250 == 0 or last:
                _progress(
                    f"{kind}: {done}/{len(pending)} items, {total_notes} notes, "
                    f"{api.stats.requests} req, {api.stats.seconds:.0f}s"
                )
        store.save(kind, records)
        stats = api.stats

    state = store.load_state()
    slot = state.setdefault(kind, {})
    slot["notes_complete"] = sum(1 for r in records.values() if "notes" in r)
    # Freshness, not presence. `notes_complete` reads 100% over an arbitrarily
    # stale corpus, so it can never go red; this one can.
    slot["notes_stale"] = sum(1 for r in records.values() if needs_notes(r))
    slot["notes_failed"] = sum(1 for r in records.values() if r.get("notes_error"))
    store.save_state(state)

    return {
        "pass": "notes",
        "kind": kind,
        "items": done,
        "failed": failed,
        "notes": total_notes,
        "requests": stats.requests,
        "retries": stats.retries,
        "seconds": round(stats.seconds, 1),
        "mib_in": round(stats.bytes_in / 1048576, 1),
        "unauthorized_paths": sorted(stats.unauthorized),
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# The corpus holds `created_at` in two offsets, because GitLab stamps local
# time across a DST change. Sorting the text puts a thread out of order.
_instant = times.instant
