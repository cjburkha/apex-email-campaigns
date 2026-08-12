"""
Tests for the pure helpers in send.py: phone normalization, UTM tagging,
HMAC tokens for unsubscribe / open-pixel / click tracking, SES tag sanitizing,
and the HTML → plain-text fallback renderer.

These are the functions that decide whether a send is attributable, suppressible,
and deliverable — worth pinning down.
"""
import re

import pytest

from send import (
    _add_utm,
    _add_utm_text,
    _click_url,
    _html_to_text,
    _make_pixel_token,
    _make_unsubscribe_token,
    _normalize_phone,
    _pixel_html,
    _unsubscribe_url,
)

UTM = {"utm_source": "apex", "utm_medium": "email", "utm_campaign": "spring"}


# ── phone normalization ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("4145551960",       "+14145551960"),   # bare 10-digit
    ("(414) 555-1960",   "+14145551960"),   # formatted
    ("414.555.1960",     "+14145551960"),
    ("14145551960",      "+14145551960"),   # 11-digit with country code
    ("+14145551960",     "+14145551960"),   # already E.164 — passed through
    ("+442071838750",    "+442071838750"),  # non-US E.164 left alone
    ("",                 ""),
    (None,               ""),
])
def test_normalize_phone(raw, expected):
    assert _normalize_phone(raw) == expected


def test_normalize_phone_leaves_unrecognized_lengths_alone():
    """Too-short input is not silently coerced into a valid-looking number."""
    assert _normalize_phone("555-1960") == "5551960"


# ── UTM tagging (HTML) ───────────────────────────────────────────────────────

def test_add_utm_tags_every_href():
    html = '<a href="https://example.com/a">A</a><a href="https://example.com/b">B</a>'
    out = _add_utm(html, UTM)
    assert out.count("utm_source=apex") == 2


def test_add_utm_preserves_existing_query_params():
    out = _add_utm('<a href="https://example.com/p?ref=xyz">x</a>', UTM)
    assert "ref=xyz" in out
    assert "utm_campaign=spring" in out


def test_add_utm_overwrites_conflicting_utm_value():
    out = _add_utm('<a href="https://example.com/p?utm_source=old">x</a>', UTM)
    assert "utm_source=apex" in out
    assert "utm_source=old" not in out


def test_add_utm_skips_shortlink_slugs():
    """Slug paths are tagged server-side by the redirect; tagging here would
    double-count attribution."""
    html = '<a href="https://example.test/spring2026-em-1">x</a>'
    assert _add_utm(html, UTM) == html


def test_add_utm_ignores_non_href_urls():
    html = '<img src="https://example.com/pixel.gif" />'
    assert _add_utm(html, UTM) == html


# ── UTM tagging (plain text / SMS) ───────────────────────────────────────────

def test_add_utm_text_tags_bare_url():
    assert "utm_medium=email" in _add_utm_text("visit https://example.com/x now", UTM)


def test_add_utm_text_keeps_trailing_punctuation_outside_the_url():
    out = _add_utm_text("see https://example.com/x.", UTM)
    assert out.endswith(".")
    assert ".?" not in out and "x.?" not in out


def test_add_utm_text_skips_shortlink_slugs():
    text = "tap https://example.test/spring2026-em-1"
    assert _add_utm_text(text, UTM) == text


# ── HMAC tokens ──────────────────────────────────────────────────────────────

def test_unsubscribe_token_is_deterministic_and_sha256_shaped():
    a = _make_unsubscribe_token(42, "a@example.com")
    b = _make_unsubscribe_token(42, "a@example.com")
    assert a == b
    assert re.fullmatch(r"[0-9a-f]{64}", a)


def test_unsubscribe_token_is_bound_to_both_lead_and_email():
    """A token must not verify for a different lead or a swapped address."""
    base = _make_unsubscribe_token(42, "a@example.com")
    assert base != _make_unsubscribe_token(43, "a@example.com")
    assert base != _make_unsubscribe_token(42, "b@example.com")


def test_unsubscribe_url_carries_id_and_token():
    tok = _make_unsubscribe_token(7, "a@example.com")
    url = _unsubscribe_url(7, tok)
    assert "id=7" in url and f"t={tok}" in url


def test_pixel_token_is_16_hex_and_varies_per_lead_and_week():
    tok = _make_pixel_token("spring", 5, 1)
    assert re.fullmatch(r"[0-9a-f]{16}", tok)
    assert tok != _make_pixel_token("spring", 6, 1)
    assert tok != _make_pixel_token("spring", 5, 2)
    assert tok != _make_pixel_token("fall", 5, 1)


def test_pixel_html_is_a_hidden_one_by_one_image():
    html = _pixel_html("spring", 5, 1)
    assert 'width="1"' in html and 'height="1"' in html
    assert "display:none" in html
    assert 'alt=""' in html          # silent — no alt text surfaced in plaintext
    assert "/t/o/spring/5/1/" in html


def test_click_url_uses_the_click_route_with_the_same_token():
    tok = _make_pixel_token("spring", 5, 1)
    assert _click_url("spring", 5, 1) == f"https://example.test/t/c/spring/5/1/{tok}"


# ── HTML → text fallback ─────────────────────────────────────────────────────

def test_html_to_text_strips_tags_and_unescapes_entities():
    out = _html_to_text("<p>Hi &amp; welcome</p>")
    assert "Hi & welcome" in out
    assert "<p>" not in out


def test_html_to_text_drops_style_and_script_content():
    out = _html_to_text("<style>.a{color:red}</style><script>var x=1</script><p>Body</p>")
    assert "color:red" not in out
    assert "var x=1" not in out
    assert "Body" in out


def test_html_to_text_renders_links_with_their_url():
    out = _html_to_text('<a href="https://example.com/s">Schedule</a>')
    assert "Schedule (https://example.com/s)" in out


def test_html_to_text_surfaces_alt_text_but_stays_silent_for_the_pixel():
    assert "Apex Energy" in _html_to_text('<img src="x.png" alt="Apex Energy" />')
    assert _html_to_text(_pixel_html("spring", 5, 1)).strip() == ""


def test_html_to_text_converts_checkmarks_to_bullets_on_one_line():
    out = _html_to_text("<div><span>✓</span>\n   Free estimate</div>")
    assert "• Free estimate" in out
    assert "✓" not in out


def test_html_to_text_collapses_runs_of_blank_lines():
    out = _html_to_text("<p>one</p><br><br><br><br><p>two</p>")
    assert "\n\n\n" not in out


def test_html_to_text_always_ends_with_a_single_newline():
    out = _html_to_text("<p>body</p>")
    assert out.endswith("\n") and not out.endswith("\n\n")
