"""One writer per corpus.

Every pass reads a whole corpus, changes some records and writes the file back.
Two passes over one corpus lose each other's records outright, and can
interleave inside the atomic replace and leave a torn file.
"""

from __future__ import annotations

import pytest

from crawler import store


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    return tmp_path


def take(*corpora: str, who: str) -> None:
    """Acquire and release, so a refusal is the only thing a test observes."""
    with store.lock(*corpora, who=who):
        pass


def test_a_second_writer_is_refused_and_told_who_has_it(data):
    with store.lock("issues", who="skeleton issues"), pytest.raises(store.Busy) as caught:
        take("issues", who="notes issues")
    assert "skeleton issues" in str(caught.value)


def test_the_lock_is_released_when_the_pass_ends(data):
    with store.lock("issues", who="first"):
        pass
    with store.lock("issues", who="second"):
        pass


def test_the_lock_is_released_when_the_pass_raises(data):
    def boom() -> None:
        raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError), store.lock("issues", who="first"):
        boom()
    with store.lock("issues", who="second"):
        pass


def test_passes_writing_different_corpora_run_together(data):
    """`diffs` writes mr_diffs.ndjson and `notes` writes merge_requests.ndjson,
    and docs/SOURCES.md documents them as a safe pair. They stay one."""
    with store.lock("mr_diffs", who="diffs"), store.lock("merge_requests", who="notes"):
        pass


def test_a_partial_acquisition_leaves_nothing_held(data):
    """`scrub` wants every corpus at once. If the second is taken, the first
    must not be left locked by the refused run."""
    with store.lock("merge_requests", who="notes merge_requests"), pytest.raises(store.Busy):
        take("issues", "merge_requests", who="scrub")
    take("issues", who="after")
