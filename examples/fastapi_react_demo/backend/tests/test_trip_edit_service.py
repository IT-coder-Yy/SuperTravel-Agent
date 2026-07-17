import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

from pydantic import BaseModel, ConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from services.trip_edit_service import TripEditError, apply_trip_edit


def _plan():
    return {
        "plan_id": "plan_1",
        "version": 3,
        "title": "北京三日游",
        "activities": [
            {"activity_id": "a1", "day": 1, "title": "故宫", "estimated_cost": 60},
            {"activity_id": "a2", "day": 2, "title": "颐和园", "estimated_cost": 30},
            {"activity_id": "a3", "day": 3, "title": "长城", "estimated_cost": 40},
        ],
        "day_preferences": {"1": {"pace": "balanced"}},
        "warnings": ["出发前核验开放时间"],
    }


def _operation(operation_type, payload, **overrides):
    operation = {
        "operation_id": f"op_{operation_type}",
        "plan_id": "plan_1",
        "base_version": 3,
        "type": operation_type,
        "payload": payload,
    }
    operation.update(overrides)
    return operation


def _structured_plan():
    activities = [
        {
            "activity_id": "a1",
            "day": 1,
            "title": "故宫",
            "transport_to_next": "地铁",
            "route_to_next": {
                "mode": "地铁",
                "distance_meters": 4200,
                "duration_minutes": 28,
                "data_type": "confirmed_live_data",
                "calculated_at": "2026-07-12T08:00:00+00:00",
            },
        },
        {"activity_id": "a1b", "day": 1, "title": "景山公园"},
        {"activity_id": "a2", "day": 2, "title": "颐和园"},
        {"activity_id": "a2b", "day": 2, "title": "圆明园"},
        {"activity_id": "a3", "day": 3, "title": "返程并关注天气"},
    ]
    return {
        "plan_id": "plan_1",
        "version": 3,
        "title": "北京三日游",
        "intent": {"destination": "北京", "days": 3, "pace": "balanced"},
        "days": 3,
        "activities": deepcopy(activities),
        "trip_days": [
            {"day": day, "revision": 1, "activities": deepcopy([item for item in activities if item["day"] == day])}
            for day in (1, 2, 3)
        ],
        "budget_summary": {},
        "map_locations": [],
        "source_references": [],
        "warnings": [],
    }


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class TripEditServiceTests(unittest.TestCase):
    def assert_unaffected_days_equal(self, before, after, affected_days):
        for day in {1, 2, 3} - set(affected_days):
            before_day = [item for item in before["activities"] if item["day"] == day]
            after_day = [item for item in after["activities"] if item["day"] == day]
            self.assertEqual(after_day, before_day)

    def test_add_activity_increments_version_and_reports_budget(self):
        before = _plan()
        result = apply_trip_edit(
            before,
            _operation(
                "add_activity",
                {
                    "activity": {
                        "activity_id": "a4",
                        "day": 2,
                        "title": "圆明园",
                        "estimated_cost": 10,
                    },
                    "position": 0,
                },
            ),
        )

        self.assertEqual(result["plan"]["version"], 4)
        self.assertEqual(result["diff"]["affected_days"], [2])
        self.assertEqual(result["diff"]["added"][0]["activity_id"], "a4")
        self.assertEqual(result["diff"]["budget_change"]["delta"], 10.0)
        self.assertEqual(before, _plan())
        self.assertEqual(result["previous_plan"], before)
        self.assert_unaffected_days_equal(before, result["plan"], [2])

    def test_remove_activity_returns_removed_snapshot(self):
        before = _plan()
        result = apply_trip_edit(before, _operation("remove_activity", {"activity_id": "a2"}))

        self.assertNotIn("a2", [item["activity_id"] for item in result["plan"]["activities"]])
        self.assertEqual(result["diff"]["removed"][0]["title"], "颐和园")
        self.assertEqual(result["diff"]["budget_change"]["delta"], -30.0)
        self.assert_unaffected_days_equal(before, result["plan"], [2])

    def test_replace_activity_keeps_id_and_only_changes_target_day(self):
        before = _plan()
        result = apply_trip_edit(
            before,
            _operation(
                "replace_activity",
                {
                    "activity_id": "a2",
                    "activity": {"title": "国家博物馆", "estimated_cost": 0},
                },
            ),
        )

        replacement = next(item for item in result["plan"]["activities"] if item["activity_id"] == "a2")
        self.assertEqual(replacement["day"], 2)
        self.assertEqual(replacement["title"], "国家博物馆")
        self.assertEqual(result["diff"]["budget_change"]["delta"], -30.0)
        self.assert_unaffected_days_equal(before, result["plan"], [2])

    def test_move_activity_reports_source_and_target_days(self):
        before = _plan()
        result = apply_trip_edit(
            before,
            _operation("move_activity", {"activity_id": "a2", "target_day": 3, "position": 0}),
        )

        moved = next(item for item in result["plan"]["activities"] if item["activity_id"] == "a2")
        self.assertEqual(moved["day"], 3)
        self.assertEqual(result["diff"]["affected_days"], [2, 3])
        self.assertEqual(
            result["diff"]["moved"],
            [{"activity_id": "a2", "from_day": 2, "to_day": 3}],
        )
        self.assert_unaffected_days_equal(before, result["plan"], [2, 3])

    def test_update_activity_time_reports_before_and_after(self):
        before = _plan()
        result = apply_trip_edit(
            before,
            _operation(
                "update_activity_time",
                {"activity_id": "a1", "start_time": "09:00", "end_time": "11:30"},
            ),
        )

        change = result["diff"]["time_changes"][0]
        self.assertEqual(change["before"], {"start_time": None, "end_time": None})
        self.assertEqual(change["after"], {"start_time": "09:00", "end_time": "11:30"})
        self.assert_unaffected_days_equal(before, result["plan"], [1])

    def test_update_day_preference_merges_only_selected_day(self):
        before = _plan()
        result = apply_trip_edit(
            before,
            _operation("update_day_preference", {"day": 1, "preference": {"pace": "relaxed"}}),
        )

        self.assertEqual(result["plan"]["day_preferences"]["1"]["pace"], "relaxed")
        self.assertEqual(result["diff"]["affected_days"], [1])
        self.assertEqual(result["diff"]["day_preference_change"]["before"]["pace"], "balanced")
        self.assertEqual(result["plan"]["activities"], before["activities"])

    def test_rejects_plan_id_and_version_conflicts_without_mutating_plan(self):
        plan = _plan()
        original = deepcopy(plan)
        cases = (
            (_operation("remove_activity", {"activity_id": "a1"}, plan_id="other"), "PLAN_ID_MISMATCH"),
            (_operation("remove_activity", {"activity_id": "a1"}, base_version=2), "VERSION_CONFLICT"),
        )

        for operation, code in cases:
            with self.subTest(code=code), self.assertRaises(TripEditError) as raised:
                apply_trip_edit(plan, operation)
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(plan, original)

    def test_accepts_pydantic_inputs_and_result_is_json_serializable(self):
        result = apply_trip_edit(
            FlexibleModel.model_validate(_plan()),
            FlexibleModel.model_validate(
                _operation("remove_activity", {"activity_id": "a3"})
            ),
        )

        serialized = json.dumps(result, ensure_ascii=False)
        self.assertIn("previous_plan", serialized)
        self.assertEqual(result["diff"]["new_warnings"], [])

    def test_rejects_unsupported_or_invalid_operations(self):
        cases = (
            (_operation("rename_plan", {}), "UNSUPPORTED_OPERATION"),
            (_operation("remove_activity", {"activity_id": "missing"}), "ACTIVITY_NOT_FOUND"),
            (_operation("update_activity_time", {"activity_id": "a1"}), "INVALID_PAYLOAD"),
        )

        for operation, code in cases:
            with self.subTest(code=code), self.assertRaises(TripEditError) as raised:
                apply_trip_edit(_plan(), operation)
            self.assertEqual(raised.exception.code, code)

    def test_recalculates_only_affected_day_routes_and_syncs_day_view(self):
        before = _structured_plan()
        result = apply_trip_edit(
            before,
            _operation(
                "update_activity_time",
                {"activity_id": "a1", "start_time": "09:00", "end_time": "11:00"},
            ),
        )

        route = result["plan"]["activities"][0]["route_to_next"]
        self.assertEqual(route["data_type"], "estimated_data")
        self.assertIsNone(route["distance_meters"])
        self.assertIsNone(route["duration_minutes"])
        self.assertNotEqual(route["calculated_at"], "2026-07-12T08:00:00+00:00")
        self.assertEqual(result["plan"]["trip_days"][1], before["trip_days"][1])
        self.assertEqual(result["plan"]["trip_days"][2], before["trip_days"][2])
        synced_activity = result["plan"]["trip_days"][0]["activities"][0]
        self.assertEqual(synced_activity["start_time"], "09:00")
        self.assertEqual(synced_activity["route_to_next"]["data_type"], "estimated_data")

    def test_reports_resolved_validator_issues_after_local_edit(self):
        before = _structured_plan()
        result = apply_trip_edit(
            before,
            _operation("remove_activity", {"activity_id": "a2b"}),
        )

        self.assertIn("MISSING_TRANSPORT", result["diff"]["resolved_issue_codes"])
        self.assertEqual(result["diff"]["affected_days"], [2])
        self.assertEqual(result["plan"]["trip_days"][0], before["trip_days"][0])
        self.assertEqual(result["plan"]["trip_days"][2], before["trip_days"][2])


if __name__ == "__main__":
    unittest.main()
