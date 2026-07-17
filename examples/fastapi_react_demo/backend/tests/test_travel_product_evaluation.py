import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
for path in (BACKEND_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from schemas.trip_models import TripActivity, TripIntent, TripPlace, TripPlan
from services.clarification_service import build_clarification_questions
from services.poi_detail_service import normalize_poi_detail
from services.text_sanitizer_service import sanitize_user_visible_text
from services.trip_intent_service import extract_trip_intent
from services.trip_plan_validator import validate_trip_plan


SCENARIOS = (
    ("北京特种兵", "从上海出发，下周帮我规划北京3天特种兵之旅，朋友2人，人均2000", "北京", "朋友", 2000),
    ("杭州亲子", "从南京出发，国庆帮我规划杭州3天亲子慢旅行，3个人，总预算5000", "杭州", "亲子", None),
    ("巴黎情侣", "从上海出发，下周去Paris玩4天，情侣2人，人均8000", "巴黎", "情侣", 8000),
    ("东京独行", "从北京出发，下周去Tokyo旅行5天，一个人，预算10000", "东京", "独行", None),
    ("未知城市", "从上海出发，下周去雷克雅未克旅行4天，一个人，预算12000", "雷克雅未克", "独行", None),
)


class TravelProductEvaluationTests(unittest.TestCase):
    def test_city_persona_and_budget_scenario_matrix(self):
        for name, query, destination, people_type, per_person in SCENARIOS:
            with self.subTest(name=name):
                intent = extract_trip_intent(query)
                self.assertEqual(intent.destination, destination)
                self.assertEqual(intent.people_type, people_type)
                self.assertEqual(intent.budget_per_person, per_person)
                self.assertLessEqual(len(build_clarification_questions(query, intent)), 1)

    def test_missing_sources_and_bad_routes_are_visible_not_fabricated(self):
        poi = normalize_poi_detail({"name": "待核验地点", "category": "景点"})
        self.assertEqual(poi["sources"], [])
        self.assertIsNone(poi["opening_hours"])
        self.assertIsNone(poi["field_evidence"]["opening_hours"]["updated_at"])

        plan = TripPlan(
            title="路线评测",
            intent=TripIntent(destination="杭州", days=1),
            days=1,
            activities=[
                TripActivity(day=1, title="西湖", place=TripPlace(name="西湖", category="景点", lat=30.24, lng=120.15), transport_to_next="打车"),
                TripActivity(day=1, title="上海博物馆", place=TripPlace(name="上海博物馆", category="景点", city="上海", lat=31.23, lng=121.47)),
            ],
        )
        codes = {issue.code for issue in validate_trip_plan(plan).issues}
        self.assertIn("CITY_MISMATCH", codes)
        self.assertIn("POSSIBLE_ROUTE_BACKTRACK", codes)

    def test_privacy_leak_regression_set(self):
        fake_token = "sk-" + "abcdefghijklmnop"
        raw = f"serper_site_search filesystem 12306 C:\\private\\prompt.json {fake_token}"
        cleaned = sanitize_user_visible_text(raw)
        for forbidden in ("serper_site_search", "filesystem", "12306", "private", fake_token):
            self.assertNotIn(forbidden, cleaned)


if __name__ == "__main__":
    unittest.main()
