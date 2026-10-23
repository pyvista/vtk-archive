"""Fetch the files referenced from issue and comment bodies.

Comment markdown spells an attachment `/uploads/<hash>/<name>`, but that path
under the project is a 404. The form that serves the file is
`/-/project/13/uploads/<hash>/<name>`. Getting this wrong silently drops every
attachment, so the fetcher records a per-file status rather than skipping.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

from crawler import store
from crawler.crawl import KINDS
from crawler.rest import PROJECT_ID, USER_AGENT, load_token

UPLOAD_RE = re.compile(r"/uploads/([0-9a-f]{32})/([^\s\)\]\"'>]+)")
# The /-/project/13/uploads/ form is an HTML route and takes the Anubis
# User-Agent allowlist: 709 of 1867 came back 403. The /api/v4 form is not
# gated and serves the same bytes. docs/SOURCES.md
SERVE = f"https://gitlab.kitware.com/api/v4/projects/{PROJECT_ID}/uploads"
OUT = Path(__file__).resolve().parent.parent / "attachments"
MANIFEST = store.DATA / "attachments.json"

# A 3 MB mesh in a bug report is normal here; a multi-GB one is not worth the
# bucket. Anything larger is recorded as skipped, with its size.
MAX_BYTES = 64 * 1024 * 1024


def _bodies(record: dict[str, Any]):
    yield record.get("description") or ""
    for note in record.get("notes") or []:
        yield note.get("body") or ""


def discover() -> dict[str, dict[str, Any]]:
    """Map `hash/name` to the issues and MRs that reference it."""
    refs: dict[str, dict[str, Any]] = {}
    for kind in sorted(KINDS):
        for iid, record in store.load(kind).items():
            for body in _bodies(record):
                for digest, name in UPLOAD_RE.findall(body):
                    key = f"{digest}/{name}"
                    entry = refs.setdefault(key, {"digest": digest, "name": name, "refs": []})
                    entry["refs"].append(f"{kind}#{iid}")
    return refs


def held(key: str, manifest: dict[str, Any]) -> bool:
    """Whether this attachment's bytes are actually on disk, not just claimed."""
    return manifest.get(key, {}).get("status") == "ok" and (OUT / key).exists()


def ok_keys(manifest: dict[str, Any]) -> frozenset[str]:
    """Every attachment the archive can really serve."""
    return frozenset(k for k in manifest if held(k, manifest))


def answered(key: str, manifest: dict[str, Any]) -> bool:
    """Whether the host has already told us about this key.

    `too_large` is an answer, so it is not probed again every run. Raising
    `MAX_BYTES` therefore needs `attachments --refetch`.
    """
    return held(key, manifest) or manifest.get(key, {}).get("status") == "too_large"


def _save(manifest: dict[str, Any]) -> None:
    """Replace the manifest atomically.

    A plain `write_text` over 3,457 entries truncates the file if the run dies
    mid-write, and the manifest is the only record of what was fetched.
    """
    store.DATA.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(MANIFEST)


def fetch(*, limit: int | None = None, delay: float = 0.2, refetch: bool = False) -> dict[str, Any]:
    with store.lock("attachments", who="attachments"):
        return _fetch(limit=limit, delay=delay, refetch=refetch)


def _fetch(*, limit: int | None, delay: float, refetch: bool) -> dict[str, Any]:
    refs = discover()
    manifest: dict[str, Any] = {}
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    # The manifest is committed and `attachments/` is not, so "the manifest
    # says ok" does not mean the bytes are here. On a fresh checkout the file
    # check is the only thing that re-fetches them.
    pending = [k for k in sorted(refs) if refetch or not answered(k, manifest)]
    if limit is not None:
        pending = pending[:limit]
    print(
        f"  {len(refs)} referenced, {len(refs) - len(pending)} already stored, "
        f"{len(pending)} to fetch",
        file=sys.stderr,
    )

    headers = {"User-Agent": USER_AGENT}
    if token := load_token():
        headers["PRIVATE-TOKEN"] = token

    OUT.mkdir(parents=True, exist_ok=True)
    counts = {"ok": 0, "too_large": 0, "failed": 0}
    total_bytes = 0

    with httpx.Client(timeout=180.0, headers=headers, follow_redirects=True) as client:
        for i, key in enumerate(pending, 1):
            entry = dict(refs[key])
            dest = OUT / entry["digest"] / entry["name"]
            try:
                head = client.head(f"{SERVE}/{key}")
                size = int(head.headers.get("content-length") or 0)
                if size > MAX_BYTES:
                    entry |= {"status": "too_large", "bytes": size}
                    counts["too_large"] += 1
                    manifest[key] = entry
                    continue
                r = client.get(f"{SERVE}/{key}")
                r.raise_for_status()
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(r.content)
                entry |= {
                    "status": "ok",
                    "bytes": len(r.content),
                    "content_type": r.headers.get("content-type"),
                    "path": str(dest.relative_to(OUT)),
                }
                counts["ok"] += 1
                total_bytes += len(r.content)
            except Exception as exc:  # noqa: BLE001 — a failed file is data, not a crash
                entry |= {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
                counts["failed"] += 1
            manifest[key] = entry
            if i % 100 == 0 or i == len(pending):
                _save(manifest)
                print(
                    f"  {i}/{len(pending)}  ok={counts['ok']} "
                    f"large={counts['too_large']} failed={counts['failed']} "
                    f"{total_bytes / 1048576:.0f} MiB",
                    file=sys.stderr,
                    flush=True,
                )

    _save(manifest)
    return {
        "pass": "attachments",
        "referenced": len(refs),
        "attempted": len(pending),
        **counts,
        "mib": round(total_bytes / 1048576, 1),
    }
