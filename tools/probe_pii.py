"""Does the crawled corpus carry email addresses in author metadata?

GitLab's /events route exposes author.public_email. If /discussions does too,
every note we publish would carry a real address.
"""

from __future__ import annotations

import collections

from crawler import store

EMAIL_KEYS = ("public_email", "email", "commit_email")

for kind in ("issues", "merge_requests"):
    records = store.load(kind)
    author_keys: collections.Counter[str] = collections.Counter()
    found: list[str] = []
    checked = 0
    for rec in records.values():
        people = [rec.get("author"), rec.get("merge_user"), rec.get("closed_by")]
        people += rec.get("assignees") or []
        people += rec.get("reviewers") or []
        for note in rec.get("notes") or []:
            people.append(note.get("author"))
        for person in people:
            if not isinstance(person, dict):
                continue
            checked += 1
            author_keys.update(person.keys())
            for key in EMAIL_KEYS:
                if person.get(key):
                    found.append(f"{key}={person[key]}")
    print(f"== {kind}: {len(records)} records, {checked} person objects")
    print(f"   person keys seen: {sorted(author_keys)}")
    print(f"   email-bearing fields populated: {len(found)}")
    if found:
        print(f"   examples: {found[:3]}")
