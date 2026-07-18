import asyncio
import json
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agents.tool.map_request_governor import MapRequestGovernor
from agents.tool.baidu_request_dispatcher import BaiduRequestDispatcher
from agents.tool.tool_base import McpToolSpec, SseServerParameters
from agents.tool.tool_manager import ToolManager


class MapRequestGovernorTests(unittest.TestCase):
    def test_tool_manager_uses_injected_application_dispatcher(self):
        dispatcher = BaiduRequestDispatcher(min_interval_seconds=0)
        manager = ToolManager(
            is_auto_discover=False,
            baidu_request_dispatcher=dispatcher,
        )

        self.assertIs(manager.baidu_request_dispatcher, dispatcher)
        self.assertIs(manager.map_request_governor.dispatcher, dispatcher)

    def test_separate_governors_share_dispatcher_cache(self):
        dispatcher = BaiduRequestDispatcher(min_interval_seconds=0)
        first_governor = MapRequestGovernor(dispatcher=dispatcher)
        second_governor = MapRequestGovernor(dispatcher=dispatcher)
        calls = 0

        def execute():
            nonlocal calls
            calls += 1
            return {"places": [{"uid": "poi-1"}]}

        first = first_governor.execute(
            tool_name="map_search_places",
            session_id="first",
            kwargs={"query": "Beijing Palace", "region": "Beijing"},
            callback=execute,
        )
        second = second_governor.execute(
            tool_name="map_search_places",
            session_id="second",
            kwargs={"keywords": "BeijingPalace", "city": "Beijing"},
            callback=execute,
        )

        self.assertEqual(first, second)
        self.assertEqual(calls, 1)

    def test_baidu_mcp_execution_obeys_network_timeout(self):
        manager = ToolManager(is_auto_discover=False)
        manager.baidu_network_timeout_seconds = 0.01
        tool = McpToolSpec(
            name="map_search_places",
            description="map",
            func=lambda: None,
            parameters={},
            required=[],
            server_name="baidu-map",
            server_params=SseServerParameters(url="http://unused"),
        )

        async def slow_execution(*args, **kwargs):
            await asyncio.sleep(0.1)

        manager._execute_sse_mcp_tool = slow_execution
        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(manager._run_mcp_tool_async(tool, "session"))

    def test_same_place_query_is_normalized_cached_and_deduplicated(self):
        governor = MapRequestGovernor(min_interval_seconds=0)
        calls = 0

        def execute():
            nonlocal calls
            calls += 1
            return {"places": [{"name": "West Lake"}]}

        first = governor.execute(
            tool_name="map_search_places",
            session_id="trip-1",
            kwargs={"query": "Hangzhou West Lake", "region": "Hangzhou"},
            callback=execute,
        )
        second = governor.execute(
            tool_name="map_search_places",
            session_id="trip-1",
            kwargs={"keywords": "HangzhouWestLake", "city": "Hangzhou"},
            callback=execute,
        )

        self.assertEqual(first, second)
        self.assertEqual(calls, 1)
        self.assertEqual(governor.scope_usage("trip-1"), 1)

    def test_concurrent_duplicate_query_executes_once(self):
        governor = MapRequestGovernor(min_interval_seconds=0)
        lock = threading.Lock()
        calls = 0

        def execute():
            nonlocal calls
            with lock:
                calls += 1
            time.sleep(0.03)
            return {"places": [{"name": "Forbidden City"}]}

        def request():
            return governor.execute(
                tool_name="map_geocode",
                session_id="trip-2",
                kwargs={"address": "Beijing Forbidden City", "city": "Beijing"},
                callback=execute,
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: request(), range(4)))

        self.assertEqual(calls, 1)
        self.assertTrue(all(result == results[0] for result in results))

    def test_scope_tracks_requests_without_hard_cap(self):
        governor = MapRequestGovernor(max_requests_per_scope=1, min_interval_seconds=0)
        governor.begin_scope("trip-3", "planning-a")

        for index in range(4):
            governor.execute(
                tool_name="map_search_places",
                session_id="trip-3",
                kwargs={"query": f"candidate-{index}"},
                callback=lambda: {"places": []},
            )

        self.assertEqual(governor.scope_usage("trip-3"), 4)
        governor.begin_scope("trip-3", "planning-b")
        self.assertEqual(governor.scope_usage("trip-3"), 0)

    def test_rate_and_concurrency_peak_are_bounded(self):
        governor = MapRequestGovernor(min_interval_seconds=0.015)
        lock = threading.Lock()
        active = 0
        peak = 0
        started_at = []

        def request(index):
            def execute():
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                    started_at.append(time.monotonic())
                time.sleep(0.01)
                with lock:
                    active -= 1
                return {"index": index}

            return governor.execute(
                tool_name="map_search_places",
                session_id="trip-4",
                kwargs={"query": f"place-{index}"},
                callback=execute,
            )

        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(request, range(3)))

        self.assertEqual(peak, 1)
        ordered_starts = sorted(started_at)
        self.assertTrue(
            all(later - earlier >= 0.012 for earlier, later in zip(ordered_starts, ordered_starts[1:]))
        )

    def test_tool_manager_uses_shared_cache_without_scope_limit(self):
        governor = MapRequestGovernor(max_requests_per_scope=1, min_interval_seconds=0)
        manager = ToolManager(is_auto_discover=False, map_request_governor=governor)
        manager.tools["map_search_places"] = McpToolSpec(
            name="map_search_places",
            description="map",
            func=lambda: None,
            parameters={"query": {"type": "string"}},
            required=["query"],
            server_name="baidu-map",
            server_params=SseServerParameters(url="http://unused"),
        )
        calls = 0

        async def fake_mcp_call(tool, session_id, **kwargs):
            nonlocal calls
            calls += 1
            return {"content": [{"type": "text", "text": json.dumps({"query": kwargs["query"]})}]}

        manager._run_mcp_tool_async = fake_mcp_call
        first = manager.run_tool("map_search_places", [], "tool-manager", query="place-a")
        second = manager.run_tool("map_search_places", [], "tool-manager", query="place-a")
        third = manager.run_tool("map_search_places", [], "tool-manager", query="place-b")

        self.assertEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
