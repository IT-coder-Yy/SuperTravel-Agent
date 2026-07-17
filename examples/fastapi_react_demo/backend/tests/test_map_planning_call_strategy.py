import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
for path in (BACKEND_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from services.chat_service import maybe_prepare_travel_experience_bundle


class _MapToolManager:
    def __init__(self, *, empty_search=False):
        self.empty_search = empty_search
        self.calls = []

    def get_tool(self, name):
        specs = {
            "map_search_places": SimpleNamespace(
                parameters={"query": {}, "region": {}, "scope": {}},
                required=["query"],
            ),
            "map_geocode": SimpleNamespace(
                parameters={"address": {}, "city": {}},
                required=["address"],
            ),
            "map_place_details": SimpleNamespace(
                parameters={"uid": {}, "query": {}, "region": {}},
                required=[],
            ),
        }
        return specs.get(name)

    def run_tool(self, name, messages, session_id, **kwargs):
        self.calls.append((name, kwargs))
        if name == "map_search_places":
            if self.empty_search:
                return {"places": []}
            return {
                "places": [
                    {
                        "uid": "forbidden-city",
                        "name": "故宫博物院",
                        "location": {"lat": 39.9163, "lng": 116.3972},
                        "address": "北京市东城区",
                        "category": "景点",
                    },
                    {
                        "uid": "hotel-city",
                        "name": "北京中心酒店",
                        "location": {"lat": 39.91, "lng": 116.40},
                        "address": "北京市东城区",
                        "category": "酒店",
                    },
                    {
                        "uid": "food-city",
                        "name": "北京风味餐厅",
                        "location": {"lat": 39.92, "lng": 116.41},
                        "address": "北京市东城区",
                        "category": "餐厅",
                    },
                ]
            }
        if name == "map_geocode":
            address = kwargs["address"]
            return {
                "places": [
                    {
                        "name": address,
                        "location": {"lat": 39.9, "lng": 116.4},
                        "address": address,
                    }
                ]
            }
        if name == "map_place_details":
            query = kwargs.get("query") or kwargs.get("uid")
            return {
                "places": [
                    {
                        "name": str(query),
                        "location": {"lat": 39.91, "lng": 116.40},
                        "address": "北京市",
                        "detail_info": {"overall_rating": "4.8"},
                    }
                ]
            }
        return {}


class MapPlanningCallStrategyTests(unittest.TestCase):
    def test_candidates_are_batched_before_only_final_poi_details(self):
        manager = _MapToolManager()
        query = "帮我规划北京三天两夜行程，包含住宿和美食"

        bundle = maybe_prepare_travel_experience_bundle(
            user_query=query,
            tool_manager=manager,
            message_history=[{"role": "user", "content": query}],
            session_id="batch-plan",
            allow_web_search=False,
        )

        search_calls = [call for call in manager.calls if call[0] == "map_search_places"]
        detail_calls = [call for call in manager.calls if call[0] == "map_place_details"]
        geocode_calls = [call for call in manager.calls if call[0] == "map_geocode"]
        self.assertEqual(len(search_calls), 3)
        self.assertLessEqual(len(detail_calls), 2)
        self.assertEqual(geocode_calls, [])
        self.assertLessEqual(len(search_calls) + len(detail_calls), 5)
        self.assertLessEqual(len(bundle["map_locations"]), 8)

    def test_geocode_runs_only_for_three_selected_fallback_places(self):
        manager = _MapToolManager(empty_search=True)
        query = "帮我规划北京三天两夜文化行程"

        bundle = maybe_prepare_travel_experience_bundle(
            user_query=query,
            tool_manager=manager,
            message_history=[{"role": "user", "content": query}],
            session_id="geocode-final-plan",
            allow_web_search=False,
        )

        search_calls = [call for call in manager.calls if call[0] == "map_search_places"]
        geocode_calls = [call for call in manager.calls if call[0] == "map_geocode"]
        detail_calls = [call for call in manager.calls if call[0] == "map_place_details"]
        geocoded_names = {call[1]["address"].replace("北京", "", 1) for call in geocode_calls}
        final_names = {location["name"] for location in bundle["map_locations"]}
        self.assertEqual(len(search_calls), 3)
        self.assertEqual(len(geocode_calls), 3)
        self.assertLessEqual(len(detail_calls), 2)
        self.assertEqual(geocoded_names, final_names)
        self.assertLessEqual(len(search_calls) + len(geocode_calls) + len(detail_calls), 8)


if __name__ == "__main__":
    unittest.main()
