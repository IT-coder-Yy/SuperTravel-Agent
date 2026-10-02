import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agents.agent.direct_executor_agent.direct_executor_agent import DirectExecutorAgent
from agents.agent.executor_agent.executor_agent import ExecutorAgent


def _chunk(tool_call_id, name, arguments):
    function = SimpleNamespace(name=name, arguments=arguments)
    tool_call = SimpleNamespace(id=tool_call_id, type="function", function=function)
    delta = SimpleNamespace(tool_calls=[tool_call])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


class StreamingToolCallArgumentsTests(unittest.TestCase):
    def test_executor_accepts_none_arguments_in_first_chunk(self):
        agent = object.__new__(ExecutorAgent)
        tool_calls = {}

        list(agent._handle_tool_calls_chunk(_chunk("call-1", "query_ticket", None), tool_calls, None))
        list(agent._handle_tool_calls_chunk(_chunk(None, None, '{"from":"杭州"}'), tool_calls, "call-1"))

        self.assertEqual(tool_calls["call-1"]["function"]["arguments"], '{"from":"杭州"}')

    def test_direct_executor_accepts_none_arguments_in_first_chunk(self):
        agent = object.__new__(DirectExecutorAgent)
        tool_calls = {}

        agent._handle_tool_calls_chunk(_chunk("call-1", "query_ticket", None), tool_calls, None)
        agent._handle_tool_calls_chunk(_chunk(None, None, '{"from":"杭州"}'), tool_calls, "call-1")

        self.assertEqual(tool_calls["call-1"]["function"]["arguments"], '{"from":"杭州"}')


if __name__ == "__main__":
    unittest.main()


def test_direct_executor_stops_after_answer_but_continues_after_tools():
    agent = object.__new__(DirectExecutorAgent)
    agent.MAX_LOOP_COUNT = 3
    agent._merge_messages = lambda messages, chunks: [*messages, *chunks]
    calls = []
    def respond(**kwargs):
        calls.append(True)
        chunk = {'role': 'assistant', 'content': '联调通过', 'type': 'do_subtask_result'}
        kwargs['all_new_response_chunks'].append(chunk)
        yield [chunk]
        return False
    agent._call_llm_and_process_response = respond
    output = list(agent._execute_loop([], [], None, 'direct-test'))
    assert len(calls) == len(output) == 1
    assert not agent._should_stop_execution([
        {'role': 'assistant', 'tool_calls': [{'id': 'call-1'}]},
        {'role': 'tool', 'content': '检索完成'},
    ])
