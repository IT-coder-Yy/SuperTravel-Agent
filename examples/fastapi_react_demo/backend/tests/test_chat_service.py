import asyncio
import json
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT.parent
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(APP_ROOT) not in sys.path:
    sys.path.append(str(APP_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from services.chat_service import (
    build_message_history,
    execute_chat_once,
    maybe_prepare_travel_experience_bundle,
    maybe_prepare_train_ticket_bundle,
    maybe_prepare_xhs_search_bundle,
    maybe_prepare_activity_images,
    _build_ticket_markdown_table,
    _build_filtered_tool_manager,
    _build_travel_structured_result,
    _build_travel_structured_result_with_routes,
    _build_destination_overview_summary,
    _build_travel_fallback_answer,
    _build_web_markdown_table,
    _build_xhs_markdown_table,
    _extract_route_from_query,
    _enrich_map_locations_with_details,
    _ensure_daily_activity_coverage,
    _filter_destination_lodging_locations,
    _filter_quality_map_locations,
    _normalized_travel_location_category,
    _normalize_direct_rows,
    _normalize_interline_rows,
    _normalize_image_search_candidates,
    _normalize_markdown_for_display,
    _normalize_web_rows,
    _merge_web_search_rows,
    _normalize_xhs_rows,
    _run_web_search_for_travel,
    _run_map_geocode_for_places,
    _run_tool_with_arg_candidates,
    _station_query_pairs,
    _is_travel_experience_query,
    _is_train_ticket_query,
    _is_xhs_query,
    _image_candidate_matches_place,
    _select_travel_final_answer,
    _shift_same_day_schedule_forward,
    _travel_bundle_to_trip_plan,
    _travel_document_filename,
    _travel_seed_places,
    _verify_selected_map_candidates,
)
from agents.tool.tool_base import McpToolSpec
from agents.tool.tool_manager import ToolManager as RuntimeToolManager
from mcp import StdioServerParameters
from services.travel_document_service import FormalPlanValidationError, adapt_v2_to_v3
from services.trip_plan_validator import validate_trip_plan


class FakeController:
    def __init__(self):
        self.called_with = None

    def run(self, messages, tool_manager, session_id, deep_thinking, summary, deep_research):
        self.called_with = {
            "messages": messages,
            "tool_manager": tool_manager,
            "session_id": session_id,
            "deep_thinking": deep_thinking,
            "summary": summary,
            "deep_research": deep_research,
        }
        return {"ok": True, "steps": 3}


class ChatServiceTests(unittest.TestCase):
    def test_international_named_seeds_use_wikipedia_when_map_tools_are_unavailable(self):
        wikipedia_place = {
            "name": "浅草寺", "city": "东京", "poi_id": "wikidata_Q615183",
            "provider_place_id": "Q615183", "lat": 35.71456, "lng": 139.79664,
            "coordinate_system": "WGS84", "coordinates_trusted": True,
            "coordinate_source": "wikidata_p625", "source": "Wikidata",
            "source_provider": "wikidata", "source_url": "https://www.wikidata.org/wiki/Q615183",
            "source_reference_id": "source_wikidata_Q615183", "data_type": "reference_data",
        }
        with patch(
            "services.chat_service.fetch_wikipedia_place_coordinates",
            return_value={"浅草寺": wikipedia_place},
        ):
            locations, used_tool, error = _run_map_geocode_for_places(
                [("浅草寺", 1)], "东京", "东京一日游", SimpleNamespace(tools={}), [], "session-seed",
            )

        self.assertEqual("", error)
        self.assertEqual("wikipedia_coordinates", used_tool)
        self.assertEqual("wikidata_Q615183", locations[0]["id"])
        self.assertTrue(locations[0]["coordinates_trusted"])

    def test_international_catalog_seed_uses_exact_wikipedia_coordinate_before_map_tools(self):
        wikipedia_place = {
            "name": "浅草寺",
            "city": "东京",
            "poi_id": "wikipedia_123",
            "provider_place_id": "123",
            "lat": 35.7148,
            "lng": 139.7967,
            "coordinate_system": "WGS84",
            "coordinates_trusted": True,
            "coordinate_source": "wikipedia_article",
            "source": "Wikipedia",
            "source_provider": "wikipedia",
            "source_url": "https://zh.wikipedia.org/wiki/浅草寺",
            "source_reference_id": "source_wikipedia_123",
            "data_type": "reference_data",
        }
        with patch(
            "services.chat_service.fetch_wikipedia_place_coordinates",
            return_value={"浅草寺": wikipedia_place},
        ):
            verified, used_tools, errors = _verify_selected_map_candidates(
                [{
                    "name": "浅草寺",
                    "city": "东京",
                    "category": "景点",
                    "source": "destination_catalog",
                    "coordinates_trusted": False,
                }],
                "东京",
                SimpleNamespace(tools={}),
                [],
                "session-wikipedia",
            )

        self.assertEqual([], errors)
        self.assertEqual(["wikipedia_coordinates"], used_tools)
        self.assertEqual("wikipedia_123", verified[0]["poi_id"])
        self.assertEqual(["source_wikipedia_123"], verified[0]["evidence_refs"])

    def test_filtered_tool_manager_shares_application_scoped_mcp_runtime(self):
        manager = RuntimeToolManager(is_auto_discover=False)
        runtime_loop = object()
        persistent_connections = {"amap-maps": {"session": object()}}
        manager._mcp_runtime_loop = runtime_loop
        manager._persistent_stdio_servers = persistent_connections
        manager.tools["maps_text_search"] = McpToolSpec(
            name="maps_text_search",
            description="",
            func=lambda: None,
            parameters={},
            required=[],
            server_name="amap-maps",
            server_params=StdioServerParameters(command="noop"),
        )

        filtered = _build_filtered_tool_manager(manager, ["amap-maps"])

        self.assertIsNot(filtered, manager)
        self.assertIs(filtered._mcp_runtime_loop, runtime_loop)
        self.assertIs(filtered._persistent_stdio_servers, persistent_connections)
        self.assertEqual(["maps_text_search"], list(filtered.tools))

    def test_same_day_schedule_is_shifted_after_current_time(self):
        plan = _travel_bundle_to_trip_plan({
            "query": "今天从上海去杭州旅行",
            "destination_city": "杭州",
            "map_locations": [
                {"name": "西湖风景名胜区", "category": "景点", "poi_id": "west-lake", "city": "杭州", "lat": 30.264, "lng": 120.158},
                {"name": "浙江省博物馆", "category": "景点", "poi_id": "museum", "city": "杭州", "lat": 30.257, "lng": 120.150},
            ],
            "trip_intent": {
                "origin": "上海",
                "destination": "杭州",
                "date_range": "2026-08-11 至 2026-08-11",
                "days": 1,
                "people_count": 1,
                "budget_total": 2000,
            },
        })

        changed = _shift_same_day_schedule_forward(
            plan,
            reference_time=datetime(2026, 8, 11, 14, 7),
        )

        self.assertTrue(changed)
        starts = [activity.start_time for activity in plan.activities if activity.day == 1]
        self.assertGreaterEqual(min(starts), "15:30")
        self.assertTrue(any("当前时间之后" in warning for warning in plan.warnings))

    def test_daily_coverage_keeps_city_classics_on_their_area_days_and_avoids_far_noise(self):
        classics = [
            ("黄鹤楼", 30.5503, 114.3091),
            ("武汉长江大桥", 30.5529, 114.2995),
            ("昙华林", 30.5577, 114.3156),
            ("湖北省博物馆", 30.5678, 114.3718),
            ("东湖生态旅游风景区", 30.5709, 114.3831),
            ("武汉大学", 30.5426, 114.3709),
            ("汉口江滩", 30.6160, 114.3260),
        ]
        central = [
            (f"武汉中心景点 {index}", 30.58 + index * 0.002, 114.30 + index * 0.002)
            for index in range(1, 7)
        ]
        far = [
            ("梁子湖远郊", 30.2610, 114.5103),
            ("远郊动物园", 30.5608, 113.9523),
        ]
        locations = [
            {
                "name": name,
                "category": "景点",
                "poi_id": f"poi-{index}",
                "lat": lat,
                "lng": lng,
            }
            for index, (name, lat, lng) in enumerate([*far, *central, *classics], start=1)
        ]

        selected = _ensure_daily_activity_coverage(
            locations,
            destination_city="武汉",
            query_text="武汉三天旅行",
            days=3,
            pace="balanced",
            meal_targets_by_day={1: [], 2: [], 3: []},
        )

        names_by_day = {
            day: [item["name"] for item in selected if item["day"] == day]
            for day in range(1, 4)
        }
        self.assertTrue({"黄鹤楼", "武汉长江大桥", "昙华林"}.issubset(names_by_day[1]))
        self.assertTrue({"湖北省博物馆", "东湖生态旅游风景区", "武汉大学"}.issubset(names_by_day[2]))
        self.assertEqual(["黄鹤楼", "武汉长江大桥", "昙华林"], names_by_day[1][:3])
        self.assertEqual(["湖北省博物馆", "东湖生态旅游风景区", "武汉大学"], names_by_day[2][:3])
        self.assertIn("汉口江滩", names_by_day[3])
        self.assertNotIn("梁子湖远郊", [item["name"] for item in selected])
        self.assertNotIn("远郊动物园", [item["name"] for item in selected])

    def test_daily_coverage_skips_lunch_only_restaurant_for_dinner(self):
        selected = _ensure_daily_activity_coverage(
            [
                {"name": "午餐一号", "category": "餐厅", "poi_id": "food-1", "lat": 30.1, "lng": 120.1, "opening_hours": "10:00-14:00"},
                {"name": "午餐二号", "category": "餐厅", "poi_id": "food-2", "lat": 30.2, "lng": 120.2, "opening_hours": "10:00-14:00"},
                {"name": "晚餐一号", "category": "餐厅", "poi_id": "food-3", "lat": 30.3, "lng": 120.3, "opening_hours": "17:00-22:00"},
            ],
            destination_city="杭州",
            query_text="杭州一日游",
            days=1,
            pace="balanced",
            meal_targets_by_day={1: ["lunch", "dinner"]},
        )

        restaurants = [item for item in selected if item["category"] == "餐厅"]
        self.assertIn(restaurants[0]["name"], {"午餐一号", "午餐二号"})
        self.assertEqual("晚餐一号", restaurants[1]["name"])
        self.assertEqual(["lunch", "dinner"], [item["meal_type"] for item in restaurants])

    def test_daily_coverage_deduplicates_city_prefixed_equivalent_attractions(self):
        selected = _ensure_daily_activity_coverage(
            [
                {"name": "西湖风景名胜区", "category": "景点", "poi_id": "west-lake", "lat": 30.264, "lng": 120.158},
                {"name": "杭州西湖风景名胜区", "category": "景点", "poi_id": "west-lake-south", "lat": 30.229, "lng": 120.128},
            ],
            destination_city="杭州",
            query_text="杭州两日游",
            days=2,
            pace="balanced",
            meal_targets_by_day={1: [], 2: []},
        )

        west_lake_names = [
            item["name"]
            for item in selected
            if "西湖" in item["name"] and "博物馆" not in item["name"]
        ]
        self.assertEqual(["西湖风景名胜区"], west_lake_names)

    def test_daily_coverage_preserves_catalog_day_hints_for_sparse_international_data(self):
        selected = _ensure_daily_activity_coverage(
            [
                {
                    "name": f"东京核验景点 {day}", "category": "景点", "poi_id": f"wiki-{day}",
                    "lat": 35.67 + day / 1000, "lng": 139.70 + day / 1000,
                    "coordinates_trusted": True, "day": day,
                }
                for day in range(1, 6)
            ],
            destination_city="东京",
            query_text="东京五日游",
            days=5,
            pace="relaxed",
            meal_targets_by_day={day: [] for day in range(1, 6)},
        )

        self.assertEqual({1, 2, 3, 4, 5}, {item["day"] for item in selected})

    def test_daily_coverage_rebalances_extra_international_places_to_four_per_day(self):
        selected = _ensure_daily_activity_coverage(
            [
                {
                    "name": f"东京核验景点 {index}",
                    "category": "景点",
                    "poi_id": f"wiki-{index}",
                    "lat": 35.60 + index / 1000,
                    "lng": 139.60 + index / 1000,
                    "coordinates_trusted": True,
                    "day": ((index - 1) // 3) + 1,
                }
                for index in range(1, 26)
            ],
            destination_city="东京",
            query_text="东京五日游",
            days=5,
            pace="balanced",
            meal_targets_by_day={day: [] for day in range(1, 6)},
        )

        self.assertEqual(20, len(selected))
        self.assertEqual(
            {day: 4 for day in range(1, 6)},
            {day: sum(row["day"] == day for row in selected) for day in range(1, 6)},
        )

    @patch("services.chat_service.fetch_image_source_descriptions", return_value=["杭州西湖实景"])
    @patch("services.chat_service.fetch_wikimedia_activity_images", return_value={})
    def test_poi_detail_defers_web_images_to_strict_activity_pipeline(self, _mocked_wikimedia, _mocked_description):
        class ToolManager:
            image_calls = 0

            def get_tool(self, name):
                if name == "map_place_details":
                    return SimpleNamespace(parameters={"uid": {}, "query": {}, "region": {}}, required=[])
                if name == "search_image_from_web":
                    return SimpleNamespace(parameters={"query": {}, "count": {}}, required=["query"])
                return None

            def run_tool(self, name, messages, session_id, **kwargs):
                if name == "map_place_details":
                    return {"result": {"uid": "west-lake", "name": "西湖", "location": {"lat": 30.24, "lng": 120.15}, "address": "杭州市西湖区", "detail_info": {"overall_rating": 4.8, "opening_hours": "全天开放"}}}
                self.image_calls += 1
                return {"images": [
                    {"image_url": "https://images.example/city.jpg", "title": "杭州旅行攻略 | 西湖"},
                    {"image_url": "https://images.example/unknown.jpg"},
                    {"image_url": "https://images.example/west-lake.jpg", "title": "杭州西湖实景", "source_url": "https://travel.example/west-lake"},
                ]}

        manager = ToolManager()
        result = _enrich_map_locations_with_details(
            [{"id": "west-lake", "place_id": "west-lake", "name": "西湖", "lat": 30.24, "lng": 120.15, "category": "景点", "source_provider": "baidu", "city": "杭州", "requested_destination": "杭州", "destination_bound": True, "day": 1}],
            "杭州",
            manager,
            [{"role": "user", "content": "杭州旅行"}],
            "poi-source-test",
        )[0]

        sources = {source["title"]: source for source in result["sources"]}
        self.assertIn("地图地点详情", sources)
        self.assertEqual(0, manager.image_calls)
        self.assertFalse(result.get("images"))
        self.assertNotIn("images", result["field_evidence"])
        self.assertEqual(result["field_evidence"]["opening_hours"]["source_reference_id"], sources["地图地点详情"]["source_reference_id"])
        bundle = {"query": "杭州西湖一日游", "destination_city": "杭州", "map_locations": [result], "web_rows": [], "xhs_rows": []}
        self.assertEqual(1, maybe_prepare_activity_images(bundle, manager))
        self.assertEqual(1, manager.image_calls)
        assets = bundle["map_locations"][0]["image_assets"]
        self.assertEqual(["https://images.example/west-lake.jpg"], [asset["url"] for asset in assets])
        self.assertEqual("Serper 图片检索", assets[0]["provider_name"])
        self.assertEqual("https://travel.example/west-lake", bundle["activity_image_sources"][0]["url"])

    def test_itinerary_candidates_do_not_invent_missing_opening_hours(self):
        class ToolManager:
            def __init__(self):
                self.detail_calls = []

            def get_tool(self, name):
                if name == "map_place_details":
                    return SimpleNamespace(parameters={"uid": {}, "query": {}, "region": {}}, required=[])
                return None

            def run_tool(self, name, messages, session_id, **kwargs):
                self.detail_calls.append(kwargs)
                place_id = kwargs.get("uid") or kwargs.get("id") or "resolved"
                return {
                    "result": {
                        "uid": place_id,
                        "name": f"地点 {place_id}",
                        "location": {"lat": 30.24, "lng": 120.15},
                        "address": "杭州市",
                        "detail_info": {"opening_hours": "09:00-22:00"},
                    }
                }

        locations = [
            {
                "id": f"place-{index}",
                "place_id": f"place-{index}",
                "provider_place_id": f"place-{index}",
                "source_provider": "baidu",
                "name": f"杭州景点 {index}",
                "lat": 30.20 + index * 0.001,
                "lng": 120.10 + index * 0.001,
                "category": "景点",
            }
            for index in range(1, 14)
        ]
        manager = ToolManager()

        enriched = _enrich_map_locations_with_details(
            locations,
            "杭州",
            manager,
            [{"role": "user", "content": "帮我规划杭州一日游行程"}],
            "candidate-detail-test",
        )

        self.assertEqual(13, len(manager.detail_calls))
        self.assertEqual(13, len(enriched))
        self.assertTrue(all(row.get("opening_hours") == "09:00-22:00" for row in enriched))

    def test_image_candidate_requires_specific_place_match_and_rejects_other_city(self):
        self.assertTrue(_image_candidate_matches_place(
            {
                "title": "南京博物院建筑外观与展馆实景",
                "image_url": "https://images.example.com/nanjing-museum.jpg",
            },
            "南京博物院",
            "南京",
        ))
        self.assertFalse(_image_candidate_matches_place(
            {
                "title": "上海博物馆建筑外观",
                "image_url": "https://images.example.com/shanghai-museum.jpg",
            },
            "南京博物院",
            "南京",
        ))
        self.assertFalse(_image_candidate_matches_place(
            {
                "title": "城市旅行风景",
                "image_url": "https://images.example.com/generic-city.jpg",
            },
            "南京博物院",
            "南京",
        ))

    def test_tavily_text_images_keep_descriptions_for_exact_place_matching(self):
        rows = _normalize_image_search_candidates(
            'Images: [{"description": "杭州西湖风景名胜区湖岸实景", "url": "https://images.example.com/west-lake.jpg"}]',
            "tavily_search",
        )

        self.assertEqual("杭州西湖风景名胜区湖岸实景", rows[0]["title"])
        self.assertEqual("https://images.example.com/west-lake.jpg", rows[0]["image_url"])

    def test_image_from_multi_place_article_is_not_bound_to_every_mentioned_poi(self):
        # 真实东京导出失败来源：正文图片 alt 为“東京跨年 | 橫濱港未來21”。
        candidate = {"title": "東京跨年2026全攻略｜迪士尼和服巡遊・增上寺敲鐘・澀谷倒數＋香港出發機票交通住宿貼士 | Trip.com"}
        self.assertFalse(_image_candidate_matches_place(candidate, "增上寺", "东京"))
        self.assertTrue(_image_candidate_matches_place({"title": "增上寺建筑外观 | 东京旅行"}, "增上寺", "东京"))
        self.assertFalse(_image_candidate_matches_place({"title": "南京博物馆建筑外观"}, "南京博物院", "南京"))

    def test_image_alt_takes_precedence_over_article_title(self):
        rows = _normalize_image_search_candidates({"images": [{
            "title": "增上寺旅行攻略", "alt": "横滨港未来21夜景",
            "image_url": "https://images.example.com/harbor.jpg",
        }]}, "search_image_from_web")
        self.assertEqual("横滨港未来21夜景", rows[0]["title"])
        self.assertFalse(_image_candidate_matches_place(rows[0], "增上寺", "东京"))

    def test_tavily_mcp_formatted_images_keep_following_description(self):
        rows = _normalize_image_search_candidates(
            "Detailed Results:\n\nImages:\n\n"
            "[1] URL: https://images.example.com/yellow-crane-tower.jpg\n"
            "   Description: 武汉黄鹤楼建筑外观与景区实景",
            "tavily_search",
        )

        self.assertEqual("武汉黄鹤楼建筑外观与景区实景", rows[0]["title"])
        self.assertEqual(
            "https://images.example.com/yellow-crane-tower.jpg",
            rows[0]["image_url"],
        )

    @patch("services.chat_service.fetch_wikimedia_activity_images", return_value={})
    def test_formal_activity_prefers_exact_map_poi_image_and_allows_export(self, _mocked_wikimedia):
        travel_bundle = {
            "query": "杭州西湖一日游",
            "destination_city": "杭州",
            "map_locations": [{
                "id": "west-lake",
                "place_id": "amap-west-lake",
                "provider_place_id": "amap-west-lake",
                "name": "西湖",
                "category": "景点",
                "city": "杭州",
                "address": "杭州市西湖区",
                "lat": 30.25,
                "lng": 120.15,
                "coordinate_system": "BD09LL",
                "coordinates_trusted": True,
                "source_provider": "amap",
                "source_providers": ["amap"],
                "provider_images": {"amap": ["https://poi.example.com/west-lake.jpg"]},
                "images": ["https://poi.example.com/west-lake.jpg"],
                "requested_destination": "杭州",
                "destination_bound": True,
                "day": 1,
            }],
            "web_rows": [],
            "xhs_rows": [],
        }

        attached = maybe_prepare_activity_images(travel_bundle)

        self.assertEqual(attached, 1)
        asset = travel_bundle["map_locations"][0]["image_assets"][0]
        self.assertEqual(asset["provider_name"], "高德地图 POI 图片")
        self.assertTrue(asset["display_allowed"])
        self.assertTrue(asset["export_allowed"])
        self.assertFalse(asset["attribution_required"])
        self.assertEqual(
            travel_bundle["activity_image_sources"][0]["reference_id"],
            asset["source_ref"],
        )

    @patch("services.chat_service._search_activity_image_candidates", return_value=[])
    @patch("services.chat_service.fetch_wikimedia_activity_images", return_value={})
    def test_formal_activity_has_no_placeholder_when_exact_image_is_missing(
        self,
        _mocked_wikimedia,
        _mocked_search,
    ):
        travel_bundle = {
            "query": "武汉三日游",
            "destination_city": "武汉",
            "cover_image": {
                "url": "https://images.unsplash.com/photo-wuhan-city",
                "alt": "武汉城市风光",
                "photographer_name": "Travel Photographer",
                "photographer_url": "https://unsplash.com/@travel-photographer",
                "unsplash_url": "https://unsplash.com/photos/wuhan-city",
                "download_location": "https://api.unsplash.com/photos/wuhan-city/download",
                "source_reference_id": "source_cover_unsplash",
            },
            "map_locations": [{
                "id": "jianghan-customs-house",
                "place_id": "amap-jianghan-customs-house",
                "name": "江汉关博物馆",
                "category": "景点",
                "city": "武汉",
                "address": "武汉市江汉区",
                "lat": 30.584,
                "lng": 114.296,
                "coordinate_system": "BD09LL",
                "coordinates_trusted": True,
                "source_provider": "amap",
                "requested_destination": "武汉",
                "destination_bound": True,
                "day": 1,
            }],
            "web_rows": [],
            "xhs_rows": [],
        }

        attached = maybe_prepare_activity_images(travel_bundle)

        self.assertEqual(0, attached)
        self.assertFalse(travel_bundle["map_locations"][0].get("image_assets"))
        self.assertEqual("武汉城市风光", travel_bundle["cover_image"]["alt"])

    def test_is_xhs_query_supports_normal_guide_question(self):
        self.assertTrue(_is_xhs_query("杭州三日游攻略怎么安排"))
        self.assertFalse(_is_xhs_query("帮我算一下 123 * 456"))

    def test_is_travel_experience_query_covers_planning_hotel_and_food_but_not_tickets(self):
        self.assertTrue(_is_travel_experience_query("帮我做一个杭州三日游行程"))
        self.assertTrue(_is_travel_experience_query("帮我规划一次北京3天2夜的文化之旅"))
        self.assertTrue(_is_travel_experience_query("成都酒店住哪比较方便"))
        self.assertTrue(_is_travel_experience_query("西安有什么美食和小吃推荐"))
        self.assertFalse(_is_travel_experience_query("查询明天杭州到南京的票"))

    def test_train_ticket_query_supports_route_with_generic_ticket_wording(self):
        self.assertTrue(_is_train_ticket_query("查询明天杭州到南京的票"))
        self.assertFalse(_is_train_ticket_query("杭州博物馆门票怎么买"))

    def test_train_ticket_route_removes_date_and_generic_ticket_wording(self):
        self.assertEqual(_extract_route_from_query("查询明天杭州到南京的票"), ("杭州", "南京"))
        self.assertEqual(_extract_route_from_query("杭州到南京6月3日的票"), ("杭州", "南京"))

    def test_tool_arg_candidates_use_registered_schema_when_available(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                return SimpleNamespace(
                    parameters={"from_city": {}, "to_city": {}, "travel_date": {}, "max_results": {}},
                    required=["from_city", "to_city", "travel_date"],
                )

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append(kwargs)
                return {"error": True, "message": "网页未返回可解析结果"}

        tool_manager = FakeToolManager()
        payload = _run_tool_with_arg_candidates(
            tool_manager=tool_manager,
            tool_name="query_flight_tickets",
            message_history=[],
            session_id="s-schema",
            arg_candidates=[
                {"date": "2026-06-03", "fromCity": "广州", "toCity": "桂林"},
                {"travel_date": "2026-06-03", "from_city": "广州", "to_city": "桂林"},
                {"query": "2026-06-03 广州到桂林机票"},
            ],
        )

        self.assertEqual(payload["message"], "网页未返回可解析结果")
        self.assertEqual(
            tool_manager.calls,
            [{"travel_date": "2026-06-03", "from_city": "广州", "to_city": "桂林"}],
        )

    def test_web_rows_parse_mcp_json_string_result(self):
        payload = {
            "content": [
                {
                    "type": "text",
                    "text": "{\"results\":[{\"title\":\"上海外滩酒店推荐\",\"url\":\"https://example.com/hotel\",\"snippet\":\"外滩附近酒店可按预算筛选。\"}]}",
                }
            ]
        }

        rows = _normalize_web_rows(payload, max_rows=3)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "上海外滩酒店推荐")
        self.assertEqual(rows[0]["snippet"], "外滩附近酒店可按预算筛选。")

    def test_tavily_web_rows_keep_content_and_infer_source_host(self):
        rows = _normalize_web_rows(
            {
                "query": "南京旅行",
                "results": [{
                    "title": "南京博物院参观指南",
                    "url": "https://www.njmuseum.com/guide",
                    "content": "开放时间、预约要求和交通方式应以官方页面为准。",
                    "score": 0.91,
                }],
            },
            max_rows=3,
        )

        self.assertEqual(rows, [{
            "title": "南京博物院参观指南",
            "snippet": "开放时间、预约要求和交通方式应以官方页面为准。",
            "url": "https://www.njmuseum.com/guide",
            "source": "njmuseum.com",
        }])

    def test_tavily_and_serper_are_both_queried_when_one_provider_fails(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                tools = {
                    "tavily_search": SimpleNamespace(
                        parameters={"query": {}, "search_depth": {}, "max_results": {}},
                        required=["query"],
                    ),
                    "search_web_page": SimpleNamespace(
                        parameters={"query": {}, "count": {}},
                        required=["query"],
                    ),
                }
                return tools.get(name)

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs))
                if name == "tavily_search":
                    return {"error": True, "message": "Tavily 暂时不可用"}
                return {"results": [{
                    "title": "南京官方旅游信息",
                    "url": "https://example.com/nanjing",
                    "snippet": "南京旅行参考。",
                }]}

        manager = FakeToolManager()
        rows, tool_name, error = _run_web_search_for_travel(
            "南京三日游",
            manager,
            [{"role": "user", "content": "南京三日游"}],
            "tavily-fallback",
        )

        self.assertEqual([name for name, _ in manager.calls], ["tavily_search", "search_web_page"])
        self.assertEqual(tool_name, "search_web_page")
        self.assertEqual(error, "")
        self.assertEqual(rows[0]["title"], "南京官方旅游信息")

    def test_tavily_and_serper_results_are_merged_deduplicated_and_ranked(self):
        rows = _merge_web_search_rows(
            [
                ("tavily_search", [
                    {
                        "title": "南京博物院参观指南",
                        "url": "https://www.njmuseum.com/guide?utm_source=tavily",
                        "snippet": "南京博物院预约信息。",
                        "source": "njmuseum.com",
                    },
                    {
                        "title": "普通旅行随笔",
                        "url": "https://blog.example.com/note",
                        "snippet": "一次旅行记录。",
                        "source": "blog.example.com",
                    },
                ]),
                ("search_web_page", [
                    {
                        "title": "南京博物院参观指南",
                        "url": "https://www.njmuseum.com/guide",
                        "snippet": "南京博物院实行实名预约，参观时间和交通方式应以官方网站最新公告为准。",
                        "source": "njmuseum.com",
                    },
                    {
                        "title": "南京市文化旅游信息",
                        "url": "https://wlj.nanjing.gov.cn/travel",
                        "snippet": "南京景点与公共文化场馆官方信息。",
                        "source": "wlj.nanjing.gov.cn",
                    },
                ]),
            ],
            "南京博物院参观预约攻略",
            max_rows=3,
        )

        self.assertEqual(len([row for row in rows if "njmuseum.com/guide" in row["url"]]), 1)
        museum = next(row for row in rows if "njmuseum.com/guide" in row["url"])
        self.assertEqual(museum["search_providers"], "Tavily, Serper")
        self.assertIn("实名预约", museum["snippet"])
        self.assertEqual(rows[0]["title"], "南京博物院参观指南")

    def test_reference_tables_escape_pipes_and_shorten_links(self):
        web_table = _build_web_markdown_table(
            [
                {
                    "title": "Klook | 北京3天2晚文化美食之旅",
                    "snippet": "故宫|景山|颐和园，适合第一次到北京的用户。",
                    "url": "https://www.klook.com/zh-CN/activity/152447-3-day-trip-in-beijing-dress-up-in-the-imperial-consort-style-in-the/",
                    "source": "web",
                }
            ]
        )
        xhs_table = _build_xhs_markdown_table(
            [
                {
                    "title": "外滩酒店|真实入住反馈",
                    "author": "旅行作者A",
                    "liked_count": "42",
                    "summary": "步行去外滩方便，适合预算中等的用户。",
                    "url": "https://www.xiaohongshu.com/explore/hotel1",
                    "source": "fake_xhs",
                }
            ]
        )

        self.assertIn("Klook ｜ 北京3天2晚文化美食之旅", web_table)
        self.assertIn("[查看](https://www.klook.com", web_table)
        self.assertNotIn("3-day-trip-in-beijing-dress-up-in-the-imperial-consort-style-in-the/ |", web_table)
        self.assertIn("外滩酒店｜真实入住反馈", xhs_table)
        self.assertIn("[查看](https://www.xiaohongshu.com/explore/hotel1)", xhs_table)

    def test_web_reference_table_uses_url_from_snippet_when_url_missing(self):
        web_table = _build_web_markdown_table(
            [
                {
                    "title": "安阳文化游攻略",
                    "snippet": "详细攻略见 https://example.com/anyang",
                    "url": "-",
                    "source": "web",
                }
            ]
        )

        self.assertIn("[查看](https://example.com/anyang)", web_table)

    def test_final_answer_markdown_normalizer_restores_headings_and_tables(self):
        raw = (
            "#杭州3天2夜文化之旅攻略---##🌦 出行天气参考|日期|天气|温度|风力 ||------|------|------|------||6月14日|多云转阴|24℃-32℃|微风|"
            "## 📍 Day1—皇城中轴线走进六百年紫禁城"
            "### 上午（8:00-11:30） 天安门广场-建议早起观看升旗。"
            "## 网络搜索参考表| 标题 | 摘要 | 链接 | 来源 || --- | --- | --- | --- |"
            "| Klook ｜ 北京3天2晚文化美食之旅 | 摘要 | [查看](https://example.com) | web |"
        )

        normalized = _normalize_markdown_for_display(raw)

        self.assertTrue(normalized.startswith("# 杭州3天2夜文化之旅攻略"))
        self.assertIn("## 🌦 出行天气参考\n|日期|天气|温度|风力 |", normalized)
        self.assertIn("|------|------|------|------|", normalized)
        self.assertIn("\n\n## 📍 Day1", normalized)
        self.assertIn("### 上午（8:00-11:30）\n\n天安门广场", normalized)
        self.assertIn("## 网络搜索参考表\n| 标题 | 摘要 | 链接 | 来源 |", normalized)
        self.assertIn("| --- | --- | --- | --- |\n| Klook", normalized)

    def test_build_message_history_generates_message_id(self):
        request_messages = [
            SimpleNamespace(role="user", content="hello", message_id=None, type="normal"),
            SimpleNamespace(role="assistant", content="world", message_id="m-1", type="final_answer"),
        ]

        result = build_message_history(request_messages)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["role"], "user")
        self.assertEqual(result[0]["content"], "hello")
        self.assertTrue(result[0]["message_id"])
        self.assertEqual(result[1]["message_id"], "m-1")
        self.assertEqual(result[1]["type"], "final_answer")

    def test_execute_chat_once_forwards_params(self):
        controller = FakeController()
        tool_manager = object()
        request_messages = [
            SimpleNamespace(role="user", content="test", message_id="u-1", type="normal"),
        ]

        payload = execute_chat_once(
            request_messages=request_messages,
            controller=controller,
            tool_manager=tool_manager,
            session_id="s-1",
            use_deepthink=True,
            use_multi_agent=False,
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["session_id"], "s-1")
        self.assertEqual(payload["result"], {"ok": True, "steps": 3})

        self.assertIsNotNone(controller.called_with)
        self.assertEqual(controller.called_with["session_id"], "s-1")
        self.assertEqual(controller.called_with["deep_thinking"], True)
        self.assertEqual(controller.called_with["deep_research"], False)
        self.assertEqual(controller.called_with["summary"], True)
        self.assertIs(controller.called_with["tool_manager"], tool_manager)

    def test_execute_chat_once_injects_selected_skill_system_message(self):
        controller = FakeController()
        tool_manager = object()
        request_messages = [
            SimpleNamespace(role="user", content="明天广州到桂林", message_id="u-skill", type="normal"),
        ]

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None) as train_mock:
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=None):
                payload = execute_chat_once(
                    request_messages=request_messages,
                    controller=controller,
                    tool_manager=tool_manager,
                    session_id="s-skill",
                    use_deepthink=True,
                    use_multi_agent=False,
                    selected_skill_ids=["rail_transport"],
                )

        self.assertEqual(payload["status"], "success")
        messages = controller.called_with["messages"]
        self.assertEqual(messages[0]["type"], "system_skill_profile")
        self.assertIn("交通票务查询", messages[0]["content"])
        self.assertIn("query_12306_realtime_tickets", messages[0]["content"])
        train_mock.assert_called_once()
        self.assertEqual(train_mock.call_args.kwargs["selected_skill_ids"], ["rail_transport"])

    def test_train_ticket_bundle_can_be_triggered_by_selected_skill(self):
        class FakeToolManager:
            def get_tool(self, name):
                return object() if name == "get-tickets" else None

            def run_tool(self, name, messages, session_id, **kwargs):
                return [
                    {
                        "train_code": "G101",
                        "from_station_name": kwargs["fromStation"],
                        "to_station_name": kwargs["toStation"],
                        "start_time": "09:00",
                        "arrive_time": "12:00",
                        "lishi": "03:00",
                        "prices": [{"seat_name": "二等座", "price": 200, "num": "有"}],
                    }
                ]

        bundle = maybe_prepare_train_ticket_bundle(
            user_query="广州到桂林明天",
            tool_manager=FakeToolManager(),
            message_history=[{"role": "user", "content": "广州到桂林明天", "message_id": "u1", "type": "normal"}],
            session_id="s1",
            selected_skill_ids=["rail_transport"],
        )

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle["direct_rows"][0]["trip_no"], "G101")

    def test_ticket_table_displays_rows_when_realtime_tickets_exist(self):
        table = _build_ticket_markdown_table(
            direct_rows=[
                {
                    "type": "直达",
                    "trip_no": "D2992",
                    "route": "广州南 -> 桂林北",
                    "depart": "2026-05-27 08:10",
                    "arrive": "2026-05-27 10:55",
                    "duration": "2小时45分钟",
                    "seat": "二等座",
                    "price": "165元",
                    "note": "余票:有",
                }
            ],
            interline_rows=[],
            user_query="从广州到桂林最佳交通",
        )

        self.assertIn("火车和高铁票信息表", table)
        self.assertIn("| 直达 | D2992 | 广州南 -> 桂林北 |", table)
        self.assertNotIn("未查到实时可售车票", table)

    def test_ticket_table_shows_empty_message_only_when_no_realtime_tickets_exist(self):
        table = _build_ticket_markdown_table(
            direct_rows=[],
            interline_rows=[],
            user_query="从广州到桂林最佳交通",
        )

        self.assertIn("未查到实时可售车票", table)
        self.assertNotIn("| - | - | - |", table)
        self.assertNotIn("飞机票信息表", table)
        self.assertNotIn("大巴票信息表", table)

    def test_ticket_table_orders_available_modes_and_omits_missing_optional_modes(self):
        table = _build_ticket_markdown_table(
            flight_rows=[
                {
                    "trip_no": "CZ3907",
                    "route": "广州白云 -> 桂林两江",
                    "depart": "2026-05-28 08:00",
                    "arrive": "2026-05-28 09:30",
                    "duration": "1小时30分钟",
                    "seat": "经济舱",
                    "price": "580元",
                    "note": "余票:12",
                }
            ],
            direct_rows=[
                {
                    "type": "直达",
                    "trip_no": "D2992",
                    "route": "广州南 -> 桂林北",
                    "depart": "2026-05-28 08:10",
                    "arrive": "2026-05-28 10:55",
                    "duration": "2小时45分钟",
                    "seat": "二等座",
                    "price": "165元",
                    "note": "余票:有",
                }
            ],
            interline_rows=[],
            bus_rows=[
                {
                    "trip_no": "班次A",
                    "route": "广州省站 -> 桂林汽车站",
                    "depart": "2026-05-28 09:00",
                    "arrive": "2026-05-28 15:00",
                    "duration": "6小时",
                    "seat": "大巴",
                    "price": "180元",
                    "note": "余票:15",
                }
            ],
            user_query="广州到桂林最佳交通",
        )

        self.assertLess(table.index("飞机票信息表"), table.index("火车和高铁票信息表"))
        self.assertLess(table.index("火车和高铁票信息表"), table.index("大巴票信息表"))
        self.assertIn("| CZ3907 | 广州白云 -> 桂林两江 |", table)
        self.assertIn("| 班次A | 广州省站 -> 桂林汽车站 |", table)

    def test_direct_ticket_normalizer_reads_nested_mcp_payload(self):
        rows = _normalize_direct_rows(
            {
                "data": {
                    "tickets": [
                        {
                            "train_code": "D2992",
                            "from_station_name": "广州南",
                            "to_station_name": "桂林北",
                            "start_time": "08:10",
                            "arrive_time": "10:55",
                            "lishi": "02:45",
                            "prices": [{"seat_name": "二等座", "price": 165, "num": "有"}],
                        }
                    ]
                }
            },
            from_station="广州南",
            to_station="桂林北",
            travel_date="2026-05-27",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trip_no"], "D2992")
        self.assertEqual(rows[0]["route"], "广州南 -> 桂林北")

    def test_direct_ticket_normalizer_uses_query_date_not_train_origin_date(self):
        rows = _normalize_direct_rows(
            [
                {
                    "start_train_code": "K348",
                    "from_station": "杭州",
                    "to_station": "南京",
                    "start_date": "2026-06-05",
                    "arrive_date": "2026-06-05",
                    "start_time": "01:55",
                    "arrive_time": "07:09",
                    "lishi": "05:14",
                    "prices": [{"seat_name": "硬座", "price": 62.5, "num": "有"}],
                }
            ],
            from_station="杭州",
            to_station="南京",
            travel_date="2026-06-06",
        )

        self.assertEqual(rows[0]["depart"], "2026-06-06 01:55")
        self.assertEqual(rows[0]["arrive"], "2026-06-06 07:09")

    def test_city_station_pairs_cover_official_guangzhou_stations(self):
        station_names = [
            "广州南",
            "广州白云",
            "广州北",
            "广州",
            "广州东",
            "广州新塘",
            "广州西",
            "广州大学城",
            "广州长隆",
            "广州莲花山",
            "桂林北",
            "桂林",
            "桂林西",
            "北京南",
            "北京北",
            "北京",
        ]

        with patch("services.chat_service._load_12306_station_names", return_value=station_names):
            pairs = _station_query_pairs("广州", "桂林")

        self.assertEqual(len(pairs), 30)
        self.assertIn(("广州白云", "桂林北"), pairs)
        self.assertIn(("广州北", "桂林"), pairs)
        self.assertIn(("广州莲花山", "桂林西"), pairs)

        with patch("services.chat_service._load_12306_station_names", return_value=station_names):
            beijing_pairs = _station_query_pairs("北京", "桂林")

        self.assertIn(("北京南", "桂林北"), beijing_pairs)
        self.assertIn(("北京北", "桂林"), beijing_pairs)

    def test_interline_normalizer_reads_12306_middle_list_payload(self):
        rows = _normalize_interline_rows(
            {
                "data": {
                    "middleList": [
                        {
                            "all_lishi_minutes": 674,
                            "arrive_date": "2026-05-28",
                            "arrive_time": "09:44",
                            "from_station_name": "广州北",
                            "middle_station_name": "衡阳-衡阳东",
                            "end_station_name": "桂林",
                            "train_date": "2026-05-27",
                            "start_time": "22:30",
                            "wait_time_minutes": 183,
                            "fullList": [
                                {
                                    "station_train_code": "K436",
                                    "from_station_name": "广州北",
                                    "to_station_name": "衡阳",
                                    "start_time": "22:30",
                                    "arrive_time": "03:51",
                                    "start_train_date": "20260527",
                                    "yz_num": "无",
                                    "yw_num": "无",
                                    "wz_num": "有",
                                },
                                {
                                    "station_train_code": "D3961",
                                    "from_station_name": "衡阳东",
                                    "to_station_name": "桂林",
                                    "start_time": "06:54",
                                    "arrive_time": "09:44",
                                    "start_train_date": "20260528",
                                    "ze_num": "有",
                                    "zy_num": "有",
                                    "wz_num": "有",
                                },
                            ],
                        }
                    ]
                }
            },
            from_station="广州",
            to_station="桂林",
            travel_date="2026-05-27",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trip_no"], "K436 + D3961")
        self.assertEqual(rows[0]["route"], "广州北 -> 衡阳-衡阳东 -> 桂林")
        self.assertEqual(rows[0]["depart"], "2026-05-27 22:30")
        self.assertEqual(rows[0]["arrive"], "2026-05-28 09:44")
        self.assertEqual(rows[0]["duration"], "11小时14分钟")
        self.assertEqual(rows[0]["seat"], "无座+二等座")
        self.assertIn("候车:3小时3分钟", rows[0]["note"])
        self.assertIn("跨站换乘", rows[0]["note"])
        self.assertIn("跨天到达", rows[0]["note"])

    def test_xhs_bundle_can_be_triggered_by_selected_skill(self):
        class FakeToolManager:
            def get_tool(self, name):
                return object() if name == "xhs_search_and_summarize" else None

            def run_tool(self, name, messages, session_id, **kwargs):
                return {
                    "summary": "找到酒店周边真实反馈",
                    "source": "fake_xhs",
                    "items": [
                        {
                            "title": "酒店附近早餐",
                            "author": "旅行者A",
                            "liked_count": "12",
                            "summary": "步行可达",
                            "url": "https://www.xiaohongshu.com/explore/1",
                            "source": "fake_xhs",
                        }
                    ],
                }

        bundle = maybe_prepare_xhs_search_bundle(
            user_query="帮我看看这家酒店附近怎么样",
            tool_manager=FakeToolManager(),
            message_history=[{"role": "user", "content": "帮我看看这家酒店附近怎么样", "message_id": "u1", "type": "normal"}],
            session_id="s1",
            selected_skill_ids=["xhs_insight"],
        )

        self.assertIsNotNone(bundle)
        self.assertIn("小红书检索结果", bundle["context_message"])
        self.assertEqual(bundle["source_tool"], "xhs_search_and_summarize")

    def test_xhs_bundle_is_triggered_for_hotel_travel_query(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                return object() if name == "xhs_search_and_summarize" else None

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs))
                return {
                    "summary": "找到外滩酒店真实入住反馈",
                    "source": "fake_xhs",
                    "items": [
                        {
                            "title": "外滩附近性价比酒店",
                            "author": "旅行作者A",
                            "liked_count": "42",
                            "summary": "适合步行去外滩",
                            "url": "https://www.xiaohongshu.com/explore/hotel1",
                            "source": "fake_xhs",
                        }
                    ],
                }

        tool_manager = FakeToolManager()
        bundle = maybe_prepare_xhs_search_bundle(
            user_query="推荐上海外滩附近性价比高的酒店",
            tool_manager=tool_manager,
            message_history=[{"role": "user", "content": "推荐上海外滩附近性价比高的酒店", "message_id": "u1", "type": "normal"}],
            session_id="s-hotel",
        )

        self.assertIsNotNone(bundle)
        self.assertEqual(tool_manager.calls[0][0], "xhs_search_and_summarize")
        self.assertEqual(bundle["rows"][0]["title"], "外滩附近性价比酒店")

    def test_xhs_bundle_uses_lodging_query_only_as_experience_reference(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                return object() if name == "xhs_search_and_summarize" else None

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs))
                return {
                    "items": [{
                        "title": "南京新街口住宿区域体验",
                        "summary": "靠近地铁，适合晚间返程。",
                        "url": "https://www.xiaohongshu.com/explore/nanjing-stay",
                    }],
                }

        tool_manager = FakeToolManager()
        bundle = maybe_prepare_xhs_search_bundle(
            user_query="帮我规划南京三日游",
            tool_manager=tool_manager,
            message_history=[],
            session_id="s-lodging-reference",
            accommodation_reference=True,
        )

        self.assertIsNotNone(bundle)
        self.assertIn("南京 住宿 酒店 民宿 区域 入住体验", tool_manager.calls[0][1]["query"])
        self.assertIn("仅可作为区域与入住体验的参考", bundle["context_message"])

    def test_xhs_bundle_filters_non_travel_and_wrong_destination_results(self):
        class FakeToolManager:
            def get_tool(self, name):
                return object() if name == "xhs_search_and_summarize" else None

            def run_tool(self, name, messages, session_id, **kwargs):
                return {
                    "summary": "杭州校园招聘与旅行内容混合返回",
                    "items": [
                        {"title": "杭州校园招聘", "summary": "杭州校招岗位", "url": "https://www.xiaohongshu.com/explore/job"},
                        {"title": "杭州三日游路线", "summary": "西湖灵隐寺旅行攻略", "url": "https://www.xiaohongshu.com/explore/travel"},
                        {"title": "上海旅行攻略", "summary": "外滩 CityWalk", "url": "https://www.xiaohongshu.com/explore/shanghai"},
                    ],
                }

        bundle = maybe_prepare_xhs_search_bundle(
            user_query="杭州三日游攻略怎么安排",
            tool_manager=FakeToolManager(),
            message_history=[],
            session_id="s-xhs-filter",
        )

        self.assertIsNotNone(bundle)
        self.assertEqual([row["title"] for row in bundle["rows"]], ["杭州三日游路线"])
        self.assertEqual(bundle["rejected_row_count"], 2)
        self.assertNotIn("校园招聘", bundle["append_markdown"])
        self.assertNotIn("校园招聘", bundle["summary"])

    def test_ticket_bundle_returns_after_single_call_timeout_without_claiming_valid_results(self):
        class SlowToolManager:
            def get_tool(self, name):
                return object() if name == "get-tickets" else None

            def run_tool(self, name, messages, session_id, **kwargs):
                time.sleep(0.2)
                return []

        with patch("services.chat_service.TICKET_QUERY_TOTAL_TIMEOUT_SECONDS", 0.05):
            with patch("services.chat_service.TICKET_TOOL_ATTEMPT_TIMEOUT_SECONDS", 0.02):
                with patch("services.chat_service._load_12306_station_names", return_value=["广州南", "桂林北"]):
                    started_at = time.monotonic()
                    bundle = maybe_prepare_train_ticket_bundle(
                        user_query="从广州到桂林最佳交通",
                        tool_manager=SlowToolManager(),
                        message_history=[],
                        session_id="s-ticket-timeout",
                    )

        self.assertLess(time.monotonic() - started_at, 0.15)
        self.assertFalse(bundle["has_valid_results"])
        self.assertIn("超过", bundle["ticket_states"]["train"]["error"])
        self.assertIn("未找到可确认的票务结果", bundle["realtime_only_answer"])

    def test_travel_experience_bundle_calls_web_and_map_tools(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                if name == "search_web_page":
                    return SimpleNamespace(parameters={"query": {}, "count": {}}, required=["query"])
                if name == "map_search_places":
                    return SimpleNamespace(parameters={"query": {}, "region": {}}, required=["query"])
                return None

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs))
                if name == "search_web_page":
                    return [
                        {
                            "title": "杭州三日游攻略",
                            "url": "https://example.com/hz",
                            "snippet": "西湖、灵隐寺和湖滨适合顺路安排。",
                            "source": "web",
                        }
                    ]
                if name == "map_search_places":
                    return {
                        "places": [
                            {
                                "uid": "hangzhou-west-lake",
                                "name": "西湖风景名胜区",
                                "location": {"lat": 30.242, "lng": 120.141},
                                "address": "杭州市西湖区",
                                "category": "景点",
                            },
                            {
                                "uid": "hangzhou-lingyin-temple",
                                "name": "灵隐寺",
                                "location": {"lat": 30.24, "lng": 120.102},
                                "address": "杭州市西湖区法云弄",
                                "category": "人文古迹",
                            },
                        ]
                    }
                return {}

        tool_manager = FakeToolManager()
        bundle = maybe_prepare_travel_experience_bundle(
            user_query="帮我做一个杭州三日游行程，顺便推荐美食",
            tool_manager=tool_manager,
            message_history=[{"role": "user", "content": "帮我做一个杭州三日游行程，顺便推荐美食", "message_id": "u1", "type": "normal"}],
            session_id="s-travel",
        )

        self.assertIsNotNone(bundle)
        self.assertIn("旅行体验增强要求", bundle["context_message"])
        self.assertIn("网络搜索参考表", bundle["append_markdown"])
        self.assertNotIn("地图地点表", bundle["append_markdown"])
        self.assertNotIn('"map_locations"', bundle["append_markdown"])
        self.assertGreaterEqual(len(bundle["map_locations"]), 2)
        self.assertFalse(any(row.get("source") == "seed_fallback" for row in bundle["map_locations"]))
        map_queries = [
            str(kwargs.get("query") or "")
            for name, kwargs in tool_manager.calls
            if name == "map_search_places"
        ]
        self.assertTrue(any("热门景点" in query for query in map_queries))
        self.assertTrue(any("博物馆 公园 人文景观" in query for query in map_queries))
        self.assertTrue(any("古迹 寺庙 古镇 历史街区" in query for query in map_queries))
        self.assertTrue(any("特色餐厅 老字号 本地菜" in query for query in map_queries))

    def test_travel_experience_bundle_resolves_named_classics_through_map_provider(self):
        class FakeToolManager:
            def get_tool(self, name):
                if name == "map_search_places":
                    return SimpleNamespace(parameters={"query": {}, "region": {}}, required=["query"])
                return None

            def run_tool(self, name, messages, session_id, **kwargs):
                query = str(kwargs.get("query") or "")
                if any(place in query for place in ("西湖风景名胜区", "灵隐寺", "河坊街")):
                    return {
                        "places": [
                            {
                                "uid": f"seed-{query}",
                                "name": query,
                                "location": {"lat": 30.242, "lng": 120.141},
                                "address": "杭州市",
                            }
                        ]
                    }
                return {"places": []}

        bundle = maybe_prepare_travel_experience_bundle(
            user_query="帮我做一个杭州三日游行程",
            tool_manager=FakeToolManager(),
            message_history=[{"role": "user", "content": "帮我做一个杭州三日游行程", "message_id": "u-seed", "type": "normal"}],
            session_id="s-seed",
            allow_web_search=False,
        )

        self.assertIsNotNone(bundle)
        self.assertTrue(bundle["map_locations"])
        self.assertTrue(all(row.get("source") == "百度地图地点检索" for row in bundle["map_locations"]))
        self.assertTrue(all(row.get("data_type") == "reference_data" for row in bundle["map_locations"]))
        self.assertTrue(all(row.get("coordinates_trusted") is True for row in bundle["map_locations"]))
        self.assertTrue({"西湖风景名胜区", "灵隐寺"}.issubset(
            {row.get("name") for row in bundle["map_locations"]}
        ))
        self.assertIn("已完成外部检索", bundle["fallback_answer"])

    def test_travel_experience_bundle_skips_web_search_when_disabled(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                if name in {"search_web_page", "map_search_places"}:
                    return SimpleNamespace(parameters={"query": {}, "region": {}}, required=["query"])
                return None

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append(name)
                if name == "map_search_places":
                    return {
                        "places": [
                            {
                                "name": "西湖风景名胜区",
                                "location": {"lat": 30.242, "lng": 120.141},
                                "category": "景点",
                            }
                        ]
                    }
                return []

        tool_manager = FakeToolManager()
        bundle = maybe_prepare_travel_experience_bundle(
            user_query="帮我做一个杭州三日游行程",
            tool_manager=tool_manager,
            message_history=[{"role": "user", "content": "帮我做一个杭州三日游行程", "message_id": "u1", "type": "normal"}],
            session_id="s-no-web",
            allow_web_search=False,
        )

        self.assertIsNotNone(bundle)
        self.assertNotIn("search_web_page", tool_manager.calls)
        self.assertIn("map_search_places", tool_manager.calls)
        self.assertEqual(bundle["web_rows"], [])
        self.assertIn("关闭网页与社区检索", bundle["web_error"])

    def test_complete_trip_plan_with_transport_word_is_not_misclassified_as_ticket_only(self):
        class EmptyToolManager:
            def get_tool(self, _name):
                return None

        query = (
            "请帮我规划一趟从上海出发、前往杭州的3天旅行。"
            "日期为2026-08-15至2026-08-17，总预算6000元，"
            "请生成完整行程，并对强实时交通和地图数据进行真实核验。"
        )
        bundle = maybe_prepare_travel_experience_bundle(
            user_query=query,
            tool_manager=EmptyToolManager(),
            message_history=[{"role": "user", "content": query, "message_id": "u-form", "type": "normal"}],
            session_id="s-form",
            allow_web_search=False,
        )

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle["destination_city"], "杭州")
        self.assertIn("旅行体验增强要求", bundle["context_message"])

    def test_travel_fallback_does_not_claim_external_unavailable_when_web_rows_exist(self):
        answer = _build_travel_fallback_answer(
            {
                "query": "推荐上海外滩附近性价比高的酒店",
                "kinds": ["酒店住宿"],
                "web_rows": [
                    {
                        "title": "上海外滩酒店推荐",
                        "snippet": "外滩附近酒店可按预算筛选。",
                        "url": "https://example.com/hotel",
                        "source": "web",
                    }
                ],
                "map_locations": [],
                "xhs_table": "",
                "append_markdown": "",
            }
        )

        self.assertIn("已完成外部检索", answer)
        self.assertNotIn("外部检索结果暂时不可用", answer)

    def test_travel_fallback_states_limit_when_only_seed_reference_exists(self):
        answer = _build_travel_fallback_answer(
            {
                "query": "帮我做一个杭州三日游行程",
                "kinds": ["行程规划"],
                "web_rows": [],
                "map_tool": "map_search_places",
                "map_locations": [
                    {
                        "name": "西湖风景名胜区",
                        "lat": 30.242,
                        "lng": 120.141,
                        "source": "seed_fallback",
                    }
                ],
                "xhs_table": "",
                "append_markdown": "",
            }
        )

        self.assertIn("没有取得可验证的外部实时结果", answer)
        self.assertNotIn("已完成外部检索", answer)

    def test_travel_fallback_does_not_invent_city_specific_recommendations(self):
        answer = _build_travel_fallback_answer(
            {
                "query": "帮我规划北京3天2夜行程",
                "kinds": ["行程规划"],
                "web_rows": [],
                "map_locations": [],
                "xhs_table": "",
                "append_markdown": "",
            }
        )

        self.assertIn("具体地点必须在地图或可靠来源返回后再填入", answer)
        for hardcoded_place in ("故宫博物院", "天安门广场", "颐和园", "798艺术区"):
            self.assertNotIn(hardcoded_place, answer)

    def test_travel_experience_bundle_defaults_hotel_locations_to_hotel_category(self):
        class FakeToolManager:
            def get_tool(self, name):
                if name == "search_web_page":
                    return SimpleNamespace(parameters={"query": {}, "count": {}}, required=["query"])
                if name == "map_search_places":
                    return SimpleNamespace(parameters={"query": {}, "region": {}}, required=["query"])
                return None

            def run_tool(self, name, messages, session_id, **kwargs):
                if name == "search_web_page":
                    return [
                        {
                            "title": "上海外滩酒店推荐",
                            "url": "https://example.com/shanghai-hotel",
                            "snippet": "外滩附近酒店可按高端、中端和经济分层选择。",
                            "source": "web",
                        }
                    ]
                if name == "map_search_places":
                    return {
                        "places": [
                            {
                                "uid": "shanghai-waldorf-bund",
                                "name": "上海外滩华尔道夫酒店",
                                "location": {"lat": 31.2366, "lng": 121.4908},
                                "address": "上海市黄浦区中山东一路2号",
                            }
                        ]
                    }
                return {}

        bundle = maybe_prepare_travel_experience_bundle(
            user_query="推荐上海外滩附近性价比高的酒店",
            tool_manager=FakeToolManager(),
            message_history=[
                {
                    "role": "user",
                    "content": "推荐上海外滩附近性价比高的酒店",
                    "message_id": "u-hotel",
                    "type": "normal",
                }
            ],
            session_id="s-hotel",
        )

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle["kinds"], ["酒店住宿"])
        self.assertEqual(bundle["map_locations"][0]["category"], "酒店")
        self.assertIn("分组样式", bundle["context_message"])
        self.assertNotIn("map_locations", bundle["append_markdown"])

    def test_travel_final_answer_does_not_use_user_query_as_h1_or_insert_generic_schedule(self):
        user_query = "帮我规划一次沈阳3天2夜的文化之旅"
        bundle = {
            "query": user_query,
            "kinds": ["行程规划"],
            "xhs_table": "",
            "web_rows": [],
            "map_locations": [],
            "fallback_answer": "",
        }

        with TemporaryDirectory() as tmpdir:
            with patch("services.chat_service.get_output_root_path", return_value=Path(tmpdir)):
                merged = _select_travel_final_answer(
                    "沈阳适合围绕故宫、张氏帅府和中街安排三天两夜的文化路线。",
                    bundle,
                    session_id="s-shenyang-structure",
                )

        self.assertNotRegex(merged, rf"(?m)^#\s+{user_query}\s*$")
        self.assertNotIn("## 推荐安排", merged)

    def test_travel_experience_bundle_map_locations_include_hotel_and_food_search_hits(self):
        class FakeToolManager:
            def get_tool(self, name):
                if name == "search_web_page":
                    return SimpleNamespace(parameters={"query": {}, "count": {}}, required=["query"])
                if name == "map_search_places":
                    return SimpleNamespace(parameters={"query": {}, "region": {}}, required=["query"])
                return None

            def run_tool(self, name, messages, session_id, **kwargs):
                if name == "search_web_page":
                    return [
                        {
                            "title": "沈阳文化游住宿与美食攻略",
                            "snippet": "故宫、中街附近适合串联住宿和餐饮。",
                            "url": "https://example.com/shenyang",
                            "source": "web",
                        }
                    ]
                if name == "map_search_places":
                    return {
                        "places": [
                            {
                                "uid": "shenyang-palace-hotel",
                                "name": "沈阳故宫附近酒店",
                                "location": {"lat": 41.7961, "lng": 123.4613},
                                "address": "沈阳市沈河区沈阳路附近",
                                "category": "酒店",
                            },
                            {
                                "uid": "shenyang-laobian-dumpling",
                                "name": "老边饺子馆中街店",
                                "location": {"lat": 41.8002, "lng": 123.4456},
                                "address": "沈阳市沈河区中街",
                                "category": "餐厅",
                            },
                        ]
                    }
                return {}

        query = "帮我规划一次沈阳3天2夜的文化之旅，包含住宿和美食"
        bundle = maybe_prepare_travel_experience_bundle(
            user_query=query,
            tool_manager=FakeToolManager(),
            message_history=[{"role": "user", "content": query, "message_id": "u-shenyang", "type": "normal"}],
            session_id="s-shenyang-map",
        )

        self.assertIsNotNone(bundle)
        location_names = {location["name"] for location in bundle["map_locations"]}
        lodging_names = {location["name"] for location in bundle["lodging_candidates"]}
        location_categories = {location["category"] for location in bundle["map_locations"]}
        self.assertIn("沈阳故宫附近酒店", lodging_names)
        self.assertNotIn("沈阳故宫附近酒店", location_names)
        self.assertIn("老边饺子馆中街店", location_names, msg=str(bundle))
        self.assertIn("餐厅", location_categories)

    def test_quality_map_filter_rejects_non_itinerary_business_and_memorial_pois(self):
        rows = _filter_quality_map_locations([
            {"name": "杭州第三公墓", "category": "景点", "description": "杭州市临平区"},
            {"name": "杭州军瑞旅行社有限公司", "category": "景点", "description": "杭州市萧山区"},
            {"name": "杭州灵山景区", "category": "景点", "description": "杭州市西湖区"},
        ], "杭州")

        self.assertEqual([row["name"] for row in rows], ["杭州灵山景区"])

    def test_lodging_filter_requires_positive_destination_signal(self):
        rows = _filter_destination_lodging_locations([
            {"name": "上海外滩酒店", "category": "酒店", "address": "上海市黄浦区中山东一路"},
            {"name": "南京中心酒店", "category": "酒店", "address": "南京市玄武区"},
            {"name": "未标注城市酒店", "category": "酒店", "address": "中心城区"},
        ], "南京")

        self.assertEqual([row["name"] for row in rows], ["南京中心酒店"])

    def test_formal_location_filter_rejects_cross_city_street_name_matches(self):
        rows = _filter_quality_map_locations([
            {"name": "梅龙镇酒家(南京西路总店)", "category": "餐厅", "address": "上海市静安区南京西路"},
            {"name": "南京博物院", "category": "景点", "address": "南京市玄武区中山东路321号"},
            {"name": "精妆汉服", "category": "生活服务", "address": "南京市秦淮区"},
            {"name": "圆雕卧狮柱础(明)", "category": "旅游景点", "address": "南京市玄武区"},
            {"name": "中山陵景区(暂停开放)", "category": "旅游景点", "address": "南京市玄武区"},
            {"name": "未标注城市地点", "category": "景点", "address": "中心城区"},
        ], "南京", require_positive_destination=True)

        self.assertEqual([row["name"] for row in rows], ["南京博物院"])

    def test_cross_city_map_rows_do_not_become_unverified_formal_agenda(self):
        plan = _travel_bundle_to_trip_plan({
            "query": "从上海出发，规划南京3天行程，朋友2人，人均2000元",
            "destination_city": "南京",
            "map_locations": [
                {"name": "绿波廊(豫园店)", "category": "餐厅", "address": "上海市黄浦区豫园路"},
                {"name": "梅龙镇酒家(南京西路总店)", "category": "餐厅", "address": "上海市静安区南京西路"},
            ],
            "trip_intent": {
                "origin": "上海",
                "destination": "南京",
                "date_range": "日期暂未确定",
                "days": 3,
                "people_count": 2,
                "people_type": "朋友",
                "budget_per_person": 2000,
            },
        })

        activity_titles = [activity.title for activity in plan.activities]
        self.assertNotIn("绿波廊(豫园店)", activity_titles)
        self.assertNotIn("梅龙镇酒家(南京西路总店)", activity_titles)
        self.assertNotIn("上海博物馆", activity_titles)
        self.assertNotIn("外滩", activity_titles)
        self.assertEqual([], plan.activities)

    def test_formal_meals_follow_confirmed_transport_windows_and_verified_pois(self):
        attractions = [
            {
                "name": f"南京景点 {index}",
                "category": "景点",
                "poi_id": f"attraction-{index}",
                "city": "南京",
                "lat": 32.04 + index * 0.001,
                "lng": 118.78 + index * 0.001,
            }
            for index in range(1, 7)
        ]
        restaurants = [
            {
                "name": f"南京餐厅 {index}",
                "category": "餐厅",
                "poi_id": f"restaurant-{index}",
                "city": "南京",
                "lat": 32.06 + index * 0.001,
                "lng": 118.80 + index * 0.001,
                "opening_hours": "10:00-22:00",
            }
            for index in range(1, 6)
        ]
        plan = _travel_bundle_to_trip_plan({
            "query": "从上海出发，规划南京3天行程",
            "destination_city": "南京",
            "map_locations": [*attractions, *restaurants],
            "trip_intent": {
                "origin": "上海",
                "destination": "南京",
                "date_range": "2026-08-01 至 2026-08-03",
                "days": 3,
                "people_count": 1,
            },
            "ticket_bundle": {
                "direct_source": "12306",
                "direct_rows": [{
                    "route": "上海虹桥 -> 南京南",
                    "depart": "2026-08-01 08:00",
                    "arrive": "2026-08-01 10:00",
                    "price": 150,
                }],
            },
            "return_ticket_bundle": {
                "direct_source": "12306",
                "direct_rows": [{
                    "route": "南京南 -> 上海虹桥",
                    "depart": "2026-08-03 16:00",
                    "arrive": "2026-08-03 18:00",
                    "price": 150,
                }],
            },
        })

        meals_by_day = {
            day: [activity.meal_type for activity in plan.activities if activity.day == day and activity.activity_type == "food"]
            for day in range(1, 4)
        }
        self.assertEqual({1: ["lunch", "dinner"], 2: ["lunch", "dinner"], 3: ["lunch"]}, meals_by_day)
        self.assertTrue(all(activity.place and activity.place.poi_id for activity in plan.activities if activity.activity_type == "food"))
        self.assertTrue(all(activity.place and activity.place.lat is not None and activity.place.lng is not None for activity in plan.activities if activity.activity_type == "food"))

    def test_structured_trip_keeps_seven_to_fifteen_unused_verified_pois_as_candidates(self):
        attractions = [
            {
                "name": f"南京候选景点 {index}",
                "category": "景点",
                "poi_id": f"candidate-attraction-{index}",
                "city": "南京",
                "lat": 32.04 + index * 0.001,
                "lng": 118.78 + index * 0.001,
                "opening_hours": "09:00-18:00",
                "coordinates_trusted": index <= 12,
            }
            for index in range(1, 20)
        ]
        restaurants = [
            {
                "name": f"南京候选餐厅 {index}",
                "category": "餐厅",
                "poi_id": f"candidate-restaurant-{index}",
                "city": "南京",
                "lat": 32.08 + index * 0.001,
                "lng": 118.81 + index * 0.001,
                "opening_hours": "10:00-22:00",
                "coordinates_trusted": True,
            }
            for index in range(1, 8)
        ]
        events, _ = _build_travel_structured_result({
            "query": "从上海出发，规划南京3天行程",
            "destination_city": "南京",
            "map_locations": [*attractions, *restaurants],
            "map_tool": "map_search_places",
            "trip_intent": {
                "origin": "上海",
                "destination": "南京",
                "date_range": "2026-08-01 至 2026-08-03",
                "days": 3,
                "people_count": 1,
                "budget_total": 5000,
            },
        })

        legacy_document = next(event["document"] for event in events if event["type"] == "trip_plan")
        document = adapt_v2_to_v3(legacy_document)

        self.assertGreaterEqual(len(document.candidate_pool), 7)
        self.assertLessEqual(len(document.candidate_pool), 15)
        self.assertTrue(all(candidate.place and candidate.place.poi_id for candidate in document.candidate_pool))
        self.assertTrue(all(
            candidate.insertion_options or candidate.insertion_unavailable_reason
            for candidate in document.candidate_pool
        ))
        self.assertTrue(any(candidate.insertion_options for candidate in document.candidate_pool))

    def test_candidate_limit_keeps_late_result_with_opening_evidence(self):
        from types import SimpleNamespace
        from services.chat_service import _candidate_places_for_document
        locations = [{"name": f"南京候选{index}", "poi_id": f"poi-{index}", "city": "南京",
                      "lat": 32.04, "lng": 118.78, "category": "景点",
                      "opening_hours": "09:00-22:00" if index == 19 else None}
                     for index in range(20)]
        candidates = _candidate_places_for_document(
            {"map_locations": locations, "destination_city": "南京"}, SimpleNamespace(activities=[]))
        self.assertEqual(15, len(candidates))
        self.assertEqual("poi-19", candidates[0]["poi_id"])
        self.assertEqual("09:00-22:00", candidates[0]["opening_hours"])

    @patch("services.chat_service.build_day_route")
    def test_stream_formal_document_persists_provider_day_routes(self, mocked_route):
        async def ready_route(**kwargs):
            activities = kwargs["activities"]
            return {
                "day": kwargs["day"],
                "plan_version": kwargs["plan_version"],
                "provider": "baidu_directionlite",
                "coordinate_system": "BD09LL",
                "legs": [{
                    "from_activity_id": activities[0]["activity_id"],
                    "to_activity_id": activities[1]["activity_id"],
                    "mode": "walking",
                    "distance_meters": 800,
                    "duration_minutes": 12,
                    "geometry": {"type": "LineString", "coordinates": [[118.78, 32.04], [118.79, 32.05]]},
                    "provider": "baidu_directionlite",
                    "coordinate_system": "BD09LL",
                    "calculated_at": "2026-08-02T00:00:00Z",
                    "status": "ready",
                }],
                "bbox": None,
                "status": "ready",
            }

        mocked_route.side_effect = ready_route
        locations = [
            {
                "name": f"南京景点 {index}", "category": "景点", "poi_id": f"poi-{index}",
                "city": "南京", "lat": 32.04 + index * 0.001, "lng": 118.78 + index * 0.001,
                "coordinates_trusted": True,
            }
            for index in range(1, 13)
        ] + [
            {
                "name": f"南京餐厅 {index}", "category": "餐厅", "poi_id": f"food-{index}",
                "city": "南京", "lat": 32.03 + index * 0.001, "lng": 118.77 + index * 0.001,
                "coordinates_trusted": True,
            }
            for index in range(1, 7)
        ]
        events, markdown = asyncio.run(_build_travel_structured_result_with_routes({
            "query": "从上海出发，规划南京3天行程",
            "destination_city": "南京",
            "map_locations": locations,
            "map_tool": "map_search_places",
            "trip_intent": {
                "origin": "上海", "destination": "南京", "date_range": "2026-08-01 至 2026-08-03",
                "days": 3, "people_count": 1, "budget_total": 5000,
            },
        }, SimpleNamespace(baidu_request_dispatcher=object())))

        legacy_document = next(event["document"] for event in events if event["type"] == "trip_plan")
        document = adapt_v2_to_v3(legacy_document)
        self.assertEqual(3, len(document.map_guidance.day_routes))
        self.assertTrue(all(route.status == "ready" for route in document.map_guidance.day_routes))
        self.assertTrue(all(route.legs and route.legs[0].geometry for route in document.map_guidance.day_routes))
        self.assertTrue(all(
            day.activities[0].route_to_next
            and day.activities[0].route_to_next.status == "ready"
            for day in document.itinerary.days
        ))
        self.assertIn("已按日计算真实道路路线", markdown)

    @patch("services.chat_service.build_day_route")
    def test_route_dropped_activities_do_not_overflow_candidate_limit(self, mocked_route):
        dropped_poi_ids = set()

        async def long_route(**kwargs):
            activities = kwargs["activities"]
            legs = []
            if len(activities) >= 2:
                dropped_poi_ids.add(activities[1]["place"]["poi_id"])
                legs.append({
                    "from_activity_id": activities[0]["activity_id"],
                    "to_activity_id": activities[1]["activity_id"],
                    "mode": "driving",
                    "distance_meters": 100_000,
                    "duration_minutes": 900,
                    "geometry": None,
                    "provider": "baidu_directionlite",
                    "coordinate_system": "BD09LL",
                    "calculated_at": "2026-08-20T00:00:00Z",
                    "status": "ready",
                })
            return {
                "day": kwargs["day"],
                "plan_version": kwargs["plan_version"],
                "provider": "baidu_directionlite",
                "coordinate_system": "BD09LL",
                "legs": legs,
                "bbox": None,
                "status": "ready",
            }

        mocked_route.side_effect = long_route
        locations = [
            {
                "name": f"成都景点 {index}", "category": "景点", "poi_id": f"poi-{index}",
                "city": "成都", "lat": 30.60 + index * 0.001, "lng": 104.05 + index * 0.001,
                "coordinates_trusted": True,
            }
            for index in range(1, 46)
        ] + [
            {
                "name": f"成都餐厅 {index}", "category": "餐厅", "poi_id": f"food-{index}",
                "city": "成都", "lat": 30.55 + index * 0.001, "lng": 104.00 + index * 0.001,
                "coordinates_trusted": True,
            }
            for index in range(1, 11)
        ]

        events, _ = asyncio.run(_build_travel_structured_result_with_routes({
            "query": "从上海出发，规划成都3天行程",
            "destination_city": "成都",
            "map_locations": locations,
            "map_tool": "map_search_places",
            "trip_intent": {
                "origin": "上海", "destination": "成都", "date_range": "2026-10-05 至 2026-10-07",
                "days": 3, "people_count": 2, "budget_per_person": 1000,
            },
        }, SimpleNamespace(baidu_request_dispatcher=object())))

        document = next(event["document"] for event in events if event["type"] == "trip_plan")

        self.assertTrue(dropped_poi_ids)
        self.assertLessEqual(len(document["candidate_places"]), 15)
        self.assertTrue(dropped_poi_ids.intersection({item["poi_id"] for item in document["candidate_places"]}))

    def test_verified_lodging_is_kept_as_map_anchor_and_clears_missing_hotel_warning(self):
        plan = _travel_bundle_to_trip_plan({
            "query": "从上海出发，规划南京3天行程",
            "destination_city": "南京",
            "map_locations": [{
                "name": "南京博物院", "category": "景点", "poi_id": "museum",
                "city": "南京", "lat": 32.043, "lng": 118.83,
            }],
            "lodging_candidates": [{
                "name": "南京中心酒店", "category": "酒店", "poi_id": "hotel-1",
                "city": "南京", "lat": 32.05, "lng": 118.79,
                "coordinates_trusted": True,
            }],
            "trip_intent": {
                "origin": "上海", "destination": "南京", "date_range": "日期暂未确定",
                "days": 3, "people_count": 1, "budget_total": 5000,
            },
        })

        validation = validate_trip_plan(plan)

        self.assertIn("南京中心酒店", [item["name"] for item in plan.map_locations])
        self.assertNotIn("MISSING_HOTEL_AREA", [issue.code for issue in validation.issues])

    def test_origin_city_never_contributes_destination_catalog_places(self):
        seeds = _travel_seed_places(
            "南京",
            "从上海出发，规划南京3天行程，朋友2人，人均2000元",
        )

        names = [name for name, _ in seeds]
        self.assertIn("南京博物院", names)
        self.assertNotIn("上海博物馆", names)
        self.assertNotIn("外滩", names)

    def test_international_catalog_seeds_cover_each_requested_day(self):
        seeds = _travel_seed_places(
            "东京",
            "2026年10月12日至16日从上海出发，规划东京五日游",
        )

        self.assertEqual({1, 2, 3, 4, 5}, {day for _name, day in seeds})

    def test_catalog_only_plan_is_rejected_instead_of_completed(self):
        events, _ = _build_travel_structured_result({
            "query": "从上海出发，规划南京3天行程，朋友2人，人均2000元",
            "destination_city": "南京",
            "map_locations": [
                {"name": "梅龙镇酒家(南京西路总店)", "category": "餐厅", "address": "上海市静安区南京西路"},
            ],
            "trip_intent": {
                "origin": "上海",
                "destination": "南京",
                "date_range": "日期暂未确定",
                "days": 3,
                "people_count": 2,
                "people_type": "朋友",
                "budget_per_person": 2000,
            },
            "date_sensitive": False,
        })

        legacy_document = next(event["document"] for event in events if event["type"] == "trip_plan")
        with self.assertRaises(FormalPlanValidationError) as captured:
            adapt_v2_to_v3(legacy_document)

        self.assertIn("EMPTY_ITINERARY", {issue.code for issue in captured.exception.issues})
        self.assertIn("MISSING_VERIFIED_POI", {issue.code for issue in captured.exception.issues})

    def test_tokyo_catalog_only_plan_is_rejected_instead_of_completed(self):
        events, _ = _build_travel_structured_result({
            "query": "上海到东京3日游",
            "destination_city": "东京",
            "map_locations": [
                {
                    "name": "开封府",
                    "category": "景点",
                    "address": "河南省开封市",
                    "lat": 34.8,
                    "lng": 114.3,
                    "coordinates_trusted": True,
                },
                {
                    "name": "天津之眼",
                    "category": "景点",
                    "address": "天津市",
                    "lat": 39.1,
                    "lng": 117.2,
                    "coordinates_trusted": True,
                },
            ],
            "trip_intent": {
                "origin": "上海",
                "destination": "东京",
                "date_range": "2026-09-01 至 2026-09-03",
                "days": 3,
                "people_count": 1,
            },
            "date_sensitive": False,
        })

        legacy_document = next(event["document"] for event in events if event["type"] == "trip_plan")
        with self.assertRaises(FormalPlanValidationError) as captured:
            adapt_v2_to_v3(legacy_document)

        self.assertIn("EMPTY_ITINERARY", {issue.code for issue in captured.exception.issues})
        self.assertIn("MISSING_VERIFIED_POI", {issue.code for issue in captured.exception.issues})

    def test_final_lodging_guard_uses_structured_destination_not_bundle_hint(self):
        events, _ = _build_travel_structured_result({
            "query": "从上海出发，规划南京3天行程，朋友2人，人均2000元",
            "destination_city": "上海",
            "map_locations": [],
            "lodging_candidates": [
                {"name": "上海外滩酒店", "category": "酒店", "address": "上海市黄浦区中山东一路"},
                {"name": "南京中心酒店", "category": "酒店", "address": "南京市玄武区中山东路"},
            ],
            "trip_intent": {
                "origin": "上海",
                "destination": "南京",
                "date_range": "日期暂未确定",
                "days": 3,
                "people_count": 2,
                "people_type": "朋友",
                "budget_per_person": 2000,
            },
            "date_sensitive": False,
        })

        document = next(event["document"] for event in events if event["type"] == "trip_plan")
        names = [item["name"] for item in document["hotel_recommendations"]["recommendations"]]
        self.assertEqual(names, ["南京中心酒店"])

    def test_destination_scoped_web_and_community_research_reject_foreign_city_rows(self):
        class FakeToolManager:
            def get_tool(self, name):
                if name in {"search_web_page", "map_search_places"}:
                    return SimpleNamespace(parameters={"query": {}, "region": {}}, required=["query"])
                return None

            def run_tool(self, name, messages, session_id, **kwargs):
                if name == "search_web_page":
                    return [
                        {"title": "上海酒店住宿攻略", "summary": "上海市中心酒店", "url": "https://example.com/shanghai"},
                        {"title": "南京新街口住宿参考", "summary": "南京市玄武区入住体验", "url": "https://example.com/nanjing"},
                    ]
                return json.dumps({"content": json.dumps({"results": []})})

        query = "从上海出发，规划南京3天行程，朋友2人，人均2000元"
        bundle = maybe_prepare_travel_experience_bundle(
            user_query=query,
            tool_manager=FakeToolManager(),
            message_history=[{"role": "user", "content": query, "message_id": "u-destination-scope", "type": "normal"}],
            session_id="s-destination-scope",
            xhs_bundle={
                "rows": [
                    {"title": "上海酒店入住体验", "summary": "上海市静安区", "url": "https://example.com/xhs-shanghai"},
                    {"title": "南京住宿区域体验", "summary": "南京市新街口", "url": "https://example.com/xhs-nanjing"},
                ],
                "append_markdown": "上海住宿资料不应保留",
                "context_message": "上海住宿资料不应进入目的地方案",
            },
            allow_date_sensitive=False,
        )

        self.assertEqual([row["title"] for row in bundle["web_rows"]], ["南京新街口住宿参考"])
        self.assertEqual([row["title"] for row in bundle["xhs_rows"]], ["南京住宿区域体验"])
        self.assertEqual([row["title"] for row in bundle["lodging_reference_rows"]], ["南京住宿区域体验", "南京新街口住宿参考"])

    def test_nanjing_catalog_completes_balanced_and_intensive_daily_agenda(self):
        balanced = _ensure_daily_activity_coverage(
            [],
            destination_city="南京",
            query_text="帮我规划南京三日游",
            days=3,
            pace="balanced",
        )
        intensive = _ensure_daily_activity_coverage(
            [],
            destination_city="南京",
            query_text="帮我规划南京三日游",
            days=3,
            pace="intensive",
        )

        for day in range(1, 4):
            balanced_day = [row for row in balanced if row["day"] == day]
            intensive_day = [row for row in intensive if row["day"] == day]
            self.assertEqual(len(balanced_day), 4)
            self.assertEqual(len(intensive_day), 5)
            self.assertEqual(sum(row["category"] == "餐厅" for row in balanced_day), 0)
            self.assertEqual(sum(row["category"] == "餐厅" for row in intensive_day), 0)

    def test_location_name_corrects_generic_search_categories_for_lodging_and_food(self):
        self.assertEqual(
            _normalized_travel_location_category({"name": "杭州维景国际大酒店", "category": "景点"}),
            "酒店",
        )
        self.assertEqual(
            _normalized_travel_location_category({"name": "杭州酒家(延安路店)", "category": "景点"}),
            "餐厅",
        )
        self.assertEqual(
            _normalized_travel_location_category({"name": "点都德(湖滨银泰店)", "category": "粤菜"}),
            "餐厅",
        )

    def test_map_hotel_is_verified_before_becoming_planning_lodging(self):
        class FakeToolManager:
            def get_tool(self, name):
                if name == "search_web_page":
                    return SimpleNamespace(parameters={"query": {}}, required=["query"])
                if name == "map_search_places":
                    return SimpleNamespace(parameters={"query": {}, "region": {}}, required=["query"])
                return None

            def run_tool(self, name, messages, session_id, **kwargs):
                if name == "search_web_page":
                    return []
                if name == "map_search_places" and (
                    "住宿" in str(kwargs.get("query", ""))
                    or "南京中心酒店" in str(kwargs.get("query", ""))
                ):
                    self.assert_region(kwargs)
                    exact_lookup = "南京中心酒店" in str(kwargs.get("query", ""))
                    return json.dumps({
                        "content": json.dumps({
                            "result_type": "poi_type",
                            "results": [{
                            "uid": "nanjing-hotel-candidate",
                            "name": "南京中心酒店",
                            "address": "南京市中心区域",
                            "category": "酒店",
                            **({"location": {"lat": 32.0602, "lng": 118.7969}} if exact_lookup else {}),
                            }],
                        }, ensure_ascii=False),
                    }, ensure_ascii=False)
                return json.dumps({"content": json.dumps({"results": []})})

            @staticmethod
            def assert_region(kwargs):
                if kwargs.get("region") != "南京":
                    raise AssertionError("地图地点检索必须携带目的地区域")

        query = "帮我规划南京三日游"
        bundle = maybe_prepare_travel_experience_bundle(
            user_query=query,
            tool_manager=FakeToolManager(),
            message_history=[{"role": "user", "content": query, "message_id": "u-nanjing", "type": "normal"}],
            session_id="s-nanjing-lodging",
            allow_date_sensitive=False,
        )

        self.assertEqual(bundle["map_locations"], [])
        self.assertEqual(bundle["lodging_candidates"][0]["name"], "南京中心酒店")
        events, _ = _build_travel_structured_result({
            **bundle,
            "trip_intent": {
                "origin": "南京",
                "destination": "南京",
                "date_range": "日期暂未确定",
                "days": 3,
                "people_count": 2,
                "people_type": "朋友",
                "budget_per_person": 2000,
            },
        })
        document = next(event["document"] for event in events if event["type"] == "trip_plan")
        lodging = document["hotel_recommendations"]["recommendations"][0]
        self.assertEqual(lodging["name"], "南京中心酒店")
        self.assertEqual(lodging["area"], "南京市中心区域")
        self.assertEqual(lodging["place"]["poi_id"], "nanjing-hotel-candidate")
        self.assertEqual(lodging["place"]["address"], "南京市中心区域")
        self.assertEqual(lodging["place"]["lat"], 32.0602)
        self.assertIsNone(lodging["nightly_price"])
        self.assertIsNone(lodging["booking_url"])

    def test_select_travel_final_answer_appends_reference_and_hidden_map_json(self):
        bundle = {
            "xhs_table": "",
            "web_rows": [
                {
                    "title": "杭州攻略",
                    "snippet": "西湖与灵隐寺顺路。",
                    "url": "https://example.com/hz",
                    "source": "web",
                }
            ],
            "map_locations": [
                {
                    "id": "travel_place_1",
                    "name": "西湖风景名胜区",
                    "lat": 30.242,
                    "lng": 120.141,
                    "description": "杭州市西湖区",
                    "category": "景点",
                    "order": 1,
                }
            ],
            "fallback_answer": "# 杭州行程建议\n\n## 结论\n可以按西湖周边安排。",
        }

        with TemporaryDirectory() as tmpdir:
            with patch("services.chat_service.get_output_root_path", return_value=Path(tmpdir)):
                merged = _select_travel_final_answer(
                    "# 杭州行程建议\n\n## 结论\n可以按西湖周边安排。",
                    bundle,
                    session_id="s-travel-doc",
                )
            document_exists = (Path(tmpdir) / "s-travel-doc" / "旅行推荐.md").exists()

        self.assertIn("网络搜索参考表", merged)
        self.assertNotIn("地图地点表", merged)
        self.assertIn('"map_locations"', merged)
        self.assertIn("西湖风景名胜区", merged)
        self.assertIn("## 地理位置信息", merged)
        self.assertIn("以下是推荐地点的地理坐标信息，方便您在地图上查看和规划路线：", merged)
        self.assertIn("## 生成的文档", merged)
        self.assertIn("/api/download/s-travel-doc/%E6%97%85%E8%A1%8C%E6%8E%A8%E8%8D%90.md", merged)
        self.assertTrue(document_exists)

    def test_select_travel_final_answer_replaces_reference_only_model_answer(self):
        bundle = {
            "query": "帮我规划一次北京3天2夜的文化之旅",
            "kinds": ["行程规划"],
            "xhs_table": "",
            "web_rows": [
                {
                    "title": "北京慕田峪长城+故宫3日文化之旅",
                    "snippet": "北京3天2晚文化美食之旅。",
                    "url": "https://example.com/beijing",
                    "source": "web",
                }
            ],
            "map_locations": [
                {
                    "id": "travel_place_1",
                    "name": "故宫博物院",
                    "lat": 39.9163,
                    "lng": 116.3972,
                    "description": "北京市东城区景山前街4号",
                    "category": "景点",
                    "order": 1,
                }
            ],
        }
        bundle["fallback_answer"] = _build_travel_fallback_answer(bundle)
        reference_only = (
            "1. 北京慕田峪长城+故宫3日文化之旅\n"
            "- 摘要：北京3天2晚文化美食之旅。\n"
            "- 链接：查看\n\n"
            "## 网络搜索参考表\n| 标题 | 摘要 | 链接 | 来源 |\n| --- | --- | --- | --- |"
        )

        with TemporaryDirectory() as tmpdir:
            with patch("services.chat_service.get_output_root_path", return_value=Path(tmpdir)):
                merged = _select_travel_final_answer(reference_only, bundle, session_id="s-beijing")

        self.assertIn("## 行程框架", merged)
        self.assertIn("具体地点必须在地图或可靠来源返回后再填入", merged)
        self.assertIn("## 网络搜索参考表", merged)
        self.assertIn("## 建议与预约提醒", merged)
        self.assertIn("## 地理位置信息", merged)
        self.assertLess(merged.index("## 行程框架"), merged.index("## 网络搜索参考表"))
        self.assertLess(merged.index("## 网络搜索参考表"), merged.index("## 建议与预约提醒"))
        self.assertLess(merged.index("## 建议与预约提醒"), merged.index("## 地理位置信息"))

    def test_anyang_travel_bundle_defaults_date_origin_weather_and_geocodes_places(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                specs = {
                    "search_web_page": SimpleNamespace(parameters={"query": {}, "count": {}}, required=["query"]),
                    "map_search_places": SimpleNamespace(parameters={"query": {}, "region": {}}, required=["query"]),
                    "map_geocode": SimpleNamespace(parameters={"address": {}, "city": {}}, required=["address"]),
                    "map_ip_location": SimpleNamespace(parameters={}, required=[]),
                    "map_weather": SimpleNamespace(parameters={"city": {}, "date": {}}, required=["city"]),
                }
                return specs.get(name)

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs))
                if name == "map_ip_location":
                    return {"content": {"address_detail": {"city": "郑州市"}}}
                if name == "map_weather":
                    return {
                        "forecasts": [
                            {
                                "date": "2026-06-14",
                                "text_day": "多云",
                                "text_night": "阴",
                                "low": "22",
                                "high": "33",
                                "wd_day": "南风",
                            }
                        ]
                    }
                if name == "search_web_page":
                    return [
                        {
                            "title": "安阳三日文化游攻略",
                            "snippet": "殷墟、中国文字博物馆和羑里城适合串联。",
                            "url": "https://example.com/anyang",
                            "source": "web",
                        }
                    ]
                if name == "map_search_places":
                    return {"places": []}
                if name == "map_geocode":
                    address = kwargs.get("address", "")
                    return {
                        "places": [
                            {
                                "uid": f"anyang-{address}",
                                "name": address,
                                "location": {"lat": 36.1 + len(self.calls) / 1000, "lng": 114.3 + len(self.calls) / 1000},
                                "address": address,
                            }
                        ]
                    }
                return {}

        tool_manager = FakeToolManager()
        with patch("services.chat_service._extract_travel_date", return_value="2026-06-14"):
            bundle = maybe_prepare_travel_experience_bundle(
                user_query="帮我规划一次安阳3天2夜的文化之旅",
                tool_manager=tool_manager,
                message_history=[
                    {
                        "role": "user",
                        "content": "帮我规划一次安阳3天2夜的文化之旅",
                        "message_id": "u-anyang",
                        "type": "normal",
                    }
                ],
                session_id="s-anyang",
            )

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle["travel_date"], "2026-06-14")
        self.assertEqual(bundle["origin_city"], "郑州")
        self.assertEqual(bundle["destination_city"], "安阳")
        self.assertIn("多云", bundle["weather_summary"])
        self.assertGreaterEqual(len(bundle["map_locations"]), 3)
        self.assertTrue(any(name == "map_ip_location" for name, _ in tool_manager.calls))
        self.assertTrue(any(name == "map_weather" for name, _ in tool_manager.calls))
        self.assertFalse(any(name == "map_weather" and "location" in kwargs for name, kwargs in tool_manager.calls))
        self.assertTrue(any(name == "map_geocode" for name, _ in tool_manager.calls))

        with TemporaryDirectory() as tmpdir:
            with patch("services.chat_service.get_output_root_path", return_value=Path(tmpdir)):
                merged = _select_travel_final_answer("", bundle, session_id="s-anyang")

        self.assertNotIn("## 出行基础信息", merged)
        self.assertNotIn("## 交通与天气建议", merged)
        self.assertIn("## 行程框架", merged)
        self.assertIn("具体地点必须在地图或可靠来源返回后再填入", merged)
        self.assertIn("安阳三日文化游攻略", merged)
        self.assertIn('"map_locations"', merged)
        self.assertIn("安阳3天2夜的文化之旅.md", merged)

    def test_xhs_rows_parse_nested_mcp_payload(self):
        payload = {
            "content": [
                {
                    "type": "text",
                    "text": "{\"data\":{\"notes\":[{\"display_title\":\"北京文化路线避坑\",\"desc\":\"故宫和国博都建议提前预约。\",\"note_id\":\"abc123\",\"author\":{\"nickname\":\"旅行者\"},\"liked_count\":\"88\"}]}}",
                }
            ]
        }

        rows = _normalize_xhs_rows(payload, max_rows=3)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "北京文化路线避坑")
        self.assertEqual(rows[0]["summary"], "故宫和国博都建议提前预约。")
        self.assertEqual(rows[0]["author"], "旅行者")
        self.assertEqual(rows[0]["url"], "https://www.xiaohongshu.com/explore/abc123")

    def test_travel_document_filename_summarizes_prompt(self):
        self.assertEqual(
            _travel_document_filename("帮我规划一次安阳3天2夜的文化之旅"),
            "安阳3天2夜的文化之旅.md",
        )

    def test_markdown_normalizer_demotes_numbered_tip_headings(self):
        normalized = _normalize_markdown_for_display("# 实用贴士\n# 1.预约提醒\n请提前预约。")

        self.assertIn("### 1. 预约提醒", normalized)
        self.assertNotIn("# 1.预约提醒", normalized)

    def test_execute_chat_once_merges_model_answer_with_realtime_table(self):
        class FakeRealtimeController(FakeController):
            def run(self, messages, tool_manager, session_id, deep_thinking, summary, deep_research):
                self.called_with = {
                    "messages": messages,
                    "tool_manager": tool_manager,
                    "session_id": session_id,
                    "deep_thinking": deep_thinking,
                    "summary": summary,
                    "deep_research": deep_research,
                }
                return {
                    "all_messages": [
                        {"role": "assistant", "type": "final_answer", "content": "虚构车次 G999"}
                    ],
                    "new_messages": [
                        {"role": "assistant", "type": "final_answer", "content": "虚构车次 G999"}
                    ],
                    "final_output": {"role": "assistant", "type": "final_answer", "content": "虚构车次 G999"},
                }

        controller = FakeRealtimeController()
        tool_manager = object()
        request_messages = [
            SimpleNamespace(role="user", content="明天从安阳到杭州最便宜交通", message_id="u-2", type="normal"),
        ]

        bundle = {
            "enforce_realtime_only": True,
            "realtime_only_answer": "仅基于实时查询结果输出，禁止编造班次与票价。\n\n火车和高铁票信息表\n| 类型 | 班次 |\n| --- | --- |\n| 直达 | G1 |",
            "append_markdown": "火车和高铁票信息表\n| 类型 | 班次 |\n| --- | --- |\n| 直达 | G1 |",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=bundle):
            payload = execute_chat_once(
                request_messages=request_messages,
                controller=controller,
                tool_manager=tool_manager,
                session_id="s-2",
                use_deepthink=True,
                use_multi_agent=False,
            )

        self.assertEqual(payload["status"], "success")
        result = payload["result"]
        final_content = result["final_output"]["content"]
        self.assertNotIn("虚构车次 G999", final_content)
        self.assertIn("仅基于实时查询结果输出", final_content)
        self.assertIn("火车和高铁票信息表", final_content)
        self.assertNotIn("###", final_content)
        self.assertNotRegex(final_content, r"(?m)^- ")
        self.assertTrue(result.get("realtime_ticket_enforced"))

    def test_execute_chat_once_sanitizes_user_visible_final_answers(self):
        class LeakyController(FakeController):
            def run(self, messages, tool_manager, session_id, deep_thinking, summary, deep_research):
                leaky = (
                    "已完成查询。\n\n"
                    "```json\n"
                    '{"tool_call_id":"call-1","tool_name":"map_geocode","arguments":{}}\n'
                    "```\n"
                    "调试文件位于 D:\\travel-agent\\cache\\result.json"
                )
                return {
                    "all_messages": [{"role": "assistant", "type": "final_answer", "content": leaky}],
                    "new_messages": [{"role": "assistant", "type": "final_answer", "content": leaky}],
                    "final_output": {"role": "assistant", "type": "final_answer", "content": leaky},
                }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
            with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=None):
                payload = execute_chat_once(
                    request_messages=[SimpleNamespace(role="user", content="解释一下查询结果", message_id="u-safe", type="normal")],
                    controller=LeakyController(),
                    tool_manager=object(),
                    session_id="s-safe",
                    use_deepthink=False,
                    use_multi_agent=False,
                    allow_web_search=False,
                )

        result = payload["result"]
        visible_values = [
            result["all_messages"][0]["content"],
            result["new_messages"][0]["content"],
            result["final_output"]["content"],
        ]
        for content in visible_values:
            self.assertIn("已完成查询", content)
            self.assertNotIn("tool_call_id", content)
            self.assertNotIn("map_geocode", content)
            self.assertNotIn(r"D:\travel-agent", content)

    def test_execute_chat_once_skips_travel_enrichment_when_ticket_bundle_exists(self):
        controller = FakeController()
        ticket_bundle = {
            "has_valid_results": True,
            "direct_rows": [{"trip_no": "G1"}],
            "interline_rows": [],
            "flight_rows": [],
            "bus_rows": [],
            "ticket_states": {
                "train": {"status": "success", "result_count": 1, "error": ""},
                "flight": {"status": "empty", "result_count": 0, "error": ""},
                "bus": {"status": "empty", "result_count": 0, "error": ""},
            },
            "context_message": "【实时票务查询结果】G1",
            "append_markdown": "火车和高铁票信息表\n| 班次 |\n| --- |\n| G1 |",
            "realtime_only_answer": "火车和高铁票信息表\n| 班次 |\n| --- |\n| G1 |",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=ticket_bundle):
            with patch("services.chat_service.maybe_prepare_travel_experience_bundle") as travel_mock:
                payload = execute_chat_once(
                    request_messages=[
                        SimpleNamespace(role="user", content="查询明天杭州到南京的票", message_id="u-ticket-skip", type="normal")
                    ],
                    controller=controller,
                    tool_manager=object(),
                    session_id="s-ticket-skip",
                    use_deepthink=True,
                    use_multi_agent=False,
                )

        travel_mock.assert_not_called()
        self.assertTrue(payload["result"].get("realtime_ticket_enforced"))
        self.assertFalse(payload["result"].get("travel_experience_enforced"))

    def test_execute_chat_once_merges_model_answer_with_xhs_table(self):
        class FakeXhsController(FakeController):
            def run(self, messages, tool_manager, session_id, deep_thinking, summary, deep_research):
                self.called_with = {
                    "messages": messages,
                    "tool_manager": tool_manager,
                    "session_id": session_id,
                    "deep_thinking": deep_thinking,
                    "summary": summary,
                    "deep_research": deep_research,
                }
                return {
                    "all_messages": [
                        {"role": "assistant", "type": "final_answer", "content": "已整理出3条建议。"}
                    ],
                    "new_messages": [
                        {"role": "assistant", "type": "final_answer", "content": "已整理出3条建议。"}
                    ],
                    "final_output": {"role": "assistant", "type": "final_answer", "content": "已整理出3条建议。"},
                }

        controller = FakeXhsController()
        tool_manager = object()
        request_messages = [
            SimpleNamespace(role="user", content="杭州三日游攻略怎么安排，帮我总结", message_id="u-xhs", type="normal"),
        ]

        xhs_bundle = {
            "context_message": "【小红书检索结果】\n用户问题: 杭州三日游攻略怎么安排，帮我总结",
            "append_markdown": "### 小红书检索资源表\n| 标题 | 作者 |\n| --- | --- |\n| 杭州路线 | 旅行博主A |",
            "fallback_answer": "已完成小红书检索并生成摘要。",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=xhs_bundle):
                with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=None):
                    payload = execute_chat_once(
                        request_messages=request_messages,
                        controller=controller,
                        tool_manager=tool_manager,
                        session_id="s-xhs",
                        use_deepthink=True,
                        use_multi_agent=False,
                    )

        self.assertEqual(payload["status"], "success")
        result = payload["result"]
        self.assertIn("已整理出3条建议", result["final_output"]["content"])
        self.assertIn("小红书检索资源表", result["final_output"]["content"])
        self.assertTrue(result.get("xhs_resource_enforced"))
        self.assertEqual(controller.called_with["messages"][-1]["type"], "system_xhs_search_context")

    def test_train_ticket_bundle_expands_city_names_to_station_pairs(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                return object() if name == "get-tickets" else None

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs.get("fromStation"), kwargs.get("toStation")))
                if kwargs.get("fromStation") == "\u5e7f\u5dde\u5357" and kwargs.get("toStation") == "\u6842\u6797\u5317":
                    return [
                        {
                            "train_code": "D2992",
                            "from_station_name": "\u5e7f\u5dde\u5357",
                            "to_station_name": "\u6842\u6797\u5317",
                            "start_time": "08:10",
                            "arrive_time": "10:55",
                            "lishi": "02:45",
                            "prices": [{"seat_name": "\u4e8c\u7b49\u5ea7", "price": 165, "num": "\u6709"}],
                        }
                    ]
                return []

        tool_manager = FakeToolManager()

        with patch(
            "services.chat_service._load_12306_station_names",
            return_value=["广州南", "广州北", "广州", "桂林北", "桂林", "桂林西"],
        ):
            bundle = maybe_prepare_train_ticket_bundle(
                user_query="\u4ece\u5e7f\u5dde\u5230\u6842\u6797\u6700\u4f73\u4ea4\u901a",
                tool_manager=tool_manager,
                message_history=[
                    {
                        "role": "user",
                        "content": "\u4ece\u5e7f\u5dde\u5230\u6842\u6797\u6700\u4f73\u4ea4\u901a",
                        "message_id": "u-city",
                        "type": "normal",
                    }
                ],
                session_id="s-city",
            )

        self.assertIsNotNone(bundle)
        self.assertIn(("get-tickets", "\u5e7f\u5dde\u5357", "\u6842\u6797\u5317"), tool_manager.calls)
        self.assertEqual(bundle["direct_rows"][0]["trip_no"], "D2992")
        self.assertEqual(bundle["direct_rows"][0]["route"], "\u5e7f\u5dde\u5357 -> \u6842\u6797\u5317")

    def test_train_ticket_bundle_calls_realtime_flight_and_bus_tools_when_available(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                return object() if name in {"query_flight_tickets", "query_bus_tickets"} else None

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs))
                if name == "query_flight_tickets":
                    if "from_city" not in kwargs:
                        raise TypeError("use snake case")
                    return {
                        "status": "success",
                        "source": "juhe_flight_query_api",
                        "flights": [
                            {
                                "flightNo": "CZ3907",
                                "airline": "南航",
                                "fromAirport": "广州白云",
                                "toAirport": "桂林两江",
                                "departureDate": kwargs["travel_date"],
                                "departureTime": "08:00",
                                "arrivalDate": kwargs["travel_date"],
                                "arrivalTime": "09:30",
                                "duration": "1小时30分钟",
                                "cabinClass": "经济舱",
                                "price": "580",
                            }
                        ],
                    }
                if name == "query_bus_tickets":
                    if "from_city" not in kwargs:
                        raise TypeError("use snake case")
                    return {
                        "status": "success",
                        "source": "jisu_bus_city2c_api",
                        "buses": [
                            {
                                "busNo": "班次A",
                                "from_station": "广州省站",
                                "to_station": "桂林汽车站",
                                "depart_date": kwargs["travel_date"],
                                "depart_time": "09:00",
                                "duration": "6小时",
                                "bus_type": "大巴",
                                "price": "180",
                                "note": "平台未返回余票",
                            }
                        ],
                    }
                return {}

        tool_manager = FakeToolManager()
        with patch(
            "services.chat_service._load_12306_station_names",
            return_value=["广州南", "广州北", "广州", "桂林北", "桂林", "桂林西"],
        ):
            bundle = maybe_prepare_train_ticket_bundle(
                user_query="从广州到桂林最佳交通",
                tool_manager=tool_manager,
                message_history=[
                    {
                        "role": "user",
                        "content": "从广州到桂林最佳交通",
                        "message_id": "u-realtime-modes",
                        "type": "normal",
                    }
                ],
                session_id="s-realtime-modes",
            )

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle["flight_source"], "query_flight_tickets")
        self.assertEqual(bundle["bus_source"], "query_bus_tickets")
        self.assertEqual(bundle["flight_rows"][0]["trip_no"], "南航CZ3907")
        self.assertEqual(bundle["bus_rows"][0]["trip_no"], "班次A")
        self.assertIn("飞机票信息表", bundle["append_markdown"])
        self.assertIn("大巴票信息表", bundle["append_markdown"])
        self.assertTrue(any(name == "query_flight_tickets" for name, _ in tool_manager.calls))
        self.assertTrue(any(name == "query_bus_tickets" for name, _ in tool_manager.calls))

    def test_train_ticket_bundle_uses_previous_route_when_latest_message_only_updates_date(self):
        class FakeToolManager:
            def __init__(self):
                self.calls = []

            def get_tool(self, name):
                return object() if name == "get-tickets" else None

            def run_tool(self, name, messages, session_id, **kwargs):
                self.calls.append((name, kwargs.get("date"), kwargs.get("fromStation"), kwargs.get("toStation")))
                if kwargs.get("fromStation") == "广州南" and kwargs.get("toStation") == "桂林北":
                    return [
                        {
                            "train_code": "D2992",
                            "from_station_name": "广州南",
                            "to_station_name": "桂林北",
                            "start_time": "08:10",
                            "arrive_time": "10:55",
                            "lishi": "02:45",
                            "prices": [{"seat_name": "二等座", "price": 165, "num": "有"}],
                        }
                    ]
                return []

        tool_manager = FakeToolManager()
        message_history = [
            {
                "role": "user",
                "content": "查询从广州到桂林的最佳交通方案",
                "message_id": "u-route",
                "type": "normal",
            },
            {
                "role": "assistant",
                "content": "可以，先确认日期。",
                "message_id": "a-route",
                "type": "final_answer",
            },
            {
                "role": "user",
                "content": "今天出发",
                "message_id": "u-date",
                "type": "normal",
            },
        ]

        with patch(
            "services.chat_service._load_12306_station_names",
            return_value=["广州南", "广州北", "广州", "桂林北", "桂林", "桂林西"],
        ):
            bundle = maybe_prepare_train_ticket_bundle(
                user_query="今天出发",
                tool_manager=tool_manager,
                message_history=message_history,
                session_id="s-followup",
            )

        self.assertIsNotNone(bundle)
        self.assertIn(("get-tickets", bundle["route"]["travel_date"], "广州南", "桂林北"), tool_manager.calls)
        self.assertEqual(bundle["direct_rows"][0]["trip_no"], "D2992")
        self.assertIn("D2992", bundle["append_markdown"])

    def test_destination_overview_summary_strips_internal_rag_envelope(self):
        summary = _build_destination_overview_summary({
            "rag_context": (
                "旅行知识库检索结果：\n"
                "使用规则：优先参考这些城市介绍、攻略和 POI 信息；不要暴露内部检索字段。\n"
                "1. [杭州 / travel_guide / 杭州旅游攻略] 杭州适合将湖山景观、历史街区与地方美食串联为舒缓的城市漫游。"
                "\n2. [杭州 / geo_location / 地理位置] 该条不应进入概述。"
            ),
        })

        self.assertEqual("杭州适合将湖山景观、历史街区与地方美食串联为舒缓的城市漫游。", summary)
        self.assertNotIn("使用规则", summary)
        self.assertNotIn("travel_guide", summary)


if __name__ == "__main__":
    unittest.main()


def test_optional_image_failure_keeps_travel_data_and_uses_reference_context_once():
    from services.chat_service import _prepare_travel_context
    history = []
    travel = {'destination_city': '杭州', 'map_locations': [{'name': '西湖'}], 'context_message': '旅行资料'}
    with patch('services.chat_service.maybe_prepare_travel_experience_bundle', return_value=travel), \
         patch('services.chat_service.maybe_prepare_destination_cover', side_effect=RuntimeError('图片不可用')):
        result = _prepare_travel_context(
            user_query='杭州旅行', tool_manager=object(), message_history=history, session_id='context-test',
            selected_skill_ids=[], xhs_bundle={'context_message': '社区参考'},
            ticket_bundle={'direct_rows': []}, allow_web_search=True,
        )
    assert result is travel and result['map_locations'][0]['name'] == '西湖'
    assert [message['content'] for message in history] == ['社区参考']
    assert history[0]['type'] == 'system_xhs_search_context'


def test_ticket_lookup_keeps_evening_trains_beyond_provider_first_twenty():
    from services.ticket_search_service import transport_section_from_bundle
    provider_rows = [{'train_code': f'G{index}', 'start_time': f'{hour:02d}:{minute:02d}',
                      'arrive_time': f'{hour + 1:02d}:{minute:02d}', 'duration': '01:00', 'price': 40}
                     for index, (hour, minute) in enumerate([(6 + i // 4, i % 4 * 15) for i in range(20)] + [(20, 0)])]
    def run_tool(name, messages, session_id, **kwargs):
        limit = kwargs.get('limitedNum', 0)
        return {'tickets': provider_rows[:limit] if limit else provider_rows}
    manager = SimpleNamespace(get_tool=lambda name: object() if name == 'get-tickets' else None, run_tool=run_tool)
    with patch('services.chat_service._station_query_pairs', return_value=[('杭州', '上海')]):
        bundle = maybe_prepare_train_ticket_bundle('杭州到上海 2026-10-10 火车票', manager, [], 'evening-test')
    section = transport_section_from_bundle(bundle, 'return', 'domestic', '2026-10-10')
    selected = next(item for item in section['options'] if item['option_id'] == section['recommended_option_id'])
    assert selected['departure_time'] == '2026-10-10 20:00'
