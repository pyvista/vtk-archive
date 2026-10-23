"""Strip personal data the archive has no reason to republish.

GitLab returns `author.public_email` on every person object. These are
addresses their owners chose to show on a profile page, but republishing
thousands of them as a bulk static corpus turns public-but-obscure into a
harvesting target, and this archive promises in /about that it does not
reproduce them. Scrubbing happens at the client, before anything is written,
so the corpus never holds one.

Note bodies are NOT scrubbed: an address a person typed into an issue is part
of the discussion being archived, and rewriting it would falsify the record.
Removal requests are handled by a human, per /about.
"""

from __future__ import annotations

import re
from typing import Any

# Fields on a person object that carry a contact address.
PERSON_EMAIL_FIELDS = frozenset({"public_email", "email", "commit_email", "work_email"})

# Identity fields anywhere in the corpus. Mantis used the address as the
# display name, so one reporter's was published on a rendered page while a
# key-name scan reported the corpus clean.
NAME_FIELDS = frozenset({"reporter", "assigned_to", "author", "name", "username"})

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def depersonalize(name: str) -> str:
    """A display name with any address reduced to its local part."""
    return EMAIL.sub(lambda m: m.group(0).split("@", 1)[0], name)


# Fields of no archival value that only add bulk or churn.
DROP_FIELDS = frozenset({"avatar_url"})


def scrub(value: Any) -> Any:
    """Recursively remove email and noise fields from an API payload."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in PERSON_EMAIL_FIELDS or k in DROP_FIELDS:
                continue
            out[k] = depersonalize(v) if k in NAME_FIELDS and isinstance(v, str) else scrub(v)
        return out
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def find_emails(value: Any, path: str = "") -> list[str]:
    """Report any surviving email-bearing field, as `path=value`. Empty is clean."""
    hits: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            here = f"{path}.{k}" if path else k
            carries_address = (k in PERSON_EMAIL_FIELDS and v) or (
                k in NAME_FIELDS and isinstance(v, str) and EMAIL.search(v)
            )
            if carries_address:
                hits.append(f"{here}={v}")
            else:
                hits.extend(find_emails(v, here))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            hits.extend(find_emails(v, f"{path}[{i}]"))
    return hits
