import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
for path in (BACKEND_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

import main
from schemas.api_models import ShareCreateRequest, TripDocumentExportRequest, TripDocumentImportRequest
from services.trip_product_service import ShareRepository


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


class TripProductRouteTests(unittest.TestCase):
    def test_template_import_and_markdown_export_routes(self):
        templates = asyncio.run(main.get_trip_templates())
        self.assertEqual(templates["count"], 5)

        imported = asyncio.run(main.import_trip_document_endpoint(TripDocumentImportRequest(format="json", content=_document())))
        self.assertNotIn("profile", imported)
        exported = asyncio.run(main.export_trip_markdown_endpoint(TripDocumentExportRequest(document=imported)))
        self.assertEqual(exported["filename"], "杭州一日游-v1.md")
        self.assertEqual(exported["version"], 1)
        self.assertIn("# 杭州一日游", exported["content"])

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
