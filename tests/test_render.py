"""Reference resolution must not touch verbatim regions.

Every case here was written to fail against a naive implementation that runs
the reference regexes over the whole document. See docs/SOURCES.md for why we
render markdown ourselves instead of taking GitLab's HTML.
"""

from __future__ import annotations

import pytest

from builder.render import Corpus, build_parser, resolve_references, to_html

HELD_UPLOAD = "0123456789abcdef0123456789abcdef"
MISSING_UPLOAD = "f" * 32
CORPUS = Corpus(
    issues=frozenset({"123", "20218"}),
    merge_requests=frozenset({"456"}),
    uploads=frozenset({HELD_UPLOAD, "a" * 32}),
    users=frozenset({"ben.boeckel"}),
)


@pytest.fixture(scope="module")
def md():
    return build_parser()


def test_prose_issue_reference_links_locally(md):
    html = to_html("See #123 for context.", CORPUS, md)
    assert '<a href="/vtk/vtk/-/issues/123"' in html
    assert ">#123<" in html


def test_prose_mr_reference_links_locally(md):
    html = to_html("Fixed by !456.", CORPUS, md)
    assert '<a href="/vtk/vtk/-/merge_requests/456"' in html


def test_reference_the_archive_lacks_is_not_linked_at_all(md):
    html = to_html("See #99999 and !99999.", CORPUS, md)
    assert "<a " not in html
    assert "#99999" in html
    assert "!99999" in html


def test_a_numbered_point_is_left_as_prose(md):
    html = to_html("Two problems: #1 the crash, #2 the leak.", CORPUS, md)
    assert "<a " not in html
    assert "#1" in html


def test_fenced_code_block_is_untouched(md):
    html = to_html("```\nint x = 1; // see #123 and !456 and @bob\n```", CORPUS, md)
    assert "<a " not in html
    assert "#123" in html and "!456" in html and "@bob" in html


def test_inline_code_is_untouched(md):
    html = to_html("Use `#123` literally, but #123 links.", CORPUS, md)
    assert html.count("<a ") == 1
    assert "<code>#123</code>" in html


def test_indented_code_block_is_untouched(md):
    html = to_html("text\n\n    see #123 here\n", CORPUS, md)
    assert "<a " not in html


def test_existing_link_is_not_rewritten_inside():
    html = '<p><a href="https://example.com/x#123">label #123</a> and #123</p>'
    out = resolve_references(html, CORPUS)
    assert out.count("<a ") == 2
    assert 'href="https://example.com/x#123"' in out


def test_href_attribute_of_a_bare_tag_is_not_rewritten():
    html = '<p><img src="/uploads/aa/b.png" alt="#123"> and #123</p>'
    out = resolve_references(html, CORPUS)
    assert 'alt="#123"' in out
    assert out.count("<a ") == 1


def test_css_color_is_not_an_issue_reference(md):
    html = to_html("The background is #123456 by default.", CORPUS, md)
    assert "<a " not in html


def test_short_hex_word_is_not_a_commit_sha(md):
    html = to_html("The magic value is deadbeef here.", CORPUS, md)
    assert "<a " not in html


def test_a_sha_the_mirror_holds_links_to_the_mirror(md):
    sha = "a" * 40
    corpus = Corpus(CORPUS.issues, CORPUS.merge_requests, mirror_commits=frozenset({sha}))
    html = to_html(f"Broken by {sha} upstream.", corpus, md)
    assert f"https://github.com/Kitware/VTK/commit/{sha}" in html
    assert ">aaaaaaaaaaaa<" in html


def test_a_sha_the_mirror_lacks_links_back_to_gitlab(md):
    # VTK force-pushes merge-request branches, so 55,089 of the 82,152 SHAs in
    # this corpus are not in the mirror and 404 there. GitLab still has them.
    sha = "b" * 40
    html = to_html(f"Broken by {sha} upstream.", CORPUS, md)
    assert f"https://gitlab.kitware.com/vtk/vtk/-/commit/{sha}" in html
    assert "github.com" not in html


def test_user_mention_links_to_profile(md):
    html = to_html("ping @ben.boeckel please", CORPUS, md)
    assert 'href="https://gitlab.kitware.com/ben.boeckel"' in html


def test_a_doxygen_tag_is_not_a_user_mention(md):
    """`@param` and friends are all over pasted code comments, and each one
    linked to a gitlab.kitware.com profile page that does not exist."""
    html = to_html("The docs say\n\n    @param foo the thing\n    @return nothing\n", CORPUS, md)
    assert "gitlab.kitware.com/param" not in html
    assert "gitlab.kitware.com/return" not in html


def test_a_mention_of_someone_the_corpus_never_saw_stays_text(md):
    html = to_html("ping @nobody.at.all please", CORPUS, md)
    assert "gitlab.kitware.com/nobody" not in html
    assert "@nobody.at.all" in html


def test_email_is_not_a_user_mention(md):
    html = to_html("write to someone@example.com about it", CORPUS, md)
    assert "gitlab.kitware.com/example" not in html


def test_upload_path_is_rewritten_to_the_archived_copy(md):
    body = "![shot](/uploads/0123456789abcdef0123456789abcdef/shot.png)"
    html = to_html(body, CORPUS, md)
    assert "/-/project/13/uploads/0123456789abcdef0123456789abcdef/shot.png" in html
    assert 'src="/uploads/' not in html


def test_absolute_upload_url_is_also_rewritten(md):
    body = "[file](https://gitlab.kitware.com/uploads/" + "a" * 32 + "/mesh.vtp)"
    html = to_html(body, CORPUS, md)
    assert "/-/project/13/uploads/" + "a" * 32 + "/mesh.vtp" in html
    assert "gitlab.kitware.com/uploads" not in html


def test_the_project_scoped_upload_spelling_is_rewritten_whole(md):
    """Anchoring on `/uploads/` alone left the host and `/vtk/vtk` in front of
    the new path and produced a URL that exists nowhere."""
    body = f"[f](https://gitlab.kitware.com/vtk/vtk/uploads/{HELD_UPLOAD}/x.png)"
    html = to_html(body, CORPUS, md)
    assert f"/-/project/13/uploads/{HELD_UPLOAD}/x.png" in html
    assert "gitlab.kitware.com" not in html


def test_an_upload_the_archive_does_not_hold_keeps_the_working_link(md):
    """Rewriting it unconditionally turned every un-fetched attachment into an
    archive 404, when the canonical URL still resolves."""
    body = f"[f](https://gitlab.kitware.com/uploads/{MISSING_UPLOAD}/x.png)"
    html = to_html(body, CORPUS, md)
    assert f"https://gitlab.kitware.com/uploads/{MISSING_UPLOAD}/x.png" in html
    assert "/-/project/13/uploads/" not in html


def test_raw_html_in_source_is_not_trusted(md):
    html = to_html("<script>alert(1)</script> and #123", CORPUS, md)
    assert "<script>" not in html
