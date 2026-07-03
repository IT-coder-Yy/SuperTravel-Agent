import json
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

from services.chat_service import generate_chat_stream


class FakeController:
    def __init__(self, chunks):
        self.chunks = chunks
        self.called_with = None

    def run_stream(self, input_messages, tool_manager, session_id, deep_thinking, summary, deep_research):
        self.called_with = {
            "input_messages": input_messages,
            "tool_manager": tool_manager,
            "session_id": session_id,
            "deep_thinking": deep_thinking,
            "summary": summary,
            "deep_research": deep_research,
        }
        for chunk in self.chunks:
            yield chunk


class ErrorController:
    def run_stream(self, **kwargs):
        raise RuntimeError("stream boom")


def sanitize_text(value: str) -> str:
    return f"clean::{value}"


class ChatStreamContractTests(unittest.IsolatedAsyncioTestCase):
    async def _collect_sse_payloads(self, generator):
        events = []
        async for line in generator:
            self.assertTrue(line.startswith("data: "))
            payload = json.loads(line[len("data: "):].strip())
            events.append(payload)
        return events

    async def test_generate_chat_stream_emits_start_chunk_complete(self):
        request_messages = [
            SimpleNamespace(role="user", content="hello", message_id=None, type="normal"),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-1",
                        "role": "assistant",
                        "content": "a",
                        "show_content": "A",
                        "type": "thinking",
                    }
                ],
                [
                    {
                        "message_id": "m-1",
                        "role": "assistant",
                        "content": "b",
                        "show_content": "B",
                        "type": "final_answer",
                    }
                ],
            ]
        )

        payloads = await self._collect_sse_payloads(
            generate_chat_stream(
                request_messages=request_messages,
                controller=controller,
                tool_manager=object(),
                selected_mcp_servers=None,
                use_deepthink=True,
                use_multi_agent=False,
                sanitize_text=sanitize_text,
            )
        )

        self.assertGreaterEqual(len(payloads), 4)
        self.assertEqual(payloads[0]["type"], "chat_start")
        self.assertEqual(payloads[1]["type"], "chat_chunk")
        self.assertEqual(payloads[1]["content"], "clean::a")
        self.assertEqual(payloads[1]["show_content"], "clean::A")
        self.assertEqual(payloads[2]["step_type"], "final_answer")
        self.assertEqual(payloads[-1]["type"], "chat_complete")

        self.assertIsNotNone(controller.called_with)
        self.assertEqual(controller.called_with["deep_thinking"], True)
        self.assertEqual(controller.called_with["summary"], True)
        self.assertEqual(controller.called_with["deep_research"], False)
        self.assertEqual(len(controller.called_with["input_messages"]), 1)

    async def test_generate_chat_stream_emits_error_event_on_exception(self):
        request_messages = [
            SimpleNamespace(role="user", content="fail", message_id="u-1", type="normal"),
        ]

        payloads = await self._collect_sse_payloads(
            generate_chat_stream(
                request_messages=request_messages,
                controller=ErrorController(),
                tool_manager=object(),
                selected_mcp_servers=None,
                use_deepthink=True,
                use_multi_agent=True,
                sanitize_text=sanitize_text,
            )
        )

        self.assertEqual(payloads[0]["type"], "chat_start")
        self.assertEqual(payloads[1]["type"], "error")
        self.assertIn("stream boom", payloads[1]["message"])

    async def test_generate_chat_stream_appends_realtime_ticket_table_when_bundle_exists(self):
        request_messages = [
            SimpleNamespace(role="user", content="明天从安阳到杭州高铁票", message_id="u-1", type="normal"),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-final",
                        "role": "assistant",
                        "content": "推荐直达车次如下。",
                        "show_content": "推荐直达车次如下。",
                        "type": "final_answer",
                    }
                ]
            ]
        )

        bundle = {
            "context_message": "【实时票务查询结果】\n查询路线: 安阳 -> 杭州",
            "append_markdown": "火车和高铁票信息表\n| 类型 | 班次 |\n| --- | --- |\n| 直达 | G1 |",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=bundle):
            payloads = await self._collect_sse_payloads(
                generate_chat_stream(
                    request_messages=request_messages,
                    controller=controller,
                    tool_manager=object(),
                    selected_mcp_servers=None,
                    use_deepthink=True,
                    use_multi_agent=False,
                    sanitize_text=sanitize_text,
                )
            )

        self.assertIsNotNone(controller.called_with)
        self.assertEqual(controller.called_with["input_messages"][-1]["role"], "system")
        self.assertIn("实时票务查询结果", controller.called_with["input_messages"][-1]["content"])

        appendix_chunks = [
            event for event in payloads
            if event.get("type") == "chat_chunk" and "火车和高铁票信息表" in event.get("content", "")
        ]
        self.assertTrue(appendix_chunks)
        self.assertEqual(appendix_chunks[-1]["message_id"], "m-final")

    async def test_generate_chat_stream_keeps_model_final_and_appends_table_when_enforced(self):
        request_messages = [
            SimpleNamespace(role="user", content="明天从安阳到杭州最便宜交通", message_id="u-2", type="normal"),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-real",
                        "role": "assistant",
                        "content": "推荐虚构车次 G999，票价99元。",
                        "show_content": "推荐虚构车次 G999，票价99元。",
                        "type": "final_answer",
                    }
                ]
            ]
        )

        bundle = {
            "context_message": "【实时票务查询结果】\n查询路线: 安阳 -> 杭州",
            "append_markdown": "火车和高铁票信息表\n| 类型 | 班次 |\n| --- | --- |\n| 中转 | G1955 + G743 |",
            "enforce_realtime_only": True,
            "realtime_only_answer": "仅基于实时查询结果输出。\n\n火车和高铁票信息表\n| 类型 | 班次 |\n| --- | --- |\n| 中转 | G1955 + G743 |",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=bundle):
            payloads = await self._collect_sse_payloads(
                generate_chat_stream(
                    request_messages=request_messages,
                    controller=controller,
                    tool_manager=object(),
                    selected_mcp_servers=None,
                    use_deepthink=True,
                    use_multi_agent=False,
                    sanitize_text=sanitize_text,
                )
            )

        chunk_contents = [
            payload.get("content", "")
            for payload in payloads
            if payload.get("type") == "chat_chunk"
        ]

        self.assertFalse(any("G999" in content for content in chunk_contents))
        self.assertTrue(any("仅基于实时查询结果输出" in content for content in chunk_contents))
        self.assertTrue(any("火车和高铁票信息表" in content for content in chunk_contents))

    async def test_generate_chat_stream_hides_ticket_intermediate_phase_noise(self):
        request_messages = [
            SimpleNamespace(role="user", content="明天从安阳到杭州怎么去", message_id="u-3", type="normal"),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-ob",
                        "role": "assistant",
                        "content": "Observation: {\"needs_more_input\": false, \"is_completed\": false, \"analysis\": \"分析失败：格式错误或获取到了无效的返回。\"}",
                        "show_content": "Observation: {\"needs_more_input\": false, \"is_completed\": false, \"analysis\": \"分析失败：格式错误或获取到了无效的返回。\"}",
                        "type": "observation_result",
                    }
                ],
                [
                    {
                        "message_id": "m-final-fail",
                        "role": "assistant",
                        "content": "观察阶段执行出现异常，已停止后续链路。请稍后重试。",
                        "show_content": "观察阶段执行出现异常，已停止后续链路。请稍后重试。",
                        "type": "final_answer",
                    }
                ],
            ]
        )

        bundle = {
            "context_message": "【实时票务查询结果】\n查询路线: 安阳 -> 杭州",
            "append_markdown": "火车和高铁票信息表\n| 类型 | 班次 |\n| --- | --- |\n| 直达 | G1 |",
            "enforce_realtime_only": True,
            "realtime_only_answer": "仅基于实时查询结果输出。\n\n火车和高铁票信息表\n| 类型 | 班次 |\n| --- | --- |\n| 直达 | G1 |",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=bundle):
            payloads = await self._collect_sse_payloads(
                generate_chat_stream(
                    request_messages=request_messages,
                    controller=controller,
                    tool_manager=object(),
                    selected_mcp_servers=None,
                    use_deepthink=True,
                    use_multi_agent=False,
                    sanitize_text=sanitize_text,
                )
            )

        chunk_contents = [
            payload.get("content", "")
            for payload in payloads
            if payload.get("type") == "chat_chunk"
        ]

        self.assertFalse(any("Observation:" in content for content in chunk_contents))
        self.assertFalse(any("观察阶段执行出现异常" in content for content in chunk_contents))
        self.assertTrue(any("仅基于实时查询结果输出" in content for content in chunk_contents))
        self.assertTrue(any("火车和高铁票信息表" in content for content in chunk_contents))

    async def test_generate_chat_stream_injects_xhs_context_and_appends_xhs_table(self):
        request_messages = [
            SimpleNamespace(role="user", content="杭州三日游攻略，帮我做一份简明总结", message_id="u-xhs", type="normal"),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-xhs",
                        "role": "assistant",
                        "content": "我先给你行程建议。",
                        "show_content": "我先给你行程建议。",
                        "type": "final_answer",
                    }
                ]
            ]
        )

        xhs_bundle = {
            "context_message": "【小红书检索结果】\n用户问题: 杭州三日游攻略，帮我做一份简明总结",
            "append_markdown": "### 小红书检索资源表\n| 标题 | 作者 |\n| --- | --- |\n| 杭州CityWalk | 旅行博主A |",
            "fallback_answer": "已完成小红书检索并总结。",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=xhs_bundle):
                with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=None):
                    payloads = await self._collect_sse_payloads(
                        generate_chat_stream(
                            request_messages=request_messages,
                            controller=controller,
                            tool_manager=object(),
                            selected_mcp_servers=None,
                            use_deepthink=True,
                            use_multi_agent=False,
                            sanitize_text=sanitize_text,
                        )
                    )

        self.assertIsNotNone(controller.called_with)
        self.assertEqual(controller.called_with["input_messages"][-1]["type"], "system_xhs_search_context")
        chunk_contents = [
            payload.get("content", "")
            for payload in payloads
            if payload.get("type") == "chat_chunk"
        ]
        self.assertTrue(any("小红书检索资源表" in content for content in chunk_contents))

    async def test_generate_chat_stream_appends_travel_map_locations(self):
        request_messages = [
            SimpleNamespace(role="user", content="推荐上海外滩附近性价比高的酒店", message_id="u-travel", type="normal"),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-travel-intro",
                        "role": "assistant",
                        "content": "I will check weather first.",
                        "show_content": "I will check weather first.",
                        "type": "final_answer",
                    },
                    {
                        "message_id": "m-travel-weather",
                        "role": "assistant",
                        "content": '[map_weather] result: {"content": "{\\"location\\": {\\"city\\": \\"Beijing\\"}}"}',
                        "show_content": '[map_weather] result: {"content": "{\\"location\\": {\\"city\\": \\"Beijing\\"}}"}',
                        "type": "final_answer",
                    },
                    {
                        "message_id": "m-travel",
                        "role": "assistant",
                        "content": "# 上海外滩酒店推荐\n\n## 结论\n优先看外滩步行范围和陆家嘴景观酒店。",
                        "show_content": "# 上海外滩酒店推荐\n\n## 结论\n优先看外滩步行范围和陆家嘴景观酒店。",
                        "type": "final_answer",
                    }
                ]
            ]
        )
        travel_bundle = {
            "query": "推荐上海外滩附近性价比高的酒店",
            "kinds": ["酒店住宿"],
            "context_message": "【旅行体验增强要求】\n分组样式\nmap_locations",
            "xhs_table": "",
            "web_rows": [
                {
                    "title": "上海外滩酒店推荐",
                    "snippet": "外滩附近酒店可分层选择。",
                    "url": "https://example.com/hotel",
                    "source": "web",
                }
            ],
            "map_locations": [
                {
                    "id": "travel_place_1",
                    "name": "上海外滩华尔道夫酒店",
                    "lat": 31.2366,
                    "lng": 121.4908,
                    "description": "中山东一路2号",
                    "category": "酒店",
                    "order": 1,
                }
            ],
            "append_markdown": "",
            "fallback_answer": "# 上海外滩酒店推荐\n\n## 结论\n优先看外滩步行范围。",
        }

        with TemporaryDirectory() as tmpdir:
            with patch("services.chat_service.get_output_root_path", return_value=Path(tmpdir)):
                with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
                    with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=None):
                        with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=travel_bundle):
                            payloads = await self._collect_sse_payloads(
                                generate_chat_stream(
                                    request_messages=request_messages,
                                    controller=controller,
                                    tool_manager=object(),
                                    selected_mcp_servers=None,
                                    use_deepthink=True,
                                    use_multi_agent=False,
                                    sanitize_text=sanitize_text,
                                )
                            )

        self.assertIsNotNone(controller.called_with)
        self.assertEqual(controller.called_with["input_messages"][-1]["type"], "system_travel_experience_context")
        travel_chunks = [payload for payload in payloads if payload.get("type") == "chat_chunk"]
        chunk_contents = [payload.get("content", "") for payload in travel_chunks]
        self.assertFalse(any("map_weather" in content for content in chunk_contents))
        self.assertFalse(any("Beijing" in content for content in chunk_contents))
        self.assertTrue(any("网络搜索参考表" in content for content in chunk_contents))
        self.assertFalse(any("地图地点表" in content for content in chunk_contents))
        self.assertTrue(any('"map_locations"' in content for content in chunk_contents))
        self.assertTrue(any("上海外滩华尔道夫酒店" in content for content in chunk_contents))
        self.assertTrue(any(payload.get("replace") is True for payload in travel_chunks))
        self.assertTrue(any("## 地理位置信息" in content for content in chunk_contents))
        self.assertTrue(any("## 生成的文档" in content for content in chunk_contents))
        self.assertTrue(any("/api/download/" in content and ".md" in content for content in chunk_contents))


if __name__ == "__main__":
    unittest.main()
