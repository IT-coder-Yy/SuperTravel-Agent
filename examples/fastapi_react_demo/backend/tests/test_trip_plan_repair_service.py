import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from schemas.trip_models import (
    TripActivity,
    TripIntent,
    TripPlace,
    TripPlan,
    TripSourceReference,
    TripValidationIssue,
    TripValidationResult,
)
from services.trip_plan_repair_service import repair_trip_plan_once
from services.trip_plan_validator import validate_trip_plan


def _activity(
    title: str,
    day: int = 1,
    lat: float | None = 39.9,
    lng: float | None = 116.4,
    transport_to_next: str | None = None,
    notes=None,
) -> TripActivity:
    return TripActivity(
        day=day,
        title=title,
        place=TripPlace(name=title, category="景点", lat=lat, lng=lng),
        transport_to_next=transport_to_next,
        notes=notes or [],
    )


class TripPlanRepairServiceTests(unittest.TestCase):
    def _plan(self, activities, days=1, pace="balanced", warnings=None):
        return TripPlan(
            title="测试行程",
            intent=TripIntent(destination="北京", days=days, pace=pace),
            days=days,
            activities=activities,
            warnings=warnings or [],
        )

    def test_source_and_activity_confidence_are_typed_and_legacy_compatible(self):
        plan = TripPlan(
            title="兼容测试",
            intent=TripIntent(destination="北京", days=1),
            days=1,
            activities=[TripActivity(day=1, title="故宫", confidence="verified")],
            source_references=[
                {
                    "type": "web",
                    "title": "官方页面",
                    "source": "official",
                    "url": "https://example.com",
                    "confidence": "reference",
                    "custom_field": "kept",
                }
            ],
        )

        self.assertEqual(plan.activities[0].data_type, "confirmed_live_data")
        self.assertIsInstance(plan.source_references[0], TripSourceReference)
        dumped = plan.model_dump()
        self.assertEqual(dumped["source_references"][0]["data_type"], "reference_data")
        self.assertNotIn("data_confidence", dumped["activities"][0])
        self.assertNotIn("data_confidence", dumped["source_references"][0])
        self.assertEqual(dumped["source_references"][0]["custom_field"], "kept")

    def test_repairs_transport_weather_and_return_without_mutating_input(self):
        plan = self._plan(
            [
                _activity("酒店", day=1),
                _activity("故宫", day=1),
                _activity("颐和园", day=2),
            ],
            days=2,
        )
        original = plan.model_dump()
        validation = validate_trip_plan(plan)

        result = repair_trip_plan_once(plan, validation)

        self.assertEqual(plan.model_dump(), original)
        self.assertTrue(result.repaired)
        self.assertIn("MISSING_TRANSPORT", result.resolved_issue_codes)
        self.assertIn("MISSING_WEATHER_REMINDER", result.resolved_issue_codes)
        self.assertIn("MISSING_RETURN_TRANSPORT", result.resolved_issue_codes)
        self.assertIsNotNone(result.plan.activities[0].transport_to_next)
        self.assertIsNone(result.plan.activities[0].estimated_cost)

    def test_repairs_only_issues_marked_by_validation(self):
        plan = self._plan([_activity("故宫"), _activity("景山公园")])
        original = plan.model_dump()
        validation = TripValidationResult(
            valid=True,
            issues=[
                TripValidationIssue(
                    code="MISSING_WEATHER_REMINDER",
                    message="缺少天气提醒。",
                    severity="info",
                )
            ],
        )

        result = repair_trip_plan_once(plan, validation)

        self.assertEqual(plan.model_dump(), original)
        self.assertEqual(result.attempted_issue_codes, ["MISSING_WEATHER_REMINDER"])
        self.assertEqual(result.resolved_issue_codes, ["MISSING_WEATHER_REMINDER"])
        self.assertTrue(any("天气" in warning for warning in result.plan.warnings))
        self.assertIsNone(result.plan.activities[0].transport_to_next)
        self.assertIn(
            "MISSING_TRANSPORT",
            [issue.code for issue in result.remaining_validation.issues],
        )

    def test_downgrades_unverified_realtime_claim_without_inventing_data(self):
        plan = self._plan(
            [
                TripActivity(
                    day=1,
                    title="已预订酒店，保证有票",
                    place=TripPlace(name="候选酒店", category="酒店"),
                    notes=["现价 100 元"],
                )
            ],
            warnings=["出发前核对天气"],
        )
        validation = validate_trip_plan(plan)

        result = repair_trip_plan_once(plan, validation)

        self.assertIn("UNVERIFIED_REALTIME_CLAIM", result.resolved_issue_codes)
        self.assertEqual(result.plan.activities[0].data_type, "estimated_data")
        self.assertIsNone(result.plan.activities[0].place.lat)
        self.assertIsNone(result.plan.activities[0].place.lng)
        self.assertIsNone(result.plan.activities[0].estimated_cost)
        self.assertFalse(any("100" in note for note in result.plan.activities[0].notes))

    def test_unsupported_issue_remains_and_is_not_attempted(self):
        plan = self._plan([_activity("未知地点", lat=None, lng=None)], warnings=["天气提醒"])
        validation = validate_trip_plan(plan)

        result = repair_trip_plan_once(plan, validation)

        self.assertNotIn("MISSING_COORDINATES", result.attempted_issue_codes)
        self.assertIn(
            "MISSING_COORDINATES",
            [issue.code for issue in result.remaining_validation.issues],
        )

    def test_balances_overloaded_day_when_another_day_has_capacity(self):
        activities = [_activity(f"第一天地点{i}", day=1) for i in range(5)]
        activities.append(_activity("第二天酒店", day=2, notes=["返程前核对天气"]))
        plan = self._plan(activities, days=2, pace="relaxed")
        validation = validate_trip_plan(plan)

        result = repair_trip_plan_once(plan, validation)

        self.assertIn("DAY_OVERLOADED", result.resolved_issue_codes)
        self.assertEqual(sum(item.day == 1 for item in result.plan.activities), 4)
        self.assertEqual(sum(item.day == 2 for item in result.plan.activities), 2)

    def test_reorders_route_only_when_coordinate_distance_improves(self):
        plan = self._plan(
            [
                _activity("A", lat=0.0, lng=0.0, transport_to_next="公共交通"),
                _activity("C", lat=0.0, lng=0.5, transport_to_next="公共交通"),
                _activity("B", lat=0.0, lng=0.25),
            ],
            warnings=["天气提醒"],
        )
        validation = validate_trip_plan(plan)

        result = repair_trip_plan_once(plan, validation)

        self.assertIn("POSSIBLE_ROUTE_BACKTRACK", result.resolved_issue_codes)
        self.assertEqual([item.title for item in result.plan.activities], ["A", "B", "C"])

    def test_empty_plan_cannot_fabricate_return_transport(self):
        plan = self._plan([], days=2, warnings=["天气提醒"])
        validation = validate_trip_plan(plan)

        result = repair_trip_plan_once(plan, validation)

        self.assertFalse(result.repaired)
        self.assertIn("MISSING_RETURN_TRANSPORT", result.attempted_issue_codes)
        self.assertNotIn("MISSING_RETURN_TRANSPORT", result.resolved_issue_codes)


if __name__ == "__main__":
    unittest.main()
