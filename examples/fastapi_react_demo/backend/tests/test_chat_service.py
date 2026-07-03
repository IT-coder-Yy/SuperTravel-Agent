import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from services.chat_service import (
    build_message_history,
    execute_chat_once,
    maybe_prepare_travel_experience_bundle,
    maybe_prepare_train_ticket_bundle,
    maybe_prepare_xhs_search_bundle,
    _build_ticket_markdown_table,
    _build_travel_fallback_answer,
    _build_web_markdown_table,
    _build_xhs_markdown_table,
    _extract_route_from_query,
    _normalize_direct_rows,
    _normalize_interline_rows,
    _normalize_markdown_for_display,
    _normalize_web_rows,
    _normalize_xhs_rows,
    _run_tool_with_arg_candidates,
    _station_query_pairs,
    _is_travel_experience_query,
    _is_train_ticket_query,
    _is_xhs_query,
    _select_travel_final_answer,
    _travel_document_filename,
)


class FakeController:
    def __init__(self):
        self.called_with = None

    def run(self, messages, tool_manager, session_id, deep_thinking, summary, deep_research):
        self.called_with = {
            "messages": messages,
            "tool_manager": tool_manager,
            "session_id": session_id,
            "deep_thinking": deep_thinking,
            "summary": summary,
            "deep_research": deep_research,
        }
        return {"ok": True, "steps": 3}


class ChatServiceTests(unittest.TestCase):
    def test_is_xhs_query_supports_normal_guide_question(self):
        self.assertTrue(_is_xhs_query("杭州三日游攻略怎么安排"))
        self.assertFalse(_is_xhs_query("帮我算一下 123 * 456"))

    def test_is_travel_experience_query_covers_planning_hotel_and_food_but_not_tickets(self):
        self.assertTrue(_is_travel_experience_query("帮我做一个杭州三日游行程"))
        self.assertTrue(_is_travel_experience_query("帮我规划一次北京3天2夜的文化之旅"))
        self.assertTrue(_is_travel_experience_query("成都酒店住哪比较方便"))
        self.assertTrue(_is_travel_experience_query("西安有什么美食和小吃推荐"))
        self.assertFalse(_is_travel_experience_query("查询明天杭州到南京的票"))

    def test_train_ticket_query_supports_route_with_generic_ticket_wording(self):
        self.assertTrue(_is_train_ticket_query("查询明天杭州到南京的票"))
        self.assertFalse(_is_train_ticket_query("杭州博物馆门票怎么买"))

    def test_train_ticket_route_removes_date_and_generic_ticket_wording(self):
        self.assertEqual(_extract_route_from_query("查询明天杭州到南京的票"), ("杭州", "南京"))
        self.assertEqual(_extract_route_from_query("杭州到南京6月3日的票"), ("杭州", "南京"))

    def test_tool_arg_candidates_use_registered_schema_when_available(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                return SimpleNamespace(
                    parameters={"from_city": {}, "to_city": {}, "travel_date": {}, "max_results": {}},
                    required=["from_city", "to_city", "travel_date"],
                )

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append(kwargs)
                return {"error": True, "message": "网页未返回可解析结果"}

        tool_manager = FakeToolManager()
        payload = _run_tool_with_arg_candidates(
            tool_manager=tool_manager,
            tool_name="query_flight_tickets",
            message_history=[],
            session_id="s-schema",
            arg_candidates=[
                {"date": "2026-06-03", "fromCity": "广州", "toCity": "桂林"},
                {"travel_date": "2026-06-03", "from_city": "广州", "to_city": "桂林"},
                {"query": "2026-06-03 广州到桂林机票"},
            ],
        )

        self.assertEqual(payload["message"], "网页未返回可解析结果")
        self.assertEqual(
            tool_manager.calls,
            [{"travel_date": "2026-06-03", "from_city": "广州", "to_city": "桂林"}],
        )

    def test_web_rows_parse_mcp_json_string_result(self):
        payload = {
            "content": [
                {
                    "type": "text",
                    "text": "{\"results\":[{\"title\":\"上海外滩酒店推荐\",\"url\":\"https://example.com/hotel\",\"snippet\":\"外滩附近酒店可按预算筛选。\"}]}",
                }
            ]
        }

        rows = _normalize_web_rows(payload, max_rows=3)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "上海外滩酒店推荐")
        self.assertEqual(rows[0]["snippet"], "外滩附近酒店可按预算筛选。")

    def test_reference_tables_escape_pipes_and_shorten_links(self):
        web_table = _build_web_markdown_table(
            [
                {
                    "title": "Klook | 北京3天2晚文化美食之旅",
                    "snippet": "故宫|景山|颐和园，适合第一次到北京的用户。",
                    "url": "https://www.klook.com/zh-CN/activity/152447-3-day-trip-in-beijing-dress-up-in-the-imperial-consort-style-in-the/",
                    "source": "web",
                }
            ]
        )
        xhs_table = _build_xhs_markdown_table(
            [
                {
                    "title": "外滩酒店|真实入住反馈",
                    "author": "旅行作者A",
                    "liked_count": "42",
                    "summary": "步行去外滩方便，适合预算中等的用户。",
                    "url": "https://www.xiaohongshu.com/explore/hotel1",
                    "source": "fake_xhs",
                }
            ]
        )

        self.assertIn("Klook ｜ 北京3天2晚文化美食之旅", web_table)
        self.assertIn("[查看](https://www.klook.com", web_table)
        self.assertNotIn("3-day-trip-in-beijing-dress-up-in-the-imperial-consort-style-in-the/ |", web_table)
        self.assertIn("外滩酒店｜真实入住反馈", xhs_table)
        self.assertIn("[查看](https://www.xiaohongshu.com/explore/hotel1)", xhs_table)

    def test_web_reference_table_uses_url_from_snippet_when_url_missing(self):
        web_table = _build_web_markdown_table(
            [
                {
                    "title": "安阳文化游攻略",
                    "snippet": "详细攻略见 https://example.com/anyang",
                    "url": "-",
                    "source": "web",
                }
            ]
        )

        self.assertIn("[查看](https://example.com/anyang)", web_table)

    def test_final_answer_markdown_normalizer_restores_headings_and_tables(self):
        raw = (
            "#杭州3天2夜文化之旅攻略---##🌦 出行天气参考|日期|天气|温度|风力 ||------|------|------|------||6月14日|多云转阴|24℃-32℃|微风|"
            "## 📍 Day1—皇城中轴线走进六百年紫禁城"
            "### 上午（8:00-11:30） 天安门广场-建议早起观看升旗。"
            "## 网络搜索参考表| 标题 | 摘要 | 链接 | 来源 || --- | --- | --- | --- |"
            "| Klook ｜ 北京3天2晚文化美食之旅 | 摘要 | [查看](https://example.com) | web |"
        )

        normalized = _normalize_markdown_for_display(raw)

        self.assertTrue(normalized.startswith("# 杭州3天2夜文化之旅攻略"))
        self.assertIn("## 🌦 出行天气参考\n|日期|天气|温度|风力 |", normalized)
        self.assertIn("|------|------|------|------|", normalized)
        self.assertIn("\n\n## 📍 Day1", normalized)
        self.assertIn("### 上午（8:00-11:30）\n\n天安门广场", normalized)
        self.assertIn("## 网络搜索参考表\n| 标题 | 摘要 | 链接 | 来源 |", normalized)
        self.assertIn("| --- | --- | --- | --- |\n| Klook", normalized)

    def test_build_message_history_generates_message_id(self):
        request_messages = [
            SimpleNamespace(role="user", content="hello", message_id=None, type="normal"),
            SimpleNamespace(role="assistant", content="world", message_id="m-1", type="final_answer"),
        ]

        result = build_message_history(request_messages)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["role"], "user")
        self.assertEqual(result[0]["content"], "hello")
        self.assertTrue(result[0]["message_id"])
        self.assertEqual(result[1]["message_id"], "m-1")
        self.assertEqual(result[1]["type"], "final_answer")

    def test_execute_chat_once_forwards_params(self):
        controller = FakeController()
        tool_manager = object()
        request_messages = [
            SimpleNamespace(role="user", content="test", message_id="u-1", type="normal"),
        ]

        payload = execute_chat_once(
            request_messages=request_messages,
            controller=controller,
            tool_manager=tool_manager,
            session_id="s-1",
            use_deepthink=True,
            use_multi_agent=False,
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["session_id"], "s-1")
        self.assertEqual(payload["result"], {"ok": True, "steps": 3})

        self.assertIsNotNone(controller.called_with)
        self.assertEqual(controller.called_with["session_id"], "s-1")
        self.assertEqual(controller.called_with["deep_thinking"], True)
        self.assertEqual(controller.called_with["deep_research"], False)
        self.assertEqual(controller.called_with["summary"], True)
        self.assertIs(controller.called_with["tool_manager"], tool_manager)

    def test_execute_chat_once_injects_selected_skill_system_message(self):
        controller = FakeController()
        tool_manager = object()
        request_messages = [
            SimpleNamespace(role="user", content="明天广州到桂林", message_id="u-skill", type="normal"),
        ]

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None) as train_mock:
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=None):
                payload = execute_chat_once(
                    request_messages=request_messages,
                    controller=controller,
                    tool_manager=tool_manager,
                    session_id="s-skill",
                    use_deepthink=True,
                    use_multi_agent=False,
                    selected_skill_ids=["rail_transport"],
                )

        self.assertEqual(payload["status"], "success")
        messages = controller.called_with["messages"]
        self.assertEqual(messages[0]["type"], "system_skill_profile")
        self.assertIn("交通票务查询", messages[0]["content"])
        self.assertIn("query_12306_realtime_tickets", messages[0]["content"])
        train_mock.assert_called_once()
        self.assertEqual(train_mock.call_args.kwargs["selected_skill_ids"], ["rail_transport"])

    def test_train_ticket_bundle_can_be_triggered_by_selected_skill(self):
        class FakeToolManager:
            def get_tool(self, name):
                return object() if name == "get-tickets" else None

            def run_tool(self, name, messages, session_id, **kwargs):
                return [
                    {
                        "train_code": "G101",
                        "from_station_name": kwargs["fromStation"],
                        "to_station_name": kwargs["toStation"],
                        "start_time": "09:00",
                        "arrive_time": "12:00",
                        "lishi": "03:00",
                        "prices": [{"seat_name": "二等座", "price": 200, "num": "有"}],
                    }
                ]

        bundle = maybe_prepare_train_ticket_bundle(
            user_query="广州到桂林明天",
            tool_manager=FakeToolManager(),
            message_history=[{"role": "user", "content": "广州到桂林明天", "message_id": "u1", "type": "normal"}],
            session_id="s1",
            selected_skill_ids=["rail_transport"],
        )

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle["direct_rows"][0]["trip_no"], "G101")

    def test_ticket_table_displays_rows_when_realtime_tickets_exist(self):
        table = _build_ticket_markdown_table(
            direct_rows=[
                {
                    "type": "直达",
                    "trip_no": "D2992",
                    "route": "广州南 -> 桂林北",
                    "depart": "2026-05-27 08:10",
                    "arrive": "2026-05-27 10:55",
                    "duration": "2小时45分钟",
                    "seat": "二等座",
                    "price": "165元",
                    "note": "余票:有",
                }
            ],
            interline_rows=[],
            user_query="从广州到桂林最佳交通",
        )

        self.assertIn("火车和高铁票信息表", table)
        self.assertIn("| 直达 | D2992 | 广州南 -> 桂林北 |", table)
        self.assertNotIn("未查到实时可售车票", table)

    def test_ticket_table_shows_empty_message_only_when_no_realtime_tickets_exist(self):
        table = _build_ticket_markdown_table(
            direct_rows=[],
            interline_rows=[],
            user_query="从广州到桂林最佳交通",
        )

        self.assertIn("未查到实时可售车票", table)
        self.assertNotIn("| - | - | - |", table)
        self.assertNotIn("飞机票信息表", table)
        self.assertNotIn("大巴票信息表", table)

    def test_ticket_table_orders_available_modes_and_omits_missing_optional_modes(self):
        table = _build_ticket_markdown_table(
            flight_rows=[
                {
                    "trip_no": "CZ3907",
                    "route": "广州白云 -> 桂林两江",
                    "depart": "2026-05-28 08:00",
                    "arrive": "2026-05-28 09:30",
                    "duration": "1小时30分钟",
                    "seat": "经济舱",
                    "price": "580元",
                    "note": "余票:12",
                }
            ],
            direct_rows=[
                {
                    "type": "直达",
                    "trip_no": "D2992",
                    "route": "广州南 -> 桂林北",
                    "depart": "2026-05-28 08:10",
                    "arrive": "2026-05-28 10:55",
                    "duration": "2小时45分钟",
                    "seat": "二等座",
                    "price": "165元",
                    "note": "余票:有",
                }
            ],
            interline_rows=[],
            bus_rows=[
                {
                    "trip_no": "班次A",
                    "route": "广州省站 -> 桂林汽车站",
                    "depart": "2026-05-28 09:00",
                    "arrive": "2026-05-28 15:00",
                    "duration": "6小时",
                    "seat": "大巴",
                    "price": "180元",
                    "note": "余票:15",
                }
            ],
            user_query="广州到桂林最佳交通",
        )

        self.assertLess(table.index("飞机票信息表"), table.index("火车和高铁票信息表"))
        self.assertLess(table.index("火车和高铁票信息表"), table.index("大巴票信息表"))
        self.assertIn("| CZ3907 | 广州白云 -> 桂林两江 |", table)
        self.assertIn("| 班次A | 广州省站 -> 桂林汽车站 |", table)

    def test_direct_ticket_normalizer_reads_nested_mcp_payload(self):
        rows = _normalize_direct_rows(
            {
                "data": {
                    "tickets": [
                        {
                            "train_code": "D2992",
                            "from_station_name": "广州南",
                            "to_station_name": "桂林北",
                            "start_time": "08:10",
                            "arrive_time": "10:55",
                            "lishi": "02:45",
                            "prices": [{"seat_name": "二等座", "price": 165, "num": "有"}],
                        }
                    ]
                }
            },
            from_station="广州南",
            to_station="桂林北",
            travel_date="2026-05-27",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trip_no"], "D2992")
        self.assertEqual(rows[0]["route"], "广州南 -> 桂林北")

    def test_direct_ticket_normalizer_uses_query_date_not_train_origin_date(self):
        rows = _normalize_direct_rows(
            [
                {
                    "start_train_code": "K348",
                    "from_station": "杭州",
                    "to_station": "南京",
                    "start_date": "2026-06-05",
                    "arrive_date": "2026-06-05",
                    "start_time": "01:55",
                    "arrive_time": "07:09",
                    "lishi": "05:14",
                    "prices": [{"seat_name": "硬座", "price": 62.5, "num": "有"}],
                }
            ],
            from_station="杭州",
            to_station="南京",
            travel_date="2026-06-06",
        )

        self.assertEqual(rows[0]["depart"], "2026-06-06 01:55")
        self.assertEqual(rows[0]["arrive"], "2026-06-06 07:09")

    def test_city_station_pairs_cover_official_guangzhou_stations(self):
        station_names = [
            "广州南",
            "广州白云",
            "广州北",
            "广州",
            "广州东",
            "广州新塘",
            "广州西",
            "广州大学城",
            "广州长隆",
            "广州莲花山",
            "桂林北",
            "桂林",
            "桂林西",
            "北京南",
            "北京北",
            "北京",
        ]

        with patch("services.chat_service._load_12306_station_names", return_value=station_names):
            pairs = _station_query_pairs("广州", "桂林")

        self.assertEqual(len(pairs), 30)
        self.assertIn(("广州白云", "桂林北"), pairs)
        self.assertIn(("广州北", "桂林"), pairs)
        self.assertIn(("广州莲花山", "桂林西"), pairs)

        with patch("services.chat_service._load_12306_station_names", return_value=station_names):
            beijing_pairs = _station_query_pairs("北京", "桂林")

        self.assertIn(("北京南", "桂林北"), beijing_pairs)
        self.assertIn(("北京北", "桂林"), beijing_pairs)

    def test_interline_normalizer_reads_12306_middle_list_payload(self):
        rows = _normalize_interline_rows(
            {
                "data": {
                    "middleList": [
                        {
                            "all_lishi_minutes": 674,
                            "arrive_date": "2026-05-28",
                            "arrive_time": "09:44",
                            "from_station_name": "广州北",
                            "middle_station_name": "衡阳-衡阳东",
                            "end_station_name": "桂林",
                            "train_date": "2026-05-27",
                            "start_time": "22:30",
                            "wait_time_minutes": 183,
                            "fullList": [
                                {
                                    "station_train_code": "K436",
                                    "from_station_name": "广州北",
                                    "to_station_name": "衡阳",
                                    "start_time": "22:30",
                                    "arrive_time": "03:51",
                                    "start_train_date": "20260527",
                                    "yz_num": "无",
                                    "yw_num": "无",
                                    "wz_num": "有",
                                },
                                {
                                    "station_train_code": "D3961",
                                    "from_station_name": "衡阳东",
                                    "to_station_name": "桂林",
                                    "start_time": "06:54",
                                    "arrive_time": "09:44",
                                    "start_train_date": "20260528",
                                    "ze_num": "有",
                                    "zy_num": "有",
                                    "wz_num": "有",
                                },
                            ],
                        }
                    ]
                }
            },
            from_station="广州",
            to_station="桂林",
            travel_date="2026-05-27",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trip_no"], "K436 + D3961")
        self.assertEqual(rows[0]["route"], "广州北 -> 衡阳-衡阳东 -> 桂林")
        self.assertEqual(rows[0]["depart"], "2026-05-27 22:30")
        self.assertEqual(rows[0]["arrive"], "2026-05-28 09:44")
        self.assertEqual(rows[0]["duration"], "11小时14分钟")
        self.assertEqual(rows[0]["seat"], "无座+二等座")
        self.assertIn("候车:3小时3分钟", rows[0]["note"])
        self.assertIn("跨站换乘", rows[0]["note"])
        self.assertIn("跨天到达", rows[0]["note"])

    def test_xhs_bundle_can_be_triggered_by_selected_skill(self):
        class FakeToolManager:
            def get_tool(self, name):
                return object() if name == "xhs_search_and_summarize" else None

            def run_tool(self, name, messages, session_id, **kwargs):
                return {
                    "summary": "找到酒店周边真实反馈",
                    "source": "fake_xhs",
                    "items": [
                        {
                            "title": "酒店附近早餐",
                            "author": "旅行者A",
                            "liked_count": "12",
                            "summary": "步行可达",
                            "url": "https://www.xiaohongshu.com/explore/1",
                            "source": "fake_xhs",
                        }
                    ],
                }

        bundle = maybe_prepare_xhs_search_bundle(
            user_query="帮我看看这家酒店附近怎么样",
            tool_manager=FakeToolManager(),
            message_history=[{"role": "user", "content": "帮我看看这家酒店附近怎么样", "message_id": "u1", "type": "normal"}],
            session_id="s1",
            selected_skill_ids=["xhs_insight"],
        )

        self.assertIsNotNone(bundle)
        self.assertIn("小红书检索结果", bundle["context_message"])
        self.assertEqual(bundle["source_tool"], "xhs_search_and_summarize")

    def test_xhs_bundle_is_triggered_for_hotel_travel_query(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                return object() if name == "xhs_search_and_summarize" else None

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs))
                return {
                    "summary": "找到外滩酒店真实入住反馈",
                    "source": "fake_xhs",
                    "items": [
                        {
                            "title": "外滩附近性价比酒店",
                            "author": "旅行作者A",
                            "liked_count": "42",
                            "summary": "适合步行去外滩",
                            "url": "https://www.xiaohongshu.com/explore/hotel1",
                            "source": "fake_xhs",
                        }
                    ],
                }

        tool_manager = FakeToolManager()
        bundle = maybe_prepare_xhs_search_bundle(
            user_query="推荐上海外滩附近性价比高的酒店",
            tool_manager=tool_manager,
            message_history=[{"role": "user", "content": "推荐上海外滩附近性价比高的酒店", "message_id": "u1", "type": "normal"}],
            session_id="s-hotel",
        )

        self.assertIsNotNone(bundle)
        self.assertEqual(tool_manager.calls[0][0], "xhs_search_and_summarize")
        self.assertEqual(bundle["rows"][0]["title"], "外滩附近性价比酒店")

    def test_travel_experience_bundle_calls_web_and_map_tools(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                if name == "search_web_page":
                    return SimpleNamespace(parameters={"query": {}, "count": {}}, required=["query"])
                if name == "map_search_places":
                    return SimpleNamespace(parameters={"query": {}, "region": {}}, required=["query"])
                return None

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs))
                if name == "search_web_page":
                    return [
                        {
                            "title": "杭州三日游攻略",
                            "url": "https://example.com/hz",
                            "snippet": "西湖、灵隐寺和湖滨适合顺路安排。",
                            "source": "web",
                        }
                    ]
                if name == "map_search_places":
                    return {
                        "places": [
                            {
                                "name": "西湖风景名胜区",
                                "location": {"lat": 30.242, "lng": 120.141},
                                "address": "杭州市西湖区",
                                "category": "景点",
                            },
                            {
                                "name": "灵隐寺",
                                "location": {"lat": 30.24, "lng": 120.102},
                                "address": "杭州市西湖区法云弄",
                                "category": "人文古迹",
                            },
                        ]
                    }
                return {}

        bundle = maybe_prepare_travel_experience_bundle(
            user_query="帮我做一个杭州三日游行程，顺便推荐美食",
            tool_manager=FakeToolManager(),
            message_history=[{"role": "user", "content": "帮我做一个杭州三日游行程，顺便推荐美食", "message_id": "u1", "type": "normal"}],
            session_id="s-travel",
        )

        self.assertIsNotNone(bundle)
        self.assertIn("旅行体验增强要求", bundle["context_message"])
        self.assertIn("网络搜索参考表", bundle["append_markdown"])
        self.assertNotIn("地图地点表", bundle["append_markdown"])
        self.assertIn('"map_locations"', bundle["append_markdown"])
        self.assertGreaterEqual(len(bundle["map_locations"]), 2)

    def test_travel_fallback_does_not_claim_external_unavailable_when_web_rows_exist(self):
        answer = _build_travel_fallback_answer(
            {
                "query": "推荐上海外滩附近性价比高的酒店",
                "kinds": ["酒店住宿"],
                "web_rows": [
                    {
                        "title": "上海外滩酒店推荐",
                        "snippet": "外滩附近酒店可按预算筛选。",
                        "url": "https://example.com/hotel",
                        "source": "web",
                    }
                ],
                "map_locations": [],
                "xhs_table": "",
                "append_markdown": "",
            }
        )

        self.assertIn("已完成外部检索", answer)
        self.assertNotIn("外部检索结果暂时不可用", answer)

    def test_travel_experience_bundle_defaults_hotel_locations_to_hotel_category(self):
        class FakeToolManager:
            def get_tool(self, name):
                if name == "search_web_page":
                    return SimpleNamespace(parameters={"query": {}, "count": {}}, required=["query"])
                if name == "map_search_places":
                    return SimpleNamespace(parameters={"query": {}, "region": {}}, required=["query"])
                return None

            def run_tool(self, name, messages, session_id, **kwargs):
                if name == "search_web_page":
                    return [
                        {
                            "title": "上海外滩酒店推荐",
                            "url": "https://example.com/shanghai-hotel",
                            "snippet": "外滩附近酒店可按高端、中端和经济分层选择。",
                            "source": "web",
                        }
                    ]
                if name == "map_search_places":
                    return {
                        "places": [
                            {
                                "name": "上海外滩华尔道夫酒店",
                                "location": {"lat": 31.2366, "lng": 121.4908},
                                "address": "中山东一路2号",
                            }
                        ]
                    }
                return {}

        bundle = maybe_prepare_travel_experience_bundle(
            user_query="推荐上海外滩附近性价比高的酒店",
            tool_manager=FakeToolManager(),
            message_history=[
                {
                    "role": "user",
                    "content": "推荐上海外滩附近性价比高的酒店",
                    "message_id": "u-hotel",
                    "type": "normal",
                }
            ],
            session_id="s-hotel",
        )

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle["kinds"], ["酒店住宿"])
        self.assertEqual(bundle["map_locations"][0]["category"], "酒店")
        self.assertIn("分组样式", bundle["context_message"])
        self.assertIn("map_locations", bundle["append_markdown"])

    def test_travel_final_answer_does_not_use_user_query_as_h1_or_insert_generic_schedule(self):
        user_query = "帮我规划一次沈阳3天2夜的文化之旅"
        bundle = {
            "query": user_query,
            "kinds": ["行程规划"],
            "xhs_table": "",
            "web_rows": [],
            "map_locations": [],
            "fallback_answer": "",
        }

        with TemporaryDirectory() as tmpdir:
            with patch("services.chat_service.get_output_root_path", return_value=Path(tmpdir)):
                merged = _select_travel_final_answer(
                    "沈阳适合围绕故宫、张氏帅府和中街安排三天两夜的文化路线。",
                    bundle,
                    session_id="s-shenyang-structure",
                )

        self.assertNotRegex(merged, rf"(?m)^#\s+{user_query}\s*$")
        self.assertNotIn("## 推荐安排", merged)

    def test_travel_experience_bundle_map_locations_include_hotel_and_food_search_hits(self):
        class FakeToolManager:
            def get_tool(self, name):
                if name == "search_web_page":
                    return SimpleNamespace(parameters={"query": {}, "count": {}}, required=["query"])
                if name == "map_search_places":
                    return SimpleNamespace(parameters={"query": {}, "region": {}}, required=["query"])
                return None

            def run_tool(self, name, messages, session_id, **kwargs):
                if name == "search_web_page":
                    return [
                        {
                            "title": "沈阳文化游住宿与美食攻略",
                            "snippet": "故宫、中街附近适合串联住宿和餐饮。",
                            "url": "https://example.com/shenyang",
                            "source": "web",
                        }
                    ]
                if name == "map_search_places":
                    return {
                        "places": [
                            {
                                "name": "沈阳故宫附近酒店",
                                "location": {"lat": 41.7961, "lng": 123.4613},
                                "address": "沈阳市沈河区沈阳路附近",
                                "category": "酒店",
                            },
                            {
                                "name": "老边饺子馆中街店",
                                "location": {"lat": 41.8002, "lng": 123.4456},
                                "address": "沈阳市沈河区中街",
                                "category": "餐厅",
                            },
                        ]
                    }
                return {}

        query = "帮我规划一次沈阳3天2夜的文化之旅，包含住宿和美食"
        bundle = maybe_prepare_travel_experience_bundle(
            user_query=query,
            tool_manager=FakeToolManager(),
            message_history=[{"role": "user", "content": query, "message_id": "u-shenyang", "type": "normal"}],
            session_id="s-shenyang-map",
        )

        self.assertIsNotNone(bundle)
        location_names = {location["name"] for location in bundle["map_locations"]}
        location_categories = {location["category"] for location in bundle["map_locations"]}
        self.assertIn("沈阳故宫附近酒店", location_names)
        self.assertIn("老边饺子馆中街店", location_names)
        self.assertTrue({"酒店", "餐厅"}.issubset(location_categories))

    def test_select_travel_final_answer_appends_reference_and_hidden_map_json(self):
        bundle = {
            "xhs_table": "",
            "web_rows": [
                {
                    "title": "杭州攻略",
                    "snippet": "西湖与灵隐寺顺路。",
                    "url": "https://example.com/hz",
                    "source": "web",
                }
            ],
            "map_locations": [
                {
                    "id": "travel_place_1",
                    "name": "西湖风景名胜区",
                    "lat": 30.242,
                    "lng": 120.141,
                    "description": "杭州市西湖区",
                    "category": "景点",
                    "order": 1,
                }
            ],
            "fallback_answer": "# 杭州行程建议\n\n## 结论\n可以按西湖周边安排。",
        }

        with TemporaryDirectory() as tmpdir:
            with patch("services.chat_service.get_output_root_path", return_value=Path(tmpdir)):
                merged = _select_travel_final_answer(
                    "# 杭州行程建议\n\n## 结论\n可以按西湖周边安排。",
                    bundle,
                    session_id="s-travel-doc",
                )
            document_exists = (Path(tmpdir) / "s-travel-doc" / "旅行推荐.md").exists()

        self.assertIn("网络搜索参考表", merged)
        self.assertNotIn("地图地点表", merged)
        self.assertIn('"map_locations"', merged)
        self.assertIn("西湖风景名胜区", merged)
        self.assertIn("## 地理位置信息", merged)
        self.assertIn("以下是推荐地点的地理坐标信息，方便您在地图上查看和规划路线：", merged)
        self.assertIn("## 生成的文档", merged)
        self.assertIn("/api/download/s-travel-doc/%E6%97%85%E8%A1%8C%E6%8E%A8%E8%8D%90.md", merged)
        self.assertTrue(document_exists)

    def test_select_travel_final_answer_replaces_reference_only_model_answer(self):
        bundle = {
            "query": "帮我规划一次北京3天2夜的文化之旅",
            "kinds": ["行程规划"],
            "xhs_table": "",
            "web_rows": [
                {
                    "title": "北京慕田峪长城+故宫3日文化之旅",
                    "snippet": "北京3天2晚文化美食之旅。",
                    "url": "https://example.com/beijing",
                    "source": "web",
                }
            ],
            "map_locations": [
                {
                    "id": "travel_place_1",
                    "name": "故宫博物院",
                    "lat": 39.9163,
                    "lng": 116.3972,
                    "description": "北京市东城区景山前街4号",
                    "category": "景点",
                    "order": 1,
                }
            ],
        }
        bundle["fallback_answer"] = _build_travel_fallback_answer(bundle)
        reference_only = (
            "1. 北京慕田峪长城+故宫3日文化之旅\n"
            "- 摘要：北京3天2晚文化美食之旅。\n"
            "- 链接：查看\n\n"
            "## 网络搜索参考表\n| 标题 | 摘要 | 链接 | 来源 |\n| --- | --- | --- | --- |"
        )

        with TemporaryDirectory() as tmpdir:
            with patch("services.chat_service.get_output_root_path", return_value=Path(tmpdir)):
                merged = _select_travel_final_answer(reference_only, bundle, session_id="s-beijing")

        self.assertIn("## Day 1：皇城中轴线与故宫深度游", merged)
        self.assertIn("## 网络搜索参考表", merged)
        self.assertIn("## 建议与预约提醒", merged)
        self.assertIn("## 地理位置信息", merged)
        self.assertLess(merged.index("## Day 1"), merged.index("## 网络搜索参考表"))
        self.assertLess(merged.index("## 网络搜索参考表"), merged.index("## 建议与预约提醒"))
        self.assertLess(merged.index("## 建议与预约提醒"), merged.index("## 地理位置信息"))

    def test_anyang_travel_bundle_defaults_date_origin_weather_and_geocodes_places(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                specs = {
                    "search_web_page": SimpleNamespace(parameters={"query": {}, "count": {}}, required=["query"]),
                    "map_search_places": SimpleNamespace(parameters={"query": {}, "region": {}}, required=["query"]),
                    "map_geocode": SimpleNamespace(parameters={"address": {}, "city": {}}, required=["address"]),
                    "map_ip_location": SimpleNamespace(parameters={}, required=[]),
                    "map_weather": SimpleNamespace(parameters={"city": {}, "date": {}}, required=["city"]),
                }
                return specs.get(name)

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs))
                if name == "map_ip_location":
                    return {"content": {"address_detail": {"city": "郑州市"}}}
                if name == "map_weather":
                    return {
                        "forecasts": [
                            {
                                "date": "2026-06-14",
                                "text_day": "多云",
                                "text_night": "阴",
                                "low": "22",
                                "high": "33",
                                "wd_day": "南风",
                            }
                        ]
                    }
                if name == "search_web_page":
                    return [
                        {
                            "title": "安阳三日文化游攻略",
                            "snippet": "殷墟、中国文字博物馆和羑里城适合串联。",
                            "url": "https://example.com/anyang",
                            "source": "web",
                        }
                    ]
                if name == "map_search_places":
                    return {"places": []}
                if name == "map_geocode":
                    address = kwargs.get("address", "")
                    return {
                        "places": [
                            {
                                "name": address,
                                "location": {"lat": 36.1 + len(self.calls) / 1000, "lng": 114.3 + len(self.calls) / 1000},
                                "address": address,
                            }
                        ]
                    }
                return {}

        tool_manager = FakeToolManager()
        with patch("services.chat_service._extract_travel_date", return_value="2026-06-14"):
            bundle = maybe_prepare_travel_experience_bundle(
                user_query="帮我规划一次安阳3天2夜的文化之旅",
                tool_manager=tool_manager,
                message_history=[
                    {
                        "role": "user",
                        "content": "帮我规划一次安阳3天2夜的文化之旅",
                        "message_id": "u-anyang",
                        "type": "normal",
                    }
                ],
                session_id="s-anyang",
            )

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle["travel_date"], "2026-06-14")
        self.assertEqual(bundle["origin_city"], "郑州")
        self.assertEqual(bundle["destination_city"], "安阳")
        self.assertIn("多云", bundle["weather_summary"])
        self.assertGreaterEqual(len(bundle["map_locations"]), 3)
        self.assertTrue(any(name == "map_ip_location" for name, _ in tool_manager.calls))
        self.assertTrue(any(name == "map_weather" for name, _ in tool_manager.calls))
        self.assertFalse(any(name == "map_weather" and "location" in kwargs for name, kwargs in tool_manager.calls))
        self.assertTrue(any(name == "map_geocode" for name, _ in tool_manager.calls))

        with TemporaryDirectory() as tmpdir:
            with patch("services.chat_service.get_output_root_path", return_value=Path(tmpdir)):
                merged = _select_travel_final_answer("", bundle, session_id="s-anyang")

        self.assertNotIn("## 出行基础信息", merged)
        self.assertNotIn("## 交通与天气建议", merged)
        self.assertIn("## Day 1：商代文明与汉字源流", merged)
        self.assertIn('"map_locations"', merged)
        self.assertIn("安阳3天2夜的文化之旅.md", merged)

    def test_xhs_rows_parse_nested_mcp_payload(self):
        payload = {
            "content": [
                {
                    "type": "text",
                    "text": "{\"data\":{\"notes\":[{\"display_title\":\"北京文化路线避坑\",\"desc\":\"故宫和国博都建议提前预约。\",\"note_id\":\"abc123\",\"author\":{\"nickname\":\"旅行者\"},\"liked_count\":\"88\"}]}}",
                }
            ]
        }

        rows = _normalize_xhs_rows(payload, max_rows=3)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "北京文化路线避坑")
        self.assertEqual(rows[0]["summary"], "故宫和国博都建议提前预约。")
        self.assertEqual(rows[0]["author"], "旅行者")
        self.assertEqual(rows[0]["url"], "https://www.xiaohongshu.com/explore/abc123")

    def test_travel_document_filename_summarizes_prompt(self):
        self.assertEqual(
            _travel_document_filename("帮我规划一次安阳3天2夜的文化之旅"),
            "安阳3天2夜的文化之旅.md",
        )

    def test_markdown_normalizer_demotes_numbered_tip_headings(self):
        normalized = _normalize_markdown_for_display("# 实用贴士\n# 1.预约提醒\n请提前预约。")

        self.assertIn("### 1. 预约提醒", normalized)
        self.assertNotIn("# 1.预约提醒", normalized)

    def test_execute_chat_once_merges_model_answer_with_realtime_table(self):
        class FakeRealtimeController(FakeController):
            def run(self, messages, tool_manager, session_id, deep_thinking, summary, deep_research):
                self.called_with = {
                    "messages": messages,
                    "tool_manager": tool_manager,
                    "session_id": session_id,
                    "deep_thinking": deep_thinking,
                    "summary": summary,
                    "deep_research": deep_research,
                }
                return {
                    "all_messages": [
                        {"role": "assistant", "type": "final_answer", "content": "虚构车次 G999"}
                    ],
                    "new_messages": [
                        {"role": "assistant", "type": "final_answer", "content": "虚构车次 G999"}
                    ],
                    "final_output": {"role": "assistant", "type": "final_answer", "content": "虚构车次 G999"},
                }

        controller = FakeRealtimeController()
        tool_manager = object()
        request_messages = [
            SimpleNamespace(role="user", content="明天从安阳到杭州最便宜交通", message_id="u-2", type="normal"),
        ]

        bundle = {
            "enforce_realtime_only": True,
            "realtime_only_answer": "仅基于实时查询结果输出，禁止编造班次与票价。\n\n火车和高铁票信息表\n| 类型 | 班次 |\n| --- | --- |\n| 直达 | G1 |",
            "append_markdown": "火车和高铁票信息表\n| 类型 | 班次 |\n| --- | --- |\n| 直达 | G1 |",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=bundle):
            payload = execute_chat_once(
                request_messages=request_messages,
                controller=controller,
                tool_manager=tool_manager,
                session_id="s-2",
                use_deepthink=True,
                use_multi_agent=False,
            )

        self.assertEqual(payload["status"], "success")
        result = payload["result"]
        final_content = result["final_output"]["content"]
        self.assertNotIn("虚构车次 G999", final_content)
        self.assertIn("仅基于实时查询结果输出", final_content)
        self.assertIn("火车和高铁票信息表", final_content)
        self.assertNotIn("###", final_content)
        self.assertNotRegex(final_content, r"(?m)^- ")
        self.assertTrue(result.get("realtime_ticket_enforced"))

    def test_execute_chat_once_skips_travel_enrichment_when_ticket_bundle_exists(self):
        controller = FakeController()
        ticket_bundle = {
            "has_valid_results": True,
            "direct_rows": [{"trip_no": "G1"}],
            "interline_rows": [],
            "flight_rows": [],
            "bus_rows": [],
            "ticket_states": {
                "train": {"status": "success", "result_count": 1, "error": ""},
                "flight": {"status": "empty", "result_count": 0, "error": ""},
                "bus": {"status": "empty", "result_count": 0, "error": ""},
            },
            "context_message": "【实时票务查询结果】G1",
            "append_markdown": "火车和高铁票信息表\n| 班次 |\n| --- |\n| G1 |",
            "realtime_only_answer": "火车和高铁票信息表\n| 班次 |\n| --- |\n| G1 |",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=ticket_bundle):
            with patch("services.chat_service.maybe_prepare_travel_experience_bundle") as travel_mock:
                payload = execute_chat_once(
                    request_messages=[
                        SimpleNamespace(role="user", content="查询明天杭州到南京的票", message_id="u-ticket-skip", type="normal")
                    ],
                    controller=controller,
                    tool_manager=object(),
                    session_id="s-ticket-skip",
                    use_deepthink=True,
                    use_multi_agent=False,
                )

        travel_mock.assert_not_called()
        self.assertTrue(payload["result"].get("realtime_ticket_enforced"))
        self.assertFalse(payload["result"].get("travel_experience_enforced"))

    def test_execute_chat_once_merges_model_answer_with_xhs_table(self):
        class FakeXhsController(FakeController):
            def run(self, messages, tool_manager, session_id, deep_thinking, summary, deep_research):
                self.called_with = {
                    "messages": messages,
                    "tool_manager": tool_manager,
                    "session_id": session_id,
                    "deep_thinking": deep_thinking,
                    "summary": summary,
                    "deep_research": deep_research,
                }
                return {
                    "all_messages": [
                        {"role": "assistant", "type": "final_answer", "content": "已整理出3条建议。"}
                    ],
                    "new_messages": [
                        {"role": "assistant", "type": "final_answer", "content": "已整理出3条建议。"}
                    ],
                    "final_output": {"role": "assistant", "type": "final_answer", "content": "已整理出3条建议。"},
                }

        controller = FakeXhsController()
        tool_manager = object()
        request_messages = [
            SimpleNamespace(role="user", content="杭州三日游攻略怎么安排，帮我总结", message_id="u-xhs", type="normal"),
        ]

        xhs_bundle = {
            "context_message": "【小红书检索结果】\n用户问题: 杭州三日游攻略怎么安排，帮我总结",
            "append_markdown": "### 小红书检索资源表\n| 标题 | 作者 |\n| --- | --- |\n| 杭州路线 | 旅行博主A |",
            "fallback_answer": "已完成小红书检索并生成摘要。",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=xhs_bundle):
                with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=None):
                    payload = execute_chat_once(
                        request_messages=request_messages,
                        controller=controller,
                        tool_manager=tool_manager,
                        session_id="s-xhs",
                        use_deepthink=True,
                        use_multi_agent=False,
                    )

        self.assertEqual(payload["status"], "success")
        result = payload["result"]
        self.assertIn("已整理出3条建议", result["final_output"]["content"])
        self.assertIn("小红书检索资源表", result["final_output"]["content"])
        self.assertTrue(result.get("xhs_resource_enforced"))
        self.assertEqual(controller.called_with["messages"][-1]["type"], "system_xhs_search_context")

    def test_train_ticket_bundle_expands_city_names_to_station_pairs(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                return object() if name == "get-tickets" else None

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs.get("fromStation"), kwargs.get("toStation")))
                if kwargs.get("fromStation") == "\u5e7f\u5dde\u5357" and kwargs.get("toStation") == "\u6842\u6797\u5317":
                    return [
                        {
                            "train_code": "D2992",
                            "from_station_name": "\u5e7f\u5dde\u5357",
                            "to_station_name": "\u6842\u6797\u5317",
                            "start_time": "08:10",
                            "arrive_time": "10:55",
                            "lishi": "02:45",
                            "prices": [{"seat_name": "\u4e8c\u7b49\u5ea7", "price": 165, "num": "\u6709"}],
                        }
                    ]
                return []

        tool_manager = FakeToolManager()

        with patch(
            "services.chat_service._load_12306_station_names",
            return_value=["广州南", "广州北", "广州", "桂林北", "桂林", "桂林西"],
        ):
            bundle = maybe_prepare_train_ticket_bundle(
                user_query="\u4ece\u5e7f\u5dde\u5230\u6842\u6797\u6700\u4f73\u4ea4\u901a",
                tool_manager=tool_manager,
                message_history=[
                    {
                        "role": "user",
                        "content": "\u4ece\u5e7f\u5dde\u5230\u6842\u6797\u6700\u4f73\u4ea4\u901a",
                        "message_id": "u-city",
                        "type": "normal",
                    }
                ],
                session_id="s-city",
            )

        self.assertIsNotNone(bundle)
        self.assertIn(("get-tickets", "\u5e7f\u5dde\u5357", "\u6842\u6797\u5317"), tool_manager.calls)
        self.assertEqual(bundle["direct_rows"][0]["trip_no"], "D2992")
        self.assertEqual(bundle["direct_rows"][0]["route"], "\u5e7f\u5dde\u5357 -> \u6842\u6797\u5317")

    def test_train_ticket_bundle_calls_realtime_flight_and_bus_tools_when_available(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                return object() if name in {"query_flight_tickets", "query_bus_tickets"} else None

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs))
                if name == "query_flight_tickets":
                    if "from_city" not in kwargs:
                        raise TypeError("use snake case")
                    return {
                        "status": "success",
                        "source": "juhe_flight_query_api",
                        "flights": [
                            {
                                "flightNo": "CZ3907",
                                "airline": "南航",
                                "fromAirport": "广州白云",
                                "toAirport": "桂林两江",
                                "departureDate": kwargs["travel_date"],
                                "departureTime": "08:00",
                                "arrivalDate": kwargs["travel_date"],
                                "arrivalTime": "09:30",
                                "duration": "1小时30分钟",
                                "cabinClass": "经济舱",
                                "price": "580",
                            }
                        ],
                    }
                if name == "query_bus_tickets":
                    if "from_city" not in kwargs:
                        raise TypeError("use snake case")
                    return {
                        "status": "success",
                        "source": "jisu_bus_city2c_api",
                        "buses": [
                            {
                                "busNo": "班次A",
                                "from_station": "广州省站",
                                "to_station": "桂林汽车站",
                                "depart_date": kwargs["travel_date"],
                                "depart_time": "09:00",
                                "duration": "6小时",
                                "bus_type": "大巴",
                                "price": "180",
                                "note": "平台未返回余票",
                            }
                        ],
                    }
                return {}

        tool_manager = FakeToolManager()
        with patch(
            "services.chat_service._load_12306_station_names",
            return_value=["广州南", "广州北", "广州", "桂林北", "桂林", "桂林西"],
        ):
            bundle = maybe_prepare_train_ticket_bundle(
                user_query="从广州到桂林最佳交通",
                tool_manager=tool_manager,
                message_history=[
                    {
                        "role": "user",
                        "content": "从广州到桂林最佳交通",
                        "message_id": "u-realtime-modes",
                        "type": "normal",
                    }
                ],
                session_id="s-realtime-modes",
            )

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle["flight_source"], "query_flight_tickets")
        self.assertEqual(bundle["bus_source"], "query_bus_tickets")
        self.assertEqual(bundle["flight_rows"][0]["trip_no"], "南航CZ3907")
        self.assertEqual(bundle["bus_rows"][0]["trip_no"], "班次A")
        self.assertIn("飞机票信息表", bundle["append_markdown"])
        self.assertIn("大巴票信息表", bundle["append_markdown"])
        self.assertTrue(any(name == "query_flight_tickets" for name, _ in tool_manager.calls))
        self.assertTrue(any(name == "query_bus_tickets" for name, _ in tool_manager.calls))

    def test_train_ticket_bundle_uses_previous_route_when_latest_message_only_updates_date(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                return object() if name == "get-tickets" else None

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs.get("date"), kwargs.get("fromStation"), kwargs.get("toStation")))
                if kwargs.get("fromStation") == "广州南" and kwargs.get("toStation") == "桂林北":
                    return [
                        {
                            "train_code": "D2992",
                            "from_station_name": "广州南",
                            "to_station_name": "桂林北",
                            "start_time": "08:10",
                            "arrive_time": "10:55",
                            "lishi": "02:45",
                            "prices": [{"seat_name": "二等座", "price": 165, "num": "有"}],
                        }
                    ]
                return []

        tool_manager = FakeToolManager()
        message_history = [
            {
                "role": "user",
                "content": "查询从广州到桂林的最佳交通方案",
                "message_id": "u-route",
                "type": "normal",
            },
            {
                "role": "assistant",
                "content": "可以，先确认日期。",
                "message_id": "a-route",
                "type": "final_answer",
            },
            {
                "role": "user",
                "content": "今天出发",
                "message_id": "u-date",
                "type": "normal",
            },
        ]

        with patch(
            "services.chat_service._load_12306_station_names",
            return_value=["广州南", "广州北", "广州", "桂林北", "桂林", "桂林西"],
        ):
            bundle = maybe_prepare_train_ticket_bundle(
                user_query="今天出发",
                tool_manager=tool_manager,
                message_history=message_history,
                session_id="s-followup",
            )

        self.assertIsNotNone(bundle)
        self.assertIn(("get-tickets", bundle["route"]["travel_date"], "广州南", "桂林北"), tool_manager.calls)
        self.assertEqual(bundle["direct_rows"][0]["trip_no"], "D2992")
        self.assertIn("D2992", bundle["append_markdown"])


if __name__ == "__main__":
    unittest.main()
