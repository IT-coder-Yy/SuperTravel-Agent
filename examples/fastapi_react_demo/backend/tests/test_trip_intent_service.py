import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from services.trip_intent_service import extract_trip_intent, is_trip_planning_query, merge_semantic_trip_analysis


class TripIntentServiceTests(unittest.TestCase):
    def test_extracts_basic_trip_intent(self):
        intent = extract_trip_intent("下周从上海出发，帮我规划杭州三日游，人均2000，轻松一点")

        self.assertEqual(intent.origin, "上海")
        self.assertEqual(intent.destination, "杭州")
        self.assertEqual(intent.date_range, "下周")
        self.assertEqual(intent.days, 3)
        self.assertEqual(intent.budget_per_person, 2000)
        self.assertEqual(intent.pace, "relaxed")

    def test_calendar_day_is_not_mistaken_for_trip_duration(self):
        intent = extract_trip_intent("我从上海出发，8月10日去北京玩3天，两个人，预算5000元")

        self.assertEqual(intent.date_range, "8月10日")
        self.assertEqual(intent.days, 3)

    def test_profile_fills_missing_defaults(self):
        intent = extract_trip_intent(
            "帮我规划杭州三日游",
            profile={
                "default_people_type": "情侣",
                "pace": "轻松",
                "preferred_budget_level": "标准",
            },
        )

        self.assertEqual(intent.people_type, "情侣")
        self.assertEqual(intent.pace, "relaxed")

    def test_profile_ignores_blank_scalar_and_list_preferences(self):
        query = "帮我规划杭州三日游"
        baseline_intent = extract_trip_intent(query)
        intent = extract_trip_intent(
            query,
            profile={
                "default_people_type": "   ",
                "pace": "",
                "preferred_budget_level": " ",
                "travel_style": ["", "  "],
                "dietary_preferences": ["", "  "],
                "hotel_preference": " ",
                "transport_preference": "",
                "disliked_items": ["", "  "],
            },
        )

        self.assertIsNone(intent.people_type)
        self.assertIsNone(intent.pace)
        self.assertIsNone(intent.travel_style)
        self.assertEqual(intent.dietary_preferences, [])
        self.assertIsNone(intent.hotel_preference)
        self.assertIsNone(intent.transport_preference)
        self.assertEqual(intent.avoid, [])
        self.assertEqual(intent.confidence, baseline_intent.confidence)

    def test_empty_or_disabled_profile_does_not_create_people_budget_or_pace_defaults(self):
        query = "帮我规划杭州三日游"

        for profile in (None, {}):
            with self.subTest(profile=profile):
                intent = extract_trip_intent(query, profile=profile)

                self.assertIsNone(intent.people_count)
                self.assertIsNone(intent.people_type)
                self.assertIsNone(intent.budget_total)
                self.assertIsNone(intent.budget_per_person)
                self.assertIsNone(intent.pace)
                self.assertIsNone(intent.travel_style)

    def test_explicit_current_request_overrides_conflicting_profile_defaults(self):
        intent = extract_trip_intent(
            "和朋友两个人去杭州三天，人均3000，安排紧凑一点",
            profile={
                "default_people_type": "亲子",
                "preferred_budget_level": "经济",
                "pace": "轻松",
                "travel_style": ["慢旅行"],
            },
        )

        self.assertEqual(intent.people_count, 2)
        self.assertEqual(intent.people_type, "朋友")
        self.assertEqual(intent.budget_per_person, 3000)
        self.assertEqual(intent.pace, "intensive")

    def test_hotel_recommendation_is_not_forced_into_clarification(self):
        self.assertFalse(is_trip_planning_query("推荐上海外滩附近性价比高的酒店"))

    def test_semantic_analysis_only_merges_fields_with_user_evidence(self):
        intent = extract_trip_intent("帮我规划北京三日游，我们仨姐妹一起")

        merged, explicit_fields, preferred_question = merge_semantic_trip_analysis(
            intent,
            {
                "intent_patch": {
                    "people_type": "朋友",
                    "budget_total": 9999,
                },
                "evidence": {
                    "people_type": "我们仨姐妹",
                    "budget_total": "用户没有说过的预算",
                },
                "next_question": {
                    "field": "origin",
                    "question": "你从哪里出发？",
                    "reason": "用于确定往返交通。",
                    "options": ["北京本地", "其他城市"],
                    "allow_custom": True,
                },
            },
            "帮我规划北京三日游，我们仨姐妹一起",
        )

        self.assertEqual(merged.people_type, "朋友")
        self.assertIsNone(merged.budget_total)
        self.assertEqual(explicit_fields, {"people_type"})
        self.assertIsNotNone(preferred_question)
        self.assertEqual(preferred_question.field, "origin")


if __name__ == "__main__":
    unittest.main()
