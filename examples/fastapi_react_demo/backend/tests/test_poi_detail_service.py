import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from services.poi_detail_service import (
    PoiDetailError,
    build_stable_poi_id,
    merge_poi_detail,
    merge_poi_details,
    normalize_poi_detail,
)


NOW = datetime(2026, 7, 13, tzinfo=timezone.utc)


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


def _poi(**overrides):
    value = {
        "poi_id": "poi_west_lake",
        "name": "West Lake",
        "coordinates": {"lat": 30.25, "lng": 120.15},
        "category": "scenic_area",
        "updated_at": "2026-07-10T08:00:00Z",
        "data_type": "reference_data",
        "source": {
            "source_reference_id": "ref_1",
            "title": "Tourism bureau",
            "url": "https://example.com/west-lake",
        },
    }
    value.update(overrides)
    return value


class PoiDetailServiceTests(unittest.TestCase):
    def test_normalizes_complete_poi_and_is_json_serializable(self):
        result = normalize_poi_detail(
            FlexibleModel.model_validate(
                _poi(
                    image_urls=["https://img.example/1.jpg"],
                    stay_duration_minutes="120",
                    business_hours={"daily": "06:00-22:00"},
                    reservation_info="not required",
                    price_reference={"amount": 0, "currency": "CNY"},
                    good_for=["families", "photography"],
                    avoid_for="limited mobility without assistance",
                )
            ),
            now=NOW,
        )

        self.assertEqual(result["coordinates"], {"lat": 30.25, "lng": 120.15})
        self.assertEqual(result["suggested_duration_minutes"], 120)
        self.assertEqual(result["images"], ["https://img.example/1.jpg"])
        self.assertEqual(result["suitable_for"], ["families", "photography"])
        self.assertEqual(result["unsuitable_for"], ["limited mobility without assistance"])
        self.assertEqual(result["data_type"], "reference_data")
        self.assertEqual(result["field_evidence"]["price"]["source_reference_id"], "ref_1")
        json.dumps(result, ensure_ascii=False)

    def test_generated_id_is_stable_for_equivalent_identity(self):
        first = {"name": " West Lake ", "lat": 30.250001, "lng": 120.150001}
        second = {"name": "WEST LAKE", "coordinates": [120.15, 30.25]}

        self.assertEqual(build_stable_poi_id(first), build_stable_poi_id(second))
        self.assertTrue(build_stable_poi_id(first).startswith("poi_"))

    def test_merges_each_field_by_confidence_priority(self):
        estimated = _poi(
            data_type="estimated_data",
            opening_hours="estimated hours",
            price="estimated price",
            updated_at="2026-07-12T00:00:00Z",
            source={"source_reference_id": "estimate_1", "title": "model estimate"},
        )
        reference = _poi(
            opening_hours="reference hours",
            price="reference price",
            updated_at="2026-07-11T00:00:00Z",
        )
        confirmed = _poi(
            data_type="confirmed_live_data",
            opening_hours="confirmed hours",
            updated_at="2026-07-09T00:00:00Z",
            source={"source_reference_id": "live_1", "title": "live tool"},
        )

        result = merge_poi_detail([estimated, reference, confirmed], now=NOW)

        self.assertEqual(result["opening_hours"], "confirmed hours")
        self.assertEqual(result["field_evidence"]["opening_hours"]["data_type"], "confirmed_live_data")
        self.assertEqual(result["price"], "reference price")
        self.assertEqual(result["field_evidence"]["price"]["data_type"], "reference_data")
        self.assertEqual(len(result["sources"]), 3)

    def test_newer_value_wins_within_same_confidence_level(self):
        older = _poi(price="CNY 80", updated_at="2026-07-01T00:00:00Z")
        newer = _poi(price="CNY 100", updated_at="2026-07-12T00:00:00Z")

        result = merge_poi_detail([newer, older], now=NOW)

        self.assertEqual(result["price"], "CNY 100")
        self.assertEqual(result["field_evidence"]["price"]["updated_at"], "2026-07-12T00:00:00Z")

    def test_expired_reference_cannot_promote_field_to_confirmed_live(self):
        stale = _poi(
            opening_hours="old hours",
            expires_at="2026-06-01T00:00:00Z",
            field_evidence={
                "opening_hours": {
                    "data_type": "confirmed_live_data",
                    "updated_at": "2026-05-01T00:00:00Z",
                }
            },
        )

        result = normalize_poi_detail(stale, now=NOW)

        evidence = result["field_evidence"]["opening_hours"]
        self.assertEqual(evidence["data_type"], "reference_data")
        self.assertTrue(evidence["is_expired"])
        self.assertNotEqual(result["data_type"], "confirmed_live_data")

    def test_field_evidence_can_set_confidence_when_parent_type_is_absent(self):
        result = normalize_poi_detail(
            {
                "id": "poi_museum",
                "name": "City Museum",
                "opening_hours": "09:00-17:00",
                "field_evidence": {
                    "opening_hours": {
                        "source_reference_id": "live_hours",
                        "data_type": "confirmed_live_data",
                        "updated_at": "2026-07-13T01:00:00Z",
                    }
                },
            },
            now=NOW,
        )

        self.assertEqual(result["poi_id"], "poi_museum")
        self.assertEqual(
            result["field_evidence"]["opening_hours"]["data_type"],
            "confirmed_live_data",
        )

    def test_optional_age_policy_marks_old_live_snapshot_as_reference(self):
        stale_live = _poi(
            data_type="confirmed_live_data",
            updated_at="2026-05-01T00:00:00Z",
            opening_hours="old live hours",
        )

        result = normalize_poi_detail(stale_live, now=NOW, max_reference_age_days=30)

        self.assertEqual(result["field_evidence"]["opening_hours"]["data_type"], "reference_data")
        self.assertTrue(result["field_evidence"]["opening_hours"]["is_expired"])

    def test_merges_only_records_with_the_same_stable_id(self):
        groups = merge_poi_details(
            [_poi(), _poi(price="free"), _poi(poi_id="poi_other", name="Other Place")],
            now=NOW,
        )

        self.assertEqual([item["poi_id"] for item in groups], ["poi_west_lake", "poi_other"])
        with self.assertRaises(PoiDetailError) as raised:
            merge_poi_detail([_poi(), _poi(poi_id="poi_other", name="Other Place")], now=NOW)
        self.assertEqual(raised.exception.code, "POI_ID_MISMATCH")

    def test_rejects_invalid_or_nameless_input(self):
        cases = (("invalid", "INVALID_POI"), ({"lat": 1, "lng": 2}, "MISSING_NAME"))

        for value, code in cases:
            with self.subTest(code=code), self.assertRaises(PoiDetailError) as raised:
                normalize_poi_detail(value, now=NOW)
            self.assertEqual(raised.exception.code, code)

    def test_missing_realtime_field_has_no_fake_source_or_update_time(self):
        result = normalize_poi_detail(_poi(opening_hours=None), now=NOW)

        self.assertIsNone(result["opening_hours"])
        self.assertIsNone(result["field_evidence"]["opening_hours"]["source_reference_id"])
        self.assertIsNone(result["field_evidence"]["opening_hours"]["updated_at"])


if __name__ == "__main__":
    unittest.main()
