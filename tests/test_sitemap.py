"""The sitemap.

An archive that exists to absorb the traffic gitlab.kitware.com is carrying has
to be findable first, and ~86,000 files is far too many to list: only the
canonical page of each item belongs here.
"""

from __future__ import annotations

from xml.etree import ElementTree

from builder.build import _sitemap
from builder.markdown import SITE

NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
CORPORA = {
    "issues": {"1": {"updated_at": "2026-01-02T03:04:05-05:00"}},
    "merge_requests": {"2": {"updated_at": "2026-02-03T04:05:06-05:00"}},
}
LABELS = {"bug": {"name": "bug", "rows": [{}, {}]}}


def locs(corpora=CORPORA, labels=LABELS):
    root = ElementTree.fromstring(_sitemap(corpora, labels))
    return [e.findtext(f"{NS}loc") for e in root.findall(f"{NS}url")]


def test_it_parses_as_xml_and_lists_every_item():
    found = locs()
    assert f"{SITE}/vtk/vtk/-/issues/1" in found
    assert f"{SITE}/vtk/vtk/-/merge_requests/2" in found


def test_the_duplicate_spellings_are_left_out():
    """`work_items`, the `.md` twin, the diffs page and `page/1` are all the
    same content under another path. Listing them asks to be deduplicated."""
    found = " ".join(locs())
    assert "work_items" not in found
    assert ".md" not in found
    assert "/diffs" not in found
    assert "/page/1" not in found


def test_a_title_cannot_break_the_xml():
    corpora = {"issues": {"1&2": {"updated_at": ""}}, "merge_requests": {}}
    assert f"{SITE}/vtk/vtk/-/issues/1&amp;2" in _sitemap(corpora, {})


def test_lastmod_is_a_date_not_a_timestamp():
    """The sitemap schema takes W3C datetime; a date is the honest precision
    for a page whose only change may have been a label."""
    root = ElementTree.fromstring(_sitemap(CORPORA, LABELS))
    stamps = [e.findtext(f"{NS}lastmod") for e in root.findall(f"{NS}url")]
    assert "2026-01-02" in stamps
    assert all(s is None or len(s) == 10 for s in stamps)


def test_a_label_with_more_than_one_page_lists_the_rest():
    labels = {"bug": {"name": "bug", "rows": [{}] * 250}}
    found = locs(labels=labels)
    assert f"{SITE}/vtk/vtk/-/labels/bug" in found
    assert f"{SITE}/vtk/vtk/-/labels/bug/page/3" in found
    assert f"{SITE}/vtk/vtk/-/labels/bug/page/4" not in found
