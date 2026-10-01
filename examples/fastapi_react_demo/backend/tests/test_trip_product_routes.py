import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
for path in (BACKEND_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

import main
from schemas.api_models import ShareCreateRequest, TripDocumentExportRequest, TripDocumentImportRequest
from services.trip_product_service import ShareRepository
from services.trip_repository import TripRepository
from services.anonymous_device_service import resolve_anonymous_device, DEVICE_COOKIE_NAME


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _document():
    return {
        "schema_version": "1.0",
        "profile": {"allergy": "private"},
        "plan": {
            "plan_id": "route-plan",
            "title": "杭州一日游",
            "intent": {"destination": "杭州", "days": 1},
            "days": 1,
            "activities": [{"activity_id": "a1", "day": 1, "title": "西湖", "place": {"name": "西湖", "category": "景点"}}],
        },
        "budget": {"budget_total": 500},
        "sources": [],
        "checklist": [],
        "notes": [],
    }


def _v3_document():
    return json.loads((FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8"))


class TripProductRouteTests(unittest.TestCase):
    def setUp(self):
        main.travel_export_cache.clear()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = TripRepository(Path(temporary.name) / "trips.sqlite3")
        repository.initialize()
        patcher = patch.object(main.runtime_state, "trip_repository", repository)
        patcher.start()
        self.addCleanup(patcher.stop)
        identity = resolve_anonymous_device(repository, None)
        repository.upsert_trip(identity.device_id, "export-trip", title="导出验收")
        repository.apply_formal_snapshot(identity.device_id, "export-trip", "initial", _v3_document())
        self.http_request = Request({"type": "http", "headers": [(b"cookie", f"{DEVICE_COOKIE_NAME}={identity.raw_token}".encode())]})
        self.export_request = TripDocumentExportRequest(trip_id="export-trip", expected_revision=1)
        self.client = TestClient(main.app)
        self.client.cookies.set(DEVICE_COOKIE_NAME, identity.raw_token)

    def test_template_import_and_v3_markdown_export_routes(self):
        templates = asyncio.run(main.get_trip_templates())
        self.assertEqual(templates["count"], 5)

        imported = asyncio.run(main.import_trip_document_endpoint(TripDocumentImportRequest(format="json", content=_document())))
        self.assertNotIn("profile", imported)
        exported = asyncio.run(main.export_trip_markdown_endpoint(self.export_request, self.http_request))
        self.assertEqual(exported["filename"], "杭州三日契约样本.md")
        self.assertEqual(exported["version"], 1)
        self.assertIn("# 杭州三日契约样本", exported["content"])
        self.assertNotIn("## 参考来源", exported["content"])

    def test_markdown_export_rejects_legacy_document(self):
        response = self.client.post("/api/trips/export/markdown", json={"document": _document()})
        self.assertEqual(response.status_code, 422)

    def test_markdown_export_reuses_the_same_formal_revision_for_ten_minutes(self):
        with patch.object(main, "export_travel_plan_v3_plain_markdown", return_value="# 已缓存方案") as renderer:
            first = asyncio.run(main.export_trip_markdown_endpoint(self.export_request, self.http_request))
            second = asyncio.run(main.export_trip_markdown_endpoint(self.export_request, self.http_request))

        self.assertEqual("# 已缓存方案", first["content"])
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(1, renderer.call_count)

    def test_pdf_export_returns_a_download_without_fetching_unpermitted_images(self):
        image_fetcher = AsyncMock(return_value={})
        with patch.object(main, "fetch_pdf_export_images", image_fetcher):
            response = asyncio.run(main.export_trip_pdf_endpoint(self.export_request, self.http_request))
            cached_response = asyncio.run(main.export_trip_pdf_endpoint(self.export_request, self.http_request))

        self.assertEqual(response.media_type, "application/pdf")
        self.assertTrue(response.body.startswith(b"%PDF"))
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn(".pdf", response.headers["content-disposition"])
        self.assertEqual("MISS", response.headers["x-export-cache"])
        self.assertEqual("HIT", cached_response.headers["x-export-cache"])
        self.assertEqual(1, image_fetcher.await_count)

    def test_pdf_export_rejects_legacy_document(self):
        response = self.client.post("/api/trips/export/pdf", json={"document": _document()})
        self.assertEqual(response.status_code, 422)

    def test_exports_reject_unapplied_stale_and_other_device_requests(self):
        for format in ("markdown", "pdf"):
            with self.subTest(format=format):
                url = f"/api/trips/export/{format}"
                forged = {**_v3_document(), "title": "未应用草稿"}
                response = self.client.post(url, json={"trip_id": "export-trip", "expected_revision": 1, "document": forged})
                self.assertEqual(response.status_code, 422)
                response = self.client.post(url, json={"trip_id": "export-trip", "expected_revision": 2})
                self.assertEqual(response.status_code, 409)
                other = TestClient(main.app)
                response = other.post(url, json={"trip_id": "export-trip", "expected_revision": 1})
                self.assertEqual(response.status_code, 404)

    def test_share_routes_are_scope_limited_and_revocable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ShareRepository(Path(temp_dir))
            with patch.object(main, "share_repository", repository):
                created = asyncio.run(main.create_share_endpoint(ShareCreateRequest(document=_document(), scopes=["itinerary"])))
                snapshot = asyncio.run(main.get_share_endpoint(created["token"]))
                self.assertIn("plan", snapshot)
                self.assertNotIn("budget", snapshot)
                self.assertNotIn("profile", str(snapshot))

                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(main.delete_share_endpoint(created["token"], "wrong-key"))
                self.assertEqual(raised.exception.status_code, 403)

                closed = asyncio.run(main.delete_share_endpoint(created["token"], created["management_key"]))
                self.assertEqual(closed["status"], "closed")
                with self.assertRaises(HTTPException) as missing:
                    asyncio.run(main.get_share_endpoint(created["token"]))
                self.assertEqual(missing.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
