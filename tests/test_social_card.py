"""The Open Graph card.

A link to this archive in a chat or a post is often the first thing anyone sees
of it, and the failure mode is silent: a scraper that cannot resolve the image
just shows nothing, and no page here would look broken.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from builder.markdown import SITE

ASSETS = Path(__file__).resolve().parent.parent / "builder" / "assets"
BASE = (
    Path(__file__).resolve().parent.parent / "builder" / "templates" / "base.html.j2"
).read_text(encoding="utf-8")


def test_the_image_the_template_names_is_actually_shipped():
    assert (ASSETS / "og.png").exists()


def test_it_is_a_png_of_the_size_the_tags_claim():
    """Scrapers crop to the declared box, so a wrong number crops the card."""
    data = (ASSETS / "og.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    assert (width, height) == (1200, 630)
    assert f'content="{width}"' in BASE
    assert f'content="{height}"' in BASE


def test_the_image_url_is_absolute():
    """A relative og:image resolves against nothing and the card renders bare."""
    assert '<meta property="og:image" content="{{ site }}/assets/og.png">' in BASE
    assert SITE.startswith("https://")


@pytest.mark.parametrize(
    "tag",
    ['property="og:title"', 'property="og:type"', 'name="twitter:card"'],
)
def test_the_tags_a_scraper_needs_are_present(tag):
    assert tag in BASE


def test_the_card_is_small_enough_to_be_fetched():
    """X drops an image over 5 MB; this one should be nowhere near it."""
    assert (ASSETS / "og.png").stat().st_size < 1_000_000
