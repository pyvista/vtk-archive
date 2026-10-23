"""Render the corpus into `dist/`.

Every page is written to `<request path>/index.html`, the one layout every
static host serves at the bare URL unconfigured. docs/HOSTING.md "Why
index.html".
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from builder import chart as chartmod
from builder import diff as diffmod
from builder import fingerprint as fpmod
from builder import labels as labelmod
from builder import markdown as mdmod
from builder.render import (
    MIRROR,
    UPLOADS,
    Corpus,
    blob_url,
    build_parser,
    commit_url,
    to_html,
)
from crawler import attachments as attachstore
from crawler import commits as commitstore
from crawler import diffs as diffstore
from crawler import mantis as mantisstore
from crawler import store
from crawler.attachments import OUT as ATTACH_DIR
from crawler.rest import USER_AGENT

ROOT = Path(__file__).resolve().parent
DIST = ROOT.parent / "dist"
CANONICAL = "https://gitlab.kitware.com/vtk/vtk"
REPO_URL = "https://github.com/pyvista/vtk-archive"
REMOVAL_URL = f"{REPO_URL}/issues/new?template=removal-request.yml"
WORKFLOW_URL = f"{REPO_URL}/actions/workflows/refresh.yml"
# Netlify reads this from the publish directory. The LAST matching rule wins and
# specificity counts for nothing, so the catch-all leads and every narrower rule
# follows it. docs/HOSTING.md "Which header rule wins".
HEADERS = f"""\
/*
  Cache-Control: public, max-age=3600, stale-while-revalidate=86400

/assets/*
  Cache-Control: public, max-age=3600

/pagefind/*
  Cache-Control: public, max-age=86400

{UPLOADS}/*
  Cache-Control: public, max-age=31536000, immutable
"""
# The cron is daily, so two missed runs is a real failure rather than a slow day.
STALE_AFTER_HOURS = 48
# Subresource integrity for the chart library, computed from the exact bytes
# cdnjs serves. A substituted file fails to execute and the static SVG stays.
PLOTLY_SRI = "sha384-f7DYOxgF8vFtWQJTH76yjeKSWvD1nr0M+XqZKbw8A11Af7XsTqZmRBvxrUcTgYW9"
PER_PAGE = 100

SIGIL = {"issues": "#", "merge_requests": "!"}
# GitLab serves an issue at both spellings, so the archive answers both.
ALIASES = {"issues": ("issues", "work_items"), "merge_requests": ("merge_requests",)}


def _env(**counts: str) -> Environment:
    """The one Jinja environment. Every global a template may read is set here.

    `StrictUndefined` turns a name a template reads but nothing supplies into a
    build failure, so a default belongs in this function rather than at a call
    site a new page could forget.
    """
    env = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["slug"] = labelmod.slug
    env.globals.update(
        line_html=diffmod.line_html,
        max_files=diffmod.MAX_FILES_RENDERED,
        blob_url=blob_url,
        repo_url=REPO_URL,
        removal_url=REMOVAL_URL,
        actions_url=WORKFLOW_URL,
        mirror=MIRROR,
        site=mdmod.SITE,
        nav=None,
        wide=False,
        indexable=True,
        # Off by default: only pages rendered on every run date themselves.
        snapshot=False,
        generated="",
    )
    return env


# Every page is `<url>/index.html`: the one layout every static host serves at
# the bare URL unconfigured. See docs/HOSTING.md "Why index.html".
CONTAINER = "index.html"
KEYS: dict[str, str] = {}


def _write(key: str, text: str) -> None:
    """Stage one page, served at `key`."""
    rel = CONTAINER if key in ("", CONTAINER) else f"{key}/{CONTAINER}"
    dest = DIST / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    KEYS[rel] = key


def _write_file(name: str, text: str) -> None:
    """Stage one literal file, served at its own name."""
    dest = DIST / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    KEYS[name] = name


def _rel(key: str) -> str:
    """The staged path that answers this request key."""
    return CONTAINER if key in ("", CONTAINER) else f"{key}/{CONTAINER}"


def _claim(rel: str, key: str) -> bool:
    """Keep a page a skipped item already owns, and say whether it is there.

    An unrendered page still belongs in `_keys.json` and must survive the
    prune, so claiming is separate from writing.
    """
    KEYS[rel] = key
    return (DIST / rel).is_file()


def _author(obj: dict[str, Any] | None) -> str:
    if not obj:
        return "unknown"
    return obj.get("name") or obj.get("username") or "unknown"


def _labels(item: dict[str, Any]) -> list[str]:
    raw = item.get("labels") or []
    return [x if isinstance(x, str) else x.get("title", "") for x in raw]


_MANTIS = re.compile(r"vtk\.org/Bug/view\.php\?id=(\d+)")


def _migration(issues: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Numbers for the About page's account of the 2015 Mantis migration.

    Computed rather than written down, so the page cannot drift from the
    corpus it describes. docs/SOURCES.md "The 2015 migration".
    """
    imported = {iid: r for iid, r in issues.items() if _MANTIS.search(r.get("description") or "")}
    matching = sum(1 for iid, r in imported.items() if _MANTIS.search(r["description"])[1] == iid)
    native = sorted(int(i) for i in issues if i not in imported)
    pre_2015 = [r for r in issues.values() if (r.get("created_at") or "")[:4] < "2015"]
    old_comments = sum(
        1
        for r in issues.values()
        if (r.get("created_at") or "")[:4] < "2015"
        for n in (r.get("notes") or [])
        if not n.get("system")
    )
    return {
        "imported_count": f"{len(imported):,}",
        "mantis_match": f"{matching:,}",
        "native_from": f"{native[0]:,}" if native else "—",
        "old_comment_count": f"{old_comments:,}",
        "pre_2015_count": f"{len(pre_2015):,}",
    }


def _usernames(corpora: dict[str, dict[str, dict[str, Any]]]) -> frozenset[str]:
    """Every handle the corpus has seen a person use.

    An `@name` links only when one of these is behind it, because doxygen's
    `@param` and `@brief` are everywhere in pasted code comments and each one
    used to become a link to a profile page that does not exist.
    """
    out: set[str] = set()

    def take(person: Any) -> None:
        if isinstance(person, dict) and (handle := person.get("username")):
            out.add(handle)

    for records in corpora.values():
        for rec in records.values():
            take(rec.get("author"))
            take(rec.get("assignee"))
            take(rec.get("merged_by"))
            take(rec.get("closed_by"))
            for group in ("assignees", "reviewers"):
                for person in rec.get(group) or []:
                    take(person)
            for note in rec.get("notes") or []:
                take(note.get("author"))
    return frozenset(out)


def _write_labels(env, index: dict[str, dict[str, Any]], generated: str) -> int:
    """An index of every label, and a paginated page per label."""
    index_tpl = env.get_template("labels.html.j2")
    label_tpl = env.get_template("label.html.j2")
    written = 0

    _write(
        "vtk/vtk/-/labels",
        index_tpl.render(
            page_title="Labels · VTK archive",
            description="Every label used on VTK's issues and merge requests.",
            canonical=None,
            generated=generated,
            snapshot=True,
            nav="labels",
            labels=list(index.values()),
        ),
    )
    written += 1

    for key, label in index.items():
        rows = [
            {
                "href": f"/vtk/vtk/-/{r['kind']}/{r['record']['iid']}",
                "sigil": SIGIL[r["kind"]],
                "title": r["record"].get("title") or f"{SIGIL[r['kind']]}{r['record']['iid']}",
                "iid": r["record"]["iid"],
                "state": r["record"].get("state") or "unknown",
                "author": _author(r["record"].get("author")),
                "created_at": r["record"].get("created_at") or "",
                "comments": sum(1 for n in (r["record"].get("notes") or []) if not n.get("system")),
                "labels": [
                    {"slug": labelmod.slug(x), "name": x}
                    for x in _labels(r["record"])
                    if labelmod.slug(x) != key
                ][:3],
            }
            for r in label["rows"]
        ]
        pages = max(1, -(-len(rows) // PER_PAGE))
        base = f"vtk/vtk/-/labels/{key}"
        for page in range(1, pages + 1):
            html = label_tpl.render(
                page_title=f"{label['name']} · VTK archive",
                description=f"Issues and merge requests labelled {label['name']}.",
                canonical=None,
                generated=generated,
                snapshot=True,
                nav="labels",
                label=label,
                page=page,
                pages=pages,
                rows=rows[(page - 1) * PER_PAGE : page * PER_PAGE],
                prev_href=f"/{base}" if page == 2 else f"/{base}/page/{page - 1}",
                next_href=f"/{base}/page/{page + 1}",
            )
            _write(base if page == 1 else f"{base}/page/{page}", html)
            written += 1
            if page == 1:
                # `/page/<n>` is published as a scheme, so `/page/1` answers
                # too rather than being the one n in it that 404s.
                _write(f"{base}/page/1", html)
                written += 1
    return written


def _conversation(item: dict[str, Any], corpus: Corpus, md) -> dict[str, Any]:
    """Timeline of real threads and system events, in time order.

    GitLab models a plain comment as a one-note discussion, so a discussion is
    a thread only when it holds a note a person wrote. The rest are events
    ("added the bug label") and draw as one compact line.
    """
    timeline: list[dict[str, Any]] = []
    anchored: dict[str, list[dict[str, Any]]] = {}
    comments = 0
    events = 0

    for disc in item.get("discussions") or []:
        raw = disc.get("notes") or []
        if not raw:
            continue
        human = [n for n in raw if not n.get("system")]

        if not human:
            for note in raw:
                events += 1
                timeline.append(
                    {
                        "system": True,
                        "at": note.get("created_at") or "",
                        "note": {
                            "id": note.get("id"),
                            "author": _author(note.get("author")),
                            "created_at": note.get("created_at") or "",
                            "body": to_html(note.get("body"), corpus, md, system=True),
                            # The markdown twin lists these as a timeline and
                            # needs the text, not GitLab's HTML.
                            "raw": note.get("body") or "",
                        },
                    }
                )
            continue

        diff = None
        notes = []
        for note in raw:
            pos = note.get("position") or {}
            if pos and diff is None:
                diff = {
                    "path": pos.get("new_path") or pos.get("old_path") or "",
                    "line": pos.get("new_line") or pos.get("old_line"),
                }
            notes.append(
                {
                    "id": note.get("id"),
                    "author": _author(note.get("author")),
                    "created_at": note.get("created_at") or "",
                    "system": bool(note.get("system")),
                    "body": to_html(note.get("body"), corpus, md, system=bool(note.get("system"))),
                    # The markdown twin serves what the author wrote, not HTML.
                    "raw": note.get("body") or "",
                }
            )
        comments += len(human)
        thread = {"notes": notes, "diff": diff, "resolved": bool(raw[0].get("resolved"))}
        timeline.append({"system": False, "at": raw[0].get("created_at") or "", "thread": thread})
        if diff and diff["path"]:
            anchored.setdefault(diff["path"], []).append(thread)

    timeline.sort(key=lambda e: e["at"])
    return {
        "timeline": timeline,
        "anchored": anchored,
        "comment_count": comments,
        "system_count": events,
    }


def build(*, full: bool = False) -> dict[str, Any]:
    corpora = {kind: store.load(kind) for kind in SIGIL}
    mr_diffs = diffstore.load()
    activity = chartmod.tally(corpora)
    sha_table = commitstore.load()
    mirror_shas = frozenset(s for s, ok in sha_table.items() if ok)
    mantis = mantisstore.load()
    mantis_pages = sum(1 for r in mantis.values() if r.get("notes"))
    mantis_notes = sum(len(r.get("notes") or []) for r in mantis.values())
    # Keyed by GitLab iid, because that is what a page is being rendered for.
    # The two numbers agree for almost every imported issue, but the record
    # carries the mapping it was crawled under rather than re-deriving it.
    mantis_by_iid = {
        r["gitlab_iid"]: r for r in mantis.values() if r.get("notes") and r.get("gitlab_iid")
    }
    corpus = Corpus(
        issues=frozenset(corpora["issues"]),
        merge_requests=frozenset(corpora["merge_requests"]),
        mirror_commits=mirror_shas,
        uploads=_held_uploads(),
        users=_usernames(corpora),
    )
    md = build_parser()
    env = _env(
        issues=f"{len(corpora['issues']):,}",
        merge_requests=f"{len(corpora['merge_requests']):,}",
    )
    now = datetime.now(UTC)
    generated = now.strftime("%Y-%m-%d")

    # dist/ is kept across runs: pages are replaced in place and orphans are
    # pruned at the end against the keys this run claimed.
    # docs/MAINTENANCE.md "Incremental builds".
    DIST.mkdir(parents=True, exist_ok=True)
    fp_path = DIST / "_fingerprints.json"
    previous = {} if full else fpmod.load(fp_path)
    current: dict[str, dict[str, Any]] = {}
    reused = 0
    rendered = 0

    item_tpl = env.get_template("item.html.j2")
    changes_tpl = env.get_template("changes.html.j2")
    index_tpl = env.get_template("index.html.j2")
    held = {k: frozenset(v) for k, v in corpora.items()}
    written = 0
    md_written = 0
    comment_total = 0
    diff_total = 0
    diff_files = 0

    for kind, records in corpora.items():
        sigil = SIGIL[kind]
        for iid, item in records.items():
            raw_diff = mr_diffs.get(iid) if kind == "merge_requests" else None
            mantis_rec = mantis_by_iid.get(iid) if kind == "issues" else None
            # Asked of the prepared change, not the raw record: 352 merge
            # requests hold a diff that renders no page, and claiming a page
            # they never write made them unskippable forever.
            change = diffmod.prepare(raw_diff) if kind == "merge_requests" else None

            # Every path this item owns, so a skip can claim them all and the
            # prune never mistakes an untouched page for an orphan.
            owned = [(_rel(f"vtk/vtk/-/{a}/{iid}"), f"vtk/vtk/-/{a}/{iid}") for a in ALIASES[kind]]
            owned += [(f"vtk/vtk/-/{a}/{iid}.md", f"vtk/vtk/-/{a}/{iid}.md") for a in ALIASES[kind]]
            if change:
                d = f"vtk/vtk/-/merge_requests/{iid}/diffs"
                owned.append((_rel(d), d))

            # The mirror answer and the site URL land in this page's own bytes,
            # so they belong in its fingerprint. A commit the text merely
            # mentions is a cross-reference, which only `--full` settles.
            fp = fpmod.page(item, raw_diff, mantis_rec, item.get("sha") in mirror_shas, mdmod.SITE)
            prior = previous.get(f"{kind}/{iid}")
            staged = [_claim(rel, key) for rel, key in owned]
            if prior and prior.get("fp") == fp and all(staged):
                comment_total += prior["comments"]
                diff_total += 1 if prior["diff_files"] else 0
                diff_files += prior["diff_files"]
                written += len(owned) - len(ALIASES[kind])
                md_written += len(ALIASES[kind])
                current[f"{kind}/{iid}"] = prior
                reused += 1
                continue

            rendered += 1
            convo = _conversation(item, corpus, md)
            comment_total += convo["comment_count"]
            current[f"{kind}/{iid}"] = {
                "fp": fp,
                "comments": convo["comment_count"],
                "diff_files": change["file_count"] if change else 0,
            }
            if change:
                diff_total += 1
                diff_files += change["file_count"]
                for f in change["files"]:
                    f["threads"] = convo["anchored"].get(f["path"], [])
            canonical = item.get("web_url") or f"{CANONICAL}/-/{kind}/{iid}"
            shaped = {**item, "labels": _labels(item)}
            author = _author(item.get("author"))
            common = {
                "description": (item.get("title") or "")[:200],
                "canonical": canonical,
                "kind": kind,
                "sigil": sigil,
                "item": shaped,
                "author": author,
                "nav": kind,
                "wide": True,
                "md_url": f"/vtk/vtk/-/{kind}/{iid}.md",
                "diff_count": change["file_count"] if change else 0,
                # Asked of the mirror itself, not inferred from state: VTK
                # force-pushes, so a merged head is often gone too.
                "code_sha": item.get("sha") if item.get("sha") in mirror_shas else None,
                "commit_href": commit_url(item["sha"], corpus) if item.get("sha") else None,
                "mantis": mantis_rec,
            }
            page = {
                "page_title": f"{sigil}{iid} {item.get('title') or ''} · VTK archive",
                "body": to_html(item.get("description"), corpus, md),
                "timeline": convo["timeline"],
                "comment_count": convo["comment_count"],
                "system_count": convo["system_count"],
            }
            # Only the canonical spelling is indexed; the alias would make every
            # issue a duplicate hit, and a diff page would swamp the index.
            for n, alias in enumerate(ALIASES[kind]):
                _write(
                    f"vtk/vtk/-/{alias}/{iid}",
                    item_tpl.render(indexable=(n == 0), **page, **common),
                )
                written += 1

            if change:
                _write(
                    f"vtk/vtk/-/merge_requests/{iid}/diffs",
                    changes_tpl.render(
                        page_title=f"Files changed · !{iid} · VTK archive",
                        diff=change,
                        indexable=False,
                        review_count=sum(len(v) for v in convo["anchored"].values()),
                        **common,
                    ),
                )
                written += 1

            twin = mdmod.render(
                {**shaped, "_timeline": convo["timeline"]},
                kind=kind,
                sigil=sigil,
                author=author,
                canonical=canonical,
                held=held,
                corpus=corpus,
                change=change,
                mantis=common["mantis"],
                mirror_sha=common["code_sha"],
            )
            for alias in ALIASES[kind]:
                _write_file(f"vtk/vtk/-/{alias}/{iid}.md", twin)
                md_written += 1

        rows = sorted(records.values(), key=lambda r: r.get("created_at") or "", reverse=True)
        pages = max(1, -(-len(rows) // PER_PAGE))
        heading = "Issues" if kind == "issues" else "Merge requests"
        for page in range(1, pages + 1):
            chunk = rows[(page - 1) * PER_PAGE : page * PER_PAGE]
            base = f"vtk/vtk/-/{kind}"
            html = index_tpl.render(
                page_title=f"{heading} · page {page} · VTK archive",
                description=f"{heading} archived from VTK's GitLab.",
                canonical=None,
                generated=generated,
                snapshot=True,
                heading=heading,
                sigil=sigil,
                kind=kind,
                nav=kind,
                total=len(rows),
                page=page,
                pages=pages,
                rows=[
                    {
                        "href": f"/{base}/{r['iid']}",
                        "title": r.get("title") or f"{sigil}{r['iid']}",
                        "iid": r["iid"],
                        "state": r.get("state") or "unknown",
                        "author": _author(r.get("author")),
                        "created_at": r.get("created_at") or "",
                        "comments": sum(1 for n in (r.get("notes") or []) if not n.get("system")),
                        "labels": _labels(r)[:4],
                    }
                    for r in chunk
                ],
                prev_href=f"/{base}" if page == 2 else f"/{base}/page/{page - 1}",
                next_href=f"/{base}/page/{page + 1}",
            )
            if page == 1:
                _write(base, html)
                # llms.txt publishes `/page/<n>`; `/page/1` used to be the one
                # value of n that answered 404.
                _write(f"{base}/page/1", html)
                written += 1
            else:
                _write(f"{base}/page/{page}", html)
            written += 1

    years = sorted(
        (r.get("created_at") or "")[:4]
        for recs in corpora.values()
        for r in recs.values()
        if r.get("created_at")
    )
    _write(
        "index.html",
        env.get_template("home.html.j2").render(
            page_title="The VTK GitLab archive",
            description=(
                "A read-only archive of VTK's GitLab issues, merge requests and discussions."
            ),
            canonical=None,
            generated=generated,
            snapshot=True,
            nav="readme",
            issue_count=f"{len(corpora['issues']):,}",
            mr_count=f"{len(corpora['merge_requests']):,}",
            comment_count=f"{comment_total:,}",
            year_span=f"{years[0]}–{years[-1]}" if years else "—",
            activity_svg=chartmod.svg(activity),
            activity_data=chartmod.payload(activity),
            plotly_sri=PLOTLY_SRI,
            sample_issue=max(int(i) for i in corpora["issues"]),
            user_agent=USER_AGENT,
            issue_max=f"{max(int(i) for i in corpora['issues']):,}",
            sha_total=f"{len(sha_table):,}",
            sha_in_mirror=f"{len(mirror_shas):,}",
            mantis_pages=f"{mantis_pages:,}" if mantis_pages else "",
            mantis_notes=f"{mantis_notes:,}" if mantis_notes else "",
            **_migration(corpora["issues"]),
        ),
    )
    # /about merged into the README. The redirect keeps links already shared.
    _write(
        "about",
        '<!doctype html><meta charset="utf-8">'
        '<meta http-equiv="refresh" content="0; url=/#affiliation">'
        '<link rel="canonical" href="/"><title>About · VTK archive</title>'
        '<p>This page moved to <a href="/#affiliation">the README</a>.</p>',
    )

    label_index = labelmod.collect(corpora)
    written += _write_labels(env, label_index, generated)
    written += 2

    _write(
        "search",
        env.get_template("search.html.j2").render(
            page_title="Search · VTK archive",
            description="Search 30 years of VTK issues, merge requests and review threads.",
            canonical=None,
            generated=generated,
            snapshot=True,
            nav="search",
        ),
    )
    written += 1

    _copy_assets()
    _write_file(
        "robots.txt",
        "User-agent: *\nAllow: /\n\n"
        f"Sitemap: {mdmod.SITE}/sitemap.xml\n\n"
        f"# A read-only archive of {CANONICAL}\n"
        f"# Agents: {mdmod.SITE}/llms.txt\n"
        f"# Contact: {REPO_URL}\n",
    )
    _write_file("sitemap.xml", _sitemap(corpora, label_index))
    _write_file("snapshot.json", json.dumps(_snapshot(now), indent=2) + "\n")
    _write_file("_headers", HEADERS)
    _write_file(
        "llms.txt",
        env.get_template("llms.txt.j2").render(
            site=mdmod.SITE,
            generated=generated,
            snapshot=True,
            issue_count=f"{len(corpora['issues']):,}",
            mr_count=f"{len(corpora['merge_requests']):,}",
            comment_count=f"{comment_total:,}",
            diff_count=f"{diff_total:,}",
            issue_max=max(int(i) for i in corpora["issues"]),
            mr_max=max(int(i) for i in corpora["merge_requests"]),
            canonical=CANONICAL,
            repo_url=REPO_URL,
        ),
    )

    attach_written = _copy_attachments()
    pruned = _prune()
    fpmod.save(fp_path, current)

    (DIST / "_keys.json").write_text(
        json.dumps(dict(sorted(KEYS.items())), indent=0) + "\n", encoding="utf-8"
    )
    manifest = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "pages": written,
        "items_rendered": rendered,
        "items_reused": reused,
        "pruned": pruned,
        "attachments": attach_written,
        "issues": len(corpora["issues"]),
        "merge_requests": len(corpora["merge_requests"]),
        "comments": comment_total,
        "merge_requests_with_diffs": diff_total,
        "changed_files": diff_files,
    }
    (DIST / "_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _sitemap(
    corpora: dict[str, dict[str, dict[str, Any]]],
    labels: dict[str, dict[str, Any]],
) -> str:
    """One URL per canonical page.

    An archive meant to absorb the traffic gitlab.kitware.com is carrying has
    to be findable first. Aliases, `.md` twins, diff pages and `page/1` are
    left out: they are the same content under another path.
    """
    out = ['<?xml version="1.0" encoding="UTF-8"?>']
    out.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')

    def url(path: str, lastmod: str = "") -> None:
        out.append("<url>")
        out.append(f"<loc>{escape(f'{mdmod.SITE}{path}')}</loc>")
        if lastmod:
            out.append(f"<lastmod>{escape(lastmod[:10])}</lastmod>")
        out.append("</url>")

    url("/")
    url("/search")
    url("/vtk/vtk/-/labels")
    for kind, records in corpora.items():
        pages = max(1, -(-len(records) // PER_PAGE))
        url(f"/vtk/vtk/-/{kind}")
        for page in range(2, pages + 1):
            url(f"/vtk/vtk/-/{kind}/page/{page}")
        for iid, item in records.items():
            url(f"/vtk/vtk/-/{kind}/{iid}", item.get("updated_at") or "")
    for key, label in labels.items():
        pages = max(1, -(-len(label["rows"]) // PER_PAGE))
        url(f"/vtk/vtk/-/labels/{key}")
        for page in range(2, pages + 1):
            url(f"/vtk/vtk/-/labels/{key}/page/{page}")
    out.append("</urlset>")
    return "\n".join(out) + "\n"


def _held_uploads() -> frozenset[str]:
    """Attachment digests whose bytes are on disk, so a link is only rewritten
    to the archive when the archive can answer it."""
    path = store.DATA / "attachments.json"
    if not path.exists():
        return frozenset()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(k.split("/", 1)[0] for k in attachstore.ok_keys(manifest))


def _snapshot(now: datetime) -> dict[str, Any]:
    """What the banner reads to date itself and to say whether it is stale.

    It is a separate file because an item page is reused across runs: a stamp
    baked into one would be that page's age, not the archive's.
    """
    run = os.environ.get("GITHUB_RUN_ID")
    repo = os.environ.get("GITHUB_REPOSITORY")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return {
        "generated": now.isoformat(timespec="seconds"),
        "stale_after_hours": STALE_AFTER_HOURS,
        "run_url": f"{server}/{repo}/actions/runs/{run}" if run and repo else WORKFLOW_URL,
    }


def _copy_assets() -> None:
    """Stage the stylesheet, icons and licences, claimed so the prune keeps them.

    The licences are not decoration: the merge-request pages reproduce VTK's
    diffs, so the BSD notice has to travel with them. See NOTICE.
    """
    for folder in ("assets", "licenses"):
        base = ROOT / "assets" if folder == "assets" else ROOT.parent / folder
        for asset in sorted(base.iterdir()):
            if not asset.is_file():
                continue
            rel = f"{folder}/{asset.name}"
            dest = DIST / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(asset, dest)
            KEYS[rel] = rel


def _prune() -> int:
    """Delete staged files this run did not claim.

    Keeping dist/ across runs means a page whose record disappeared would be
    served forever. Everything the build owns registers in `KEYS`, so anything
    else under dist/ is an orphan. `pagefind/` belongs to the index pass and
    files starting `_` are this build's own bookkeeping.
    """
    removed = 0
    for path in DIST.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(DIST).as_posix()
        if rel in KEYS or rel.startswith(("pagefind/", "_")):
            continue
        path.unlink()
        removed += 1
    for path in sorted(DIST.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return removed


def _copy_attachments() -> int:
    """Place each stored attachment at the path the rendered HTML points to."""
    manifest_path = store.DATA / "attachments.json"
    if not manifest_path.exists() or not ATTACH_DIR.exists():
        return 0
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    count = 0
    for key, entry in entries.items():
        if entry.get("status") != "ok":
            continue
        src = ATTACH_DIR / key
        if not src.exists():
            continue
        rel = f"-/project/13/uploads/{key}"
        dest = DIST / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Claimed so the prune keeps it, and copied only when it differs, so a
        # rebuild does not rewrite a gigabyte of unchanged binaries.
        KEYS[rel] = rel
        if not dest.exists() or dest.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dest)
        count += 1
    return count
