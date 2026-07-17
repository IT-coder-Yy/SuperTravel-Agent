import tempfile
import unittest
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
for path in (BACKEND_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from services.destination_catalog_service import (
    canonical_destination,
    classic_places_for_city,
    domestic_city_names,
    is_invalid_poi_name,
)
from services.trip_product_service import (
    ShareRepository,
    TRIP_TEMPLATES,
    export_trip_markdown,
    import_trip_document,
)


def _document():
    return {
        "schema_version": "1.0",
        "profile": {"diet": "private"},
        "plan": {
            "title": "杭州两日游",
            "intent": {"destination": "杭州", "days": 2},
            "days": 2,
            "activities": [
                {
                    "activity_id": "a1",
                    "day": 1,
                    "title": "西湖风景名胜区",
                    "place": {"name": "西湖风景名胜区", "category": "景点", "lat": 30.25, "lng": 120.15},
                    "data_type": "reference_data",
                }
            ],
            "source_references": [],
        },
        "budget": {"budget_total": 1200, "known_total": 800},
        "sources": [{"title": "官方页面", "api_key": "secret", "internal_context": "hidden"}],
        "checklist": [{"id": "c1", "text": "购买门票", "completed": False, "day": 1}],
        "notes": [{"id": "n1", "content": "同行人海鲜过敏", "target_type": "trip"}],
    }


class TripProductServiceTests(unittest.TestCase):
    def test_templates_are_executable_and_require_clarification(self):
        self.assertEqual(len(TRIP_TEMPLATES), 5)
        for template in TRIP_TEMPLATES:
            self.assertIn("继续逐项澄清", template["prompt"])
            self.assertTrue(template["preferences"])

    def test_destination_catalog_covers_domestic_international_and_bad_poi(self):
        self.assertGreaterEqual(len(domestic_city_names()), 290)
        self.assertEqual(canonical_destination("Plan a 3-day trip to Paris"), "巴黎")
        self.assertEqual(canonical_destination("去东京旅行"), "东京")
        self.assertTrue(classic_places_for_city("London"))
        self.assertTrue(is_invalid_poi_name("规划道路"))
        self.assertFalse(is_invalid_poi_name("西湖风景名胜区"))

    def test_json_and_markdown_round_trip_are_validated(self):
        normalized = import_trip_document("json", _document())
        self.assertEqual(normalized["plan"]["intent"]["destination"], "杭州")
        self.assertNotIn("profile", normalized)
        markdown = export_trip_markdown(normalized)
        self.assertIn("# 杭州两日游", markdown)
        self.assertIn("购买门票", markdown)
        imported_markdown = import_trip_document("markdown", markdown)
        self.assertEqual(imported_markdown["plan"]["days"], 1)
        self.assertEqual(len(imported_markdown["plan"]["activities"]), 1)
        self.assertEqual(imported_markdown["checklist"][0]["text"], "购买门票")
        self.assertEqual(imported_markdown["notes"][0]["content"], "同行人海鲜过敏")
        self.assertEqual(imported_markdown["budget"]["estimated_total"], 800)
        self.assertIn("Markdown 导入", imported_markdown["plan"]["warnings"][0])

    def test_share_is_scope_limited_private_and_revocable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ShareRepository(Path(temp_dir))
            created = repository.create(_document(), ["itinerary", "sources"])
            snapshot = repository.read(created["token"])
            self.assertIn("plan", snapshot)
            self.assertIn("sources", snapshot)
            self.assertNotIn("budget", snapshot)
            serialized = str(snapshot)
            self.assertNotIn("secret", serialized)
            self.assertNotIn("internal_context", serialized)
            with self.assertRaises(PermissionError):
                repository.delete(created["token"], "wrong")
            repository.delete(created["token"], created["management_key"])
            with self.assertRaises(FileNotFoundError):
                repository.read(created["token"])

    def test_import_rejects_unstructured_payload(self):
        with self.assertRaisesRegex(ValueError, "结构化 plan"):
            import_trip_document("json", {"title": "not a trip"})


if __name__ == "__main__":
    unittest.main()
