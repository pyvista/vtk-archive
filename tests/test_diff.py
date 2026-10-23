"""Unified-diff parsing, the two size caps, and what reaches the page."""

from __future__ import annotations

import pytest

from builder import diff as diffmod
from builder.render import build_parser
from tests.test_templates import changes_page

SAMPLE = """@@ -10,4 +10,5 @@ void vtkThing::Execute()
 context one
-  int old = 1;
+  int fresh = 1;
+  int added = 2;
 context two
"""


@pytest.fixture
def md():
    return build_parser()


def _record(**over):
    entry = {
        "new_path": "Filters/Core/vtkThing.cxx",
        "old_path": "Filters/Core/vtkThing.cxx",
        "diff": SAMPLE,
    }
    entry.update(over)
    return {"iid": "1", "truncated": False, "files": [entry]}


def test_line_numbers_advance_per_side():
    lines = diffmod.parse_hunks(SAMPLE)[0]["lines"]
    assert [(line["kind"], line["old"], line["new"]) for line in lines] == [
        ("ctx", 10, 10),
        ("del", 11, None),
        ("add", None, 11),
        ("add", None, 12),
        ("ctx", 12, 13),
    ]


def test_counts_ignore_the_file_header():
    text = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
    assert diffmod.counts(text) == (1, 1)


def test_no_newline_marker_is_its_own_kind():
    hunks = diffmod.parse_hunks("@@ -1 +1 @@\n-a\n\\ No newline at end of file\n")
    assert hunks[0]["lines"][-1]["kind"] == "meta"


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ({"new_file": True}, "added"),
        ({"deleted_file": True}, "deleted"),
        ({"renamed_file": True}, "renamed"),
        ({}, "modified"),
    ],
)
def test_status_is_read_from_the_entry(flags, expected):
    assert diffmod.prepare(_record(**flags))["files"][0]["status"] == expected


def test_totals_are_summed_across_files():
    rec = {
        "iid": "1",
        "files": [{"new_path": "a", "diff": SAMPLE}, {"new_path": "b", "diff": SAMPLE}],
    }
    out = diffmod.prepare(rec)
    assert (out["file_count"], out["added"], out["removed"]) == (2, 4, 2)


def test_a_file_over_the_byte_cap_is_not_parsed():
    big = "@@ -1 +1 @@\n" + "+x\n" * (diffmod.MAX_FILE_BYTES // 3 + 10)
    out = diffmod.prepare(_record(diff=big))["files"][0]
    assert out["omitted"] == "size"
    assert out["hunks"] == []
    assert out["added"] > 0  # the tally is still honest


def test_files_past_the_file_cap_are_not_parsed():
    many = {"iid": "1", "files": [{"new_path": f"f{i}", "diff": SAMPLE} for i in range(305)]}
    out = diffmod.prepare(many)
    assert out["files"][diffmod.MAX_FILES_RENDERED - 1]["omitted"] is None
    assert out["files"][diffmod.MAX_FILES_RENDERED]["omitted"] == "file-cap"
    assert out["omitted_count"] == 305 - diffmod.MAX_FILES_RENDERED


def test_an_empty_record_renders_no_section():
    assert diffmod.prepare({"iid": "1", "files": []}) is None
    assert diffmod.prepare(None) is None


def test_a_binary_file_says_so_rather_than_rendering_empty(md):
    page = changes_page(diffmod.prepare(_record(diff="")))
    assert "No textual diff was recorded" in page


def test_the_page_draws_the_diff_rows(md):
    page = changes_page(diffmod.prepare(_record()))
    assert '<tr class="l-add">' in page
    assert '<tr class="l-del">' in page
    assert "int fresh = 1;" in page
    assert "1 changed file" in page


def test_diff_source_is_escaped_on_the_page(md):
    rec = _record(diff="@@ -1 +1 @@\n+<script>alert(1)</script>\n")
    page = changes_page(diffmod.prepare(rec))
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_a_merged_head_links_to_that_exact_revision(md):
    page = changes_page(diffmod.prepare(_record()))
    assert "https://github.com/Kitware/VTK/blob/" + "a" * 40 in page
    assert "source (master)" not in page


def test_an_unmerged_head_falls_back_to_master_and_says_so(md):
    # An open or closed branch was never pushed to the mirror, so its head SHA
    # 404s there. Verified against 6 real MRs; see docs/SOURCES.md.
    page = changes_page(diffmod.prepare(_record()), code_sha=None)
    assert "https://github.com/Kitware/VTK/blob/master/" in page
    assert "source (master)" in page
    assert "https://github.com/Kitware/VTK/blob/" + "a" * 40 not in page
    assert "not in the public mirror" in page  # the sidebar says so too


def test_leading_indentation_survives(md):
    rec = _record(diff="@@ -1 +1 @@\n+    indented\n")
    page = changes_page(diffmod.prepare(rec))
    assert "&nbsp;&nbsp;&nbsp;&nbsp;indented" in page
