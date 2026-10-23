"""Parsing VTK's retired Mantis tracker.

Driven by a real saved page rather than hand-built HTML, because the thing
most likely to break here is Mantis's markup, and a fixture I wrote myself
would only prove the parser agrees with my idea of it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crawler.mantis import parse

FIXTURE = Path(__file__).parent / "fixtures" / "mantis-49.html"


@pytest.fixture
def page():
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def rec(page):
    return parse(page, 49)


def test_an_empty_id_parses_to_nothing():
    assert parse("<html><body></body></html>", 3) is None


def test_the_header_row_fields_are_read(rec):
    assert rec["date_submitted"] == "2003-07-01 11:13"
    assert rec["last_update"] == "2007-07-28 08:59"
    assert rec["category"] == "(No Category)"


def test_the_label_value_fields_are_read(rec):
    assert rec["reporter"] == "Brad King"
    assert rec["assigned_to"] == "Amy Galbraith"
    assert rec["status"] == "closed"
    assert rec["resolution"] == "no change required"
    assert rec["severity"] == "minor"


def test_the_id_prefix_is_stripped_from_the_summary(rec):
    assert rec["summary"] == "Goodsite"


def test_the_description_keeps_its_line_structure(rec):
    assert "RTLD_GLOBAL" in rec["description"]
    assert "sys.setdlopenflags" in rec["description"]
    assert "\n" in rec["description"]


def test_every_note_is_recovered_with_author_and_date(rec):
    # The whole point of this crawl: GitLab's import dropped these.
    assert len(rec["notes"]) == 2
    first = rec["notes"][0]
    assert first["id"] == "120"
    assert first["author"] == "Brad King"
    assert first["created_at"] == "2003-09-23 16:30"
    assert "fixed in the CVS version" in first["body"]


def test_a_note_whose_author_was_stripped_still_parses(rec):
    # Note 8228 is 2007 link spam with no author left on the page. It is part
    # of the record, so it is kept rather than filtered.
    second = rec["notes"][1]
    assert second["id"] == "8228"
    assert second["author"] == ""
    assert second["created_at"] == "2007-07-28 08:59"


def test_mantis_table_markup_does_not_survive_into_any_field(rec):
    for key, value in rec.items():
        if isinstance(value, str):
            assert "<td" not in value and "<span" not in value and "<tr" not in value, key
    for note in rec["notes"]:
        assert "<td" not in note["body"] and "<span" not in note["body"]


def test_a_tag_the_author_typed_is_kept_as_text(rec):
    # Note 8228's body had `&lt;a href=` in the source: the spammer typed those
    # characters, so decoding them is restoring the record, not leaking markup.
    # Escaping on render is what keeps it inert; see builder/render.py.
    assert "<a href=" in rec["notes"][1]["body"]
