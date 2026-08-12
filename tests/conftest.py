"""
Shared test setup.

The helpers under test read their signing secrets from the environment at call
time, so tests pin them to fixed values — tokens are then deterministic and can
be asserted against exactly. Nothing here touches AWS or the database.
"""
import os
import sys
from pathlib import Path

# Import the modules under test from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("UNSUBSCRIBE_SECRET", "test-unsubscribe-secret")
os.environ.setdefault("PIXEL_SECRET", "test-pixel-secret")
os.environ.setdefault("REFERRAL_SECRET", "test-referral-secret")
os.environ.setdefault("PIXEL_BASE_URL", "https://example.test")
os.environ.setdefault("UNSUBSCRIBE_BASE_URL", "https://example.test/unsubscribe")
os.environ.setdefault("SHORTLINK_HOST", "https://example.test")
