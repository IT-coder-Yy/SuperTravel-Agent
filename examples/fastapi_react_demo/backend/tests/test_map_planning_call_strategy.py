import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
for path in (BACKEND_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from services.chat_service import (
    _direct_baidu_place_lookup,
    _direct_baidu_place_search,
    _merge_map_locations,
    _normalize_map_locations,
    _normalize_web_rows,
    _run_map_geocode_for_places,
    _verify_selected_map_candidates,
    maybe_prepare_travel_experience_bundle,
)


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
                        "uid": f"geocoded-{address}",
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
    def test_real_amap_call_tool_result_and_single_detail_object_are_normalized(self):
        payload = {
            "isError": False,
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "id": "B0FFHANGZHOU",
                    "name": "西湖风景名胜区",
                    "location": "120.148828,30.242320",
                    "address": "杭州市西湖区龙井路1号",
                    "city": "杭州市",
                    "type": "风景名胜;风景名胜相关;旅游景点",
                    "photos": {"provider": "amap", "url": "https://example.com/west-lake.jpg"},
                    "opentime2": "全天开放",
                    "rating": "4.9",
                }, ensure_ascii=False),
            }],
        }

        rows = _normalize_map_locations(payload, source_tool="maps_search_detail")

        self.assertEqual(1, len(rows))
        self.assertEqual("B0FFHANGZHOU", rows[0]["provider_place_id"])
        self.assertEqual("全天开放", rows[0]["opening_hours"])
        self.assertEqual(["https://example.com/west-lake.jpg"], rows[0]["images"])
        self.assertTrue(rows[0]["coordinates_trusted"])

    def test_real_tavily_text_result_is_normalized(self):
        payload = {
            "isError": False,
            "content": [{
                "type": "text",
                "text": (
                    "Detailed Results:\n\n"
                    "Title: 南京博物院参观须知\n"
                    "URL: https://www.njmuseum.com/visit\n"
                    "Content: 官方页面说明开放时间与预约要求。\n\n"
                    "Title: 中山陵游览信息\n"
                    "URL: https://example.org/zhongshan\n"
                    "Content: 景区交通与开放信息。"
                ),
            }],
        }

        rows = _normalize_web_rows(payload, max_rows=8)

        self.assertEqual(2, len(rows))
        self.assertEqual("南京博物院参观须知", rows[0]["title"])
        self.assertEqual("njmuseum.com", rows[0]["source"])

    def test_amap_location_string_is_converted_from_gcj02_to_baidu_coordinates(self):
        rows = _normalize_map_locations(
            {
                "pois": [{
                    "id": "amap-forbidden-city",
                    "name": "故宫博物院",
                    "location": "116.397428,39.909230",
                    "address": "北京市东城区景山前街4号",
                    "cityname": "北京市",
                    "adname": "东城区",
                    "type": "风景名胜;风景名胜相关;旅游景点",
                    "business": {"rating": "4.9", "opentime_today": "08:30-17:00"},
                }]
            },
            source_tool="maps_text_search",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["place_id"], "amap-forbidden-city")
        self.assertEqual(rows[0]["provider_coordinate_system"], "GCJ02")
        self.assertEqual(rows[0]["coordinate_system"], "BD09LL")
        self.assertEqual(rows[0]["source_provider"], "amap")
        self.assertAlmostEqual(rows[0]["provider_longitude"], 116.397428, places=6)
        self.assertAlmostEqual(rows[0]["provider_latitude"], 39.909230, places=6)
        self.assertGreater(rows[0]["lng"], 116.403)
        self.assertGreater(rows[0]["lat"], 39.915)
        self.assertEqual(rows[0]["rating"], "4.9")

    def test_amap_and_baidu_seed_results_are_fused_and_single_failure_degrades_cleanly(self):
        class AmapFirstManager:
            def __init__(self, *, fail_amap=False):
                self.fail_amap = fail_amap
                self.calls = []

            def get_tool(self, name):
                specs = {
                    "maps_text_search": SimpleNamespace(
                        parameters={"keywords": {}, "city": {}, "types": {}},
                        required=["keywords"],
                    ),
                    "map_search_places": SimpleNamespace(
                        parameters={"query": {}, "region": {}, "count": {}},
                        required=["query"],
                    ),
                }
                return specs.get(name)

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs))
                if name == "maps_text_search":
                    if self.fail_amap:
                        return {"error": True, "message": "高德暂时不可用"}
                    return {"pois": [{
                        "id": "amap-palace",
                        "name": "故宫博物院",
                        "location": "116.397428,39.909230",
                        "address": "北京市东城区景山前街4号",
                        "cityname": "北京市",
                        "type": "风景名胜",
                    }]}
                return {"places": [{
                    "uid": "baidu-palace",
                    "name": "故宫博物院",
                    "location": {"lat": 39.9163, "lng": 116.3972},
                    "address": "北京市东城区景山前街4号",
                    "category": "景点",
                }]}

        primary = AmapFirstManager()
        locations, used_tools, error = _run_map_geocode_for_places(
            [("故宫博物院", 1)],
            "北京",
            "北京一日游",
            primary,
            [{"role": "user", "content": "北京一日游"}],
            "amap-primary",
        )

        self.assertEqual(error, "")
        self.assertEqual(used_tools, "maps_text_search, map_search_places")
        self.assertEqual({name for name, _ in primary.calls}, {"maps_text_search", "map_search_places"})
        self.assertEqual(locations[0]["source"], "高德地图地点检索")
        self.assertEqual(locations[0]["coordinate_system"], "BD09LL")
        self.assertEqual(locations[0]["source_providers"], ["amap", "baidu"])
        self.assertEqual(
            locations[0]["provider_place_ids"],
            {"amap": "amap-palace", "baidu": "baidu-palace"},
        )

        fallback = AmapFirstManager(fail_amap=True)
        locations, used_tools, error = _run_map_geocode_for_places(
            [("故宫博物院", 1)],
            "北京",
            "北京一日游",
            fallback,
            [{"role": "user", "content": "北京一日游"}],
            "amap-fallback",
        )

        self.assertEqual(error, "")
        self.assertEqual(used_tools, "map_search_places")
        self.assertEqual({name for name, _ in fallback.calls}, {"maps_text_search", "map_search_places"})
        self.assertEqual(locations[0]["source"], "百度地图地点检索")

    def test_map_fusion_uses_name_distance_and_complements_provider_fields(self):
        fused = _merge_map_locations(
            [{
                "name": "南京博物院",
                "place_id": "amap-museum",
                "provider_place_id": "amap-museum",
                "source_provider": "amap",
                "lat": 32.0432,
                "lng": 118.8301,
                "coordinate_system": "BD09LL",
                "address": "南京市玄武区中山东路321号",
                "rating": "4.8",
                "images": ["https://a.example/museum.jpg"],
            }],
            [{
                "name": "南京博物院景区",
                "place_id": "baidu-museum",
                "provider_place_id": "baidu-museum",
                "source_provider": "baidu",
                "lat": 32.0433,
                "lng": 118.8302,
                "coordinate_system": "BD09LL",
                "address": "江苏省南京市玄武区中山东路321号",
                "opening_hours": "09:00-17:00",
                "telephone": "025-12345678",
                "images": ["https://b.example/museum.jpg"],
            }],
        )

        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0]["source_providers"], ["amap", "baidu"])
        self.assertEqual(fused[0]["opening_hours"], "09:00-17:00")
        self.assertEqual(fused[0]["telephone"], "025-12345678")
        self.assertEqual(len(fused[0]["images"]), 2)
        self.assertEqual(fused[0]["provider_evidence"]["amap"]["rating"], "4.8")
        self.assertEqual(fused[0]["provider_evidence"]["baidu"]["opening_hours"], "09:00-17:00")

    @patch.dict(os.environ, {
        "BAIDU_DIRECT_PLACE_FALLBACK_ENABLED": "true",
        "BAIDU_MAP_API_KEY": "test-key",
    })
    @patch("services.chat_service.requests.get")
    def test_direct_baidu_fallback_keeps_exact_classic_after_mcp_chain_failure(self, mocked_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "status": 0,
            "results": [{
                "uid": "lingyin-baidu-uid",
                "name": "灵隐寺",
                "location": {"lat": 30.24719, "lng": 120.108133},
                "address": "杭州市西湖区法云弄1号",
                "detail_info": {"tag": "旅游景点;寺庙"},
            }],
        }
        mocked_get.return_value = response

        location = _direct_baidu_place_lookup("灵隐寺", "杭州")

        self.assertIsNotNone(location)
        self.assertEqual("lingyin-baidu-uid", location["place_id"])
        self.assertEqual("BD09LL", location["coordinate_system"])
        self.assertTrue(location["coordinates_trusted"])

    @patch.dict(os.environ, {
        "BAIDU_DIRECT_PLACE_FALLBACK_ENABLED": "true",
        "BAIDU_MAP_API_KEY": "test-key",
    })
    @patch("services.chat_service.requests.get")
    def test_direct_baidu_search_returns_navigable_density_candidates(self, mocked_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "status": 0,
            "results": [
                {
                    "uid": "food-1",
                    "name": "武汉老字号餐厅",
                    "location": {"lat": 30.58, "lng": 114.30},
                    "address": "武汉市江汉区",
                    "detail_info": {"tag": "美食;中餐厅"},
                },
                {
                    "uid": "food-2",
                    "name": "湖北菜馆",
                    "location": {"lat": 30.56, "lng": 114.31},
                    "address": "武汉市武昌区",
                    "detail_info": {"tag": "美食;中餐厅"},
                },
            ],
        }
        mocked_get.return_value = response

        locations = _direct_baidu_place_search(
            "特色餐厅",
            "武汉",
            max_rows=20,
            category="餐厅",
        )

        self.assertEqual(2, len(locations))
        self.assertTrue(all(location["coordinates_trusted"] for location in locations))
        self.assertTrue(all(location["requested_destination"] == "武汉" for location in locations))
        self.assertTrue(all(location["category"] == "餐厅" for location in locations))
        self.assertEqual(20, mocked_get.call_args.kwargs["params"]["page_size"])

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
        generic_search_calls = [
            call for call in search_calls
            if any(marker in str(call[1].get("query", "")) for marker in ("热门景点", "住宿 酒店", "美食 餐厅"))
        ]
        self.assertEqual(len(generic_search_calls), 3)
        self.assertGreaterEqual(len(search_calls), 8)
        self.assertLessEqual(len(detail_calls), 48)
        self.assertEqual(len(detail_calls), len({str(call[1]) for call in detail_calls}))
        geocoded_names = {call[1]["address"].replace("北京", "", 1) for call in geocode_calls}
        self.assertNotIn("故宫博物院", geocoded_names)
        self.assertLessEqual(len(geocode_calls), 7)
        self.assertLessEqual(len(search_calls) + len(geocode_calls) + len(detail_calls), 68)
        self.assertLessEqual(len(bundle["map_locations"]), 25)
        self.assertTrue({"天安门广场", "故宫博物院", "颐和园"}.issubset(
            {location["name"] for location in bundle["map_locations"]}
        ))
        self.assertIn("formal", {call[1].get("_baidu_priority") for call in search_calls})
        self.assertIn("scheduled_dining", {call[1].get("_baidu_priority") for call in search_calls})
        self.assertTrue(all(call[1].get("_baidu_priority") == "supplemental" for call in detail_calls))

    def test_geocode_runs_only_for_selected_fallback_places(self):
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
        generic_search_calls = [
            call for call in search_calls
            if any(marker in str(call[1].get("query", "")) for marker in ("热门景点", "住宿 酒店", "美食 餐厅"))
        ]
        self.assertEqual(len(generic_search_calls), 3)
        self.assertLessEqual(len(geocode_calls), 7)
        self.assertLessEqual(len(detail_calls), 48)
        self.assertEqual(len(detail_calls), len({str(call[1]) for call in detail_calls}))
        self.assertEqual(geocoded_names, final_names)
        self.assertLessEqual(len(search_calls) + len(geocode_calls) + len(detail_calls), 68)

    def test_coordinates_only_poi_is_kept_with_stable_navigation_identity(self):
        class CoordinatesOnlyManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                if name == "map_place_search":
                    return SimpleNamespace(
                        parameters={"query": {}, "region": {}, "count": {}},
                        required=["query"],
                    )
                return None

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs))
                return {
                    "places": [{
                        "name": "黄鹤楼公园",
                        "location": {"lat": 30.54454, "lng": 114.30635},
                        "address": "武汉市武昌区",
                        "category": "旅游景点",
                    }]
                }

        manager = CoordinatesOnlyManager()
        locations, used_tools, error = _run_map_geocode_for_places(
            [("黄鹤楼", 1)],
            "武汉",
            "规划武汉三日游",
            manager,
            [{"role": "user", "content": "规划武汉三日游"}],
            "coordinate-only",
        )

        self.assertEqual(error, "")
        self.assertEqual(used_tools, "map_place_search")
        self.assertEqual(len(manager.calls), 1)
        self.assertEqual(locations[0]["name"], "黄鹤楼")
        self.assertTrue(locations[0]["place_id"].startswith("baidu_geo_"))
        self.assertEqual(locations[0]["coordinate_system"], "BD09LL")
        self.assertEqual(locations[0]["coordinate_source"], "provider_coordinates")
        self.assertTrue(locations[0]["coordinates_trusted"])

    def test_only_selected_poi_with_missing_coordinates_is_verified(self):
        manager = _MapToolManager()
        selected = [
            {
                "place_id": "trusted-poi",
                "name": "Trusted Place",
                "city": "北京市",
                "lat": 39.9,
                "lng": 116.4,
                "coordinates_trusted": True,
                "category": "attraction",
            },
            {
                "place_id": "missing-coordinate-poi",
                "name": "Missing Coordinate Place",
                "lat": None,
                "lng": None,
                "coordinates_trusted": False,
                "category": "attraction",
            },
        ]

        verified, used_tools, errors = _verify_selected_map_candidates(
            selected,
            "北京",
            manager,
            [{"role": "user", "content": "plan"}],
            "coordinate-reuse",
        )

        detail_calls = [call for call in manager.calls if call[0] == "map_place_details"]
        geocode_calls = [call for call in manager.calls if call[0] == "map_geocode"]
        self.assertEqual(len(verified), 2)
        self.assertEqual(verified[0]["coordinate_source"], "search_reused")
        self.assertEqual(len(detail_calls), 1)
        self.assertEqual(geocode_calls, [])
        self.assertEqual(used_tools, ["map_place_details"])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()


def test_international_primary_search_reaches_verified_fallback_without_domestic_tools():
    from services import chat_service as service
    manager = SimpleNamespace(get_tool=lambda _name: None)
    place = service._catalog_seed_location('东京', '浅草寺', 1)
    place.update(poi_id='wikipedia_sensoji', place_id='wikipedia_sensoji', id='wikipedia_sensoji',
                 lat=35.7148, lng=139.7967, coordinates_trusted=True,
                 coordinate_source='provider_poi', source_provider='wikipedia', city='东京',
                 destination_bound=True, requested_destination='东京')
    with patch.object(service, '_direct_baidu_place_search', return_value=[]), \
         patch.object(service, '_run_map_geocode_for_places', return_value=([place], 'wikipedia_coordinates', '')) as lookup:
        locations, provider, _, _ = service._run_map_search_for_travel(
            '上海到东京3天旅行', '东京', manager, [], 'no-domestic-map', research_role='research')
    assert lookup.called
    assert locations and locations[0]['poi_id'] == 'wikipedia_sensoji'
    assert 'wikipedia_coordinates' in provider


def test_international_lodging_reaches_verified_osm_when_domestic_search_is_empty():
    from services import chat_service as service
    manager = SimpleNamespace(get_tool=lambda _name: None)
    hotel = {
        'poi_id': 'osm_way_1234', 'place_id': 'osm_way_1234', 'name': '東京ホテル',
        'category': 'hotel', 'city': '东京', 'country_code': 'JP',
        'lat': 35.68, 'lng': 139.76, 'coordinate_system': 'WGS84',
        'coordinates_trusted': True, 'source_provider': 'openstreetmap',
        'source_url': 'https://www.openstreetmap.org/way/1234',
    }
    with patch.object(service, 'search_international_places', return_value=[hotel]) as lookup:
        _, provider, _, lodging = service._run_map_search_for_travel(
            '上海到东京5天旅行', '东京', manager, [], 'international-lodging', research_role='lodging')
    lookup.assert_called_once_with('hotel', '东京', 'hotel')
    assert len(lodging) == 1
    assert lodging[0]['poi_id'] == hotel['poi_id']
    assert lodging[0]['coordinates_trusted']
    assert 'openstreetmap_nominatim' in provider


def test_wrong_city_food_results_do_not_suppress_international_restaurant_lookup():
    from services import chat_service as service
    class WrongCityManager:
        def get_tool(self, name):
            return SimpleNamespace(parameters={'keywords': {}, 'city': {}}, required=['keywords']) if name == 'maps_text_search' else None

        def run_tool(self, *args, **kwargs):
            return {'pois': [
                {'id': f'wrong-city-{index}', 'name': f'东京风味餐厅{index}',
                 'location': f'{116.40 + index * .01},39.91', 'cityname': '北京市',
                 'address': '北京市朝阳区', 'type': '餐饮服务;中餐厅'}
                for index in range(20)
            ]}

    restaurant = {
        'poi_id': 'osm_node_1234', 'place_id': 'osm_node_1234', 'name': '浅草食堂',
        'category': '餐厅', 'city': '东京', 'country_code': 'JP',
        'lat': 35.71, 'lng': 139.79, 'coordinate_system': 'WGS84',
        'coordinates_trusted': True, 'source_provider': 'openstreetmap',
        'destination_bound': True, 'requested_destination': '东京',
    }
    with patch.object(service, 'fetch_osm_restaurants', return_value=[restaurant]) as lookup, \
         patch.object(service, '_direct_baidu_place_search', return_value=[]):
        locations, _, _, _ = service._run_map_search_for_travel(
            '上海到东京5天旅行', '东京', WrongCityManager(), [], 'wrong-city-food', research_role='dining')
    lookup.assert_called_once()
    assert [place['poi_id'] for place in locations] == ['osm_node_1234']


def test_recorded_domestic_tokyo_name_matches_cannot_satisfy_international_food_density():
    """真实64工具链曾以19个国内误匹配餐厅满足14项阈值，跳过OSM后清零。"""
    from services import chat_service as service
    wrong_city_rows = json.loads((Path(__file__).parent / 'fixtures/tokyo_wrong_city_restaurants.json').read_text(encoding='utf-8'))
    manager = SimpleNamespace(get_tool=lambda name: SimpleNamespace(parameters={}) if name == 'map_search_places' else None)
    restaurant = {
        'poi_id': 'osm_node_1234', 'place_id': 'osm_node_1234', 'name': '浅草食堂',
        'category': '餐厅', 'city': '东京', 'country_code': 'JP',
        'lat': 35.71, 'lng': 139.79, 'coordinate_system': 'WGS84',
        'coordinates_trusted': True, 'source_provider': 'openstreetmap',
        'destination_bound': True, 'requested_destination': '东京',
    }
    merged_counts = []
    real_merge = service._merge_map_locations
    def record_merge(*args, **kwargs):
        rows = real_merge(*args, **kwargs)
        merged_counts.append(len(rows))
        return rows
    with patch.object(service, '_run_tool_with_arg_candidates', return_value={'results': wrong_city_rows}), \
         patch.object(service, 'fetch_osm_restaurants', return_value=[restaurant]) as lookup, \
         patch.object(service, '_direct_baidu_place_search', return_value=[]), \
         patch.object(service, '_merge_map_locations', side_effect=record_merge):
        locations, _, _, _ = service._run_map_search_for_travel(
            '东京5天旅行美食', '东京', manager, [], 'recorded-wrong-city-food', research_role='dining')
    assert max(merged_counts) >= 14, '回归输入必须覆盖真正触发错误的密度阈值'
    lookup.assert_called_once_with('东京', limit=14)
    assert [place['poi_id'] for place in locations] == ['osm_node_1234']
