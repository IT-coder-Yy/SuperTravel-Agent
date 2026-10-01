import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from services import skill_profile_service


class FakeToolManager:
    def __init__(self, tool_names):
        self._tool_names = tool_names

    def list_tools_simplified(self):
        return [{"name": name, "description": name} for name in self._tool_names]


def build_app_config():
    return SimpleNamespace(
        mcp=SimpleNamespace(
            servers={
                "12306-mcp": SimpleNamespace(disabled=False),
                "baidu-map": SimpleNamespace(disabled=False),
                "xhs-mcp": SimpleNamespace(disabled=False),
                "serper_web_search": SimpleNamespace(disabled=False),
                "fetch": SimpleNamespace(disabled=False),
            }
        )
    )


class SkillProfileServiceTests(unittest.TestCase):
    def test_builtin_profiles_are_stable(self):
        profiles = skill_profile_service.get_builtin_skill_profiles()

        self.assertEqual(len(profiles), 7)
        self.assertEqual(
            [profile.id for profile in profiles],
            [
                "travel_planner",
                "rail_transport",
                "map_route",
                "xhs_insight",
                "destination_research",
                "budget_optimizer",
                "local_discovery",
            ],
        )

    def test_resolve_skill_profiles_filters_invalid_ids_and_preserves_order(self):
        profiles = skill_profile_service.resolve_skill_profiles(
            ["xhs_insight", "missing", "rail_transport", "xhs_insight"]
        )

        self.assertEqual([profile.id for profile in profiles], ["xhs_insight", "rail_transport"])

    def test_build_skill_system_message_merges_prompts_and_workflows(self):
        message = skill_profile_service.build_skill_system_message(["rail_transport", "budget_optimizer"])

        self.assertEqual(message["role"], "system")
        self.assertEqual(message["type"], "system_skill_profile")
        self.assertIn("交通票务查询", message["content"])
        self.assertIn("预算优化", message["content"])
        self.assertIn("query_12306_realtime_tickets", message["content"])
        self.assertIn("calculate", message["content"])

    def test_merge_skill_mcp_servers_combines_user_and_skill_scope(self):
        merged = skill_profile_service.merge_skill_mcp_servers(
            selected_mcp_servers=["fetch"],
            selected_skill_ids=["rail_transport", "map_route"],
        )

        self.assertEqual(merged, ["fetch", "12306-mcp", "amap-maps", "baidu-map"])

    def test_collect_skill_local_tools_returns_deduped_tools(self):
        tools = skill_profile_service.collect_skill_local_tools(
            ["rail_transport", "budget_optimizer", "rail_transport"]
        )

        self.assertEqual(
            tools,
            ["query_12306_realtime_tickets", "query_12306_tickets_by_query", "calculate"],
        )

    def test_list_skill_infos_marks_missing_dependencies(self):
        infos = skill_profile_service.list_skill_infos(
            tool_manager=FakeToolManager(["calculate"]),
            app_config=build_app_config(),
        )
        by_id = {info["id"]: info for info in infos}

        self.assertFalse(by_id["rail_transport"]["available"])
        self.assertEqual(
            by_id["rail_transport"]["missing_local_tools"],
            ["query_12306_realtime_tickets", "query_12306_tickets_by_query"],
        )
        self.assertTrue(by_id["budget_optimizer"]["available"])

    def test_map_skill_accepts_either_amap_or_baidu_provider(self):
        app_config = SimpleNamespace(
            mcp=SimpleNamespace(
                servers={
                    "amap-maps": SimpleNamespace(disabled=False),
                    "baidu-map": SimpleNamespace(disabled=True),
                }
            )
        )

        infos = skill_profile_service.list_skill_infos(
            tool_manager=FakeToolManager(["maps_text_search"]),
            app_config=app_config,
        )
        by_id = {info["id"]: info for info in infos}

        self.assertTrue(by_id["map_route"]["available"])
        self.assertIn("baidu-map", by_id["map_route"]["missing_mcp_servers"])

    def test_destination_research_accepts_tavily_as_search_provider(self):
        infos = skill_profile_service.list_skill_infos(
            tool_manager=FakeToolManager(["tavily_search"]),
            app_config=build_app_config(),
        )
        by_id = {info["id"]: info for info in infos}

        self.assertTrue(by_id["destination_research"]["available"])


if __name__ == "__main__":
    unittest.main()
