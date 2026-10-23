"""Pasted gitlab.kitware.com URLs resolve inside the archive.

The sigil forms (`#123`, `!456`) are covered in test_render.py; these are the
full URLs people paste into a description, which markdown-it linkifies into an
`<a>` that reference resolution deliberately will not touch.
"""

from __future__ import annotations

import pytest

from builder.render import Corpus, build_parser, to_html

CORPUS = Corpus(issues=frozenset({"20218"}), merge_requests=frozenset({"13729"}))


@pytest.fixture
def md():
    return build_parser()


def test_issue_url_becomes_a_local_link(md):
    html = to_html("see https://gitlab.kitware.com/vtk/vtk/-/issues/20218", CORPUS, md)
    assert 'href="/vtk/vtk/-/issues/20218"' in html
    assert "gitlab.kitware.com/vtk/vtk/-/issues/20218" not in html


def test_work_item_url_keeps_its_spelling(md):
    html = to_html("https://gitlab.kitware.com/vtk/vtk/-/work_items/20218", CORPUS, md)
    assert 'href="/vtk/vtk/-/work_items/20218"' in html


def test_merge_request_url_becomes_a_local_link(md):
    html = to_html("https://gitlab.kitware.com/vtk/vtk/-/merge_requests/13729", CORPUS, md)
    assert 'href="/vtk/vtk/-/merge_requests/13729"' in html


def test_a_bare_url_is_shown_as_its_sigil(md):
    html = to_html("https://gitlab.kitware.com/vtk/vtk/-/merge_requests/13729", CORPUS, md)
    assert ">!13729</a>" in html


def test_custom_link_text_is_preserved(md):
    html = to_html("[the fix](https://gitlab.kitware.com/vtk/vtk/-/issues/20218)", CORPUS, md)
    assert ">the fix</a>" in html
    assert 'href="/vtk/vtk/-/issues/20218"' in html


def test_a_note_fragment_survives(md):
    body = "https://gitlab.kitware.com/vtk/vtk/-/issues/20218#note_1234567"
    html = to_html(body, CORPUS, md)
    assert 'href="/vtk/vtk/-/issues/20218#note_1234567"' in html


def test_a_diffs_suffix_lands_on_the_archived_page(md):
    html = to_html("https://gitlab.kitware.com/vtk/vtk/-/merge_requests/13729/diffs", CORPUS, md)
    assert 'href="/vtk/vtk/-/merge_requests/13729"' in html


def test_an_iid_the_archive_lacks_stays_external(md):
    html = to_html("https://gitlab.kitware.com/vtk/vtk/-/issues/999999", CORPUS, md)
    assert "https://gitlab.kitware.com/vtk/vtk/-/issues/999999" in html
    assert 'href="/vtk/vtk/-/issues/999999"' not in html


def test_another_project_stays_external(md):
    html = to_html("https://gitlab.kitware.com/paraview/paraview/-/issues/20218", CORPUS, md)
    assert "gitlab.kitware.com/paraview/paraview" in html
    assert 'href="/vtk/vtk/' not in html


def test_a_commit_url_moves_to_the_mirror_only_when_it_is_there(md):
    sha = "a" * 40
    url = f"https://gitlab.kitware.com/vtk/vtk/-/commit/{sha}"
    held = Corpus(CORPUS.issues, CORPUS.merge_requests, mirror_commits=frozenset({sha}))
    assert f"https://github.com/Kitware/VTK/commit/{sha}" in to_html(url, held, md)


def test_a_commit_url_the_mirror_lacks_stays_on_gitlab(md):
    url = "https://gitlab.kitware.com/vtk/vtk/-/commit/" + "a" * 40
    html = to_html(url, CORPUS, md)
    assert "gitlab.kitware.com/vtk/vtk/-/commit/" in html
    assert "github.com" not in html


def test_a_short_sha_link_is_left_where_it_was(md):
    # Seven characters cannot be checked against the table, so moving it would
    # be a guess that 404s two times in three.
    url = "https://gitlab.kitware.com/vtk/vtk/-/commit/abc1234"
    assert "gitlab.kitware.com" in to_html(url, CORPUS, md)


def test_a_blob_url_keeps_its_ref_and_path(md):
    url = "https://gitlab.kitware.com/vtk/vtk/-/blob/master/Common/Core/vtkObject.cxx"
    html = to_html(url, CORPUS, md)
    assert "https://github.com/Kitware/VTK/blob/master/Common/Core/vtkObject.cxx" in html


def test_a_raw_url_goes_to_raw_githubusercontent(md):
    url = "https://gitlab.kitware.com/vtk/vtk/-/raw/master/README.md"
    html = to_html(url, CORPUS, md)
    assert "https://raw.githubusercontent.com/Kitware/VTK/master/README.md" in html


def test_another_project_keeps_its_own_code_links(md):
    url = "https://gitlab.kitware.com/paraview/paraview/-/blob/master/x.cxx"
    html = to_html(url, CORPUS, md)
    assert "gitlab.kitware.com/paraview/paraview" in html
    assert "github.com/Kitware/VTK" not in html


def test_a_url_inside_a_code_fence_is_left_alone(md):
    html = to_html("```\nhttps://gitlab.kitware.com/vtk/vtk/-/issues/20218\n```", CORPUS, md)
    assert "gitlab.kitware.com/vtk/vtk/-/issues/20218" in html
    assert "<a href" not in html


def test_an_outside_host_is_untouched(md):
    html = to_html("https://example.com/vtk/vtk/-/issues/20218", CORPUS, md)
    assert "example.com" in html
    assert 'href="/vtk/vtk/-/issues/20218"' not in html
