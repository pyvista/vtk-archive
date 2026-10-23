"""What an incremental build is allowed to skip.

The danger in skipping work is silence: a page that should have changed and
did not looks exactly like a page that was correctly left alone.
"""

from __future__ import annotations

import json

import pytest

from builder import fingerprint as fp

ITEM = {"iid": "1", "title": "t", "updated_at": "2026-01-01T00:00:00+00:00"}


@pytest.fixture(autouse=True)
def _clear_salt():
    fp.builder_salt.cache_clear()
    yield
    fp.builder_salt.cache_clear()


def test_the_same_inputs_give_the_same_fingerprint():
    assert fp.page(ITEM, None, None) == fp.page(ITEM, None, None)


def test_a_changed_record_changes_the_fingerprint():
    assert fp.page(ITEM, None, None) != fp.page({**ITEM, "title": "other"}, None, None)


def test_a_new_comment_changes_the_fingerprint():
    """The notes pass rewrites `notes`, so the page must re-render."""
    with_note = {**ITEM, "notes": [{"id": 1, "body": "hi"}]}
    assert fp.page(ITEM, None, None) != fp.page(with_note, None, None)


def test_a_changed_diff_changes_the_fingerprint():
    assert fp.page(ITEM, {"a": 1}, None) != fp.page(ITEM, {"a": 2}, None)


def test_recovered_mantis_notes_change_the_fingerprint():
    assert fp.page(ITEM, None, None) != fp.page(ITEM, None, {"notes": [{"id": "1"}]})


def test_the_three_slots_do_not_collide():
    """Without a separator, a value moving between slots would hash the same."""
    assert fp.page(ITEM, None, None) != fp.page(None, ITEM, None)
    assert fp.page({"a": 1}, {"b": 2}, None) != fp.page({"a": 1, "b": 2}, None, None)


def test_editing_a_template_invalidates_every_page(tmp_path, monkeypatch):
    """The reason the salt is read off disk. A version constant someone has to
    bump would serve the old markup with nothing reporting it."""
    root = tmp_path / "builder"
    (root / "templates").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "build.py").write_text("x = 1")
    tpl = root / "templates" / "item.html.j2"
    tpl.write_text("<p>{{ title }}</p>")
    monkeypatch.setattr(fp, "ROOT", root)

    before = fp.page(ITEM)
    tpl.write_text("<h1>{{ title }}</h1>")
    fp.builder_salt.cache_clear()
    assert fp.page(ITEM) != before


def test_editing_the_stylesheet_invalidates_every_page(tmp_path, monkeypatch):
    root = tmp_path / "builder"
    (root / "templates").mkdir(parents=True)
    (root / "assets").mkdir()
    css = root / "assets" / "style.css"
    css.write_text("body { color: red }")
    monkeypatch.setattr(fp, "ROOT", root)

    before = fp.page(ITEM)
    css.write_text("body { color: blue }")
    fp.builder_salt.cache_clear()
    assert fp.page(ITEM) != before


def test_renaming_a_template_invalidates(tmp_path, monkeypatch):
    root = tmp_path / "builder"
    (root / "templates").mkdir(parents=True)
    (root / "assets").mkdir()
    old = root / "templates" / "a.j2"
    old.write_text("same bytes")
    monkeypatch.setattr(fp, "ROOT", root)

    before = fp.page(ITEM)
    old.rename(root / "templates" / "b.j2")
    fp.builder_salt.cache_clear()
    assert fp.page(ITEM) != before


def test_a_corrupt_fingerprint_file_rebuilds_everything(tmp_path):
    """Unreadable bookkeeping must mean 'render it', never 'skip it'."""
    path = tmp_path / "_fingerprints.json"
    path.write_text("{not json")
    assert fp.load(path) == {}
    path.write_text("[1, 2, 3]")
    assert fp.load(path) == {}
    assert fp.load(tmp_path / "missing.json") == {}


def test_a_round_trip_survives(tmp_path):
    path = tmp_path / "_fingerprints.json"
    prints = {"issues/1": {"fp": "abc", "comments": 3, "diff_files": 0}}
    fp.save(path, prints)
    assert fp.load(path) == prints
    assert json.loads(path.read_text())  # valid JSON on disk
