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

from agents.tool.map_request_governor import MapRequestGovernor, MapRequestLimitExceeded
from agents.tool.tool_base import McpToolSpec, SseServerParameters
from agents.tool.tool_manager import ToolManager


class MapRequestGovernorTests(unittest.TestCase):
    def test_same_place_query_is_normalized_cached_and_deduplicated(self):
        governor = MapRequestGovernor(max_requests_per_scope=10, min_interval_seconds=0)
        calls = 0

        def execute():
            nonlocal calls
            calls += 1
            return {"places": [{"name": "西湖"}]}

        first = governor.execute(
            tool_name="map_search_places",
            session_id="trip-1",
            kwargs={"query": "杭州 西湖", "region": "杭州"},
            callback=execute,
        )
        second = governor.execute(
            tool_name="map_search_places",
            session_id="trip-1",
            kwargs={"keywords": "杭州西湖", "city": "杭州"},
            callback=execute,
        )

        self.assertEqual(first, second)
        self.assertEqual(calls, 1)
        self.assertEqual(governor.scope_usage("trip-1"), 1)

    def test_concurrent_duplicate_query_executes_once(self):
        governor = MapRequestGovernor(max_requests_per_scope=10, max_concurrency=2, min_interval_seconds=0)
        lock = threading.Lock()
        calls = 0

        def execute():
            nonlocal calls
            with lock:
                calls += 1
            time.sleep(0.03)
            return {"places": [{"name": "故宫"}]}

        def request():
            return governor.execute(
                tool_name="map_geocode",
                session_id="trip-2",
                kwargs={"address": "北京故宫", "city": "北京"},
                callback=execute,
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: request(), range(4)))

        self.assertEqual(calls, 1)
        self.assertTrue(all(result == results[0] for result in results))

    def test_scope_budget_limits_actual_requests_and_can_be_reset(self):
        governor = MapRequestGovernor(max_requests_per_scope=3, min_interval_seconds=0)
        governor.begin_scope("trip-3", "planning-a")

        for index in range(3):
            governor.execute(
                tool_name="map_search_places",
                session_id="trip-3",
                kwargs={"query": f"候选地点{index}"},
                callback=lambda: {"places": []},
            )
        with self.assertRaises(MapRequestLimitExceeded):
            governor.execute(
                tool_name="map_search_places",
                session_id="trip-3",
                kwargs={"query": "超额地点"},
                callback=lambda: {"places": []},
            )

        governor.begin_scope("trip-3", "planning-b")
        governor.execute(
            tool_name="map_search_places",
            session_id="trip-3",
            kwargs={"query": "新规划地点"},
            callback=lambda: {"places": []},
        )
        self.assertEqual(governor.scope_usage("trip-3"), 1)

    def test_rate_and_concurrency_peak_are_bounded(self):
        governor = MapRequestGovernor(
            max_requests_per_scope=10,
            max_concurrency=1,
            min_interval_seconds=0.015,
        )
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
                kwargs={"query": f"地点{index}"},
                callback=execute,
            )

        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(request, range(3)))

        self.assertEqual(peak, 1)
        ordered_starts = sorted(started_at)
        self.assertTrue(all(later - earlier >= 0.012 for earlier, later in zip(ordered_starts, ordered_starts[1:])))

    def test_tool_manager_uses_shared_cache_and_returns_limit_error(self):
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
        first = manager.run_tool("map_search_places", [], "tool-manager", query="北京故宫")
        second = manager.run_tool("map_search_places", [], "tool-manager", query="北京故宫")
        limited = json.loads(manager.run_tool("map_search_places", [], "tool-manager", query="北京颐和园"))

        self.assertEqual(first, second)
        self.assertEqual(calls, 1)
        self.assertEqual(limited["error_type"], "MAP_REQUEST_LIMIT")


if __name__ == "__main__":
    unittest.main()
