import unittest
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from backend.schemas.trip_models import TripActivity, TripIntent, TripPlace, TripPlan
from backend.services.meal_schedule_service import apply_meal_schedule, meal_targets_by_day
from services.trip_plan_validator import validate_trip_plan


def _food(activity_id: str, title: str, *, opening_hours: str | None = None, verified: bool = True) -> TripActivity:
    return TripActivity(
        activity_id=activity_id,
        day=1,
        title=title,
        activity_type="food",
        place=TripPlace(
            poi_id=activity_id if verified else None,
            name=title,
            category="餐厅",
            lat=30.2 if verified else None,
            lng=120.1 if verified else None,
            opening_hours=opening_hours,
        ),
    )


class MealScheduleServiceTests(unittest.TestCase):
    def test_one_two_three_flexible_days_all_have_lunch_and_dinner(self):
        for days in (1, 2, 3):
            with self.subTest(days=days):
                self.assertEqual(
                    {day: ["lunch", "dinner"] for day in range(1, days + 1)},
                    meal_targets_by_day(days=days, date_mode="flexible"),
                )

    def test_fixed_dates_use_confirmed_arrival_and_departure_windows(self):
        targets = meal_targets_by_day(
            days=3,
            date_mode="fixed",
            outbound_arrival="2026-08-01 15:00",
            return_departure="2026-08-03 12:00",
        )

        self.assertEqual({1: ["dinner"], 2: ["lunch", "dinner"], 3: []}, targets)

    def test_flexible_dates_keep_reference_day_meals(self):
        targets = meal_targets_by_day(
            days=4,
            date_mode="flexible",
            breakfast_requested=True,
        )

        self.assertEqual(["breakfast", "lunch", "dinner"], targets[1])
        self.assertEqual(["breakfast", "lunch", "dinner"], targets[2])
        self.assertEqual(["breakfast", "lunch", "dinner"], targets[3])
        self.assertEqual(["breakfast", "lunch", "dinner"], targets[4])

    def test_fixed_dates_without_confirmed_transport_keep_full_day_meals(self):
        targets = meal_targets_by_day(days=3, date_mode="fixed")

        self.assertEqual(
            {1: ["lunch", "dinner"], 2: ["lunch", "dinner"], 3: ["lunch", "dinner"]},
            targets,
        )

    def test_formal_schedule_keeps_only_verified_pois_and_uses_meal_windows(self):
        lunch = _food("lunch", "西湖午餐", opening_hours="11:00-14:00")
        dinner = _food("dinner", "西湖晚餐", opening_hours="17:00-21:00")
        unverified = _food("draft", "未核验餐厅", verified=False)
        attraction = TripActivity(
            activity_id="west-lake",
            day=1,
            start_time="09:00",
            title="西湖",
            activity_type="attraction",
            place=TripPlace(poi_id="west-lake", name="西湖", category="景点", lat=30.2, lng=120.1),
        )
        plan = TripPlan(
            title="杭州一日游",
            intent=TripIntent(destination="杭州", days=1),
            days=1,
            activities=[dinner, unverified, attraction, lunch],
        )

        warnings = apply_meal_schedule(
            plan,
            targets_by_day={1: ["lunch", "dinner"]},
            domestic=True,
        )

        self.assertIn("未核验餐厅", " ".join(warnings))
        self.assertEqual(["西湖", "西湖午餐", "西湖晚餐"], [activity.title for activity in plan.activities])
        self.assertEqual(["lunch", "dinner"], [activity.meal_type for activity in plan.activities[1:]])
        self.assertEqual(["11:30", "17:30"], [activity.start_time for activity in plan.activities[1:]])

    def test_international_windows_are_not_domestic_lunch_defaults(self):
        lunch = _food("tokyo-lunch", "东京午餐", opening_hours="11:00-15:00")
        plan = TripPlan(
            title="东京一日游",
            intent=TripIntent(destination="东京", days=1),
            days=1,
            activities=[lunch],
        )

        apply_meal_schedule(plan, targets_by_day={1: ["lunch"]}, domestic=False)

        self.assertEqual("12:00", plan.activities[0].start_time)

    def test_missing_required_meal_warning_blocks_formal_validation(self):
        plan = TripPlan(
            title="东京一日游",
            intent=TripIntent(destination="东京", days=1),
            days=1,
            activities=[],
            warnings=["第1天缺少可核验的午餐地点，未强行补入餐饮活动"],
        )

        validation = validate_trip_plan(plan)

        self.assertFalse(validation.valid)
        issue = next(item for item in validation.issues if item.code == "MISSING_REQUIRED_MEAL")
        self.assertEqual("error", issue.severity)


if __name__ == "__main__":
    unittest.main()
