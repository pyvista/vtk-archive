"""The `.md` twin is what agents read, so its structure has to parse.

llms.txt points agents at these files, and nothing rendered them until now, so
a broken table or unparseable front matter looked fine from the HTML side.
"""

from __future__ import annotations

import pytest

from builder.markdown import render
from builder.render import Corpus

yaml = pytest.importorskip("yaml")

CORPUS = Corpus(issues=frozenset({"1"}), merge_requests=frozenset({"2"}))
HELD = {"issues": frozenset({"1"}), "merge_requests": frozenset({"2"})}

CHANGE = {
    "file_count": 2,
    "added": 3,
    "removed": 1,
    "truncated": False,
    "files": [
        {"path": "Rendering/Core/vtkActor.cxx", "status": "modified", "added": 2, "removed": 1},
        {"path": "Common/New.h", "status": "added", "added": 1, "removed": 0},
    ],
}


def twin(item, **over):
    return render(
        item,
        kind=over.pop("kind", "merge_requests"),
        sigil="!",
        author="Someone",
        canonical="https://gitlab.kitware.com/vtk/vtk/-/merge_requests/2",
        held=HELD,
        corpus=CORPUS,
        **over,
    )


def front_matter(text: str):
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---\n")[1])


MR = {"iid": "2", "title": "t", "state": "opened", "created_at": "2026-01-01", "labels": []}


def test_the_file_table_is_contiguous_when_the_branch_is_not_mirrored():
    """The caveat used to land between the delimiter row and the first data
    row. A blank line there ends the table and prints raw pipes."""
    text = twin(MR, change=CHANGE, mirror_sha=None)
    lines = text.splitlines()
    start = lines.index("| --- | --- | --- | --- |")
    assert lines[start + 1].startswith("| `Rendering/Core/vtkActor.cxx`")
    assert lines[start + 2].startswith("| `Common/New.h`")


def test_the_mirror_note_is_still_present_just_above_the_table():
    text = twin(MR, change=CHANGE, mirror_sha=None)
    assert "not in the GitHub" in text
    assert text.index("not in the GitHub") < text.index("| file | status")


def test_a_merged_branch_gets_no_caveat_and_links_the_revision():
    text = twin({**MR, "state": "merged", "sha": "a" * 40}, change=CHANGE, mirror_sha="a" * 40)
    assert "not in the GitHub" not in text
    assert "a" * 40 in text


def test_mirror_membership_is_asked_not_inferred_from_state():
    """62 closed merge requests have a head commit the mirror does hold."""
    closed = {**MR, "state": "closed", "sha": "b" * 40}
    assert "not in the GitHub" not in twin(closed, change=CHANGE, mirror_sha="b" * 40)
    assert "not in the GitHub" in twin(closed, change=CHANGE, mirror_sha=None)


def test_a_truncated_diff_says_so():
    text = twin(MR, change={**CHANGE, "truncated": True}, mirror_sha=None)
    assert "caps this endpoint" in text


def test_a_backslash_title_still_parses_as_yaml():
    """`ThirdParty\\hdf5\\vtkhdf5` was a YAML parse error under double quotes,
    and another title's `\\v` decoded into a vertical tab."""
    title = r"VTK 6.3.0 ThirdParty\hdf5\vtkhdf5\src\H5.c fails"
    meta = front_matter(twin({**MR, "title": title}, change=None))
    assert meta["title"] == title

    leak = r"Memory leaks in graphics\vtkdatasetsurfacefilter.cxx"
    assert front_matter(twin({**MR, "title": leak}, change=None))["title"] == leak


def test_the_merge_request_sigil_does_not_open_a_yaml_tag():
    """`id: !2` is a YAML tag indicator, so the front matter of all 13,870
    merge-request twins failed to parse."""
    meta = front_matter(twin(MR, change=None))
    assert meta["id"] == "!2"
    assert meta["canonical"].startswith("https://")
    assert meta["html"].startswith("https://")


def test_an_issue_id_keeps_its_hash():
    text = twin({**MR, "iid": "1"}, change=None, kind="issues")
    assert front_matter(text)["id"] == "!1"


def test_a_quote_in_a_title_survives():
    title = 'it\'s "quoted" both ways'
    assert front_matter(twin({**MR, "title": title}, change=None))["title"] == title


def note(author, body, *, system=False):
    return {
        "id": 1,
        "author": author,
        "created_at": "2026-01-02T03:04:05+00:00",
        "system": system,
        "body": body,
        "raw": body,
    }


def thread(*notes):
    return {
        "system": False,
        "at": "2026-01-02T03:04:05+00:00",
        "thread": {"notes": list(notes), "diff": None, "resolved": False},
    }


def event(body):
    return {
        "system": True,
        "at": "2026-01-02T03:04:05+00:00",
        "note": note("bot", body, system=True),
    }


def test_the_comment_count_counts_only_what_a_person_wrote():
    """A thread's `notes` list carries GitLab's own events alongside the
    comments, so counting the list put "changed the description" into the
    heading and disagreed with the body right under it."""
    timeline = [thread(note("Ann", "real comment"), note("bot", "changed title", system=True))]
    text = twin({**MR, "_timeline": timeline}, change=None)
    assert "## Comments (1)" in text


def test_system_events_appear_as_a_timeline():
    """129,383 of these are on the HTML pages. The twin dropped every one, so
    an agent reading the `.md` could not tell who closed a thing or when."""
    text = twin({**MR, "_timeline": [event("added the bug label")]}, change=None)
    assert "## Timeline (1 events)" in text
    assert "added the bug label" in text


def test_an_item_with_no_events_gets_no_timeline_section():
    text = twin({**MR, "_timeline": [thread(note("Ann", "hi"))]}, change=None)
    assert "## Timeline" not in text


def test_a_system_event_is_one_line():
    """GitLab wraps some of them; a newline inside a list item ends the list."""
    text = twin({**MR, "_timeline": [event("assigned to\n@someone\nand unassigned")]}, change=None)
    line = next(x for x in text.splitlines() if x.startswith("- ") and "assigned" in x)
    assert line.endswith("and unassigned")


def test_labels_and_branches_are_quoted():
    item = {
        **MR,
        "labels": ["area: rendering", "triage: needs info"],
        "source_branch": "fix: thing",
        "target_branch": "master",
    }
    meta = front_matter(twin(item, change=None))
    assert meta["labels"] == ["area: rendering", "triage: needs info"]
    assert meta["source_branch"] == "fix: thing"
