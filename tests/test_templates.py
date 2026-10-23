"""The rendered page, not the renderer.

`to_html` returning correct HTML proves nothing about what reaches the page:
the templates autoescape, so a body that is a plain `str` lands on screen as
literal `<p>` text. These drive the real Jinja environment the build uses.
"""

from __future__ import annotations

import pytest

from builder.build import _env
from builder.render import Corpus, build_parser, to_html

CORPUS = Corpus(issues=frozenset({"123"}), merge_requests=frozenset())

# `sha` is present so the sidebar's head-commit branch is actually reached; an
# item without it skipped that branch and hid a real build failure.
ITEM = {
    "iid": "1",
    "title": "t",
    "state": "opened",
    "created_at": "2026-01-01",
    "labels": [],
    "sha": "a" * 40,
}


@pytest.fixture
def md():
    return build_parser()


def _common(kind: str = "issues") -> dict:
    return {
        "description": "d",
        "canonical": f"https://gitlab.kitware.com/vtk/vtk/-/{kind}/1",
        "generated": "2026-01-01",
        "kind": kind,
        "sigil": "#" if kind == "issues" else "!",
        "item": ITEM,
        "author": "Someone",
        "md_url": f"/vtk/vtk/-/{kind}/1.md",
        "code_sha": "a" * 40,
        "mantis": None,
    }


def _page(body_markdown: str, md, *, timeline=None, **over) -> str:
    return (
        _env()
        .get_template("item.html.j2")
        .render(
            page_title="t",
            body=to_html(body_markdown, CORPUS, md),
            timeline=timeline or [],
            comment_count=0,
            system_count=0,
            diff_count=0,
            **{**_common(), **over},
        )
    )


def changes_page(diff: dict, **over) -> str:
    """The files-changed page, which is where a diff renders."""
    return (
        _env()
        .get_template("changes.html.j2")
        .render(
            page_title="Files changed",
            diff=diff,
            diff_count=diff["file_count"],
            review_count=0,
            **{**_common("merge_requests"), **over},
        )
    )


def test_markdown_reaches_the_page_as_markup(md):
    page = _page("A `vtkThing` call and a #123 reference.", md)
    assert "<code>vtkThing</code>" in page
    assert "&lt;code&gt;" not in page
    assert '<a href="/vtk/vtk/-/issues/123"' in page


def test_fenced_code_is_a_pre_block_on_the_page(md):
    page = _page("```c++\nint x = 0;\n```", md)
    assert "<pre>" in page
    assert "&lt;pre&gt;" not in page


def test_author_html_is_still_inert_on_the_page(md):
    page = _page("<script>alert(1)</script>", md)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


MANTIS = {
    "url": "https://vtk.org/Bug/view.php?id=49",
    "steps_to_reproduce": "Run ctest -R Spline",
    "additional_information": "",
    "notes": [
        {
            "id": "7",
            "author": "Berk Geveci",
            "created_at": "2007-08-03 15:01",
            "body": "Fixed on the branch. *not emphasis* and _not italics_.",
        },
    ],
}


def test_recovered_mantis_discussion_renders_on_the_issue(md):
    page = _page("body", md, mantis=MANTIS)
    assert "Fixed on the branch." in page
    assert "Berk Geveci" in page
    assert 'id="mantis_7"' in page
    assert "Run ctest -R Spline" in page
    # Mantis predates markdown, so these stay literal rather than becoming <em>.
    assert "*not emphasis*" in page
    assert "<em>" not in page


def test_an_issue_without_mantis_notes_grows_no_section(md):
    assert "Mantis" not in _page("body", md)
    assert "Mantis" not in _page("body", md, mantis={**MANTIS, "notes": []})
