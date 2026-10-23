"""Anonymous GraphQL client for gitlab.kitware.com.

REST `/notes` answers 401 to an anonymous caller; GraphQL serves full comment
bodies with no token, and returns GitLab's own rendered HTML
(`descriptionHtml`, `bodyHtml`) so we never reimplement GitLab-flavored
markdown, reference linking or syntax highlighting. See docs/SOURCES.md.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

ENDPOINT = "https://gitlab.kitware.com/api/graphql"
PROJECT = "vtk/vtk"

# Identify the crawler: unattributed scraping is what broke this host.
USER_AGENT = "vtk-archive/0.1 (+https://github.com/pyvista/vtk-archive; archival mirror)"

# GitLab caps a connection at 100; anonymous query complexity caps the nesting.
# MRs carry ~20 notes each against an issue's ~7, so they page smaller.
ISSUE_PAGE = 100
MR_PAGE = 25
NOTE_PAGE = 100

_AUTHOR = "author { username name }"

_NOTE_FIELDS = f"""
  id
  body
  bodyHtml
  createdAt
  updatedAt
  system
  systemNoteIconName
  internal
  {_AUTHOR}
  awardEmoji {{ nodes {{ name user {{ username }} }} }}
  position {{ newPath oldPath newLine oldLine }}
"""

ISSUES_QUERY = f"""
query Issues($path: ID!, $first: Int!, $after: String, $updatedAfter: Time) {{
  project(fullPath: $path) {{
    issues(first: $first, after: $after, updatedAfter: $updatedAfter, sort: UPDATED_ASC) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        iid
        title
        titleHtml
        description
        descriptionHtml
        state
        createdAt
        updatedAt
        closedAt
        webUrl
        upvotes
        downvotes
        userNotesCount
        {_AUTHOR}
        labels {{ nodes {{ title color description }} }}
        milestone {{ title webUrl dueDate }}
        assignees {{ nodes {{ username name }} }}
        awardEmoji {{ nodes {{ name user {{ username }} }} }}
        notes(first: {NOTE_PAGE}) {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{ {_NOTE_FIELDS} }}
        }}
      }}
    }}
  }}
}}
"""

MRS_QUERY = f"""
query MergeRequests($path: ID!, $first: Int!, $after: String, $updatedAfter: Time) {{
  project(fullPath: $path) {{
    mergeRequests(first: $first, after: $after, updatedAfter: $updatedAfter, sort: UPDATED_ASC) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        iid
        title
        titleHtml
        description
        descriptionHtml
        state
        createdAt
        updatedAt
        mergedAt
        webUrl
        sourceBranch
        targetBranch
        commitCount
        diffRefs {{ baseSha headSha startSha }}
        diffStatsSummary {{ additions deletions fileCount changes }}
        {_AUTHOR}
        mergeUser {{ username name }}
        labels {{ nodes {{ title color }} }}
        milestone {{ title webUrl }}
        reviewers {{ nodes {{ username name }} }}
        assignees {{ nodes {{ username name }} }}
        awardEmoji {{ nodes {{ name user {{ username }} }} }}
        notes(first: {NOTE_PAGE}) {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{ {_NOTE_FIELDS} }}
        }}
        discussions(first: 100) {{
          pageInfo {{ hasNextPage }}
          nodes {{
            id
            resolved
            resolvedAt
            resolvedBy {{ username }}
            notes(first: {NOTE_PAGE}) {{ nodes {{ id }} }}
          }}
        }}
      }}
    }}
  }}
}}
"""

ISSUE_NOTES_QUERY = f"""
query IssueNotes($path: ID!, $iid: String!, $after: String) {{
  project(fullPath: $path) {{
    issues(iids: [$iid]) {{
      nodes {{
        notes(first: {NOTE_PAGE}, after: $after) {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{ {_NOTE_FIELDS} }}
        }}
      }}
    }}
  }}
}}
"""

MR_NOTES_QUERY = f"""
query MrNotes($path: ID!, $iid: String!, $after: String) {{
  project(fullPath: $path) {{
    mergeRequests(iids: [$iid]) {{
      nodes {{
        notes(first: {NOTE_PAGE}, after: $after) {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{ {_NOTE_FIELDS} }}
        }}
      }}
    }}
  }}
}}
"""


class GraphQLError(RuntimeError):
    """The server answered 200 with an `errors` array."""


class ChallengedError(RuntimeError):
    """Anubis served its proof-of-work page instead of answering."""


@dataclass
class Stats:
    requests: int = 0
    retries: int = 0
    seconds: float = 0.0
    bytes_in: int = 0


class GitLab:
    """One sequential, polite, anonymous GraphQL caller.

    Deliberately not concurrent: uncoordinated parallel scraping is what made
    this host unusable, and the corpus is small enough that sequential works.
    """

    def __init__(self, *, delay: float = 0.25, timeout: float = 180.0, retries: int = 5):
        self.delay = delay
        self.retries = retries
        self.stats = Stats()
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitLab:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        payload = {"query": query, "variables": variables}
        last: Exception | None = None
        for attempt in range(self.retries):
            if attempt:
                self.stats.retries += 1
                time.sleep(min(2**attempt, 60))
            started = time.monotonic()
            try:
                r = self._client.post(ENDPOINT, json=payload)
            except httpx.HTTPError as exc:
                last = exc
                continue
            finally:
                self.stats.requests += 1
                self.stats.seconds += time.monotonic() - started

            if r.status_code in (429, 500, 502, 503, 504):
                last = GraphQLError(f"HTTP {r.status_code}")
                continue
            if r.status_code == 403 and "Verifying connection" in r.text[:400]:
                raise ChallengedError(
                    f"Anubis challenged User-Agent {USER_AGENT!r} on {ENDPOINT}. "
                    "This endpoint is allowlisted to curl/* and Wget/* only; we do not "
                    "spoof those. See README 'Blocked' and docs/SOURCES.md."
                )
            r.raise_for_status()
            self.stats.bytes_in += len(r.content)
            body = r.json()
            if "errors" in body:
                # Schema and complexity errors are permanent; retrying wastes the server's time.
                raise GraphQLError(str(body["errors"])[:2000])
            time.sleep(self.delay)
            return body["data"]
        raise GraphQLError(f"giving up after {self.retries} attempts: {last}")

    def pages(
        self,
        query: str,
        *,
        connection: str,
        page_size: int,
        updated_after: str | None = None,
        after: str | None = None,
    ):
        """Yield `(nodes, end_cursor, has_next)` from a project-level connection."""
        cursor = after
        while True:
            data = self.query(
                query,
                {
                    "path": PROJECT,
                    "first": page_size,
                    "after": cursor,
                    "updatedAfter": updated_after,
                },
            )
            conn = data["project"][connection]
            info = conn["pageInfo"]
            cursor = info["endCursor"]
            yield conn["nodes"], cursor, info["hasNextPage"]
            if not info["hasNextPage"]:
                return

    def remaining_notes(self, kind: str, iid: str, after: str) -> list[dict[str, Any]]:
        """Drain notes past the first page of one long thread."""
        query = ISSUE_NOTES_QUERY if kind == "issue" else MR_NOTES_QUERY
        connection = "issues" if kind == "issue" else "mergeRequests"
        extra: list[dict[str, Any]] = []
        cursor = after
        while True:
            data = self.query(query, {"path": PROJECT, "iid": iid, "after": cursor})
            nodes = data["project"][connection]["nodes"]
            if not nodes:
                return extra
            notes = nodes[0]["notes"]
            extra.extend(notes["nodes"])
            if not notes["pageInfo"]["hasNextPage"]:
                return extra
            cursor = notes["pageInfo"]["endCursor"]
