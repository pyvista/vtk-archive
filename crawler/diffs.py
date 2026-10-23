"""Fetch the code change on every merge request.

Kept in `data/mr_diffs.ndjson`, not in the merge-request corpus: diffs are
roughly 1.5 GB against the corpus's 400 MB, and a separate file means this
pass and the notes pass can run at the same time without either one's
`store.save` clobbering the other's work.

`/diffs` is the final state of the branch. `/versions` serves each review
round separately and is reachable with this token, but is not collected here —
see docs/SOURCES.md "What the archive does not hold".
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crawler import store
from crawler.crawl import is_stale
from crawler.rest import Rest, Unauthorized

PATH = store.DATA / "mr_diffs.ndjson"
CHECKPOINT = 250

# GitLab answers `/diffs` with at most this many files; beyond it the change is
# recorded as truncated and the page sends the reader to the canonical MR.
MAX_FILES = 1000


def load() -> dict[str, dict[str, Any]]:
    if not PATH.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with PATH.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rec = json.loads(line)
                out[str(rec["iid"])] = rec
    return out


def save(records: dict[str, dict[str, Any]]) -> Path:
    store.DATA.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".ndjson.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for iid in sorted(records, key=int):
            fh.write(json.dumps(records[iid], ensure_ascii=False, sort_keys=True))
            fh.write("\n")
    tmp.replace(PATH)
    return PATH


def _progress(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr, flush=True)


def crawl(*, limit: int | None = None, delay: float = 0.2, refetch: bool = False) -> dict[str, Any]:
    """Attach the file-by-file diff to every merge request already on disk."""
    # `mr_diffs` only, so this still runs beside `notes`, which writes the
    # merge-request corpus itself and is only read here.
    with store.lock("mr_diffs", who="diffs"):
        return _crawl(limit=limit, delay=delay, refetch=refetch)


def _crawl(*, limit: int | None, delay: float, refetch: bool) -> dict[str, Any]:
    mrs = store.load("merge_requests")
    if not mrs:
        raise RuntimeError("no merge_requests on disk; run the skeleton pass first")

    held = load()
    # `/diffs` is the branch's final state, so every push to an open merge
    # request changes it. Presence alone froze all 357 at first fetch.
    pending = [
        iid
        for iid in sorted(mrs, key=int)
        if refetch
        or iid not in held
        or is_stale(mrs[iid].get("updated_at"), held[iid].get("fetched_at"))
    ]
    if limit is not None:
        pending = pending[:limit]

    with Rest(delay=delay) as api:
        who = api.whoami()
        if not who:
            raise Unauthorized(
                "the diffs pass needs a gitlab.kitware.com token with read_api scope. "
                "Put GITLAB_TOKEN in ~/.config/vtk-archive/env. See README 'Access'."
            )
        _progress(f"authenticated as {who['username']}")
        _progress(f"merge_requests: {len(pending)} of {len(mrs)} need diffs")

        done = 0
        failed = 0
        files_total = 0
        for iid in pending:
            # Stamped before the request, for the reason `crawl.notes` is.
            started = datetime.now(UTC).isoformat(timespec="seconds")
            try:
                rows = api.all_rows(f"merge_requests/{iid}/diffs", per_page=100)
            except Exception as exc:  # noqa: BLE001 — one bad MR is data, not a crash
                failed += 1
                _progress(f"merge_requests {iid}: {type(exc).__name__}: {str(exc)[:100]}")
                continue
            held[iid] = {
                "iid": iid,
                "fetched_at": started,
                "truncated": len(rows) >= MAX_FILES,
                "files": rows,
            }
            files_total += len(rows)
            done += 1
            last = done == len(pending)
            if done % CHECKPOINT == 0 or last:
                save(held)
            if done % 250 == 0 or last:
                _progress(
                    f"diffs: {done}/{len(pending)} MRs, {files_total} files, "
                    f"{api.stats.requests} req, {api.stats.seconds:.0f}s, "
                    f"{api.stats.bytes_in / 1048576:.0f} MiB"
                )
        save(held)
        stats = api.stats

    return {
        "pass": "diffs",
        "merge_requests": done,
        "failed": failed,
        "files": files_total,
        "total_held": len(held),
        "requests": stats.requests,
        "retries": stats.retries,
        "seconds": round(stats.seconds, 1),
        "mib_in": round(stats.bytes_in / 1048576, 1),
    }
