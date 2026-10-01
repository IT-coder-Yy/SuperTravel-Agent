import unittest
from datetime import date
from unittest.mock import patch

from backend.schemas.trip_models import TripActivity, TripIntent, TripPlace
from backend.services.budget_service import apply_reference_costs, calculate_budget_summary
from backend.services.exchange_rate_service import ExchangeRateQuote


class BudgetServiceTests(unittest.TestCase):
    def test_uses_verified_child_discount_and_conservative_senior_price(self):
        intent = TripIntent(
            people_count=3,
            adult_count=1,
            child_count=1,
            senior_count=1,
            budget_total=100,
        )
        activity = TripActivity(
            title="东京博物馆",
            day=1,
            activity_type="attraction",
            place=TripPlace(name="东京博物馆", category="景点"),
            estimated_cost=100,
            estimated_cost_currency="JPY",
            estimated_cost_cny_reference_amount=5,
            estimated_cost_exchange_rate_as_of="2026-07-28",
            estimated_cost_unit="per_traveler",
            official_traveler_prices={"child": 50},
        )

        result = calculate_budget_summary(intent, [activity])

        self.assertEqual(12.5, result["estimated_total"])
        self.assertEqual(12.5, result["categories"]["activities"])
        self.assertEqual(1, len(result["currency_breakdown"]["categories"]))
        category = result["currency_breakdown"]["categories"][0]
        self.assertEqual(250, category["amount"]["amount"])
        self.assertEqual(12.5, category["amount"]["cny_reference_amount"])
        child = next(item for item in result["currency_breakdown"]["traveler_costs"] if item["traveler_type"] == "child")
        senior = next(item for item in result["currency_breakdown"]["traveler_costs"] if item["traveler_type"] == "senior")
        self.assertEqual("official_discount_verified", child["pricing_status"])
        self.assertEqual("adult_price_assumed", senior["pricing_status"])
        self.assertIn("老人优惠未获官方确认，已按成人价估算", result["warnings"])

    def test_keeps_foreign_group_price_without_fabricating_cny_reference(self):
        intent = TripIntent(adult_count=2, child_count=0, senior_count=0, people_count=2)
        activity = TripActivity(
            title="海外住宿",
            day=1,
            activity_type="hotel",
            place=TripPlace(name="海外住宿", category="酒店"),
            estimated_cost=200,
            estimated_cost_currency="USD",
        )

        result = calculate_budget_summary(intent, [activity])

        self.assertIsNone(result["estimated_total"])
        self.assertEqual(200, result["currency_breakdown"]["categories"][0]["amount"]["amount"])
        self.assertEqual("USD", result["currency_breakdown"]["categories"][0]["amount"]["currency"])
        self.assertNotIn("cny_reference_amount", result["currency_breakdown"]["categories"][0]["amount"])
        self.assertIn("1 项外币费用缺少人民币参考价，保留原币且不计入人民币预算比较", result["warnings"])

    @patch("backend.services.budget_service.quote_currency_to_cny")
    def test_fills_missing_international_estimates_in_original_and_cny_currencies(self, quote_mock):
        quote_mock.return_value = ExchangeRateQuote(
            currency="JPY",
            cny_per_unit=0.05,
            as_of=date(2026, 8, 19),
        )
        activities = [
            TripActivity(
                title="东京景点",
                day=1,
                activity_type="attraction",
                place=TripPlace(name="东京景点", category="景点"),
            ),
            TripActivity(
                title="东京餐厅",
                day=1,
                activity_type="food",
                place=TripPlace(name="东京餐厅", category="餐厅"),
            ),
        ]

        warnings = apply_reference_costs(activities, destination_currency="JPY")
        result = calculate_budget_summary(TripIntent(people_count=1), activities)

        self.assertEqual(1200, activities[0].estimated_cost)
        self.assertEqual(1600, activities[1].estimated_cost)
        self.assertTrue(all(activity.estimated_cost_currency == "JPY" for activity in activities))
        self.assertTrue(all(activity.estimated_cost_cny_reference_amount is not None for activity in activities))
        self.assertEqual({"activities", "food"}, {
            item["category"] for item in result["currency_breakdown"]["categories"]
        })
        self.assertTrue(all(
            item["amount"]["currency"] == "JPY"
            and item["amount"]["cny_reference_amount"] > 0
            and item["amount"]["exchange_rate_as_of"] == "2026-08-19"
            for item in result["currency_breakdown"]["categories"]
        ))
        self.assertIn("欧洲央行", " ".join(warnings))


if __name__ == "__main__":
    unittest.main()
