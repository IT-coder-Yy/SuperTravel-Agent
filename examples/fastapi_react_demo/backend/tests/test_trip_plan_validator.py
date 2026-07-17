import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from schemas.trip_models import TripActivity, TripIntent, TripPlace, TripPlan
from services.trip_plan_validator import validate_trip_plan


def _activity(
    title: str,
    day: int = 1,
    lat: float = 39.9,
    lng: float = 116.4,
    category: str = "景点",
    cost: float = 0,
    transport_to_next: str = "",
    notes=None,
) -> TripActivity:
    return TripActivity(
        day=day,
        title=title,
        place=TripPlace(name=title, category=category, lat=lat, lng=lng, source="test"),
        estimated_cost=cost,
        transport_to_next=transport_to_next or None,
        notes=notes or [],
    )


class TripPlanValidatorTests(unittest.TestCase):
    def _plan(self, activities, intent=None, days=1):
        return TripPlan(
            title="测试行程",
            intent=intent or TripIntent(destination="北京", days=days),
            days=days,
            activities=activities,
        )

    def test_flags_overloaded_day_by_pace(self):
        intent = TripIntent(destination="北京", days=1, pace="relaxed")
        plan = self._plan([_activity(f"地点{i}") for i in range(5)], intent=intent)

        result = validate_trip_plan(plan)

        self.assertIn("DAY_OVERLOADED", [issue.code for issue in result.issues])
        self.assertTrue(result.valid)

    def test_preserves_all_three_data_type_classifications(self):
        classifications = (
            "confirmed_live_data",
            "reference_data",
            "estimated_data",
        )

        for classification in classifications:
            with self.subTest(classification=classification):
                plan = TripPlan(
                    title="可信度分类测试",
                    intent=TripIntent(destination="北京", days=1),
                    days=1,
                    activities=[
                        TripActivity(
                            day=1,
                            title="故宫",
                            place=TripPlace(
                                name="故宫",
                                category="景点",
                                lat=39.9241,
                                lng=116.4034,
                            ),
                            notes=["出发前核对天气。"],
                            data_type=classification,
                        )
                    ],
                    source_references=[
                        {
                            "type": "web",
                            "title": "参考来源",
                            "data_type": classification,
                        }
                    ],
                )

                result = validate_trip_plan(plan)

                self.assertTrue(result.valid)
                dumped = plan.model_dump()
                self.assertEqual(plan.activities[0].data_type, classification)
                self.assertEqual(plan.source_references[0].data_type, classification)
                self.assertEqual(dumped["activities"][0]["data_type"], classification)
                self.assertEqual(dumped["source_references"][0]["data_type"], classification)
                self.assertNotIn("data_confidence", dumped["activities"][0])
                self.assertNotIn("data_confidence", dumped["source_references"][0])

    def test_flags_missing_coordinates(self):
        activity = TripActivity(
            day=1,
            title="未核验地点",
            place=TripPlace(name="未核验地点", category="景点"),
        )
        plan = self._plan([activity])

        result = validate_trip_plan(plan)

        self.assertIn("MISSING_COORDINATES", [issue.code for issue in result.issues])

    def test_flags_budget_exceeded(self):
        intent = TripIntent(destination="北京", days=1, budget_total=100)
        plan = self._plan([_activity("收费项目", cost=180)], intent=intent)

        result = validate_trip_plan(plan)

        self.assertIn("BUDGET_EXCEEDED", [issue.code for issue in result.issues])

    def test_realtime_or_booking_claim_is_error(self):
        plan = self._plan([_activity("已预订酒店，保证有票", category="酒店")])

        result = validate_trip_plan(plan)

        self.assertFalse(result.valid)
        self.assertIn("UNVERIFIED_REALTIME_CLAIM", [issue.code for issue in result.issues])

    def test_unverified_opening_hours_train_and_ticket_price_are_errors(self):
        for title in (
            "故宫开放时间为 08:30-17:00",
            "建议乘坐车次 G1234",
            "景区门票 120 元",
        ):
            with self.subTest(title=title):
                plan = self._plan([_activity(title)])

                result = validate_trip_plan(plan)

                self.assertFalse(result.valid)
                self.assertIn("UNVERIFIED_REALTIME_CLAIM", [issue.code for issue in result.issues])

    def test_confirmed_or_disclaimed_realtime_facts_are_not_errors(self):
        confirmed = TripActivity(
            day=1,
            title="实时确认车次 G1234",
            notes=["出发前核对天气。"],
            data_type="confirmed_live_data",
        )
        referenced = TripActivity(
            day=1,
            title="参考门票 120 元，仅供参考，需以官方为准",
            notes=["出发前核对天气。"],
            data_type="reference_data",
        )

        for activity in (confirmed, referenced):
            with self.subTest(title=activity.title):
                result = validate_trip_plan(self._plan([activity]))

                self.assertNotIn("UNVERIFIED_REALTIME_CLAIM", [issue.code for issue in result.issues])

    def test_multiday_plan_needs_hotel_signal(self):
        plan = self._plan(
            [
                _activity("故宫博物院", day=1),
                _activity("颐和园", day=2),
            ],
            intent=TripIntent(destination="北京", days=2),
            days=2,
        )

        result = validate_trip_plan(plan)

        self.assertIn("MISSING_HOTEL_AREA", [issue.code for issue in result.issues])

    def test_multiday_plan_with_hotel_signal_passes_hotel_check(self):
        plan = self._plan(
            [
                _activity("王府井附近酒店", day=1, category="酒店"),
                _activity("故宫博物院", day=2),
            ],
            intent=TripIntent(destination="北京", days=2, budget_total=1000),
            days=2,
        )

        result = validate_trip_plan(plan)

        self.assertTrue(result.valid)
        self.assertNotIn("MISSING_HOTEL_AREA", [issue.code for issue in result.issues])

    def test_flags_hotel_route_mismatch_when_most_visits_are_over_40km_away(self):
        plan = self._plan(
            [
                _activity(
                    "王府井酒店",
                    lat=39.9087,
                    lng=116.4180,
                    category="酒店",
                    transport_to_next="公共交通",
                    notes=["出发前核对天气。"],
                ),
                _activity(
                    "八达岭长城",
                    lat=40.3626,
                    lng=116.0241,
                    transport_to_next="公共交通",
                ),
                _activity(
                    "天津古文化街",
                    lat=39.1426,
                    lng=117.1965,
                    transport_to_next="公共交通",
                ),
                _activity("故宫博物院", lat=39.9241, lng=116.4034),
            ]
        )

        result = validate_trip_plan(plan)

        issues = {issue.code: issue for issue in result.issues}
        self.assertIn("HOTEL_ROUTE_MISMATCH", issues)
        self.assertIn("2个主要地点", issues["HOTEL_ROUTE_MISMATCH"].message)
        self.assertIn("超过40公里", issues["HOTEL_ROUTE_MISMATCH"].message)

    def test_flags_possible_route_backtrack_for_long_jumps(self):
        plan = self._plan(
            [
                _activity("故宫博物院", lat=39.9241, lng=116.4034, transport_to_next="打车"),
                _activity("八达岭长城", lat=40.3626, lng=116.0241),
            ],
            intent=TripIntent(destination="北京", days=1),
        )

        result = validate_trip_plan(plan)

        self.assertIn("POSSIBLE_ROUTE_BACKTRACK", [issue.code for issue in result.issues])

    def test_flags_missing_return_transport_for_multiday_plan(self):
        plan = self._plan(
            [
                _activity("王府井附近酒店", day=1, category="酒店", notes=["出发前核对天气。"]),
                _activity("故宫博物院", day=2),
            ],
            intent=TripIntent(destination="北京", days=2),
            days=2,
        )

        result = validate_trip_plan(plan)

        self.assertIn("MISSING_RETURN_TRANSPORT", [issue.code for issue in result.issues])

    def test_flags_missing_weather_reminder(self):
        plan = self._plan([_activity("故宫博物院")])

        result = validate_trip_plan(plan)

        self.assertIn("MISSING_WEATHER_REMINDER", [issue.code for issue in result.issues])

    def test_weather_and_return_reminders_can_be_satisfied_by_notes(self):
        plan = self._plan(
            [
                _activity("王府井附近酒店", day=1, category="酒店", notes=["出发前核对天气。"]),
                _activity("故宫博物院", day=2, notes=["晚上返程回到出发城市。"]),
            ],
            intent=TripIntent(destination="北京", days=2),
            days=2,
        )

        result = validate_trip_plan(plan)
        codes = [issue.code for issue in result.issues]

        self.assertNotIn("MISSING_WEATHER_REMINDER", codes)
        self.assertNotIn("MISSING_RETURN_TRANSPORT", codes)

    def test_flags_invalid_duplicate_and_misclassified_pois(self):
        activities = [
            _activity("规划道路"),
            _activity("西湖餐厅", category="景点"),
            _activity("西湖餐厅", category="景点"),
            _activity("湖滨酒店", category="景点"),
        ]
        result = validate_trip_plan(self._plan(activities, intent=TripIntent(destination="杭州", days=1)))
        codes = {issue.code for issue in result.issues}

        self.assertIn("INVALID_POI", codes)
        self.assertIn("DUPLICATE_POI", codes)
        self.assertIn("POI_TYPE_MISMATCH", codes)

    def test_flags_explicit_cross_city_location(self):
        activity = TripActivity(
            day=1,
            title="异地景点",
            place=TripPlace(name="故宫博物院", category="景点", city="北京", lat=39.92, lng=116.40),
            notes=["出发前核对天气。"],
        )
        result = validate_trip_plan(self._plan([activity], intent=TripIntent(destination="杭州", days=1)))

        self.assertIn("CITY_MISMATCH", {issue.code for issue in result.issues})
        self.assertFalse(result.valid)

    def test_flags_missing_classic_place_and_user_preferences(self):
        intent = TripIntent(destination="杭州", days=1, must_visit=["西湖风景名胜区"], interests=["摄影"])
        result = validate_trip_plan(self._plan([_activity("普通商场")], intent=intent))
        codes = {issue.code for issue in result.issues}

        self.assertIn("MISSING_CLASSIC_POI", codes)
        self.assertIn("PREFERENCE_MISMATCH", codes)


if __name__ == "__main__":
    unittest.main()
