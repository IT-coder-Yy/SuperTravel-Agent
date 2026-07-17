import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from services.clarification_service import (
    build_clarification_questions,
    count_clarification_fields,
)
from services.trip_intent_service import extract_trip_intent
from schemas.trip_models import (
    ClarificationQuestion,
    ClarificationRequest,
    TripIntent,
    UserTravelProfile,
)


class ClarificationServiceTests(unittest.TestCase):
    def test_builds_only_the_next_key_question_for_vague_plan(self):
        query = "帮我规划杭州三日游"
        intent = extract_trip_intent(query)

        questions = build_clarification_questions(query, intent)

        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].field, "origin")

    def test_profile_default_is_offered_as_actual_value_instead_of_silently_applied(self):
        query = "下周从上海出发，帮我规划杭州三日游"
        intent = extract_trip_intent(query)

        questions = build_clarification_questions(
            query,
            intent,
            profile=UserTravelProfile(
                pace="轻松",
                default_people_type="情侣",
                preferred_budget_level="标准",
            ),
        )
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].field, "people_type")
        self.assertEqual(questions[0].profile_default_value, "情侣")
        self.assertEqual(questions[0].skip_value, "__skip__")

    def test_synonymous_fields_count_once_and_are_not_reasked(self):
        query = "下周从上海出发，帮我规划北京三日游"
        intent = extract_trip_intent(query)

        questions = build_clarification_questions(
            query,
            intent,
            answered_fields={"people_count", "people_type"},
        )

        self.assertEqual(count_clarification_fields({"people_count", "people_type"}), 1)
        self.assertEqual(count_clarification_fields({"budget_total", "budget_per_person"}), 1)
        self.assertEqual(count_clarification_fields({"pace", "travel_style"}), 1)
        self.assertEqual(questions[0].field, "budget_total")

    def test_clarification_request_schema_requires_exactly_one_question(self):
        question = ClarificationQuestion(
            id="q_origin",
            field="origin",
            question="从哪里出发？",
            reason="用于规划往返交通。",
            options=["上海", "杭州"],
        )

        request = ClarificationRequest(
            session_id="session-1",
            intent=TripIntent(destination="北京"),
            questions=[question],
        )

        self.assertEqual(len(request.questions), 1)
        question_schema = ClarificationRequest.model_json_schema()["properties"]["questions"]
        self.assertEqual(question_schema["minItems"], 1)
        self.assertEqual(question_schema["maxItems"], 1)
        with self.assertRaises(ValidationError):
            ClarificationRequest(session_id="session-1", intent=TripIntent(), questions=[])
        with self.assertRaises(ValidationError):
            ClarificationRequest(
                session_id="session-1",
                intent=TripIntent(),
                questions=[question, question],
            )

    def test_beijing_special_forces_trip_asks_missing_execution_fields(self):
        query = "帮我规划一次北京3天2夜的特种兵之旅"
        intent = extract_trip_intent(
            query,
            profile={
                "default_people_type": "情侣",
                "pace": "轻松",
                "preferred_budget_level": "标准",
            },
        )

        questions = build_clarification_questions(
            query,
            intent,
            profile=UserTravelProfile(
                pace="轻松",
                default_people_type="情侣",
                preferred_budget_level="标准",
            ),
        )
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].field, "origin")
        self.assertNotIn(questions[0].field, {"destination", "days", "pace"})

    def test_does_not_repeat_fields_already_stated_by_user(self):
        query = "我在上海，下周帮我规划北京3天2夜特种兵之旅，和朋友两个人，人均2000"
        intent = extract_trip_intent(query)

        questions = build_clarification_questions(query, intent)
        fields = [question.field for question in questions]

        self.assertNotIn("origin", fields)
        self.assertNotIn("date_range", fields)
        self.assertNotIn("days", fields)
        self.assertNotIn("people_type", fields)
        self.assertNotIn("budget_total", fields)

    def test_known_date_without_duration_still_asks_trip_length(self):
        query = "下周从上海出发，帮我规划北京旅行，和朋友一起，人均2000，轻松一点"
        intent = extract_trip_intent(query)

        questions = build_clarification_questions(query, intent)

        self.assertEqual([question.field for question in questions], ["days"])

    def test_user_can_explicitly_leave_budget_or_date_open(self):
        query = "上海本地出发，日期还没定，帮我规划北京3天亲子游，预算不限"
        intent = extract_trip_intent(query)

        questions = build_clarification_questions(query, intent)
        fields = [question.field for question in questions]

        self.assertNotIn("origin", fields)
        self.assertNotIn("date_range", fields)
        self.assertNotIn("budget_total", fields)
        self.assertNotIn("people_type", fields)

    def test_complete_request_needs_no_clarification(self):
        query = "下周从上海出发，帮我规划北京3天2夜特种兵之旅，和朋友两个人，人均2000"
        intent = extract_trip_intent(query)

        questions = build_clarification_questions(query, intent)

        self.assertEqual(questions, [])


if __name__ == "__main__":
    unittest.main()
