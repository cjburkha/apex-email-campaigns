"""
Tests for the drip helpers that encode a lead id into a public URL: base36
encoding and the signed referral code decoded by the website at /r/:code.

If these drift, referral links stop resolving to the right customer — so the
round-trip and the tamper-resistance are both asserted here.
"""
import re

import pytest

from drip import _base36, _referral_code, _referral_url


@pytest.mark.parametrize("n,expected", [
    (0,  "0"),
    (9,  "9"),
    (10, "a"),
    (35, "z"),
    (36, "10"),
])
def test_base36_known_values(n, expected):
    assert _base36(n) == expected


@pytest.mark.parametrize("n", [1, 7, 35, 36, 1259, 20367, 999999])
def test_base36_round_trips_through_int(n):
    assert int(_base36(n), 36) == n


def test_referral_code_shape():
    """base36(lead_id) followed by exactly 6 hex chars of HMAC."""
    code = _referral_code(20367)
    assert code.startswith(_base36(20367))
    mac = code[len(_base36(20367)):]
    assert re.fullmatch(r"[0-9a-f]{6}", mac)


def test_referral_code_is_deterministic():
    assert _referral_code(42) == _referral_code(42)


def test_referral_code_differs_per_lead():
    assert _referral_code(42) != _referral_code(43)


def test_referral_code_prefix_decodes_back_to_the_lead_id():
    """The website decodes the base36 prefix to find the lead — keep it recoverable."""
    for lead_id in (1, 42, 1259, 20367):
        code = _referral_code(lead_id)
        assert int(code[:-6], 36) == lead_id


@pytest.mark.parametrize("bad", [0, -1, None])
def test_referral_code_rejects_non_positive_ids(bad):
    with pytest.raises(ValueError):
        _referral_code(bad)


def test_referral_url_points_at_the_r_route():
    assert _referral_url(42) == f"https://example.test/r/{_referral_code(42)}"
