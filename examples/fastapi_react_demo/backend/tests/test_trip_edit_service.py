import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

from pydantic import BaseModel, ConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from services.trip_edit_service import (
    TripEditError,
    apply_trip_edit,
    get_activity_delete_impact,
    get_day_route_optimization_preview,
)


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


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


def _v3_document():
    return json.loads((FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8"))


def _v3_document_with_delete_dependencies():
    document = _v3_document()
    day = document["itinerary"]["days"][0]
    deleted = day["activities"][0]
    successor = deepcopy(deleted)
    successor.update({
        "activity_id": "act_hz_1_successor",
        "title": "杭州契约景点一后续活动",
        "order": 2,
        "start_at": "13:00",
        "end_at": "15:00",
        "note_id": None,
        "images": [],
        "route_to_next": None,
    })
    successor["place"] = {
        **successor["place"],
        "poi_id": "poi_hz_1_successor",
        "name": "杭州契约景点一后续活动",
    }
    deleted["note_id"] = "note_act_hz_1"
    deleted["images"] = [{
        "image_id": "image_act_hz_1",
        "url": "https://example.test/act-hz-1.jpg",
        "alt": "杭州契约景点一图片",
    }]
    deleted["route_to_next"] = {
        "from_id": deleted["activity_id"],
        "to_id": successor["activity_id"],
        "mode": "walk",
        "status": "unavailable",
    }
    day["activities"].append(successor)
    document["notes"].append({
        "note_id": "note_act_hz_1",
        "scope": "activity",
        "target_id": deleted["activity_id"],
        "content": "提前到场核验预约",
        "updated_at": "2026-07-17T10:00:00+08:00",
    })
    document["action_items"].extend([
        {
            "action_id": "reminder_act_hz_1",
            "kind": "reservation_reminder",
            "scope": "activity",
            "target_id": deleted["activity_id"],
            "text": "确认预约",
            "status": "pending",
        },
        {
            "action_id": "checklist_act_hz_1",
            "kind": "checklist",
            "scope": "activity",
            "target_id": deleted["activity_id"],
            "text": "携带证件",
            "status": "pending",
        },
    ])
    document["map_guidance"]["formal_location_ids"].insert(1, successor["activity_id"])
    document["map_guidance"]["day_routes"][0] = {
        "day": 1,
        "status": "partial",
        "legs": [deepcopy(deleted["route_to_next"])],
    }
    return document


def _v3_document_with_route_optimization_opportunity():
    document = _v3_document()
    day = document["itinerary"]["days"][0]
    far_activity = day["activities"][0]
    far_activity.update({"start_at": "10:00", "end_at": "11:00", "fixed_time": False})
    far_activity["place"] = {
        **far_activity["place"],
        "poi_id": "poi_hz_route_far",
        "name": "杭州路线远端活动",
        "coordinates": {"latitude": 30.05, "longitude": 119.70, "coordinate_system": "WGS84"},
    }
    far_activity["title"] = "杭州路线远端活动"
    near_activity = deepcopy(far_activity)
    near_activity.update({
        "activity_id": "act_hz_route_near",
        "title": "杭州路线近端活动",
        "order": 2,
        "start_at": "11:00",
        "end_at": "12:00",
    })
    near_activity["place"] = {
        **near_activity["place"],
        "poi_id": "poi_hz_route_near",
        "name": "杭州路线近端活动",
        "coordinates": {"latitude": 30.290, "longitude": 120.210, "coordinate_system": "WGS84"},
    }
    fixed_activity = deepcopy(far_activity)
    fixed_activity.update({
        "activity_id": "act_hz_route_fixed",
        "title": "杭州固定时间活动",
        "order": 3,
        "start_at": "13:00",
        "end_at": "14:00",
        "fixed_time": True,
    })
    fixed_activity["place"] = {
        **fixed_activity["place"],
        "poi_id": "poi_hz_route_fixed",
        "name": "杭州固定时间活动",
        "coordinates": {"latitude": 30.260, "longitude": 120.142, "coordinate_system": "WGS84"},
    }
    day["activities"] = [far_activity, near_activity, fixed_activity]
    formal_location_ids = document["map_guidance"]["formal_location_ids"]
    original_index = formal_location_ids.index(far_activity["activity_id"])
    formal_location_ids[original_index:original_index + 1] = [
        far_activity["activity_id"], near_activity["activity_id"], fixed_activity["activity_id"],
    ]
    return document


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

    def test_v3_moves_scheduled_activity_to_candidate_without_retaining_stale_route(self):
        before = _v3_document()
        activity_id = before["itinerary"]["days"][0]["activities"][0]["activity_id"]

        result = apply_trip_edit(
            before,
            _operation(
                "move_activity_to_candidate",
                {"activity_id": activity_id},
                plan_id=before["plan_id"],
                base_version=before["revision"],
            ),
        )

        document = result["document"]
        candidate = next(item for item in document["candidate_pool"] if item["activity_id"] == activity_id)
        self.assertEqual(2, document["revision"])
        self.assertIsNone(candidate["day"])
        self.assertIsNone(candidate["order"])
        self.assertIsNone(candidate["start_at"])
        self.assertFalse(candidate["fixed_time"])
        self.assertIsNone(candidate["route_to_next"])
        self.assertNotIn(
            activity_id,
            [item["activity_id"] for day in document["itinerary"]["days"] for item in day["activities"]],
        )
        self.assertEqual(
            "unavailable",
            next(route["status"] for route in document["map_guidance"]["day_routes"] if route["day"] == 1),
        )
        self.assertTrue(result["diff"]["routes_revalidation_required"])
        self.assertIn(
            "ROUTE_REVALIDATION_REQUIRED",
            [issue["code"] for issue in document["validation"]["issues"]],
        )

    def test_v3_permanent_delete_previews_and_cascades_related_data(self):
        before = _v3_document_with_delete_dependencies()
        activity_id = before["itinerary"]["days"][0]["activities"][0]["activity_id"]

        impact = get_activity_delete_impact(before, activity_id)
        self.assertEqual({
            "notes": 1,
            "reservation_reminders": 1,
            "checklist_items": 1,
            "image_references": 1,
            "route_segments": 1,
            "map_markers": 1,
        }, {key: impact[key] for key in (
            "notes", "reservation_reminders", "checklist_items", "image_references", "route_segments", "map_markers",
        )})

        result = apply_trip_edit(
            before,
            _operation(
                "delete_activity",
                {"activity_id": activity_id, "confirmed": True},
                plan_id=before["plan_id"],
                base_version=before["revision"],
            ),
        )

        document = result["document"]
        self.assertEqual(impact, result["diff"]["delete_impact"])
        self.assertNotIn(
            activity_id,
            [item["activity_id"] for day in document["itinerary"]["days"] for item in day["activities"]],
        )
        self.assertFalse(any(note["target_id"] == activity_id for note in document["notes"]))
        self.assertFalse(any(item.get("target_id") == activity_id for item in document["action_items"]))
        self.assertNotIn(activity_id, document["map_guidance"]["formal_location_ids"])
        self.assertEqual([], document["map_guidance"]["day_routes"][0]["legs"])
        self.assertEqual("unavailable", document["map_guidance"]["day_routes"][0]["status"])
        self.assertEqual(1, document["itinerary"]["days"][0]["activities"][0]["order"])
        self.assertEqual("act_hz_1_successor", document["itinerary"]["days"][0]["activities"][0]["activity_id"])

    def test_v3_permanent_delete_requires_explicit_confirmation(self):
        before = _v3_document()
        activity_id = before["itinerary"]["days"][0]["activities"][0]["activity_id"]

        with self.assertRaises(TripEditError) as raised:
            apply_trip_edit(
                before,
                _operation(
                    "delete_activity",
                    {"activity_id": activity_id},
                    plan_id=before["plan_id"],
                    base_version=before["revision"],
                ),
            )

        self.assertEqual("DELETE_CONFIRMATION_REQUIRED", raised.exception.code)
        self.assertEqual(before, _v3_document())

    def test_v3_route_optimization_reorders_only_flexible_activities_and_reschedules_them(self):
        before = _v3_document_with_route_optimization_opportunity()

        preview = get_day_route_optimization_preview(before, 1)
        self.assertTrue(preview["can_optimize"])
        self.assertEqual(
            ["act_hz_route_near", "act_hz_1", "act_hz_route_fixed"],
            [item["activity_id"] for item in preview["optimized_order"]],
        )
        self.assertEqual(1, preview["fixed_activity_count"])
        self.assertTrue(preview["order_changes"])
        self.assertTrue(preview["time_changes"])
        self.assertEqual("13:00", before["itinerary"]["days"][0]["activities"][2]["start_at"])

        result = apply_trip_edit(
            before,
            _operation(
                "optimize_day_route",
                {"day": 1, "confirmed": True},
                plan_id=before["plan_id"],
                base_version=before["revision"],
            ),
        )

        document = result["document"]
        optimized = document["itinerary"]["days"][0]["activities"]
        self.assertEqual(
            ["act_hz_route_near", "act_hz_1", "act_hz_route_fixed"],
            [item["activity_id"] for item in optimized],
        )
        self.assertEqual([1, 2, 3], [item["order"] for item in optimized])
        self.assertEqual(("10:00", "11:00"), (optimized[0]["start_at"], optimized[0]["end_at"]))
        self.assertEqual(("11:00", "12:00"), (optimized[1]["start_at"], optimized[1]["end_at"]))
        self.assertEqual(("13:00", "14:00"), (optimized[2]["start_at"], optimized[2]["end_at"]))
        self.assertTrue(optimized[2]["fixed_time"])
        self.assertEqual(1, document["revision"] - before["revision"])
        self.assertEqual("unavailable", document["map_guidance"]["day_routes"][0]["status"])
        self.assertTrue(result["diff"]["routes_revalidation_required"])
        self.assertTrue(result["diff"]["route_optimization"]["order_changes"])

    def test_v3_route_optimization_requires_explicit_confirmation(self):
        before = _v3_document_with_route_optimization_opportunity()

        with self.assertRaises(TripEditError) as raised:
            apply_trip_edit(
                before,
                _operation(
                    "optimize_day_route",
                    {"day": 1},
                    plan_id=before["plan_id"],
                    base_version=before["revision"],
                ),
            )

        self.assertEqual("ROUTE_OPTIMIZATION_CONFIRMATION_REQUIRED", raised.exception.code)

    def test_v3_inserts_candidate_only_into_a_server_checked_feasible_slot(self):
        before = _v3_document()
        candidate_id = before["candidate_pool"][0]["activity_id"]

        result = apply_trip_edit(
            before,
            _operation(
                "insert_candidate",
                {"activity_id": candidate_id, "target_day": 2, "position": 1},
                plan_id=before["plan_id"],
                base_version=before["revision"],
            ),
        )

        day_two = result["document"]["itinerary"]["days"][1]["activities"]
        inserted = next(item for item in day_two if item["activity_id"] == candidate_id)
        self.assertEqual(candidate_id, inserted["activity_id"])
        self.assertEqual(2, inserted["day"])
        self.assertEqual(2, inserted["order"])
        self.assertEqual("11:30", inserted["start_at"])
        self.assertEqual("13:30", inserted["end_at"])
        self.assertFalse(inserted["fixed_time"])
        self.assertEqual([], inserted["insertion_options"])
        self.assertFalse(any(item["activity_id"] == candidate_id for item in result["document"]["candidate_pool"]))

    def test_v3_keeps_candidate_when_requested_day_has_no_feasible_slot(self):
        before = _v3_document()
        candidate_id = before["candidate_pool"][0]["activity_id"]
        before["candidate_pool"][0]["place"].pop("opening_hours")

        with self.assertRaises(TripEditError) as raised:
            apply_trip_edit(
                before,
                _operation(
                    "insert_candidate",
                    {"activity_id": candidate_id, "target_day": 2},
                    plan_id=before["plan_id"],
                    base_version=before["revision"],
                ),
            )

        self.assertEqual("CANDIDATE_NO_FEASIBLE_SLOT", raised.exception.code)

    def test_v3_adjusts_time_sets_fixed_state_and_updates_activity_note(self):
        before = _v3_document()
        activity_id = before["itinerary"]["days"][0]["activities"][0]["activity_id"]
        shifted = apply_trip_edit(
            before,
            _operation(
                "shift_activity_time",
                {"activity_id": activity_id, "delta_minutes": 15},
                plan_id=before["plan_id"],
                base_version=before["revision"],
            ),
        )["document"]
        fixed = apply_trip_edit(
            shifted,
            _operation(
                "set_activity_fixed_time",
                {"activity_id": activity_id, "fixed_time": True},
                plan_id=shifted["plan_id"],
                base_version=shifted["revision"],
            ),
        )["document"]
        noted = apply_trip_edit(
            fixed,
            _operation(
                "update_activity_note",
                {"activity_id": activity_id, "content": "提前 15 分钟到场安检"},
                plan_id=fixed["plan_id"],
                base_version=fixed["revision"],
            ),
        )["document"]

        activity = noted["itinerary"]["days"][0]["activities"][0]
        self.assertEqual("10:15", activity["start_at"])
        self.assertTrue(activity["fixed_time"])
        self.assertTrue(activity["note_id"])
        note = next(item for item in noted["notes"] if item["note_id"] == activity["note_id"])
        self.assertEqual("activity", note["scope"])
        self.assertEqual(activity_id, note["target_id"])
        self.assertEqual("提前 15 分钟到场安检", note["content"])

    def test_v3_selects_existing_transport_and_lodging_options_then_refreshes_anchors(self):
        before = _v3_document()
        alternative_transport = deepcopy(before["outbound_transport"]["options"][0])
        alternative_transport["option_id"] = "transport_hz_out_alt"
        alternative_transport["service_number"] = "G999"
        before["outbound_transport"]["options"].append(alternative_transport)
        alternative_lodging = deepcopy(before["lodging_plan"]["options"][0])
        alternative_lodging["lodging_id"] = "lodging_hz_alt"
        alternative_lodging["name"] = "杭州备选规划住宿"
        alternative_lodging["place"]["poi_id"] = "poi_hz_lodging_alt"
        alternative_lodging["place"]["name"] = "杭州备选规划住宿"
        before["lodging_plan"]["options"].append(alternative_lodging)

        transport_selected = apply_trip_edit(
            before,
            _operation(
                "select_transport_option",
                {"direction": "outbound", "option_id": "transport_hz_out_alt"},
                plan_id=before["plan_id"],
                base_version=before["revision"],
            ),
        )["document"]
        lodging_selected = apply_trip_edit(
            transport_selected,
            _operation(
                "select_lodging_option",
                {"lodging_id": "lodging_hz_alt"},
                plan_id=transport_selected["plan_id"],
                base_version=transport_selected["revision"],
            ),
        )["document"]

        self.assertEqual("transport_hz_out_alt", lodging_selected["outbound_transport"]["selected_option_id"])
        self.assertEqual("lodging_hz_alt", lodging_selected["lodging_plan"]["planning_lodging_id"])
        lodging_anchors = [
            anchor
            for day in lodging_selected["itinerary"]["days"]
            for anchor in day["anchors"]
            if anchor["kind"].startswith("lodging_")
        ]
        self.assertTrue(lodging_anchors)
        self.assertTrue(all(anchor["place"]["poi_id"] == "poi_hz_lodging_alt" for anchor in lodging_anchors))

    def test_v3_rejects_invalid_time_shift_and_unknown_option_without_mutating_document(self):
        before = _v3_document()
        activity_id = before["itinerary"]["days"][0]["activities"][0]["activity_id"]
        original = deepcopy(before)
        cases = (
            (_operation("shift_activity_time", {"activity_id": activity_id, "delta_minutes": 30}, plan_id=before["plan_id"], base_version=before["revision"]), "INVALID_PAYLOAD"),
            (_operation("select_lodging_option", {"lodging_id": "missing"}, plan_id=before["plan_id"], base_version=before["revision"]), "LODGING_OPTION_NOT_FOUND"),
        )

        for operation, code in cases:
            with self.subTest(code=code), self.assertRaises(TripEditError) as raised:
                apply_trip_edit(before, operation)
            self.assertEqual(code, raised.exception.code)
            self.assertEqual(original, before)


if __name__ == "__main__":
    unittest.main()
