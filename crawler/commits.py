"""Which commit SHAs Kitware's GitHub mirror actually holds.

VTK force-pushes merge-request branches, and the mirror only carries what is
reachable from the real refs, so most SHAs named in this corpus 404 there. A
treeless bare clone answers the question exactly, offline, for every SHA at
once; the GitHub API would need one rate-limited request each.

A SHA the mirror lacks still resolves on gitlab.kitware.com, which keeps the
objects its merge requests reference, so that is the fallback.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from crawler import store

MIRROR_URL = "https://github.com/Kitware/VTK.git"
CLONE = Path(__file__).resolve().parent.parent / ".cache" / "vtk-mirror.git"
PATH = store.DATA / "commits.json"

SHA = re.compile(r"(?<![\w/])([0-9a-f]{40})(?![\w/])")


def ensure_clone(*, refresh: bool = True) -> Path:
    """A treeless bare mirror: every commit, no file contents, about 94 MB."""
    if CLONE.exists():
        if refresh:
            subprocess.run(
                ["git", "--git-dir", str(CLONE), "fetch", "--all", "--prune"],
                check=True,
                capture_output=True,
            )
        return CLONE
    CLONE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--bare", "--filter=blob:none", MIRROR_URL, str(CLONE)],
        check=True,
        capture_output=True,
    )
    return CLONE


def discover() -> set[str]:
    """Every 40-character SHA the corpus names, from any field that shows one."""
    found: set[str] = set()
    for kind in ("issues", "merge_requests"):
        for rec in store.load(kind).values():
            if sha := rec.get("sha"):
                found.add(sha)
            for key in ("merge_commit_sha", "squash_commit_sha"):
                if value := rec.get(key):
                    found.add(value)
            found.update(SHA.findall(rec.get("description") or ""))
            for note in rec.get("notes") or []:
                found.update(SHA.findall(note.get("body") or ""))
    return found


def resolve(shas: set[str]) -> dict[str, bool]:
    """Ask the clone about every SHA in one pass.

    Raises rather than defaulting a SHA to absent: every unanswered SHA routes
    its link back to gitlab.kitware.com, which is the load this archive exists
    to remove, and nothing downstream can tell that apart from a real miss.
    """
    if not shas:
        return {}
    ordered = sorted(shas)
    # GIT_NO_LAZY_FETCH keeps a miss local; without it a treeless clone asks
    # the remote about every unknown SHA and the pass takes hours.
    proc = subprocess.run(
        ["git", "--git-dir", str(CLONE), "cat-file", "--batch-check"],
        input="\n".join(ordered) + "\n",
        capture_output=True,
        text=True,
        env={"GIT_NO_LAZY_FETCH": "1", "PATH": "/usr/bin:/bin:/usr/local/bin"},
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git cat-file exited {proc.returncode} over {len(ordered)} SHAs: "
            f"{proc.stderr.strip()[:300] or '(no stderr)'}"
        )
    present: dict[str, bool] = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            # `--batch-check` answers "<sha> missing" for an object it lacks,
            # so every input gets a line and a gap means git stopped early.
            present[parts[0]] = parts[1] == "commit"
    unanswered = [s for s in ordered if s not in present]
    if unanswered:
        raise RuntimeError(
            f"git answered {len(present)} of {len(ordered)} SHAs; first unanswered {unanswered[0]}"
        )
    return {s: present[s] for s in ordered}


def load() -> dict[str, bool]:
    if not PATH.exists():
        return {}
    return json.loads(PATH.read_text(encoding="utf-8"))


def build(*, refresh: bool = True) -> dict[str, Any]:
    """Resolve every SHA in the corpus and record the answer."""
    ensure_clone(refresh=refresh)
    shas = discover()
    print(f"  commits: {len(shas)} distinct SHAs named in the corpus", file=sys.stderr, flush=True)
    table = resolve(shas)
    store.DATA.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(table, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    hits = sum(1 for v in table.values() if v)
    result = {
        "pass": "commits",
        "shas": len(table),
        "in_mirror": hits,
        "not_in_mirror": len(table) - hits,
    }
    state = store.load_state()
    state["commits"] = {k: v for k, v in result.items() if k != "pass"}
    store.save_state(state)
    return result
