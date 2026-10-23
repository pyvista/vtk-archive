"""Labels as pages.

A label chip has to go somewhere, and on a static site the destination is a
real page rather than a query the server would have to answer. That also gives
an agent a URL it can construct instead of a search it has to run.

The slug keeps GitLab's label text recoverable: VTK uses `area:web` and
`priority::high`, so the colon is the one character that has to survive.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

_UNSAFE = re.compile(r"[^a-z0-9]+")


def slug(label: str) -> str:
    """A filesystem- and URL-safe name for one label."""
    out = _UNSAFE.sub("-", label.strip().lower()).strip("-")
    return out or "unlabelled"


def collect(corpora: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group every issue and merge request by each label it carries."""
    by_slug: dict[str, dict[str, Any]] = {}
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for kind, records in corpora.items():
        for rec in records.values():
            for raw in rec.get("labels") or []:
                name = raw if isinstance(raw, str) else raw.get("title", "")
                if not name:
                    continue
                key = slug(name)
                entry = by_slug.setdefault(
                    key, {"slug": key, "name": name, "issues": 0, "merge_requests": 0}
                )
                entry[kind] += 1
                rows[key].append({"kind": kind, "record": rec})

    for key, entry in by_slug.items():
        entry["total"] = entry["issues"] + entry["merge_requests"]
        entry["rows"] = sorted(
            rows[key],
            key=lambda r: r["record"].get("created_at") or "",
            reverse=True,
        )
    return dict(sorted(by_slug.items(), key=lambda kv: (-kv[1]["total"], kv[1]["name"])))
