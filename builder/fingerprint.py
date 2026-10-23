"""Deciding which pages a rebuild can skip.

A page is rebuilt when any input feeding it changed: the record, the merge
request's diff, the recovered Mantis discussion, and the builder's own code
and templates.

The builder half is hashed from the files on disk, never from a version
constant someone must remember to bump, because a forgotten bump serves stale
markup with no error anywhere.

Cross-page references are deliberately out of scope; see
docs/MAINTENANCE.md "What an incremental build can miss".
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
# Everything whose contents can change a rendered page.
SOURCES = ("*.py", "templates/*", "assets/*")


@lru_cache(maxsize=1)
def builder_salt() -> str:
    """One hash over every builder file that can affect a page's bytes."""
    h = hashlib.sha256()
    for pattern in SOURCES:
        for path in sorted(ROOT.glob(pattern)):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            # The name is hashed too, so a rename alone invalidates.
            h.update(path.name.encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def page(*parts: Any) -> str:
    """The fingerprint for one page, from its record and everything beside it."""
    h = hashlib.sha256(builder_salt().encode())
    for part in parts:
        h.update(b"\x00")
        if part is not None:
            h.update(json.dumps(part, sort_keys=True, default=str).encode())
    return h.hexdigest()


def load(path: Path) -> dict[str, str]:
    """Fingerprints from the previous build, or nothing on any doubt."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(path: Path, prints: dict[str, str]) -> None:
    path.write_text(json.dumps(prints, sort_keys=True, indent=0) + "\n", encoding="utf-8")
