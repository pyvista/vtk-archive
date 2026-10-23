"""The `.md` twin of every page.

An agent reading `/vtk/vtk/-/issues/20218.md` gets the same record as the HTML
page at a fraction of the tokens, and gets back the markdown the author
actually wrote rather than markdown re-derived from rendered HTML.

Image and attachment links are rewritten to absolute URLs on this site, so a
model that wants to look at a screenshot can fetch it without first resolving
a relative path against a page it may not have.
"""

from __future__ import annotations

import os
import re
from typing import Any

from builder.render import Corpus, blob_url, rewrite_code_links

UPLOADS = "/-/project/13/uploads"
SITE = os.environ.get("VTK_ARCHIVE_SITE", "https://vtk-archive.pyvista.org").rstrip("/")
# Matches builder/diff.py's own cap, so the two surfaces agree.
MAX_TABLE_FILES = 300

_UPLOAD = re.compile(r"(?:https?://gitlab\.kitware\.com)?/uploads/([0-9a-f]{32})/")
_ITEM_URL = re.compile(
    r"https?://gitlab\.kitware\.com/vtk/vtk/(?:-/)?(issues|work_items|merge_requests)/(\d{1,6})"
)


# A fenced block or an inline span in the markdown itself. The HTML renderer
# has `render.outside_code` for the same job; this is the raw-markdown twin.
_VERBATIM = re.compile(
    r" {0,3}(?:`{3,}|~{3,})[^\n]*\n.*?(?:^ {0,3}(?:`{3,}|~{3,})[ \t]*$|\Z)"
    r"|`+[^`\n]*`+",
    re.DOTALL | re.MULTILINE,
)


def outside_code(text: str, rewrite) -> str:
    """Apply `rewrite` to everything except a code fence or an inline span."""
    out: list[str] = []
    end = 0
    for m in _VERBATIM.finditer(text):
        out.append(rewrite(text[end : m.start()]))
        out.append(m.group(0))
        end = m.end()
    out.append(rewrite(text[end:]))
    return "".join(out)


def absolutize(text: str, held: dict[str, frozenset[str]], corpus) -> str:
    """Point uploads at this site, items at the archive, code at the mirror."""

    def item(m: re.Match[str]) -> str:
        kind, iid = m.group(1), m.group(2)
        pool = held["issues"] if kind in ("issues", "work_items") else held["merge_requests"]
        if iid in pool:
            return f"{SITE}/vtk/vtk/-/{kind}/{iid}"
        return m.group(0)

    def rewrite(segment: str) -> str:
        segment = _UPLOAD.sub(rf"{SITE}{UPLOADS}/\1/", segment)
        segment = rewrite_code_links(segment, corpus)
        return _ITEM_URL.sub(item, segment)

    return outside_code(text, rewrite)


def _yaml(value: str) -> str:
    """One scalar a YAML parser reads back exactly.

    Single quotes are the only YAML form with no escape sequences at all.
    Double quotes made `ThirdParty\\hdf5\\vtkhdf5` a parse error and decoded
    the `\\v` in another title into a vertical tab, corrupting the title the
    archive exists to preserve.
    """
    return "'" + str(value).replace("'", "''") + "'"


def _front(item: dict[str, Any], kind: str, sigil: str, author: str, canonical: str) -> list[str]:
    labels = [x if isinstance(x, str) else x.get("title", "") for x in (item.get("labels") or [])]
    out = [
        "---",
        f"title: {_yaml(item.get('title') or '')}",
        # Quoted because `!` opens a YAML tag: `id: !2` made the front matter
        # of every merge request twin a parse error.
        f"id: {_yaml(f'{sigil}{item["iid"]}')}",
        f"kind: {'merge_request' if kind == 'merge_requests' else 'issue'}",
        f"state: {item.get('state') or 'unknown'}",
        f"author: {_yaml(author)}",
        f"created: {_yaml(item.get('created_at') or '')}",
    ]
    if item.get("updated_at"):
        out.append(f"updated: {_yaml(item['updated_at'])}")
    if item.get("merged_at"):
        out.append(f"merged: {_yaml(item['merged_at'])}")
    if item.get("closed_at"):
        out.append(f"closed: {_yaml(item['closed_at'])}")
    if labels:
        out.append("labels: [" + ", ".join(_yaml(x) for x in labels) + "]")
    if item.get("milestone"):
        out.append(f"milestone: {_yaml(item['milestone'].get('title') or '')}")
    if kind == "merge_requests":
        out.append(f"source_branch: {_yaml(item.get('source_branch') or '')}")
        out.append(f"target_branch: {_yaml(item.get('target_branch') or '')}")
        if item.get("sha"):
            out.append(f"head_sha: {_yaml(item['sha'])}")
    out.append(f"canonical: {_yaml(canonical)}")
    out.append(f"html: {_yaml(f'{SITE}/vtk/vtk/-/{kind}/{item["iid"]}')}")
    out.append("---")
    return out


def render(
    item: dict[str, Any],
    *,
    kind: str,
    sigil: str,
    author: str,
    canonical: str,
    held: dict[str, frozenset[str]],
    corpus: Corpus,
    change: dict[str, Any] | None,
    mantis: dict[str, Any] | None = None,
    mirror_sha: str | None = None,
) -> str:
    """One item as markdown: front matter, body, threads, change summary."""
    lines = _front(item, kind, sigil, author, canonical)
    lines += ["", f"# {item.get('title') or ''}", ""]

    body = (item.get("description") or "").strip()
    lines.append(absolutize(body, held, corpus) if body else "_No description was given._")

    if change:
        lines += ["", "## Files changed", ""]
        lines.append(
            f"{change['file_count']} file(s), +{change['added']} -{change['removed']}. "
            f"Full diff: {SITE}/vtk/vtk/-/merge_requests/{item['iid']}/diffs"
        )
        if change.get("truncated"):
            lines.append(
                f"GitLab caps this endpoint at {change['file_count']} files, so the "
                "list below is the start of a larger change."
            )
            lines.append("")
        # Asked of the mirror, never inferred from `state`. 62 closed merge
        # requests have a head commit the mirror does hold, and 18 merged ones
        # carry no sha at all.
        sha = mirror_sha
        if not sha:
            lines.append(
                "The head commit of this merge request is not in the GitHub "
                "mirror, so the source links below point at master. The diff "
                "above is the only record of the change itself."
            )
            lines.append("")
        # Every row of the table must be contiguous: a blank line between the
        # delimiter and the first row ends the table and prints raw pipes.
        lines.append("| file | status | +/- | source |")
        lines.append("| --- | --- | --- | --- |")
        for f in change["files"][:MAX_TABLE_FILES]:
            src = "" if f["status"] == "deleted" else blob_url(sha, f["path"])
            lines.append(
                f"| `{f['path']}` | {f['status']} | +{f['added']} -{f['removed']} | {src} |"
            )
        if change["file_count"] > MAX_TABLE_FILES:
            lines.append(f"| _{change['file_count'] - MAX_TABLE_FILES} more_ | | | |")

    timeline = item.get("_timeline") or []
    comments = [e for e in timeline if not e["system"]]
    events = [e for e in timeline if e["system"]]
    # Only the notes a person wrote. Counting a thread's whole `notes` list put
    # GitLab's own events into the heading and disagreed with the body below.
    written = sum(1 for e in comments for n in e["thread"]["notes"] if not n["system"])
    lines += ["", f"## Comments ({written})", ""]
    if not comments:
        lines.append("_No comments were recorded._")
    for entry in comments:
        thread = entry["thread"]
        if thread["diff"] and thread["diff"]["path"]:
            anchor = thread["diff"]["path"]
            if thread["diff"].get("line"):
                anchor += f":{thread['diff']['line']}"
            lines.append(f"### Review thread on `{anchor}`")
            lines.append("")
        for note in thread["notes"]:
            if note["system"]:
                continue
            when = note["created_at"][:16].replace("T", " ")
            lines.append(f"**{note['author']}** at {when}")
            lines.append("")
            lines.append(absolutize((note.get("raw") or "").strip(), held, corpus) or "_empty_")
            lines.append("")
        lines.append("---")
        lines.append("")

    # 129,383 of these across the corpus, on the page but not in the twin, so
    # an agent reading the `.md` could not tell when a label or milestone
    # changed or who closed the thing.
    if events:
        lines += ["", f"## Timeline ({len(events)} events)", ""]
        lines.append("GitLab's own record of label, milestone, assignee and state changes.")
        lines.append("")
        for entry in events:
            note = entry["note"]
            when = (note.get("created_at") or "")[:16].replace("T", " ")
            said = " ".join((note.get("raw") or "").split())
            lines.append(f"- {when} **{note['author']}** {absolutize(said, held, corpus)}")

    if mantis and mantis.get("notes"):
        lines += [
            "",
            f"## From VTK's Mantis tracker ({len(mantis['notes'])} comments)",
            "",
            "GitLab's 2015 migration carried this report but not the discussion "
            f"under it. Archived from {mantis['url']}; it appears on no GitLab page.",
            "",
        ]
        for field, head in (
            ("steps_to_reproduce", "Steps to reproduce"),
            ("additional_information", "Additional information"),
        ):
            if mantis.get(field):
                lines += [f"### {head}", "", "```text", mantis[field], "```", ""]
        for note in mantis["notes"]:
            lines.append(f"**{note['author'] or 'unknown'}** at {note['created_at']}")
            lines += ["", "```text", note["body"] or "", "```", "", "---", ""]

    return "\n".join(lines).rstrip() + "\n"
