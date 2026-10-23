"""NDJSON corpus on disk, keyed by iid.

NDJSON so a crawl appends without rewriting, and so git stores a day's delta as
a few changed lines rather than a reserialized blob.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent.parent / "data"


def path_for(kind: str) -> Path:
    return DATA / f"{kind}.ndjson"


class Busy(RuntimeError):
    """Another pass already holds the corpus this one wants to write."""


@contextmanager
def lock(*corpora: str, who: str) -> Iterator[None]:
    """Hold the named corpora exclusively for the length of one pass.

    One lock file per corpus, so passes writing different files still run
    together. Raises `Busy` rather than waiting, naming the holder.
    See docs/MAINTENANCE.md "One writer per corpus".
    """
    DATA.mkdir(parents=True, exist_ok=True)
    handles = []
    try:
        # Sorted, so two passes wanting the same pair cannot deadlock.
        for name in sorted(corpora):
            path = DATA / f".lock-{name}"
            fh = path.open("a+", encoding="utf-8")
            handles.append(fh)
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                fh.seek(0)
                holder = fh.read().strip() or "another pass"
                raise Busy(f"{name}: held by {holder}. Run one pass per corpus.") from exc
            fh.seek(0)
            fh.truncate()
            fh.write(f"{who}, pid {os.getpid()}, since {datetime.now(UTC).isoformat()}\n")
            fh.flush()
        yield
    finally:
        # flock belongs to the open file, so closing releases it, and the
        # kernel releases it anyway if the process is killed.
        for fh in reversed(handles):
            fh.close()


def load(kind: str) -> dict[str, dict[str, Any]]:
    """Read a corpus into `{iid: record}`. Missing file is an empty corpus."""
    p = path_for(kind)
    if not p.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            out[str(rec["iid"])] = rec
    return out


def save(kind: str, records: dict[str, dict[str, Any]]) -> Path:
    """Write the corpus sorted by numeric iid, so a diff is readable."""
    DATA.mkdir(parents=True, exist_ok=True)
    p = path_for(kind)
    tmp = p.with_suffix(".ndjson.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for iid in sorted(records, key=int):
            fh.write(json.dumps(records[iid], ensure_ascii=False, sort_keys=True))
            fh.write("\n")
    tmp.replace(p)
    return p


def load_state() -> dict[str, Any]:
    p = DATA / "state.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    p = DATA / "state.json"
    p.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
