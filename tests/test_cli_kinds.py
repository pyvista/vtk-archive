"""Naming no corpus on the command line.

`skeleton` and `notes` take zero or more kinds and mean "all of them" when
given none, which is the form the scheduled workflow uses. Before 3.14,
argparse validates a list default against `choices=` and rejects its own
default, so that form exited 2 on every supported Python but the newest, and
the local interpreter was the one version where it worked.
"""

from __future__ import annotations

import argparse

import pytest

from crawler.cli import _kinds, main
from crawler.crawl import KINDS


@pytest.fixture
def parser():
    return argparse.ArgumentParser(prog="vtk-archive")


def test_naming_nothing_means_every_corpus(parser):
    assert _kinds(parser, []) == sorted(KINDS)


def test_naming_one_takes_only_that_one(parser):
    assert _kinds(parser, ["issues"]) == ["issues"]


def test_a_misspelt_kind_is_refused(parser, capsys):
    """Dropping `choices=` must not drop the error it gave."""
    with pytest.raises(SystemExit) as exc:
        _kinds(parser, ["issues", "merge-requests"])
    assert exc.value.code == 2
    assert "merge-requests" in capsys.readouterr().err


def test_the_parser_accepts_the_bare_subcommand(monkeypatch):
    """The whole bug: `skeleton` with no kind named must reach the crawl."""
    seen = []
    monkeypatch.setattr("crawler.cli.skeleton", lambda kind, **kw: seen.append(kind) or {})
    monkeypatch.setattr("crawler.cli.store.load_state", dict)
    assert main(["skeleton", "--limit", "0"]) == 0
    assert seen == sorted(KINDS)


def test_the_bare_notes_subcommand_too(monkeypatch):
    seen = []
    monkeypatch.setattr("crawler.cli.notes", lambda kind, **kw: seen.append(kind) or {})
    assert main(["notes", "--limit", "0"]) == 0
    assert seen == sorted(KINDS)
