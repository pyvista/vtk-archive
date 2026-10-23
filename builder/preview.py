"""Local preview server that answers the way the real host will.

Pages are staged as `<url>/index.html`, which every static host serves at the
pretty URL, so this resolves a request to the same file the CDN would pick. It
also stands in for the gateway Worker's `?uri=` redirect. If a link works here
it works in production.
"""

from __future__ import annotations

import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from builder import build

DIST = Path(__file__).resolve().parent.parent / "dist"
CONTAINER = build.CONTAINER


def resolve(key: str) -> Path | None:
    """The staged file that answers this request path, or None.

    Asked of the filesystem per request rather than from a snapshot taken at
    startup. A snapshot made the server lie after any rebuild: a `vtk-archive
    index` run against a server already up left every Pagefind asset 404, so
    the search page reported the index missing while it sat on disk.
    """
    key = key.strip("/")
    if key.split("/", 1)[0].startswith("_"):
        return None
    base = (DIST / key).resolve() if key else DIST.resolve()
    # Nothing outside dist/, whatever `..` the request carries.
    if base != DIST.resolve() and DIST.resolve() not in base.parents:
        return None
    if base.is_file():
        return base
    # The pretty URL every static host resolves to the container document.
    pretty = base / CONTAINER
    return pretty if pretty.is_file() else None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's interface
        parsed = urlparse(self.path)
        key = unquote(parsed.path).lstrip("/")

        # The gateway Worker handles both of these in production. The redirect
        # is checked first: `/?uri=...` is a root request with a query, so
        # resolving the root first would swallow it.
        if gateway := _gateway_target(parsed.query):
            self.send_response(302)
            self.send_header("Location", gateway)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path = resolve(key)
        if path is None:
            body = (
                b"<!doctype html><meta charset=utf-8>"
                b"<h1>404</h1><p>No object at this key. "
                b'<a href="/">Archive home</a></p>'
            )
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _content_type(key))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _gateway_target(query: str) -> str | None:
    """Mimic the `?uri=` redirect gateway.

    Parsed as a URL rather than split on a substring: the old form accepted the
    marker anywhere, so `?uri=https://evil.test/?x=gitlab.kitware.com//evil.test/p`
    redirected off the site. A protocol-relative path does the same, so only a
    single-slash path on the canonical host is followed.
    """
    for part in query.split("&"):
        if not part.startswith("uri="):
            continue
        try:
            p = urlparse(unquote(part[4:]))
        except ValueError:
            return None
        if p.hostname != "gitlab.kitware.com":
            return None
        path = p.path or "/"
        if not path.startswith("/") or path.startswith("//"):
            return None
        return path + (f"#{p.fragment}" if p.fragment else "")
    return None


def _content_type(key: str) -> str:
    """Resolve from the staged filename, the way a CDN does."""
    name = key.rsplit("/", 1)[-1]
    if "." not in name:
        return "text/html; charset=utf-8"
    if name.endswith(".wasm"):
        return "application/wasm"  # instantiateStreaming refuses anything else
    guess, _ = mimetypes.guess_type(key)
    if guess and guess.startswith("text/"):
        return f"{guess}; charset=utf-8"
    return guess or "application/octet-stream"


def serve(port: int = 8787) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"serving {DIST} (resolved per request, so a rebuild needs no restart)")
    print(f"  http://127.0.0.1:{port}/")
    print(f"  http://127.0.0.1:{port}/vtk/vtk/-/issues")
    print(f"  http://127.0.0.1:{port}/vtk/vtk/-/issues/20218")
    print(f"  http://127.0.0.1:{port}/about")
    server.serve_forever()


if __name__ == "__main__":
    serve()
