"""Which Mantis ids a run goes back for.

The 2015 migration carried each report but not the discussion under it, and the
instance serving that discussion is retired, so a page we fail to fetch is a
page we may not get another chance at. A failure that nothing retries is the
one way to lose it for good.
"""

from __future__ import annotations

from crawler.mantis import pending

TARGETS = {"49": "49"}


def test_an_error_stub_is_retried_on_the_next_run():
    """One timeout used to be permanent: the stub it wrote satisfied the
    `m not in held` test and no later run looked at that id again."""
    held = {"49": {"mantis_id": "49", "error": "ReadTimeout: ...", "notes": []}}
    assert pending(TARGETS, held) == ["49"]


def test_a_page_with_content_is_not_refetched():
    held = {"49": {"mantis_id": "49", "summary": "s", "notes": [{"id": "1"}]}}
    assert pending(TARGETS, held) == []


def test_an_empty_id_is_an_answer_and_stands():
    """`empty` means the id really holds nothing, which many of them do. Only
    `--refetch` asks again."""
    held = {"49": {"mantis_id": "49", "empty": True, "notes": []}}
    assert pending(TARGETS, held) == []
    assert pending(TARGETS, held, refetch=True) == ["49"]


def test_an_id_never_seen_is_fetched():
    assert pending(TARGETS, {}) == ["49"]


def test_ids_come_back_in_numeric_order():
    """Keyed by string, so sorting as text would run 1000 before 9 and make an
    interrupted run resume somewhere arbitrary."""
    targets = {"100": "100", "9": "9", "1000": "1000"}
    assert pending(targets, {}) == ["9", "100", "1000"]
