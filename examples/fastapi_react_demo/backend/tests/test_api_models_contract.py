import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from pydantic import ValidationError

from schemas.api_models import (
    ChatMessage,
    ChatRequest,
    ConfigRequest,
    PowerPaintStatus,
    SkillInfo,
    SystemStatus,
    TripEditRequest,
)


class ApiModelsContractTests(unittest.TestCase):
    def test_trip_edit_accepts_document_only_and_rejects_missing_fact_source(self):
        request = TripEditRequest(
            document={"schema_version": "3.0"},
            operation={
                "operation_id": "op-document-only",
                "plan_id": "plan-v3",
                "base_version": 1,
                "type": "shift_activity_time",
                "payload": {"activity_id": "a1", "delta_minutes": 15},
            },
        )

        self.assertIsNone(request.plan)
        self.assertEqual("3.0", request.document["schema_version"])
        with self.assertRaises(ValidationError):
            TripEditRequest(
                operation={
                    "operation_id": "op-missing",
                    "plan_id": "plan-v3",
                    "base_version": 1,
                    "type": "shift_activity_time",
                    "payload": {},
                }
            )

    def test_chat_request_defaults_are_stable(self):
        request = ChatRequest(messages=[ChatMessage(role="user", content="hello")])

        self.assertEqual(request.type, "chat")
        self.assertEqual(request.use_deepthink, True)
        self.assertEqual(request.use_multi_agent, True)
        self.assertEqual(request.session_id, None)
        self.assertIsNone(request.selected_mcp_servers)
        self.assertEqual(request.selected_skill_ids, [])
        self.assertEqual(request.profile, {})
        self.assertEqual(request.planning_mode, None)
        self.assertEqual(request.allow_web_search, True)
        self.assertEqual(request.clarification_answers, {})
        self.assertIsNone(request.request_id)
        self.assertEqual(request.selected_knowledge_context, [])
        self.assertEqual(request.input_source, "natural_language")
        self.assertIsNone(request.structured_trip_request)

        another = ChatRequest(messages=[ChatMessage(role="user", content="world")])
        self.assertIsNone(another.selected_mcp_servers)
        self.assertIsNot(request.selected_skill_ids, another.selected_skill_ids)
        self.assertIsNot(request.profile, another.profile)
        self.assertIsNot(request.clarification_answers, another.clarification_answers)
        self.assertIsNot(request.selected_knowledge_context, another.selected_knowledge_context)

    def test_chat_request_accepts_disabling_web_search(self):
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="hello")],
            allow_web_search=False,
        )

        self.assertFalse(request.allow_web_search)

    def test_chat_request_accepts_a_complete_structured_trip(self):
        start_date = date.today() + timedelta(days=30)
        end_date = start_date + timedelta(days=2)
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="请规划杭州三日游")],
            input_source="structured_form",
            structured_trip_request={
                "origin": "上海",
                "destination": "杭州",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "adults": 2,
                "children": 0,
                "seniors": 0,
                "budget": 6000,
                "party_type": "情侣",
                "preferences": ["人文历史", "当地美食"],
            },
        )

        self.assertEqual(request.structured_trip_request.destination, "杭州")
        self.assertEqual(request.structured_trip_request.party_type, "情侣")

    def test_structured_source_requires_a_valid_structured_trip(self):
        with self.assertRaises(ValidationError):
            ChatRequest(
                messages=[ChatMessage(role="user", content="请规划行程")],
                input_source="structured_form",
            )

        with self.assertRaises(ValidationError):
            ChatRequest(
                messages=[ChatMessage(role="user", content="请规划行程")],
                input_source="structured_form",
                structured_trip_request={
                    "origin": "杭州",
                    "destination": "杭州",
                    "start_date": "2026-08-15",
                    "end_date": "2026-08-17",
                    "adults": 0,
                    "children": 0,
                    "seniors": 0,
                    "budget": 6000,
                },
            )

    def test_config_request_defaults_are_stable(self):
        config = ConfigRequest(api_key="k-1")

        self.assertEqual(config.model_name, "deepseek-chat")
        self.assertEqual(config.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(config.max_tokens, 4096)
        self.assertEqual(config.temperature, 0.7)

    def test_system_status_default_version_is_stable(self):
        status = SystemStatus(
            status="running",
            agents_count=7,
            tools_count=2,
            active_sessions=1,
        )

        self.assertEqual(status.version, "0.8")

    def test_powerpaint_status_contract_fields_are_stable(self):
        status = PowerPaintStatus(
            url="http://localhost:7860",
            reachable=True,
            message="PowerPaint 服务可访问。",
        )

        self.assertEqual(status.url, "http://localhost:7860")
        self.assertEqual(status.reachable, True)
        self.assertEqual(status.message, "PowerPaint 服务可访问。")

    def test_skill_info_contract_contains_runtime_availability(self):
        skill = SkillInfo(
            id="rail_transport",
            name="交通票务查询",
            description="查询火车票",
            workflow_steps=["识别路线", "查询票务"],
            allowed_mcp_servers=["12306-mcp"],
            allowed_local_tools=["query_12306_realtime_tickets"],
            answer_style="先结论后表格",
            fallback="实时数据不可用时说明无法确认",
            required_env=[],
            available=False,
            missing_mcp_servers=[],
            missing_local_tools=["query_12306_realtime_tickets"],
            missing_env=[],
        )

        self.assertEqual(skill.id, "rail_transport")
        self.assertFalse(skill.available)
        self.assertEqual(skill.missing_local_tools, ["query_12306_realtime_tickets"])


if __name__ == "__main__":
    unittest.main()
