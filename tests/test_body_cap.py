"""An overlong comment body.

Merge request 4298 holds two notes of 7.2 MB each, someone's build log pasted
twice, and they rendered to a 12.7 MB HTML page. Eleven pages in the corpus
carry a body over 200 KB. The cap is on the HTML only; the Markdown twin is
the complete record and stays that way.
"""

from __future__ import annotations

import pytest

from builder.markdown import render
from builder.render import BODY_CAP, Corpus, build_parser, cap_body, to_html

CORPUS = Corpus(
    issues=frozenset(),
    merge_requests=frozenset(),
    uploads=frozenset(),
    users=frozenset(),
)
LOG = "\n".join(f"[{i:07d}] cmake: compiling something" for i in range(12_000))
ITEM = {
    "iid": "4298",
    "title": "A long one",
    "state": "merged",
    "created_at": "2026-01-01",
    "labels": [],
    "description": LOG,
}


@pytest.fixture(scope="module")
def md():
    return build_parser()


def test_a_body_under_the_cap_is_untouched():
    assert cap_body("short") == ("short", False)


def test_the_log_fixture_is_actually_over_the_cap():
    """Otherwise every assertion below passes for the wrong reason."""
    assert len(LOG) > BODY_CAP


def test_an_overlong_body_is_cut_and_says_so(md):
    html = to_html(LOG, CORPUS, md)
    assert "note-cap" in html
    assert "Append <code>.md</code>" in html
    assert len(html) < len(LOG)


def test_the_cut_lands_on_a_line_boundary():
    kept, capped = cap_body(LOG)
    assert capped
    assert kept.endswith("compiling something")
    assert len(kept) <= BODY_CAP


def test_the_notice_lands_outside_a_cut_code_block(md):
    """The cut can land inside a fenced block, and a notice emitted inside the
    `<pre>` reads as part of the log it is explaining."""
    html = to_html("```\n" + LOG, CORPUS, md)
    assert html.count("<pre>") == html.count("</pre>") == 1
    assert html.index("note-cap") > html.index("</pre>")


def test_the_markdown_twin_keeps_every_byte():
    """The cap is a rendering decision. The twin is the archive."""
    text = render(
        ITEM,
        kind="merge_requests",
        sigil="!",
        author="Someone",
        canonical="https://gitlab.kitware.com/vtk/vtk/-/merge_requests/4298",
        held={"issues": frozenset(), "merge_requests": frozenset()},
        corpus=CORPUS,
        change=None,
    )
    assert LOG in text
    assert "note-cap" not in text
