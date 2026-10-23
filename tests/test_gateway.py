"""The `?uri=` gateway.

It exists to accept a pasted gitlab.kitware.com URL and land on the archived
copy, which makes "follow whatever is in this query value" its whole job and an
open redirect the obvious way to get it wrong. Every case here redirected off
the site against one implementation or another.
"""

from __future__ import annotations

import pytest

from builder.preview import _gateway_target as target

FOLLOWED = [
    ("uri=https://gitlab.kitware.com/vtk/vtk/-/issues/20218", "/vtk/vtk/-/issues/20218"),
    (
        "uri=http://gitlab.kitware.com/vtk/vtk/-/merge_requests/13000",
        "/vtk/vtk/-/merge_requests/13000",
    ),
    (
        "uri=https://gitlab.kitware.com/vtk/vtk/-/issues/1#note_5",
        "/vtk/vtk/-/issues/1#note_5",
    ),
]

REFUSED = [
    # Protocol-relative: `//host/path` leaves the site even as a Location value.
    "uri=https://gitlab.kitware.com//evil.example.com/x",
    "uri=//evil.example.com/x",
    # The canonical host as a prefix of someone else's.
    "uri=https://gitlab.kitware.com.evil.example.com/vtk/vtk/-/issues/1",
    # The canonical host as userinfo, which is not the host at all.
    "uri=http://gitlab.kitware.com@evil.example.com/x",
    # The marker anywhere in the string, which a substring match accepted.
    "uri=https://evil.test/?x=gitlab.kitware.com//evil.test/p",
    "uri=https://evil.example.com/vtk/vtk/-/issues/1",
    # A backslash, which some clients normalise to a forward slash.
    r"uri=/\evil.example.com/x",
]


@pytest.mark.parametrize(("query", "expected"), FOLLOWED)
def test_a_canonical_url_lands_on_the_archived_page(query, expected):
    assert target(query) == expected


@pytest.mark.parametrize("query", REFUSED)
def test_nothing_that_would_leave_the_site_is_followed(query):
    assert target(query) is None


def test_an_encoded_value_is_decoded_before_it_is_judged():
    """Percent-encoding is how the protocol-relative case arrives in practice,
    so the check has to run on the decoded value."""
    assert target("uri=https%3A%2F%2Fgitlab.kitware.com%2F%2Fevil.example.com%2Fx") is None
    assert (
        target("uri=https%3A%2F%2Fgitlab.kitware.com%2Fvtk%2Fvtk%2F-%2Fissues%2F7")
        == "/vtk/vtk/-/issues/7"
    )


def test_a_query_with_no_uri_is_not_a_redirect():
    assert target("") is None
    assert target("q=something") is None


def test_the_value_is_read_from_its_own_parameter():
    """`uri=` matched as a substring anywhere let another parameter supply it."""
    assert target("ref=uri=https://evil.example.com/x") is None
