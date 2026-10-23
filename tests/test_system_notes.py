"""The allowlist that lets GitLab's own system-note HTML through.

The fixtures are the exact shapes measured in the corpus (`<ul>`, `<li>`,
`<code class="idiff">`, `<span class="idiff left right addition">`) plus the
shapes the allowlist exists to refuse.
"""

from __future__ import annotations

import pytest

from builder.render import Corpus, build_parser, to_html

CORPUS = Corpus.empty()


@pytest.fixture
def md():
    return build_parser()


def test_commit_list_renders_as_a_list(md):
    body = "added 6 commits\n\n<ul><li>30a0b067 - DOC: fix a typo</li></ul>"
    html = to_html(body, CORPUS, md, system=True)
    assert "<ul><li>" in html
    assert "&lt;ul&gt;" not in html


def test_idiff_span_keeps_its_class(md):
    body = '<span class="idiff left right addition">new title</span>'
    html = to_html(body, CORPUS, md, system=True)
    assert '<span class="idiff left right addition">' in html


def test_a_link_is_not_in_the_allowlist(md):
    html = to_html('<a href="javascript:alert(1)">x</a>', CORPUS, md, system=True)
    assert "<a href=" not in html
    assert "&lt;a href=" in html


def test_an_event_handler_attribute_is_refused(md):
    html = to_html('<span onclick="alert(1)">x</span>', CORPUS, md, system=True)
    assert "<span" not in html
    assert "onclick" in html  # still present, still inert


def test_a_class_value_with_a_quote_is_refused(md):
    html = to_html('<span class="a" onload="y">x</span>', CORPUS, md, system=True)
    assert "<span" not in html


def test_an_author_body_keeps_every_tag_escaped(md):
    body = "<ul><li>not a list</li></ul> and std::vector<int>"
    html = to_html(body, CORPUS, md, system=False)
    assert "<ul>" not in html
    assert "&lt;ul&gt;" in html
    assert "&lt;int&gt;" in html


def test_system_default_is_off(md):
    assert "&lt;ul&gt;" in to_html("<ul><li>x</li></ul>", CORPUS, md)
