import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agents.agent.agent_controller import AgentController
from agents.agent.observation_agent.observation_agent import ObservationAgent
from agents.agent.planning_agent.planning_agent import PlanningAgent
from agents.tool.train_ticket_tool import TrainTicketTool
from services.chat_service import (
    _build_realtime_only_answer,
    _build_ticket_mode_state,
    _build_ticket_status_note,
    _extract_route_from_query,
    _select_ticket_final_answer,
    _ticket_bundle_has_valid_results,
    execute_chat_once,
    maybe_prepare_train_ticket_bundle,
)


def _flight_payload(travel_date: str):
    return {
        "status": "success",
        "flights": [
            {
                "flightNo": "CZ100",
                "airline": "南航",
                "fromAirport": "杭州萧山",
                "toAirport": "南京禄口",
                "departureDate": travel_date,
                "departureTime": "08:00",
                "arrivalDate": travel_date,
                "arrivalTime": "09:10",
                "duration": "1小时10分钟",
                "cabinClass": "经济舱",
                "price": "500",
            }
        ],
    }


def _bus_payload(travel_date: str):
    return {
        "status": "success",
        "buses": [
            {
                "busNo": "班次B1",
                "from_station": "杭州汽车站",
                "to_station": "南京汽车站",
                "depart_date": travel_date,
                "depart_time": "09:00",
                "duration": "4小时",
                "bus_type": "大巴",
                "price": "120",
            }
        ],
    }


class RecordingTicketManager:
    def __init__(self, train_result=None, flight_result=None, bus_result=None):
        self.train_result = train_result
        self.flight_result = flight_result
        self.bus_result = bus_result
        self.calls = []

    def get_tool(self, name):
        if name == "get-tickets":
            return object()
        if name == "query_flight_tickets":
            return SimpleNamespace(
                parameters={"travel_date": {}, "from_city": {}, "to_city": {}, "max_results": {}},
                required=["travel_date", "from_city", "to_city"],
            )
        if name == "query_bus_tickets":
            return SimpleNamespace(
                parameters={"travel_date": {}, "from_city": {}, "to_city": {}, "max_results": {}},
                required=["travel_date", "from_city", "to_city"],
            )
        return None

    def run_tool(self, name, messages, session_id, **kwargs):
        self.calls.append((name, kwargs))
        result = {
            "get-tickets": self.train_result,
            "query_flight_tickets": self.flight_result,
            "query_bus_tickets": self.bus_result,
        }[name]
        if isinstance(result, Exception):
            raise result
        return result


class Fake12306Response:
    def __init__(self, payload=None, text="", headers=None):
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.headers = headers or {"content-type": "application/json"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class Fake12306Session:
    def __init__(self):
        self.calls = []
        self.headers = {}
        self.cookies = {"RAIL_EXPIRATION": "test"}

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params or {}, headers or {}))
        if url.endswith("/leftTicket/init") or url == TrainTicketTool.INDEX_URL:
            return Fake12306Response(payload={}, text="<html></html>", headers={"content-type": "text/html"})
        if url.endswith("/leftTicket/query"):
            return Fake12306Response({"status": False, "c_url": "leftTicket/queryG"})
        if url.endswith("/leftTicket/queryG"):
            return Fake12306Response({"status": True, "data": {"result": ["row"], "map": {"HZH": "杭州"}}})
        return Fake12306Response({"status": False})


class FallbackTicketManager:
    def __init__(self):
        self.calls = []

    def get_tool(self, name):
        if name in {"get-tickets", "query_12306_realtime_tickets"}:
            return object()
        return None

    def run_tool(self, name, messages, session_id, **kwargs):
        self.calls.append((name, kwargs))
        if name == "get-tickets":
            return {"error": True, "message": "12306网关拦截"}
        if name == "query_12306_realtime_tickets":
            return {
                "status": "success",
                "tickets": [
                    {
                        "train_code": "G88",
                        "from_station": kwargs["from_station"],
                        "to_station": kwargs["to_station"],
                        "depart_time": "08:00",
                        "arrive_time": "10:00",
                        "duration": "02:00",
                        "seat_recommendation": "二等座",
                        "seat_left": "有",
                        "price": "120元",
                    }
                ],
            }
        raise AssertionError(f"unexpected tool: {name}")


class TicketQueryFixTests(unittest.TestCase):
    def _prepare_bundle(self, manager):
        with patch("services.chat_service._load_12306_station_names", return_value=["杭州", "南京"]):
            return maybe_prepare_train_ticket_bundle(
                user_query="给我查一下明天从杭州到南京的票",
                tool_manager=manager,
                message_history=[
                    {
                        "role": "user",
                        "content": "给我查一下明天从杭州到南京的票",
                        "message_id": "u-ticket",
                        "type": "normal",
                    }
                ],
                session_id="s-ticket",
            )

    def test_shared_route_parser_removes_request_prefix_and_ticket_suffix(self):
        query = "给我查一下明天从杭州到南京的票"

        self.assertEqual(_extract_route_from_query(query), ("杭州", "南京"))
        self.assertEqual(TrainTicketTool()._extract_route_from_query(query), ("杭州", "南京"))

    def test_all_ticket_modes_receive_the_same_normalized_route(self):
        manager = RecordingTicketManager(
            train_result=[],
            flight_result={"status": "success", "flights": []},
            bus_result={"status": "success", "buses": []},
        )

        bundle = self._prepare_bundle(manager)

        self.assertEqual(bundle["route"]["from_station"], "杭州")
        self.assertEqual(bundle["route"]["to_station"], "南京")
        train_call = next(kwargs for name, kwargs in manager.calls if name == "get-tickets")
        flight_call = next(kwargs for name, kwargs in manager.calls if name == "query_flight_tickets")
        bus_call = next(kwargs for name, kwargs in manager.calls if name == "query_bus_tickets")
        self.assertEqual((train_call["fromStation"], train_call["toStation"]), ("杭州", "南京"))
        self.assertEqual((flight_call["from_city"], flight_call["to_city"]), ("杭州", "南京"))
        self.assertEqual((bus_call["from_city"], bus_call["to_city"]), ("杭州", "南京"))

    def test_left_ticket_query_follows_dynamic_c_url_endpoint(self):
        session = Fake12306Session()

        with patch("agents.tool.train_ticket_tool.requests.Session", return_value=session):
            payload = TrainTicketTool()._fetch_left_tickets(
                from_code="HZH",
                to_code="NJH",
                travel_date="2026-06-06",
                purpose_codes="ADULT",
            )

        query_urls = [url for url, _params, _headers in session.calls if "/leftTicket/query" in url]
        self.assertEqual(query_urls[0], TrainTicketTool.LEFT_TICKET_URL)
        self.assertIn("https://kyfw.12306.cn/otn/leftTicket/queryG", query_urls)
        self.assertEqual(payload["result"], ["row"])

    def test_mcp_gateway_error_falls_back_to_local_12306_tool(self):
        manager = FallbackTicketManager()

        bundle = self._prepare_bundle(manager)

        called_tools = [name for name, _kwargs in manager.calls]
        self.assertEqual(called_tools, ["get-tickets", "query_12306_realtime_tickets"])
        self.assertTrue(bundle["has_valid_results"])
        self.assertEqual(bundle["ticket_states"]["train"]["status"], "success")
        self.assertEqual(bundle["direct_rows"][0]["trip_no"], "G88")
        self.assertEqual(bundle["direct_source"], "get-tickets, query_12306_realtime_tickets")

    def test_train_success_is_preserved_when_flight_and_bus_fail(self):
        manager = RecordingTicketManager(
            train_result=[
                {
                    "train_code": "G101",
                    "from_station_name": "杭州",
                    "to_station_name": "南京",
                    "start_time": "08:00",
                    "arrive_time": "10:00",
                    "lishi": "02:00",
                    "prices": [{"seat_name": "二等座", "price": 200, "num": "有"}],
                }
            ],
            flight_result={"error": True, "message": "flight down"},
            bus_result={"error": True, "message": "bus down"},
        )

        bundle = self._prepare_bundle(manager)
        answer = _build_realtime_only_answer(bundle)

        self.assertTrue(bundle["has_valid_results"])
        self.assertEqual(bundle["ticket_states"]["train"]["status"], "success")
        self.assertEqual(bundle["ticket_states"]["flight"]["status"], "error")
        self.assertEqual(bundle["ticket_states"]["bus"]["status"], "error")
        self.assertIn("G101", answer)
        self.assertIn("飞机查询失败", answer)
        self.assertIn("大巴查询失败", answer)

    def test_flight_success_is_preserved_when_other_modes_fail(self):
        manager = RecordingTicketManager(
            train_result=RuntimeError("train down"),
            flight_result=_flight_payload("2026-06-05"),
            bus_result={"error": True, "message": "bus down"},
        )

        bundle = self._prepare_bundle(manager)
        answer = _build_realtime_only_answer(bundle)

        self.assertTrue(bundle["has_valid_results"])
        self.assertEqual(bundle["ticket_states"]["train"]["status"], "error")
        self.assertEqual(bundle["ticket_states"]["flight"]["status"], "success")
        self.assertEqual(bundle["ticket_states"]["bus"]["status"], "error")
        self.assertIn("南航CZ100", answer)

    def test_bus_success_is_preserved_when_other_modes_fail(self):
        manager = RecordingTicketManager(
            train_result={"error": True, "message": "train down"},
            flight_result={"error": True, "message": "flight down"},
            bus_result=_bus_payload("2026-06-05"),
        )

        bundle = self._prepare_bundle(manager)
        answer = _build_realtime_only_answer(bundle)

        self.assertTrue(bundle["has_valid_results"])
        self.assertEqual(bundle["ticket_states"]["train"]["status"], "error")
        self.assertEqual(bundle["ticket_states"]["flight"]["status"], "error")
        self.assertEqual(bundle["ticket_states"]["bus"]["status"], "success")
        self.assertIn("班次B1", answer)

    def test_only_all_empty_or_failed_modes_return_not_found(self):
        manager = RecordingTicketManager(
            train_result=[],
            flight_result={"status": "success", "flights": []},
            bus_result={"error": True, "message": "bus down"},
        )

        bundle = self._prepare_bundle(manager)
        answer = _build_realtime_only_answer(bundle)

        self.assertFalse(bundle["has_valid_results"])
        self.assertFalse(_ticket_bundle_has_valid_results(bundle))
        self.assertIn("未找到可确认的票务结果", answer)

    def test_partial_train_failure_is_not_misclassified_as_empty(self):
        state = _build_ticket_mode_state(
            rows=[],
            sources={"direct": "get-tickets", "interline": "get-interline-tickets"},
            attempted=True,
            successful_attempt=True,
            errors=["中转票查询失败"],
        )

        self.assertEqual(state["status"], "error")

    def test_success_status_note_hides_internal_partial_failure_details(self):
        bundle = {
            "has_valid_results": True,
            "direct_rows": [{"trip_no": "G101"}],
            "interline_rows": [],
            "flight_rows": [],
            "bus_rows": [],
            "ticket_states": {
                "train": {"status": "success", "result_count": 1, "error": "中转票查询失败"},
                "flight": {"status": "empty", "result_count": 0, "error": ""},
                "bus": {"status": "empty", "result_count": 0, "error": ""},
            },
        }

        status_note = _build_ticket_status_note(bundle)
        self.assertIn("部分渠道未返回", status_note)
        self.assertNotIn("中转票查询失败", status_note)

    def test_empty_prequery_does_not_overwrite_later_valid_answer(self):
        later_answer = "已通过后续查询确认火车车次 G123。\n\n火车和高铁票信息表\n| 班次 |\n| --- |\n| G123 |"
        empty_bundle = {
            "has_valid_results": False,
            "ticket_states": {
                "train": {"status": "empty", "result_count": 0, "error": ""},
                "flight": {"status": "error", "result_count": 0, "error": "flight down"},
                "bus": {"status": "error", "result_count": 0, "error": "bus down"},
            },
            "enforce_realtime_only": True,
            "realtime_only_answer": "未找到可确认的票务结果。",
            "append_markdown": "火车和高铁票信息表\n\n未查到实时可售车票",
        }

        self.assertEqual(_select_ticket_final_answer(later_answer, empty_bundle), later_answer)

    def test_empty_prequery_preserves_later_answer_with_confirmed_train_code(self):
        later_answer = "后续实时工具已经确认火车车次 G123 可售。"
        empty_bundle = {
            "has_valid_results": False,
            "ticket_states": {
                "train": {"status": "empty", "result_count": 0, "error": ""},
                "flight": {"status": "error", "result_count": 0, "error": "flight down"},
                "bus": {"status": "error", "result_count": 0, "error": "bus down"},
            },
            "realtime_only_answer": "未找到可确认的票务结果。",
            "append_markdown": "火车和高铁票信息表\n\n未查到实时可售车票",
        }

        self.assertEqual(_select_ticket_final_answer(later_answer, empty_bundle), later_answer)

    def test_later_ticket_table_is_merged_with_prequery_results(self):
        later_answer = "后续查询确认飞机班次 MU100。\n\n飞机票信息表\n| 班次 |\n| --- |\n| MU100 |"
        bundle = {
            "has_valid_results": True,
            "direct_rows": [{"trip_no": "G101"}],
            "interline_rows": [],
            "flight_rows": [],
            "bus_rows": [],
            "ticket_states": {
                "train": {"status": "success", "result_count": 1, "error": ""},
                "flight": {"status": "empty", "result_count": 0, "error": ""},
                "bus": {"status": "empty", "result_count": 0, "error": ""},
            },
            "append_markdown": "火车和高铁票信息表\n| 班次 |\n| --- |\n| G101 |",
        }

        merged = _select_ticket_final_answer(later_answer, bundle)

        self.assertIn("MU100", merged)
        self.assertIn("G101", merged)

    def test_empty_ticket_bundle_returns_directly_without_waiting_for_later_agent_result(self):
        class FakeController:
            def __init__(self):
                self.messages = []

            def run(self, messages, tool_manager, session_id, deep_thinking, summary, deep_research):
                self.messages = messages
                final_answer = "后续工具确认车次 G123。\n\n火车和高铁票信息表\n| 班次 |\n| --- |\n| G123 |"
                return {
                    "all_messages": [{"role": "assistant", "type": "final_answer", "content": final_answer}],
                    "new_messages": [{"role": "assistant", "type": "final_answer", "content": final_answer}],
                    "final_output": {"role": "assistant", "type": "final_answer", "content": final_answer},
                }

        controller = FakeController()
        empty_bundle = {
            "has_valid_results": False,
            "ticket_states": {
                "train": {"status": "empty", "result_count": 0, "error": ""},
                "flight": {"status": "error", "result_count": 0, "error": "flight down"},
                "bus": {"status": "error", "result_count": 0, "error": "bus down"},
            },
            "context_message": "【实时票务查询结果】三种票务均无可确认结果",
            "realtime_only_answer": "未找到可确认的票务结果。",
            "append_markdown": "火车和高铁票信息表\n\n未查到实时可售车票",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=empty_bundle):
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=None):
                with patch("services.chat_service.maybe_prepare_travel_rag_context", return_value="旅行RAG上下文"):
                    payload = execute_chat_once(
                        request_messages=[
                            SimpleNamespace(
                                role="user",
                                content="给我查一下明天从杭州到南京的票",
                                message_id="u-rag-ticket",
                                type="normal",
                            )
                        ],
                        controller=controller,
                        tool_manager=object(),
                        session_id="s-rag-ticket",
                        use_deepthink=True,
                        use_multi_agent=False,
                    )

        self.assertEqual(controller.messages, [])
        self.assertIn("未找到可确认的票务结果", payload["result"]["final_output"]["content"])
        self.assertNotIn("G123", payload["result"]["final_output"]["content"])


class AgentTicketContextAndGuardTests(unittest.TestCase):
    def test_realtime_ticket_system_context_is_visible_to_planning_and_observation(self):
        messages = [
            {"role": "user", "content": "查票", "type": "normal"},
            {
                "role": "system",
                "content": "【实时票务查询结果】火车票状态: success",
                "type": "system_realtime_ticket_context",
            },
        ]
        planning_agent = PlanningAgent.__new__(PlanningAgent)
        planning_agent.max_conversation_bytes = 10000
        observation_agent = ObservationAgent.__new__(ObservationAgent)
        observation_agent.max_conversation_bytes = 10000

        planning_context = planning_agent._extract_completed_actions(messages)
        observation_context = observation_agent._extract_execution_results_to_str(messages)

        self.assertIn("实时票务查询结果", planning_context)
        self.assertIn("实时票务查询结果", observation_context)

    def test_realtime_ticket_context_survives_long_completed_actions(self):
        messages = [
            {"role": "user", "content": "查票", "type": "normal"},
            {
                "role": "system",
                "content": "【实时票务查询结果】火车票状态: success，飞机票状态: error，大巴票状态: empty",
                "type": "system_realtime_ticket_context",
            },
            {"role": "tool", "content": "很长的工具结果" * 2000, "type": "tool_call_result"},
        ]
        planning_agent = PlanningAgent.__new__(PlanningAgent)
        planning_agent.max_conversation_bytes = 2200

        completed_actions = planning_agent._extract_completed_actions(messages)

        self.assertIn("实时票务查询结果", completed_actions)
        self.assertLessEqual(len(completed_actions.encode("utf-8")), 2200)

    def test_planning_required_tools_are_filtered_to_real_available_names(self):
        planning_agent = PlanningAgent.__new__(PlanningAgent)
        xml = """
<next_step_description>查询票务</next_step_description>
<required_tools>["query_station_code", "get-tickets", "query_bus_tickets", "search_trains"]</required_tools>
<expected_output>返回票务结果</expected_output>
<success_criteria>获得可确认结果</success_criteria>
"""

        result = planning_agent.convert_xlm_to_json(xml, ["get-tickets", "query_bus_tickets"])

        self.assertEqual(result["next_step"]["required_tools"], ["get-tickets", "query_bus_tickets"])

    def test_repeated_tool_call_stops_non_stream_loop_before_ten_rounds(self):
        class FakePlanningAgent:
            def run(self, messages, tool_manager, system_context=None):
                return [
                    {
                        "role": "assistant",
                        "type": "planning_result",
                        "content": 'Planning: {"next_step": {"description": "查车站", "required_tools": ["get-station-code"]}}',
                    }
                ]

        class FakeExecutorAgent:
            def __init__(self):
                self.calls = 0

            def run(self, messages, tool_manager, session_id=None, system_context=None):
                self.calls += 1
                return [
                    {
                        "role": "assistant",
                        "type": "tool_call",
                        "tool_calls": [
                            {
                                "id": f"call-{self.calls}",
                                "type": "function",
                                "function": {
                                    "name": "get-station-code",
                                    "arguments": '{"station":"杭州"}',
                                },
                            }
                        ],
                    }
                ]

        class FakeObservationAgent:
            def run(self, messages, system_context=None):
                return [
                    {
                        "role": "assistant",
                        "type": "observation_result",
                        "content": "Observation: "
                        + json.dumps(
                            {
                                "needs_more_input": False,
                                "finish_percent": 10,
                                "is_completed": False,
                                "analysis": "继续查询",
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]

        controller = AgentController.__new__(AgentController)
        controller.planning_agent = FakePlanningAgent()
        controller.executor_agent = FakeExecutorAgent()
        controller.observation_agent = FakeObservationAgent()
        all_messages = [{"role": "user", "content": "查杭州车站代码", "type": "normal"}]
        new_messages = []

        controller._execute_main_loop_non_stream(
            all_messages=all_messages,
            new_messages=new_messages,
            tool_manager=None,
            session_id="s-loop",
            max_loop_count=10,
            system_context={},
        )

        self.assertEqual(controller.executor_agent.calls, 2)
        self.assertTrue(any(msg.get("type") == "system_loop_guard_context" for msg in all_messages))

    def test_repeated_tool_call_stops_stream_loop_before_ten_rounds(self):
        class FakeMerger:
            def _merge_messages(self, messages, chunk):
                messages.extend(chunk)
                return messages

        class FakePlanningAgent:
            def run_stream(self, messages, tool_manager, system_context=None, session_id=None):
                yield [
                    {
                        "role": "assistant",
                        "type": "planning_result",
                        "content": 'Planning: {"next_step": {"description": "查车站", "required_tools": ["get-station-code"]}}',
                    }
                ]

        class FakeExecutorAgent:
            def __init__(self):
                self.calls = 0

            def run_stream(self, messages, tool_manager, system_context=None, session_id=None):
                self.calls += 1
                yield [
                    {
                        "role": "assistant",
                        "type": "tool_call",
                        "tool_calls": [
                            {
                                "id": f"stream-call-{self.calls}",
                                "type": "function",
                                "function": {
                                    "name": "get-station-code",
                                    "arguments": '{"station":"杭州"}',
                                },
                            }
                        ],
                    }
                ]

        class FakeObservationAgent:
            def run_stream(self, messages, tool_manager, system_context=None, session_id=None):
                yield [
                    {
                        "role": "assistant",
                        "type": "observation_result",
                        "content": "Observation: "
                        + json.dumps(
                            {
                                "needs_more_input": False,
                                "finish_percent": 10,
                                "is_completed": False,
                                "analysis": "继续查询",
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]

        controller = AgentController.__new__(AgentController)
        controller.task_analysis_agent = FakeMerger()
        controller.planning_agent = FakePlanningAgent()
        controller.executor_agent = FakeExecutorAgent()
        controller.observation_agent = FakeObservationAgent()
        all_messages = [{"role": "user", "content": "查杭州车站代码", "type": "normal"}]

        list(
            controller._execute_main_loop(
                all_messages=all_messages,
                tool_manager=None,
                system_context={},
                session_id="s-stream-loop",
                max_loop_count=10,
            )
        )

        self.assertEqual(controller.executor_agent.calls, 2)
        self.assertTrue(any(msg.get("type") == "system_loop_guard_context" for msg in all_messages))

    def test_stagnant_finish_percent_triggers_loop_guard(self):
        controller = AgentController.__new__(AgentController)
        guard_state = {}
        messages = []
        reason = ""

        for index in range(3):
            messages.append(
                {
                    "role": "assistant",
                    "type": "observation_result",
                    "content": f'Observation: {{"finish_percent": 20, "is_completed": false, "round": {index}}}',
                }
            )
            reason = controller._update_stagnation_guard(messages, guard_state)

        self.assertIn("连续未增长", reason)


if __name__ == "__main__":
    unittest.main()
