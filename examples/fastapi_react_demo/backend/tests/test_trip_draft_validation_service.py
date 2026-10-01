import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from services.trip_draft_validation_service import validate_trip_draft


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _document():
    return json.loads((FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8"))


class TripDraftValidationServiceTests(unittest.TestCase):
    def test_degraded_external_data_can_be_saved(self):
        result = validate_trip_draft(_document())

        self.assertTrue(result["can_apply"])
        self.assertEqual("degraded", result["status"])
        self.assertEqual([], result["hard_errors"])
        self.assertIn("ROUTE_DATA_PENDING", [issue["code"] for issue in result["soft_warnings"]])
        self.assertIn("REFERENCE_PRICE_PENDING", [issue["code"] for issue in result["soft_warnings"]])

    def test_fixed_time_conflict_blocks_apply_without_losing_draft(self):
        document = _document()
        first = document["itinerary"]["days"][0]["activities"][0]
        first.update({"fixed_time": True, "start_at": "10:00", "end_at": "11:30"})
        document["itinerary"]["days"][0]["activities"].append({
            "activity_id": "act_fixed_conflict",
            "kind": "free_time",
            "title": "午间休息",
            "day": 1,
            "order": 2,
            "start_at": "11:00",
            "end_at": "12:00",
            "fixed_time": True,
        })

        result = validate_trip_draft(document)

        self.assertFalse(result["can_apply"])
        self.assertEqual("blocked", result["status"])
        self.assertIn("FIXED_TIME_CONFLICT", [issue["code"] for issue in result["hard_errors"]])

    def test_incomplete_or_invalid_time_blocks_apply(self):
        document = _document()
        activity = document["itinerary"]["days"][0]["activities"][0]
        activity.update({"fixed_time": True, "start_at": "12:00", "end_at": "11:30"})

        result = validate_trip_draft(document)

        self.assertFalse(result["can_apply"])
        self.assertIn("INVALID_ACTIVITY_TIME", [issue["code"] for issue in result["hard_errors"]])

    def test_out_of_range_clock_time_blocks_apply(self):
        document = _document()
        activity = document["itinerary"]["days"][0]["activities"][0]
        activity.update({"start_at": "25:00", "end_at": "26:00"})

        result = validate_trip_draft(document)

        self.assertFalse(result["can_apply"])
        self.assertIn("INVALID_ACTIVITY_TIME", [issue["code"] for issue in result["hard_errors"]])

    def test_selected_transport_window_returns_an_actionable_hard_error(self):
        document = _document()
        option = document["outbound_transport"]["options"][0]
        option["arrival_time"].update({
            "display_text": "2026-08-01 12:00",
            "utc": "2026-08-01T04:00:00Z",
            "local_iso": "2026-08-01T12:00:00+08:00",
            "beijing_iso": "2026-08-01T12:00:00+08:00",
        })

        result = validate_trip_draft(document)

        self.assertFalse(result["can_apply"])
        conflict = next(issue for issue in result["hard_errors"] if issue["code"] == "OUTBOUND_ARRIVAL_CONFLICT")
        self.assertEqual("act_hz_1", conflict["target_id"])
        self.assertEqual(1, conflict["day"])

    def test_invalid_structure_is_a_hard_error(self):
        document = deepcopy(_document())
        document["itinerary"]["days"].pop()

        result = validate_trip_draft(document)

        self.assertFalse(result["can_apply"])
        self.assertEqual(["INVALID_TRIP_DOCUMENT"], [issue["code"] for issue in result["hard_errors"]])


if __name__ == "__main__":
    unittest.main()
