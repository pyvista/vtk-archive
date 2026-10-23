"""`vtk-archive` command line."""

from __future__ import annotations

import argparse
import json
import sys

from crawler import commits, mantis, store
from crawler.attachments import fetch
from crawler.crawl import KINDS, notes, skeleton
from crawler.diffs import crawl as diffs_crawl
from crawler.rest import Rest, Unauthorized
from crawler.scrub import find_emails, scrub


def _add_kinds(p: argparse.ArgumentParser) -> None:
    # No `choices=`: before 3.14 argparse validates a list default against it and
    # rejects its own default, so `skeleton` with no kind named exits 2.
    p.add_argument(
        "kinds",
        nargs="*",
        metavar="{" + ",".join(sorted(KINDS)) + "}",
        help="which corpora to work on (default: all)",
    )
    p.add_argument("--limit", type=int, help="stop after roughly N records")
    p.add_argument("--delay", type=float, default=0.2, help="seconds between requests")


def _kinds(parser: argparse.ArgumentParser, named: list[str]) -> list[str]:
    """Every kind when none is named, and a real error when one is misspelt."""
    if not named:
        return sorted(KINDS)
    unknown = [k for k in named if k not in KINDS]
    if unknown:
        parser.error(
            f"unknown kind(s): {', '.join(unknown)} (choose from {', '.join(sorted(KINDS))})"
        )
    return named


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vtk-archive")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("skeleton", help="every issue/MR with its body (no token needed)")
    _add_kinds(s)
    s.add_argument("--since", help="only records updated after this ISO timestamp")
    s.add_argument("--incremental", action="store_true", help="use the stored watermark")

    n = sub.add_parser("notes", help="attach comment threads (needs a read_api token)")
    _add_kinds(n)
    n.add_argument("--refetch", action="store_true", help="re-fetch items that already have notes")

    d = sub.add_parser("diffs", help="fetch the code change on every merge request")
    d.add_argument("--limit", type=int, help="stop after N merge requests")
    d.add_argument("--delay", type=float, default=0.2)
    d.add_argument("--refetch", action="store_true", help="re-fetch MRs that already have a diff")

    m = sub.add_parser("mantis", help="fetch the pre-2015 Mantis discussion (no token needed)")
    m.add_argument("--limit", type=int, help="stop after N pages")
    m.add_argument("--delay", type=float, default=0.5)
    m.add_argument("--refetch", action="store_true", help="re-fetch pages already stored")

    c = sub.add_parser("commits", help="record which SHAs the GitHub mirror holds")
    # Fetching is the default: a clone that is never refreshed answers "not in
    # the mirror" for every commit pushed since it was made, and that answer
    # sends the reader back to gitlab.kitware.com.
    c.add_argument(
        "--no-refresh",
        dest="refresh",
        action="store_false",
        help="use the clone as it stands instead of fetching first",
    )

    a = sub.add_parser("attachments", help="fetch files referenced from bodies and comments")
    a.add_argument("--limit", type=int, help="stop after N files")
    a.add_argument("--delay", type=float, default=0.2)
    a.add_argument(
        "--refetch", action="store_true", help="re-probe files the host already answered"
    )

    b = sub.add_parser("build", help="render the corpus into dist/")
    b.add_argument(
        "--full",
        action="store_true",
        help="re-render every page, ignoring the fingerprints from the last build",
    )
    sub.add_parser("index", help="build the Pagefind search index over dist/")
    sub.add_parser("scrub", help="remove personal data from the corpus already on disk")
    p = sub.add_parser("preview", help="serve dist/ locally the way the host will")
    p.add_argument("--port", type=int, default=8787)
    sub.add_parser("status", help="what the local corpus holds")
    sub.add_parser("check-access", help="probe which endpoints this credential can reach")

    args = parser.parse_args(argv)

    if args.command == "status":
        return _status()
    if args.command == "check-access":
        return _check_access()
    if args.command == "preview":
        from builder.preview import serve  # noqa: PLC0415 — keeps http.server off other paths

        serve(port=args.port)
        return 0
    if args.command == "scrub":
        return _scrub()
    if args.command == "build":
        from builder.build import build  # noqa: PLC0415 — keeps jinja off the crawl path

        json.dump(build(full=args.full), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.command == "index":
        return _index()
    if args.command == "mantis":
        result = mantis.crawl(limit=args.limit, delay=args.delay, refetch=args.refetch)
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.command == "commits":
        json.dump(commits.build(refresh=args.refresh), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.command == "diffs":
        result = diffs_crawl(limit=args.limit, delay=args.delay, refetch=args.refetch)
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.command == "attachments":
        json.dump(
            fetch(limit=args.limit, delay=args.delay, refetch=args.refetch), sys.stdout, indent=2
        )
        sys.stdout.write("\n")
        return 0

    results = []
    for kind in _kinds(parser, args.kinds):
        if args.command == "skeleton":
            since = args.since
            if args.incremental and not since:
                since = store.load_state().get(kind, {}).get("skeleton_through")
            print(f"skeleton {kind} (since={since or 'beginning'})", file=sys.stderr)
            results.append(skeleton(kind, updated_after=since, limit=args.limit, delay=args.delay))
        else:
            print(f"notes {kind}", file=sys.stderr)
            results.append(notes(kind, limit=args.limit, delay=args.delay, refetch=args.refetch))

    json.dump(results, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _index() -> int:
    """Build the Pagefind index over dist/, which `build` deliberately keeps."""
    import shutil  # noqa: PLC0415 — only this path needs it
    import subprocess  # noqa: PLC0415 — only this path needs it
    from pathlib import Path  # noqa: PLC0415 — only this path needs it

    dist = Path(__file__).resolve().parent.parent / "dist"
    if not (dist / "index.html").exists():
        print("dist/ has no pages; run `vtk-archive build` first", file=sys.stderr)
        return 1
    runner = shutil.which("npx")
    if not runner:
        print("npx not found; install Node to build the search index", file=sys.stderr)
        return 1
    # Pagefind adds fragments and leaves the ones it no longer references, so
    # an index built over a dist/ that lost pages keeps serving results that
    # 404. The index is derived from dist/, so throwing it away costs a rebuild.
    shutil.rmtree(dist / "pagefind", ignore_errors=True)
    cmd = [runner, "-y", "pagefind@1.5.2", "--site", str(dist), "--output-subdir", "pagefind"]
    return subprocess.run(cmd, check=False).returncode


def _scrub() -> int:
    """Strip personal data from a corpus crawled before scrubbing was added.

    Covers the Mantis corpus too. Scanning only `KINDS` left an address
    published as an issue reporter's display name while this reported clean.
    """
    with store.lock(*sorted(KINDS), "mantis", who="scrub"):
        return _scrub_held()


def _scrub_held() -> int:
    total_before = 0
    failed = 0
    for kind in sorted(KINDS):
        records = store.load(kind)
        if not records:
            continue
        before = len(find_emails(records))
        total_before += before
        cleaned = {iid: scrub(rec) for iid, rec in records.items()}
        after = len(find_emails(cleaned))
        store.save(kind, cleaned)
        print(f"{kind:16} {before:>6} removed, {after} remaining")
        failed += after

    held = mantis.load()
    if held:
        before = len(find_emails(held))
        total_before += before
        cleaned = {mid: scrub(rec) for mid, rec in held.items()}
        after = len(find_emails(cleaned))
        mantis.save(cleaned)
        print(f"{'mantis':16} {before:>6} removed, {after} remaining")
        failed += after

    print(f"\n{total_before} personal-data fields removed in total")
    if failed:
        print(f"FAILED: {failed} survived scrubbing")
        return 1
    return 0


def _status() -> int:
    state = store.load_state()
    for kind in sorted(KINDS):
        records = store.load(kind)
        with_notes = sum(1 for r in records.values() if "notes" in r)
        n_notes = sum(len(r.get("notes") or []) for r in records.values())
        path = store.path_for(kind)
        size = path.stat().st_size / 1048576 if path.exists() else 0.0
        slot = state.get(kind, {})
        print(
            f"{kind:16} {len(records):>6} records  {with_notes:>6} with notes  "
            f"{n_notes:>7} notes  {size:>7.1f} MiB  through={slot.get('skeleton_through') or '-'}"
        )
    return 0


def _check_access() -> int:
    """Name exactly what this credential unlocks, so a token is proven not assumed."""
    probes = [
        ("issues list", "issues", {"per_page": 1, "state": "all"}),
        ("merge_requests list", "merge_requests", {"per_page": 1, "state": "all"}),
        ("issue notes", "issues/20218/notes", {"per_page": 1}),
        ("MR notes", "merge_requests/13000/notes", {"per_page": 1}),
        ("MR discussions", "merge_requests/13000/discussions", {"per_page": 1}),
        ("labels", "labels", {"per_page": 1}),
        ("milestones", "milestones", {"per_page": 1}),
        ("issue links", "issues/20218/links", {}),
        ("resource label events", "issues/20218/resource_label_events", {}),
        ("MR diffs", "merge_requests/13000/diffs", {"per_page": 1}),
        ("MR commits", "merge_requests/13000/commits", {"per_page": 1}),
        ("award emoji", "merge_requests/13000/award_emoji", {}),
    ]
    with Rest(delay=0.1) as api:
        try:
            who = api.whoami()
        except Unauthorized as exc:
            print(f"token present but rejected: {exc}")
            return 1
        print(f"identity: {who['username']} ({who.get('name')})" if who else "identity: anonymous")
        blocked = 0
        for label, path, params in probes:
            try:
                api.get(path, **params)
                print(f"  {label:24} OK")
            except Unauthorized:
                blocked += 1
                print(f"  {label:24} 401 UNAUTHORIZED")
            except Exception as exc:  # noqa: BLE001 — a probe reports, it does not crash
                print(f"  {label:24} {type(exc).__name__}: {str(exc)[:60]}")
    print(f"\n{blocked} of {len(probes)} probes blocked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
