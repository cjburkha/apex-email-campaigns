"""
auth.py — Microsoft Graph authentication via device code flow.

Uses Microsoft's own Graph Command Line Tools client (pre-registered in every
O365 tenant, no admin or app registration required). On first run you'll be
prompted to visit https://microsoft.com/devicelogin and enter a short code —
MFA, conditional access, and all normal sign-in policies apply. The resulting
token is cached in .token_cache.json so every subsequent run is silent.

Usage:
    from auth import get_access_token
    token = get_access_token()   # prompts browser sign-in on first run only
"""

import json
import sys
from pathlib import Path

import msal

# Microsoft Graph Command Line Tools — a public client pre-registered in every
# O365 / Azure AD tenant. Users can self-consent without IT involvement.
_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
_AUTHORITY  = "https://login.microsoftonline.com/common"
_SCOPES     = ["Mail.Send"]
_CACHE_FILE = Path(__file__).parent / ".token_cache.json"


def _build_app() -> msal.PublicClientApplication:
    cache = msal.SerializableTokenCache()
    if _CACHE_FILE.exists():
        cache.deserialize(_CACHE_FILE.read_text())

    app = msal.PublicClientApplication(
        client_id=_CLIENT_ID,
        authority=_AUTHORITY,
        token_cache=cache,
    )
    return app, cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        _CACHE_FILE.write_text(cache.serialize())


def get_access_token() -> str:
    """
    Return a valid Graph access token, refreshing silently if possible.
    Falls back to device code flow (browser sign-in) on first run or if the
    refresh token has expired.
    """
    app, cache = _build_app()

    # Try silent acquisition from cache first
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache)
            return result["access_token"]

    # Fall back to device code flow (user signs in via browser)
    flow = app.initiate_device_flow(scopes=_SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to create device flow: {flow.get('error_description', flow)}")

    print("\n" + "─" * 60)
    print("  Sign in to Microsoft to authorise email sending:")
    print(f"  1. Open:  {flow['verification_uri']}")
    print(f"  2. Enter: {flow['user_code']}")
    print("─" * 60 + "\n")

    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise RuntimeError(
            f"Authentication failed: {result.get('error_description', result.get('error', 'unknown'))}"
        )

    _save_cache(cache)
    print("✅  Signed in — token cached for future runs\n")
    return result["access_token"]
