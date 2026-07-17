import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agents.agent.task_summary_agent.task_summary_agent import TaskSummaryAgent


def _content_chunk(content: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=None)
            )
        ],
        usage=None,
    )


def _tool_call_chunk(call_id: str, name: str, arguments: str):
    tool_call = SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=None, tool_calls=[tool_call])
            )
        ],
        usage=None,
    )


class FakeCompletions:
    def __init__(self, responses):
        self.calls = []
        self.responses = responses

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response_index = min(len(self.calls) - 1, len(self.responses) - 1)
        return iter(self.responses[response_index])


class FakeModel:
    def __init__(self, responses):
        self.chat = SimpleNamespace(
            completions=FakeCompletions(responses)
        )


class FakeToolManager:
    def __init__(self):
        self.calls = []

    def get_openai_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "map_geocode",
                    "description": "地图地理编码",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "address": {"type": "string"},
                            "city": {"type": "string"},
                        },
                    },
                },
            }
        ]

    def run_tool(self, name, messages, session_id, **kwargs):
        call = {
            "name": name,
            "messages": messages,
            "session_id": session_id,
            "kwargs": kwargs,
        }
        self.calls.append(call)
        self.last_call = call
        return {
            "formatted_address": "北京市东城区天安门",
            "location": {"lat": 39.9087, "lng": 116.3975},
            "debug_path": r"D:\travel-agent\cache\geocode.json",
        }


class TaskSummaryAgentToolFollowupTests(unittest.TestCase):
    INITIAL_TOOL_CALL = [
        _tool_call_chunk(
            "call-map-1",
            "map_geocode",
            '{"address":"天安门","city":"北京"}',
        )
    ]

    def _run_agent(self, responses):
        model = FakeModel(responses)
        tool_manager = FakeToolManager()
        agent = TaskSummaryAgent(
            model=model,
            model_config={"max_input_bytes": 20000, "max_conversation_bytes": 12000},
        )
        chunks = []
        for batch in agent.run_stream(
            messages=[
                {
                    "role": "user",
                    "content": "帮我规划北京三天两夜行程",
                    "type": "normal",
                }
            ],
            tool_manager=tool_manager,
            session_id="summary-test",
            system_context={},
        ):
            chunks.extend(batch)
        final_text = "".join(
            chunk.get("content", "")
            for chunk in chunks
            if chunk.get("type") == "final_answer"
        )
        return model, tool_manager, final_text

    def test_tool_result_is_sent_back_to_model_before_final_answer(self):
        model, tool_manager, final_text = self._run_agent([
            self.INITIAL_TOOL_CALL,
            [_content_chunk("已查询到天安门坐标，可作为第一天核心点位。")],
        ])

        self.assertIn("已查询到天安门坐标", final_text)
        self.assertEqual(len(model.chat.completions.calls), 2)
        self.assertEqual(len(tool_manager.calls), 1)

        followup_messages = model.chat.completions.calls[1]["messages"]
        self.assertTrue(any(message.get("role") == "tool" for message in followup_messages))
        self.assertIn("tools", model.chat.completions.calls[1])

    def test_raw_tool_followup_output_is_replaced_by_readable_fallback(self):
        _, _, final_text = self._run_agent([
            self.INITIAL_TOOL_CALL,
            [_content_chunk(
                '[map_geocode] 结果:\n'
                '{"formatted_address":"北京市东城区天安门",'
                '"debug_path":"D:\\\\travel-agent\\\\cache\\\\geocode.json"}'
            )],
        ])

        self.assertIn("已完成必要的信息查询", final_text)
        self.assertIn("北京市东城区天安门", final_text)
        for leaked_value in (
            "map_geocode",
            "formatted_address",
            "debug_path",
            "tool_call_id",
            r"D:\travel-agent",
        ):
            self.assertNotIn(leaked_value, final_text)
        self.assertFalse(final_text.lstrip().startswith(("{", "[")))

    def test_model_can_request_another_tool_after_receiving_tool_result(self):
        model, tool_manager, final_text = self._run_agent([
            self.INITIAL_TOOL_CALL,
            [_tool_call_chunk(
                "call-map-2",
                "map_geocode",
                '{"address":"故宫","city":"北京"}',
            )],
            [_content_chunk("天安门和故宫适合安排在同一天步行游览。")],
        ])

        self.assertEqual(len(model.chat.completions.calls), 3)
        self.assertEqual(len(tool_manager.calls), 2)
        self.assertEqual(tool_manager.calls[1]["kwargs"]["address"], "故宫")
        third_messages = model.chat.completions.calls[2]["messages"]
        self.assertEqual(
            sum(message.get("role") == "tool" for message in third_messages),
            2,
        )
        self.assertIn("天安门和故宫", final_text)

    def test_tool_loop_is_limited_and_forced_final_request_has_no_tools(self):
        repeated_tool_calls = [
            [_tool_call_chunk(
                f"call-map-{index}",
                "map_geocode",
                f'{{"address":"地点{index}","city":"北京"}}',
            )]
            for index in range(1, TaskSummaryAgent.MAX_SUMMARY_TOOL_ROUNDS + 1)
        ]
        model, tool_manager, final_text = self._run_agent([
            *repeated_tool_calls,
            [_content_chunk("已有地点信息足够制定行程，建议按相邻区域顺序游览。")],
        ])

        self.assertEqual(
            len(tool_manager.calls),
            TaskSummaryAgent.MAX_SUMMARY_TOOL_ROUNDS,
        )
        self.assertEqual(
            len(model.chat.completions.calls),
            TaskSummaryAgent.MAX_SUMMARY_TOOL_ROUNDS + 1,
        )
        self.assertNotIn("tools", model.chat.completions.calls[-1])
        self.assertIn("已有地点信息足够", final_text)


if __name__ == "__main__":
    unittest.main()
