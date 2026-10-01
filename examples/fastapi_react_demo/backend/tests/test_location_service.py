import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from services.location_service import (
    LocationLookupUnavailable,
    _city_from_payload,
    reverse_geocode_current_location,
)


class FakeDispatcher:
    def __init__(self, payload):
        self.payload = payload
        self.kwargs = None

    async def execute_async(self, _callback, **kwargs):
        self.kwargs = kwargs
        return self.payload


class LocationServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_city_parser_prefers_city_and_removes_suffix(self):
        result = _city_from_payload({
            "status": 0,
            "result": {
                "formatted_address": "浙江省杭州市西湖区",
                "addressComponent": {
                    "province": "浙江省",
                    "city": "杭州市",
                    "district": "西湖区",
                },
            },
        })

        self.assertEqual(result["city"], "杭州")
        self.assertEqual(result["coordinate_system"], "WGS84")

    async def test_reverse_geocode_uses_shared_dispatcher_and_cache(self):
        dispatcher = FakeDispatcher({
            "status": 0,
            "result": {"addressComponent": {"city": "上海市"}},
        })

        result = await reverse_geocode_current_location(
            latitude=31.2304,
            longitude=121.4737,
            dispatcher=dispatcher,
            api_key="test-key",
        )

        self.assertEqual(result["city"], "上海")
        self.assertEqual(dispatcher.kwargs["provider"], "baidu_reverse_geocode")
        self.assertEqual(dispatcher.kwargs["priority"], "supplemental")
        self.assertEqual(dispatcher.kwargs["cache_ttl_seconds"], 86400)

    async def test_reverse_geocode_requires_configured_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(LocationLookupUnavailable):
                await reverse_geocode_current_location(
                    latitude=31.2304,
                    longitude=121.4737,
                    dispatcher=FakeDispatcher({}),
                )


if __name__ == "__main__":
    unittest.main()
