"""Cache headers, which are the bandwidth bill.

Netlify counts every byte it serves, so a crawler that re-fetches what it
already has is money. 1.18 GB of the site is attachments whose URL carries
their own digest, and those can be cached forever; nothing else can, because
the archive is rebuilt daily.

The LAST matching rule wins and specificity counts for nothing. Measured on a
draft deploy, because Netlify documents neither: `/x/*` then `/x/deep/*` served
the second, and `/y/deep/*` then `/y/*` also served the second. This file first
asserted the opposite and passed while every rule but the catch-all was dead,
so `scripts/check-headers.sh` asks the live site after each deploy.
"""

from __future__ import annotations

from builder.build import HEADERS
from builder.render import UPLOADS

CATCH_ALL = "/*"


def _blocks() -> list[tuple[str, dict[str, str]]]:
    """Every path block in file order, with the headers it sets."""
    out: list[tuple[str, dict[str, str]]] = []
    for chunk in HEADERS.split("\n\n"):
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        out.append((lines[0].strip(), headers))
    return out


def _matches(rule: str, path: str) -> bool:
    return path.startswith(rule[:-1]) if rule.endswith("*") else rule == path


def _effective(path: str) -> str:
    """The Cache-Control the site really serves: the last match, not the first."""
    winner = None
    for rule, headers in _blocks():
        if _matches(rule, path) and "cache-control" in headers:
            winner = headers["cache-control"]
    if winner is None:
        raise AssertionError(f"no Cache-Control reaches {path}")
    return winner


def test_an_attachment_is_immutable_because_its_url_is_its_digest():
    rule = _effective(f"{UPLOADS}/0af05ade/image.png")
    assert "immutable" in rule
    assert "max-age=31536000" in rule


def test_a_page_is_not_cached_for_a_year():
    """The archive is rebuilt daily; a year-long page cache serves a corpse."""
    seconds = int(_effective("/issues/1234").split("max-age=")[1].split(",")[0])
    assert seconds <= 86400


def test_the_stylesheet_is_not_cached_past_a_rebuild():
    """`style.css` has no content hash in its name, so a long cache on it
    serves yesterday's CSS against today's markup."""
    rule = _effective("/assets/style.css")
    assert int(rule.split("max-age=")[1].split(",")[0]) <= 86400
    assert "stale-while-revalidate" not in rule


def test_the_catch_all_is_first():
    """Last match wins, so a catch-all written last buries every rule above it.
    Flip these two blocks and the immutable test above goes red."""
    paths = [rule for rule, _ in _blocks()]
    assert paths[0] == CATCH_ALL
    assert paths.count(CATCH_ALL) == 1


def test_every_narrower_rule_actually_changes_something():
    """A rule that restates what the catch-all already said is dead weight, and
    an unreachable one reads as protection that is not there."""
    catch_all = dict(_blocks())[CATCH_ALL]["cache-control"]
    narrower = [(r, h) for r, h in _blocks() if r != CATCH_ALL]
    assert narrower
    for rule, headers in narrower:
        assert "cache-control" in headers, rule
        assert headers["cache-control"] != catch_all, rule
        probe = rule[:-1] + "probe" if rule.endswith("*") else rule
        assert _effective(probe) == headers["cache-control"], rule
