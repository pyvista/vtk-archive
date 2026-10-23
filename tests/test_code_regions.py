"""A code region shows what the author typed.

`resolve_references` protected itself from the start. The upload, code-link and
canonical-URL rewrites did not, so a URL inside a fenced block came back
altered on 19 pages of the corpus: the archive was showing a command nobody
ran. The markdown twin had the same hole, against the raw markdown.
"""

from __future__ import annotations

import pytest

from builder.markdown import absolutize
from builder.render import Corpus, build_parser, to_html

CORPUS = Corpus(
    issues=frozenset({"123"}),
    merge_requests=frozenset({"456"}),
    uploads=frozenset({"a" * 32}),
    users=frozenset({"ben.boeckel"}),
)
HELD = {"issues": frozenset({"123"}), "merge_requests": frozenset({"456"})}

BLOB = "https://gitlab.kitware.com/vtk/vtk/-/blob/master/Common/Core/vtkObject.h"
UPLOAD = f"https://gitlab.kitware.com/uploads/{'a' * 32}/shot.png"
ITEM = "https://gitlab.kitware.com/vtk/vtk/-/issues/123"


@pytest.fixture(scope="module")
def md():
    return build_parser()


@pytest.mark.parametrize("url", [BLOB, UPLOAD, ITEM])
def test_a_url_in_a_fenced_block_is_untouched(md, url):
    html = to_html(f"Run this:\n\n```sh\ncurl {url}\n```\n", CORPUS, md)
    assert url in html


@pytest.mark.parametrize("url", [BLOB, UPLOAD, ITEM])
def test_a_url_in_an_inline_span_is_untouched(md, url):
    html = to_html(f"as in `curl {url}` above", CORPUS, md)
    assert url in html


@pytest.mark.parametrize("url", [BLOB, UPLOAD, ITEM])
def test_the_same_url_in_prose_is_still_rewritten(md, url):
    """The protection must not turn the rewrite off everywhere."""
    html = to_html(f"see {url} for this", CORPUS, md)
    assert f'href="{url}"' not in html


def test_an_indented_code_block_is_untouched(md):
    html = to_html(f"Run:\n\n    curl {BLOB}\n", CORPUS, md)
    assert BLOB in html


@pytest.mark.parametrize("url", [BLOB, UPLOAD, ITEM])
def test_the_markdown_twin_leaves_a_fenced_block_alone(url):
    text = absolutize(f"Run:\n\n```sh\ncurl {url}\n```\n", HELD, CORPUS)
    assert url in text


@pytest.mark.parametrize("url", [BLOB, UPLOAD, ITEM])
def test_the_markdown_twin_leaves_an_inline_span_alone(url):
    assert url in absolutize(f"as in `curl {url}` above", HELD, CORPUS)


@pytest.mark.parametrize("url", [BLOB, UPLOAD, ITEM])
def test_the_markdown_twin_still_rewrites_prose(url):
    assert url not in absolutize(f"see {url} for this", HELD, CORPUS)


def test_an_unclosed_fence_does_not_swallow_the_rest_of_the_body():
    """A body that opens a fence and never closes it is common in bug reports.
    Everything after it is code as far as the parser is concerned, so the only
    requirement is that nothing is lost."""
    body = f"Start\n\n```\ncurl {BLOB}\n\nand then\n"
    out = absolutize(body, HELD, CORPUS)
    assert "and then" in out
    assert BLOB in out


def test_a_tilde_fence_counts_as_code():
    body = f"~~~\ncurl {BLOB}\n~~~\n"
    assert BLOB in absolutize(body, HELD, CORPUS)
