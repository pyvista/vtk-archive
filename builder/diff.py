"""Turn GitLab's unified-diff text into rows a template can draw.

Parsing here rather than in the template because the line numbers are state:
a unified diff gives them only on the `@@` header and every following line
advances one side, the other, or both.

Two caps keep a page servable. VTK's largest merge request changes 1,000 files
and carries 1.9 MB of diff text, which renders to tens of megabytes of table
rows; past the caps the page lists what changed and sends the reader to the
canonical merge request for the body.
"""

from __future__ import annotations

import re
from typing import Any

from markupsafe import Markup, escape

MAX_FILE_BYTES = 120_000
MAX_FILES_RENDERED = 300

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _status(entry: dict[str, Any]) -> str:
    if entry.get("new_file"):
        return "added"
    if entry.get("deleted_file"):
        return "deleted"
    if entry.get("renamed_file"):
        return "renamed"
    return "modified"


def parse_hunks(text: str) -> list[dict[str, Any]]:
    """Split one file's unified diff into hunks of numbered, classified lines."""
    hunks: list[dict[str, Any]] = []
    old = new = 0
    raws = text.split("\n")
    # Only the final element, which the trailing newline manufactures. An
    # interior "" is a real blank context line and keeps its number.
    if raws and raws[-1] == "":
        raws.pop()
    for raw in raws:
        m = _HUNK.match(raw)
        if m:
            old, new = int(m.group(1)), int(m.group(3))
            hunks.append({"header": raw, "lines": []})
            continue
        if not hunks:
            continue
        if raw.startswith("+"):
            hunks[-1]["lines"].append({"kind": "add", "old": None, "new": new, "text": raw[1:]})
            new += 1
        elif raw.startswith("-"):
            hunks[-1]["lines"].append({"kind": "del", "old": old, "new": None, "text": raw[1:]})
            old += 1
        elif raw.startswith("\\"):
            hunks[-1]["lines"].append({"kind": "meta", "old": None, "new": None, "text": raw})
        else:
            body = raw[1:] if raw.startswith(" ") else raw
            hunks[-1]["lines"].append({"kind": "ctx", "old": old, "new": new, "text": body})
            old += 1
            new += 1
    return hunks


def counts(text: str) -> tuple[int, int]:
    """Added and removed line counts, ignoring the `+++`/`---` file headers."""
    added = removed = 0
    for line in text.split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def prepare(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Shape one MR's stored diff for the template, applying both caps."""
    if not record:
        return None
    entries = record.get("files") or []
    if not entries:
        return None

    files: list[dict[str, Any]] = []
    added_total = removed_total = 0
    for i, entry in enumerate(entries):
        text = entry.get("diff") or ""
        added, removed = counts(text)
        added_total += added
        removed_total += removed
        path = entry.get("new_path") or entry.get("old_path") or ""
        over_cap = i >= MAX_FILES_RENDERED
        too_big = len(text) > MAX_FILE_BYTES
        files.append(
            {
                "path": path,
                "old_path": entry.get("old_path") if entry.get("renamed_file") else None,
                "status": _status(entry),
                "added": added,
                "removed": removed,
                "generated": bool(entry.get("generated_file")),
                "omitted": "file-cap" if over_cap else ("size" if too_big else None),
                "bytes": len(text),
                "hunks": [] if (over_cap or too_big or not text) else parse_hunks(text),
                # Review threads anchored to this file; the build fills them in.
                "threads": [],
            }
        )

    return {
        "files": files,
        "file_count": len(files),
        "added": added_total,
        "removed": removed_total,
        "truncated": bool(record.get("truncated")),
        "omitted_count": sum(1 for f in files if f["omitted"]),
    }


def line_html(text: str) -> Markup:
    """Escape one diff line, keeping leading indentation visible.

    `Markup.replace` escapes its replacement, so the entity is built after the
    escape rather than passed through it.
    """
    indent = len(text) - len(text.lstrip(" "))
    return Markup("&nbsp;" * indent) + escape(text[indent:])
