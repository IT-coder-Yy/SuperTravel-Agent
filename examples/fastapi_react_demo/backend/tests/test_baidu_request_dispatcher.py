import asyncio
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agents.tool.baidu_request_dispatcher import (
    BaiduRequestDispatcher,
    BaiduRequestQueueTimeout,
)
from agents.tool.tool_base import McpToolSpec, SseServerParameters
from agents.tool.tool_manager import ToolManager
from backend.services.route_providers.baidu import BaiduRouteProvider


class BaiduRequestDispatcherTests(unittest.TestCase):
    def test_runtime_defaults_enforce_product_minimums(self):
        with patch.dict(
            os.environ,
            {
                "BAIDU_MAP_MIN_INTERVAL_SECONDS": "0.45",
                "BAIDU_MAP_COOLDOWN_INTERVAL_SECONDS": "1",
                "BAIDU_MAP_QUEUE_TIMEOUT_SECONDS": "10",
            },
        ):
            dispatcher = BaiduRequestDispatcher()

        self.assertEqual(dispatcher.max_concurrency, 1)
        self.assertEqual(dispatcher.min_interval_seconds, 2)
        self.assertEqual(dispatcher.cooldown_interval_seconds, 5)
        self.assertEqual(dispatcher.queue_timeout_seconds, 300)

    def test_sync_and_async_requests_share_one_execution_slot(self):
        dispatcher = BaiduRequestDispatcher(min_interval_seconds=0, queue_timeout_seconds=1)
        lock = threading.Lock()
        active = 0
        peak = 0

        def enter():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)

        def leave():
            nonlocal active
            with lock:
                active -= 1

        async def run():
            async def async_callback():
                enter()
                await asyncio.sleep(0.03)
                leave()
                return "async"

            def sync_callback():
                enter()
                time.sleep(0.01)
                leave()
                return "sync"

            async_task = asyncio.create_task(dispatcher.execute_async(async_callback))
            await asyncio.sleep(0.005)
            sync_result = await asyncio.to_thread(dispatcher.execute, sync_callback)
            return await async_task, sync_result

        self.assertEqual(asyncio.run(run()), ("async", "sync"))
        self.assertEqual(peak, 1)

    def test_queue_timeout_does_not_start_waiting_callback(self):
        dispatcher = BaiduRequestDispatcher(min_interval_seconds=0, queue_timeout_seconds=0.03)
        entered = threading.Event()
        release = threading.Event()
        waiting_callback_started = False

        def blocker():
            entered.set()
            release.wait(timeout=1)

        worker = threading.Thread(target=lambda: dispatcher.execute(blocker))
        worker.start()
        self.assertTrue(entered.wait(timeout=1))

        def waiting_callback():
            nonlocal waiting_callback_started
            waiting_callback_started = True

        with self.assertRaises(BaiduRequestQueueTimeout):
            dispatcher.execute(waiting_callback)
        release.set()
        worker.join(timeout=1)

        self.assertFalse(waiting_callback_started)
        self.assertEqual(dispatcher.metrics_snapshot()["queue_timeouts"], 1)

    def test_pressure_response_applies_cooldown_before_next_start(self):
        dispatcher = BaiduRequestDispatcher(
            min_interval_seconds=0,
            cooldown_interval_seconds=0.03,
            queue_timeout_seconds=1,
        )
        dispatcher.execute(lambda: {"message": "rate limit"})
        started = time.monotonic()
        dispatcher.execute(lambda: {"status": 0})
        elapsed = time.monotonic() - started

        self.assertGreaterEqual(elapsed, 0.025)
        self.assertEqual(dispatcher.metrics_snapshot()["pressure_signals"], 1)

    def test_sync_owner_and_async_duplicate_share_singleflight_result(self):
        dispatcher = BaiduRequestDispatcher(min_interval_seconds=0, queue_timeout_seconds=1)
        entered = threading.Event()
        release = threading.Event()
        calls = 0

        def owner_callback():
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(timeout=1)
            return {"places": [{"uid": "poi-1"}]}

        owner_result = []
        owner = threading.Thread(
            target=lambda: owner_result.append(
                dispatcher.execute(
                    owner_callback,
                    provider="baidu-map",
                    operation="map_search_places",
                    arguments={"query": "Beijing Palace", "region": "Beijing"},
                    cache_ttl_seconds=60,
                )
            )
        )
        owner.start()
        self.assertTrue(entered.wait(timeout=1))

        async def duplicate_request():
            async def should_not_run():
                raise AssertionError("singleflight duplicate executed")

            task = asyncio.create_task(
                dispatcher.execute_async(
                    should_not_run,
                    provider="baidu-map",
                    operation="map_search_places",
                    arguments={"keywords": "BeijingPalace", "city": "Beijing"},
                    cache_ttl_seconds=60,
                )
            )
            await asyncio.sleep(0.01)
            release.set()
            return await task

        duplicate_result = asyncio.run(duplicate_request())
        owner.join(timeout=1)

        cached_result = dispatcher.execute(
            lambda: self.fail("cached duplicate executed"),
            provider="baidu-map",
            operation="map_search_places",
            arguments={"keywords": "BeijingPalace", "city": "Beijing"},
            cache_ttl_seconds=60,
        )

        self.assertEqual(calls, 1)
        self.assertEqual(owner_result[0], duplicate_result)
        self.assertEqual(owner_result[0], cached_result)
        metrics = dispatcher.metrics_snapshot()
        self.assertEqual(metrics["total_requests"], 3)
        self.assertEqual(metrics["provider_executions"], 1)
        self.assertEqual(metrics["singleflight_waits"], 1)
        self.assertEqual(metrics["cache_hits"], 1)
        self.assertEqual(metrics["peak_concurrency"], 1)

    def test_priority_queue_runs_formal_before_queued_supplemental(self):
        dispatcher = BaiduRequestDispatcher(min_interval_seconds=0, queue_timeout_seconds=1)
        blocker_entered = threading.Event()
        release_blocker = threading.Event()
        execution_order = []
        emitted_events = []
        dispatcher.add_event_listener(emitted_events.append)

        def blocker():
            blocker_entered.set()
            release_blocker.wait(timeout=1)

        blocker_thread = threading.Thread(target=lambda: dispatcher.execute(blocker, priority="formal"))
        blocker_thread.start()
        self.assertTrue(blocker_entered.wait(timeout=1))

        low_thread = threading.Thread(
            target=lambda: dispatcher.execute(
                lambda: execution_order.append("supplemental"),
                priority="supplemental",
            )
        )
        high_thread = threading.Thread(
            target=lambda: dispatcher.execute(
                lambda: execution_order.append("formal"),
                priority="formal",
            )
        )
        low_thread.start()
        time.sleep(0.01)
        high_thread.start()

        deadline = time.monotonic() + 1
        while len(dispatcher._queue) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        release_blocker.set()
        for thread in (blocker_thread, low_thread, high_thread):
            thread.join(timeout=1)

        self.assertEqual(execution_order, ["formal", "supplemental"])
        self.assertEqual(
            [event["type"] for event in emitted_events],
            ["provider_queue_waiting", "provider_queue_waiting"],
        )
        self.assertTrue(
            all(event["payload"]["message"] == "地点核验排队中" for event in emitted_events)
        )
        metrics = dispatcher.metrics_snapshot()
        self.assertEqual(metrics["provider_executions"], 3)
        self.assertEqual(metrics["queue_waits"], 2)
        self.assertGreater(metrics["total_queue_wait_ms"], 0)
        self.assertEqual(metrics["peak_concurrency"], 1)

    def test_provider_error_response_is_not_cached(self):
        dispatcher = BaiduRequestDispatcher(min_interval_seconds=0)
        calls = 0

        def error_response():
            nonlocal calls
            calls += 1
            return {"status": 1, "message": "provider error"}

        for _ in range(2):
            dispatcher.execute(
                error_response,
                provider="baidu-map",
                operation="map_search_places",
                arguments={"query": "same-place"},
                cache_ttl_seconds=60,
            )

        self.assertEqual(calls, 2)

    def test_mcp_and_direct_provider_pressure_share_metrics_and_one_slot(self):
        dispatcher = BaiduRequestDispatcher(min_interval_seconds=0, queue_timeout_seconds=10)
        manager = ToolManager(is_auto_discover=False, baidu_request_dispatcher=dispatcher)
        manager.tools["map_search_places"] = McpToolSpec(
            name="map_search_places",
            description="fake Baidu place search",
            func=lambda **_: None,
            parameters={},
            required=[],
            server_name="baidu-map",
            server_params=SseServerParameters(url="http://unused.test"),
        )
        route_provider = BaiduRouteProvider(api_key="test-key", dispatcher=dispatcher)
        lock = threading.Lock()
        active = 0
        observed_peak = 0
        emitted_events = []
        all_waiters_queued = threading.Event()

        def record_waiting(event):
            with lock:
                emitted_events.append(event)
                if len(emitted_events) >= 5:
                    all_waiters_queued.set()

        dispatcher.add_event_listener(record_waiting)

        async def hold_slot_until_requests_queue():
            # 首个请求保持执行槽，直到其余五个请求明确排队，
            # 避免依赖 10ms 内所有线程恰好启动的时序假设。
            if not await asyncio.to_thread(all_waiters_queued.wait, 5):
                raise AssertionError("其余五个地图请求未进入共享队列")

        def enter() -> None:
            nonlocal active, observed_peak
            with lock:
                active += 1
                observed_peak = max(observed_peak, active)

        def leave() -> None:
            nonlocal active
            with lock:
                active -= 1

        async def fake_mcp_call(tool, session_id=None, **kwargs):
            del tool, session_id
            enter()
            await hold_slot_until_requests_queue()
            leave()
            return {"content": [{"text": str(kwargs.get("query"))}]}

        manager._run_mcp_tool_async = fake_mcp_call

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {
                    "status": 0,
                    "result": {
                        "routes": [
                            {
                                "distance": 1200,
                                "duration": 600,
                                "steps": [{"path": "116.1,39.1;116.2,39.2"}],
                            }
                        ]
                    },
                }

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                del args, kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                del exc_type, exc, tb

            async def get(self, *args, **kwargs):
                del args, kwargs
                enter()
                await hold_slot_until_requests_queue()
                leave()
                return FakeResponse()

        async def run_pressure():
            mcp_calls = [
                asyncio.to_thread(
                    manager.run_tool,
                    "map_search_places",
                    [],
                    f"session-{index}",
                    query=f"place-{index}",
                    region="Beijing",
                    _baidu_priority="candidate",
                )
                for index in range(3)
            ]
            direct_calls = [
                route_provider.route(
                    [116.0 + index * 0.01, 39.0],
                    [116.3 + index * 0.01, 39.3],
                    "walking",
                )
                for index in range(3)
            ]
            return await asyncio.gather(*mcp_calls, *direct_calls)

        with patch(
            "backend.services.route_providers.baidu.httpx.AsyncClient",
            FakeAsyncClient,
        ):
            results = asyncio.run(run_pressure())

        self.assertEqual(len(results), 6)
        self.assertEqual(observed_peak, 1)
        metrics = manager.get_map_request_metrics()
        self.assertEqual(metrics["provider_executions"], 6)
        self.assertEqual(metrics["peak_concurrency"], 1)
        self.assertGreaterEqual(metrics["queue_waits"], 5)
        self.assertEqual(metrics["waiting_count"], 0)
        self.assertTrue(emitted_events)
        self.assertTrue(
            all(event["type"] == "provider_queue_waiting" for event in emitted_events)
        )


if __name__ == "__main__":
    unittest.main()
