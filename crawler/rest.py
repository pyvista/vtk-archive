"""REST client for gitlab.kitware.com `/api/v4`.

REST is the primary source, not GraphQL: `/api/v4` is not User-Agent gated, so
an honestly-identified crawler works here while GraphQL answers 403. Notes,
labels, milestones and issue links need a `read_api` token; everything else is
open. See docs/SOURCES.md.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from crawler import times
from crawler.scrub import scrub

BASE = "https://gitlab.kitware.com/api/v4"
PROJECT_ID = 13
USER_AGENT = "vtk-archive/0.1 (+https://github.com/pyvista/vtk-archive; archival mirror)"
ENV_FILE = Path.home() / ".config" / "vtk-archive" / "env"


def load_token() -> str | None:
    """Read `GITLAB_TOKEN` from the environment, else from the env file."""
    if tok := os.environ.get("GITLAB_TOKEN"):
        return tok.strip()
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("GITLAB_TOKEN=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


class Unauthorized(RuntimeError):
    """The endpoint needs a token and we do not have a usable one."""


@dataclass
class Stats:
    requests: int = 0
    retries: int = 0
    seconds: float = 0.0
    bytes_in: int = 0
    unauthorized: set[str] = field(default_factory=set)


class Rest:
    """Sequential, polite, optionally authenticated.

    Deliberately not concurrent: uncoordinated parallel scraping is what made
    this host unusable, and the corpus is small enough that sequential works.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        delay: float = 0.2,
        timeout: float = 120.0,
        retries: int = 5,
    ):
        self.token = token if token is not None else load_token()
        self.delay = delay
        self.retries = retries
        self.stats = Stats()
        headers = {"User-Agent": USER_AGENT}
        if self.token:
            headers["PRIVATE-TOKEN"] = self.token
        self._client = httpx.Client(timeout=timeout, headers=headers, follow_redirects=False)

    @property
    def authenticated(self) -> bool:
        return bool(self.token)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Rest:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def whoami(self) -> dict[str, Any] | None:
        """Identify the token's owner, or None when unauthenticated."""
        if not self.token:
            return None
        r = self._client.get(f"{BASE}/user")
        if r.status_code == 200:
            return r.json()
        raise Unauthorized(f"GET /user returned {r.status_code}: {r.text[:200]}")

    def get(self, path: str, **params: Any) -> httpx.Response:
        url = f"{BASE}/projects/{PROJECT_ID}/{path.lstrip('/')}"
        last: Exception | None = None
        for attempt in range(self.retries):
            if attempt:
                self.stats.retries += 1
                time.sleep(min(2**attempt, 60))
            started = time.monotonic()
            try:
                r = self._client.get(url, params={k: v for k, v in params.items() if v is not None})
            except httpx.HTTPError as exc:
                last = exc
                continue
            finally:
                self.stats.requests += 1
                self.stats.seconds += time.monotonic() - started
            if r.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {r.status_code} on {path}")
                continue
            if r.status_code == 401:
                self.stats.unauthorized.add(path.split("?")[0])
                raise Unauthorized(path)
            r.raise_for_status()
            self.stats.bytes_in += len(r.content)
            time.sleep(self.delay)
            return r
        raise RuntimeError(f"giving up on {path} after {self.retries} attempts: {last}")

    def paged(self, path: str, *, per_page: int = 100, **params: Any):
        """Yield successive pages of a list endpoint, scrubbed of personal data."""
        page = 1
        while True:
            r = self.get(path, per_page=per_page, page=page, **params)
            rows = scrub(r.json())
            if not rows:
                return
            yield rows
            nxt = r.headers.get("x-next-page")
            if not nxt:
                return
            page = int(nxt)

    def walk_updated(
        self, path: str, *, per_page: int = 100, since: str | None = None, **params: Any
    ):
        """Pages of a list endpoint in `updated_at` order, by timestamp cursor.

        Offset pagination over a list ordered by a column that is changing
        drops rows, and the dropped row never comes back.
        See docs/MAINTENANCE.md "Why the skeleton pass does not page by offset".
        """
        cursor = since
        page = 1
        while True:
            r = self.get(
                path,
                per_page=per_page,
                page=page,
                order_by="updated_at",
                sort="asc",
                updated_after=cursor,
                **params,
            )
            rows = scrub(r.json())
            if not rows:
                return
            yield rows
            seen = times.newest(row.get("updated_at") for row in rows)
            if seen and seen != cursor:
                # Re-anchor. `updated_after` is inclusive, so the row carrying
                # this stamp comes back; the corpus is keyed by iid, so a
                # duplicate costs one request and changes nothing.
                cursor, page = seen, 1
            else:
                # A whole page shares one timestamp, so the cursor cannot move.
                page += 1

    def all_rows(self, path: str, *, per_page: int = 100, **params: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for rows in self.paged(path, per_page=per_page, **params):
            out.extend(rows)
        return out
