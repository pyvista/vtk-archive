"""The archive promises it does not republish contact addresses.

A scan over four key names in two of six data files reported the corpus clean
while one address was published as an issue reporter's display name.
"""

from __future__ import annotations

from crawler.scrub import depersonalize, find_emails, scrub


def test_a_profile_email_field_is_dropped():
    rec = {"author": {"name": "A", "public_email": "a@example.com"}}
    assert "public_email" not in scrub(rec)["author"]
    assert find_emails(scrub(rec)) == []


def test_an_address_used_as_a_display_name_is_reduced():
    """Mantis used the address as the name, so dropping the field would lose
    the attribution. The local part keeps it without publishing the address."""
    rec = {"reporter": "mark@scandy.co"}
    assert scrub(rec)["reporter"] == "mark"


def test_the_scan_sees_an_address_in_a_value_not_just_a_key_name():
    assert find_emails({"reporter": "mark@scandy.co"})
    assert find_emails({"assigned_to": "someone@example.org"})
    assert find_emails({"notes": [{"author": "x@y.co"}]})


def test_a_plain_name_is_untouched():
    for name in ("Berk Geveci", "Kitware Robot", "", "no-at-sign"):
        assert depersonalize(name) == name
    assert find_emails({"reporter": "Berk Geveci"}) == []


def test_a_note_body_keeps_its_address():
    """An address a person typed into a report is part of the record. Editing
    it would falsify the thing being archived."""
    rec = {"notes": [{"author": "Berk", "body": "mail me at berk@example.com"}]}
    cleaned = scrub(rec)
    assert "berk@example.com" in cleaned["notes"][0]["body"]
    assert find_emails(cleaned) == []


def test_nested_records_are_reached():
    rec = {"a": [{"b": {"author": "deep@example.com"}}]}
    assert find_emails(rec)
    assert find_emails(scrub(rec)) == []
