import asyncio
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "main.py"
SPEC = importlib.util.spec_from_file_location("unsplash_mcp_main", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_missing_key_is_explicit_and_does_not_expose_secret(monkeypatch):
    monkeypatch.delenv("UNSPLASH_ACCESS_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NOT_CONFIGURED"):
        MODULE._headers()


def test_public_photo_keeps_hotlink_and_minimum_attribution(monkeypatch):
    monkeypatch.setenv("UNSPLASH_APP_NAME", "supertravelagent")
    photo = MODULE._public_photo({
        "id": "p1", "width": 1200, "height": 800, "alt_description": "Tokyo",
        "urls": {"regular": "https://images.unsplash.com/photo-1"},
        "links": {"html": "https://unsplash.com/photos/p1", "download_location": "https://api.unsplash.com/photos/p1/download"},
        "user": {"name": "Author", "links": {"html": "https://unsplash.com/@author"}},
    })
    assert photo["url"].startswith("https://images.unsplash.com/")
    assert "utm_source=supertravelagent" in photo["photographer_url"]
    assert photo["download_location"].startswith("https://api.unsplash.com/")


def test_download_tracking_rejects_non_unsplash_domain():
    with pytest.raises(ValueError, match="NOT_ALLOWED"):
        asyncio.run(MODULE.unsplash_track_download("https://example.com/photos/p1/download"))
