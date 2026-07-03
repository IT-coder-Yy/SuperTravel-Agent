import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from schemas.api_models import ChatMessage, ChatRequest, ConfigRequest, SkillInfo, SystemStatus, PowerPaintStatus


class ApiModelsContractTests(unittest.TestCase):
    def test_chat_request_defaults_are_stable(self):
        request = ChatRequest(messages=[ChatMessage(role="user", content="hello")])

        self.assertEqual(request.type, "chat")
        self.assertEqual(request.use_deepthink, True)
        self.assertEqual(request.use_multi_agent, True)
        self.assertEqual(request.session_id, None)
        self.assertEqual(request.selected_mcp_servers, [])
        self.assertEqual(request.selected_skill_ids, [])

        another = ChatRequest(messages=[ChatMessage(role="user", content="world")])
        self.assertIsNot(request.selected_mcp_servers, another.selected_mcp_servers)
        self.assertIsNot(request.selected_skill_ids, another.selected_skill_ids)

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
