"""VTK's pre-2015 Mantis tracker.

GitLab's 2015 migration imported each Mantis bug's report but not the
discussion under it. That discussion is still served at
`vtk.org/Bug/view.php?id=<n>` by an instance whose front page already answers
404. See docs/SOURCES.md "The 2015 migration".

Scoped to ids the archive already references, because the Mantis id and the
GitLab iid are the same number for 1,950 of 1,952 imported issues.
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from crawler import store
from crawler.rest import USER_AGENT

BASE = "https://vtk.org/Bug/view.php?id={}"
PATH = store.DATA / "mantis.ndjson"
CHECKPOINT = 100

_NOTE = re.compile(
    r'<tr class="bugnote" id="c(\d+)">(.*?)</tr>\s*(?=<tr class="spacer">|</table>)',
    re.DOTALL,
)
_NOTE_META = re.compile(r'<td class="bugnote-public">(.*?)</td>', re.DOTALL)
_NOTE_BODY = re.compile(r'<td class="bugnote-note-public">(.*?)</td>', re.DOTALL)
_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
_ROW = re.compile(
    r'<td class="category">\s*(?:<[^>]+>)*\s*([^<]+?)\s*(?:</[^>]+>)*\s*</td>'
    r"\s*<td[^>]*>(.*?)</td>",
    re.DOTALL,
)
_HEADER = re.compile(
    r'<tr>((?:\s*<td class="category"[^>]*>[^<]*</td>){4,})</tr>'
    r"\s*<tr[^>]*>((?:\s*<td[^>]*>.*?</td>)+?)</tr>",
    re.DOTALL,
)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_ROLE = re.compile(r"\s*\((developer|reporter|manager|administrator|updater|viewer)\)")
_ID_PREFIX = re.compile(r"^0*\d+:\s*")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")


def _text(fragment: str) -> str:
    """Tags out, entities decoded, line structure kept."""
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"</(p|div|tr|li)>", "\n", fragment, flags=re.IGNORECASE)
    out = html.unescape(_TAG.sub("", fragment))
    out = _WS.sub(" ", out)
    return "\n".join(line.strip() for line in out.split("\n")).strip()


def parse(body: str, mid: int) -> dict[str, Any] | None:
    """One Mantis page to a record, or None when the id holds nothing."""
    if "View Issue Details" not in body:
        return None

    fields: dict[str, str] = {}
    # The ID/Project/Category/Date header is one row of labels over one row of
    # values, not the label/value pairs the rest of the table uses.
    if head := _HEADER.search(body):
        labels = [_text(x).lower().replace(" ", "_") for x in _CELL.findall(head.group(1))]
        values = [_text(x) for x in _CELL.findall(head.group(2))]
        fields.update(dict(zip(labels, values, strict=False)))
    for label, value in _ROW.findall(body):
        key = _text(label).rstrip(":").strip().lower().replace(" ", "_")
        if key and key not in fields:
            fields[key] = _text(value)

    notes = []
    for note_id, chunk in _NOTE.findall(body):
        meta_hit = _NOTE_META.search(chunk)
        body_hit = _NOTE_BODY.search(chunk)
        meta = _text(meta_hit.group(1)) if meta_hit else ""
        stamp = _STAMP.search(meta)
        author = ""
        for line in (x for x in meta.split("\n") if x.strip()):
            if line.startswith("(") or _STAMP.search(line):
                continue
            author = _ROLE.sub("", line).strip()
            break
        notes.append(
            {
                "id": note_id,
                "author": author,
                "created_at": stamp.group(1) if stamp else "",
                "body": _text(body_hit.group(1)) if body_hit else "",
            }
        )

    keep = [
        "summary",
        "description",
        "steps_to_reproduce",
        "additional_information",
        "reporter",
        "assigned_to",
        "category",
        "severity",
        "priority",
        "status",
        "resolution",
        "date_submitted",
        "last_update",
    ]
    rec: dict[str, Any] = {"mantis_id": str(mid), "url": BASE.format(mid), "notes": notes}
    rec.update({k: fields.get(k, "") for k in keep})
    # Mantis prints the summary as "0000049: text"; the number is already the key.
    rec["summary"] = _ID_PREFIX.sub("", rec["summary"])
    return rec


_BLANKS = re.compile(r"\n{3,}")


def _squeeze(text: str) -> str:
    """Runs of blank lines down to one.

    Mantis closes each line with both `<br>` and a block tag, so the stored
    capture doubles every paragraph break. The runs are an artifact of turning
    its HTML into text, not something an author typed.
    """
    return _BLANKS.sub("\n\n", text).strip()


def load() -> dict[str, dict[str, Any]]:
    if not PATH.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with PATH.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            for field in ("description", "steps_to_reproduce", "additional_information"):
                if rec.get(field):
                    rec[field] = _squeeze(rec[field])
            for note in rec.get("notes") or []:
                note["body"] = _squeeze(note.get("body") or "")
            out[str(rec["mantis_id"])] = rec
    return out


def save(records: dict[str, dict[str, Any]]) -> Path:
    store.DATA.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".ndjson.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for mid in sorted(records, key=int):
            fh.write(json.dumps(records[mid], ensure_ascii=False, sort_keys=True))
            fh.write("\n")
    tmp.replace(PATH)
    return PATH


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


_CITED = re.compile(r"vtk\.org/Bug/view\.php\?id=(\d+)")


def wanted() -> dict[str, str]:
    """Mantis ids the archive has a GitLab issue for, mapped to that iid."""
    out: dict[str, str] = {}
    for iid, rec in store.load("issues").items():
        if hit := _CITED.search(rec.get("description") or ""):
            out[hit.group(1)] = iid
    return out


def retryable(rec: dict[str, Any]) -> bool:
    """Whether a stored record is a failure rather than an answer.

    An error stub used to be permanent: one timeout wrote it and no later run
    ever looked at that id again. An `empty` page is a real answer (the id
    holds nothing) and is only revisited under `--refetch`.
    """
    return bool(rec.get("error"))


def pending(targets, held: dict[str, dict[str, Any]], *, refetch: bool = False) -> list[str]:
    """Which Mantis ids this run should fetch, oldest id first."""
    return [m for m in sorted(targets, key=int) if refetch or m not in held or retryable(held[m])]


def crawl(*, limit: int | None = None, delay: float = 0.5, refetch: bool = False) -> dict[str, Any]:
    """Fetch every referenced Mantis page. Needs no credential."""
    with store.lock("mantis", who="mantis"):
        return _crawl(limit=limit, delay=delay, refetch=refetch)


def _crawl(*, limit: int | None, delay: float, refetch: bool) -> dict[str, Any]:
    targets = wanted()
    held = load()
    todo = pending(targets, held, refetch=refetch)
    if limit is not None:
        todo = todo[:limit]

    retrying = sum(1 for m in todo if m in held)
    print(
        f"  mantis: {len(targets)} referenced, {len(targets) - len(todo)} held, "
        f"{len(todo)} to fetch ({retrying} retrying a failure)",
        file=sys.stderr,
        flush=True,
    )

    done = empty = failed = notes_total = 0
    started = time.monotonic()
    with httpx.Client(
        timeout=60.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        for mid in todo:
            try:
                r = client.get(BASE.format(mid))
                r.raise_for_status()
                rec = parse(r.text, int(mid))
            except Exception as exc:  # noqa: BLE001 — a failed page is data, not a crash
                failed += 1
                held[mid] = {
                    "mantis_id": mid,
                    "url": BASE.format(mid),
                    "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                    "fetched_at": _stamp(),
                    "notes": [],
                }
                continue
            if rec is None:
                empty += 1
                held[mid] = {
                    "mantis_id": mid,
                    "url": BASE.format(mid),
                    "empty": True,
                    "fetched_at": _stamp(),
                    "notes": [],
                }
            else:
                rec["gitlab_iid"] = targets[mid]
                rec["fetched_at"] = _stamp()
                held[mid] = rec
                notes_total += len(rec["notes"])
            done += 1
            if done % CHECKPOINT == 0 or done == len(todo):
                save(held)
                print(
                    f"  mantis: {done}/{len(todo)}, {notes_total} notes, "
                    f"{empty} empty, {failed} failed, {time.monotonic() - started:.0f}s",
                    file=sys.stderr,
                    flush=True,
                )
            time.sleep(delay)
    save(held)

    state = store.load_state()
    # `errors` is the one that can go red: it counts stubs still standing in
    # for a page, and the next run retries every one of them.
    state["mantis"] = {
        "referenced": len(targets),
        "held": len(held),
        "errors": sum(1 for r in held.values() if r.get("error")),
        "empty": sum(1 for r in held.values() if r.get("empty")),
        "notes": sum(len(r.get("notes") or []) for r in held.values()),
    }
    store.save_state(state)

    return {
        "pass": "mantis",
        "fetched": done,
        "notes": notes_total,
        "empty": empty,
        "failed": failed,
        "total_held": len(held),
        "errors_held": state["mantis"]["errors"],
        "seconds": round(time.monotonic() - started, 1),
    }
