"""Cache headers, which are the bandwidth bill.

Netlify counts every byte it serves, so a crawler that re-fetches what it
already has is money. 1.18 GB of the site is attachments whose URL carries
their own digest, and those can be cached forever; nothing else can, because
the archive is rebuilt daily.
"""

from __future__ import annotations

from builder.build import HEADERS
from builder.render import UPLOADS


def _rule(path: str) -> str:
    """The Cache-Control the first matching block sets, Netlify's own order."""
    current = None
    for line in HEADERS.splitlines():
        if not line.startswith(" ") and line.strip():
            current = line.strip()
        elif line.strip().lower().startswith("cache-control:") and current == path:
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no Cache-Control for {path}")


def test_an_attachment_is_immutable_because_its_url_is_its_digest():
    rule = _rule(f"{UPLOADS}/*")
    assert "immutable" in rule
    assert "max-age=31536000" in rule


def test_a_page_is_not_cached_for_a_year():
    """The archive is rebuilt daily; a year-long page cache serves a corpse."""
    seconds = int(_rule("/*").split("max-age=")[1].split(",")[0])
    assert seconds <= 86400


def test_the_stylesheet_is_not_cached_past_a_rebuild():
    """`style.css` has no content hash in its name, so a long cache on it
    serves yesterday's CSS against today's markup."""
    seconds = int(_rule("/assets/*").split("max-age=")[1].split(",")[0])
    assert seconds <= 86400


def test_the_catch_all_is_last():
    """Netlify applies the first match, so `/*` above a specific path would
    swallow it and every rule after it would be dead."""
    paths = [ln.strip() for ln in HEADERS.splitlines() if ln and not ln.startswith(" ")]
    assert paths[-1] == "/*"
    assert paths.count("/*") == 1


def test_every_block_actually_sets_something():
    blocks = [b for b in HEADERS.split("\n\n") if b.strip()]
    assert len(blocks) >= 4
    for block in blocks:
        assert "Cache-Control:" in block, block
