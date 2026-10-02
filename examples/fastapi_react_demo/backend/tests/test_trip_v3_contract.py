import copy
import json
import unittest
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from backend.schemas.trip_v3_models import (
    AgentStagePayload,
    ImageAssetV3,
    PlanningEventEnvelope,
    ProviderQueueWaitingPayload,
    RouteLegV3,
    RunStartedPayload,
    TransportOptionV3,
    TravelerCountV3,
    TravelPlanDocumentV3,
    TripActivityV3,
    USER_VISIBLE_STATUS_LABELS,
)
from backend.services.travel_document_service import (
    FormalPlanValidationError,
    adapt_v2_to_v3,
    calculate_document_checksum,
    export_travel_plan_v3_plain_markdown,
    export_travel_plan_v3_markdown,
    planning_event_json_schema,
    safe_delivery_filename,
    travel_plan_v3_json_schema,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"
SCHEMA_SNAPSHOT = Path(__file__).parents[1] / "schemas" / "travel_plan_v3.schema.json"
EVENT_SCHEMA_SNAPSHOT = Path(__file__).parents[1] / "schemas" / "planning_event.schema.json"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def build_legacy_v2_document() -> dict:
    return {
        "schema_version": "2.0",
        "plan_id": "plan_legacy_fixture",
        "version": 2,
        "title": "V2 迁移契约样本",
        "generated_at": "2026-07-17T10:00:00+08:00",
        "locale": "zh-CN",
        "intent": {
            "origin": "上海",
            "destination": "杭州",
            "date_range": "2026-08-01 至 2026-08-01",
            "days": 1,
            "people_count": 2,
            "budget_total": 2000,
            "pace": "balanced",
        },
        "destination_overview": {
            "status": "ready",
            "name_zh": "杭州",
            "country_code": "CN",
            "timezone": "Asia/Shanghai",
            "currency": "CNY",
            "source_reference_ids": ["杭州地图契约数据"],
        },
        "outbound_transport": {
            "direction": "outbound",
            "scope": "domestic",
            "status": "unavailable",
            "status_reason": "实时交通待重新查询",
            "options": [],
        },
        "hotel_recommendations": {
            "status": "unavailable",
            "status_reason": "住宿点待用户确认",
            "recommendations": [],
        },
        "itinerary": {
            "status": "ready",
            "days": [
                {
                    "day": 1,
                    "date": "2026-08-01",
                    "theme": "契约测试",
                    "activities": [
                        {
                            "activity_id": "legacy_act_1",
                            "day": 1,
                            "start_time": "10:00",
                            "end_time": "12:00",
                            "title": "V2 契约地点",
                            "activity_type": "attraction",
                            "place": {
                                "poi_id": "poi_legacy_1",
                                "name": "V2 契约地点",
                                "category": "attraction",
                                "city": "杭州",
                                "lat": 30.25,
                                "lng": 120.16,
                                "data_type": "reference_data",
                            },
                            "data_type": "reference_data",
                        }
                    ],
                }
            ],
        },
        "return_transport": {
            "direction": "return",
            "scope": "domestic",
            "status": "unavailable",
            "status_reason": "实时交通待重新查询",
            "options": [],
        },
        "friendly_reminders": {
            "status": "ready",
            "items": [{"category": "other", "content": "契约测试提醒"}],
        },
        "map_guidance": {
            "status": "unavailable",
            "status_reason": "路线待重新计算",
            "location_ids": ["poi_legacy_1"],
            "day_routes": [],
        },
        "delivery": {"status": "ready", "markdown_filename": "V2迁移样本.md"},
        "budget": {"estimated_total": 1200, "unknown_count": 1},
        "sources": [
            {
                "title": "杭州地图契约数据",
                "source": "契约测试地图 Provider",
                "data_type": "reference_data",
                "updated_at": "2026-07-17T09:50:00+08:00",
            }
        ],
        "validation": {"issues": []},
        "checklist": [],
        "notes": [],
    }


class TravelPlanV3ContractTests(unittest.TestCase):
    def test_v2_adapter_rejects_an_empty_formal_plan_instead_of_marking_it_valid(self):
        payload = build_legacy_v2_document()
        payload["itinerary"]["days"][0]["activities"] = []
        payload["map_guidance"]["location_ids"] = []
        payload["budget"] = {"estimated_total": 0, "known_total": 0}

        with self.assertRaises(FormalPlanValidationError) as captured:
            adapt_v2_to_v3(payload)

        self.assertEqual(
            {"EMPTY_ITINERARY", "MISSING_VERIFIED_POI", "EMPTY_BUDGET_ESTIMATE"},
            {issue.code for issue in captured.exception.issues},
        )

    def test_v2_adapter_keeps_legacy_errors_blocking(self):
        payload = build_legacy_v2_document()
        payload["validation"]["issues"] = [{
            "code": "CITY_MISMATCH",
            "message": "发现不属于目的地的地点。",
            "severity": "error",
        }]

        with self.assertRaises(FormalPlanValidationError) as captured:
            adapt_v2_to_v3(payload)

        self.assertIn("CITY_MISMATCH", {issue.code for issue in captured.exception.issues})

    def test_v2_adapter_ignores_blank_friendly_reminders(self):
        payload = build_legacy_v2_document()
        payload["friendly_reminders"]["items"].extend(
            [
                {"category": "other", "content": ""},
                {"category": "weather", "content": "   "},
            ]
        )

        document = adapt_v2_to_v3(payload)

        self.assertEqual(["契约测试提醒"], [item.text for item in document.action_items])

    def test_v2_adapter_preserves_baidu_coordinate_system_for_map_places(self):
        payload = build_legacy_v2_document()
        legacy_place = payload["itinerary"]["days"][0]["activities"][0]["place"]
        legacy_place.update({
            "poi_id": "baidu_geo_test_point",
            "source": "百度地图地点检索",
            "coordinate_system": "BD09LL",
        })
        payload["map_guidance"]["location_ids"] = ["baidu_geo_test_point"]

        document = adapt_v2_to_v3(payload)
        coordinates = document.itinerary.days[0].activities[0].place.coordinates

        self.assertIsNotNone(coordinates)
        self.assertEqual("BD09LL", coordinates.coordinate_system)

    def test_domestic_three_day_fixture_is_valid_and_keeps_candidates_off_map(self):
        document = TravelPlanDocumentV3.model_validate(
            load_fixture("trip_v3_domestic_3d.json")
        )

        self.assertEqual(3, document.intent.days)
        self.assertEqual([1, 2, 3], [day.day for day in document.itinerary.days])
        self.assertFalse(document.map_guidance.candidates_visible_by_default)
        self.assertNotIn(
            document.candidate_pool[0].activity_id,
            document.map_guidance.formal_location_ids,
        )
        self.assertEqual("degraded", document.status)

    def test_international_five_day_fixture_preserves_local_and_beijing_times(self):
        document = TravelPlanDocumentV3.model_validate(
            load_fixture("trip_v3_international_5d.json")
        )
        outbound = document.outbound_transport.options[0]

        self.assertEqual(5, document.intent.days)
        self.assertEqual("Asia/Tokyo", document.destination_overview.timezone)
        self.assertEqual("Asia/Tokyo", outbound.arrival_time.timezone)
        self.assertEqual(14, outbound.arrival_time.local_iso.hour)
        self.assertEqual(13, outbound.arrival_time.beijing_iso.hour)
        self.assertEqual("JPY", document.budget.total_budget.currency)
        self.assertIsNotNone(document.budget.total_budget.cny_reference_amount)

    def test_v3_markdown_labels_international_times_and_overnight_arrival(self):
        payload = load_fixture("trip_v3_international_5d.json")
        arrival = payload["outbound_transport"]["options"][0]["arrival_time"]
        arrival.update({
            "utc": "2026-10-01T16:00:00Z",
            "local_iso": "2026-10-02T01:00:00+09:00",
            "beijing_iso": "2026-10-02T00:00:00+08:00",
            "day_offset": 1,
        })
        document = TravelPlanDocumentV3.model_validate(payload)

        markdown = export_travel_plan_v3_markdown(document)

        self.assertIn("出发：当地时间 2026-10-01 09:00（Asia/Shanghai） / 北京时间 2026-10-01 09:00", markdown)
        self.assertIn("到达：当地时间 2026-10-02 01:00（Asia/Tokyo） / 北京时间 2026-10-02 00:00（次日抵达）", markdown)
        self.assertIn("JPY 500000.00（约 CNY 25000.00，汇率参考日期 2026-07-17）", markdown)

    def test_plain_markdown_exports_formal_content_without_internal_or_online_only_data(self):
        payload = load_fixture("trip_v3_domestic_3d.json")
        payload["candidate_pool"][0]["title"] = "候选池不得导出"
        payload["sources"][0].update({
            "title": "来源不得导出",
            "url": "https://example.com/internal-source",
        })
        payload["notes"] = [
            {
                "note_id": "note_trip",
                "scope": "trip",
                "content": "整体备注内容",
                "updated_at": "2026-07-29T09:00:00+08:00",
            },
            {
                "note_id": "note_day",
                "scope": "day",
                "target_id": "1",
                "content": "第 1 天备注内容",
                "updated_at": "2026-07-29T09:00:00+08:00",
            },
            {
                "note_id": "note_activity",
                "scope": "activity",
                "target_id": "act_hz_1",
                "content": "活动备注内容",
                "updated_at": "2026-07-29T09:00:00+08:00",
            },
        ]
        payload["itinerary"]["days"][0]["note_id"] = "note_day"
        payload["itinerary"]["days"][0]["activities"][0]["note_id"] = "note_activity"
        payload["action_items"] = [
            {
                "action_id": "checklist_export",
                "kind": "checklist",
                "scope": "trip",
                "text": "准备身份证",
                "status": "completed",
            },
            {
                "action_id": "reminder_export",
                "kind": "reservation_reminder",
                "scope": "activity",
                "target_id": "act_hz_1",
                "text": "提前预约景点",
                "status": "overdue",
            },
        ]
        document = TravelPlanDocumentV3.model_validate(payload)

        markdown = export_travel_plan_v3_plain_markdown(document)

        for text in ("整体备注内容", "第 1 天备注内容", "活动备注内容", "准备身份证", "提前预约景点"):
            self.assertIn(text, markdown)
        self.assertIn("## 去返程交通", markdown)
        self.assertIn("## 住宿建议", markdown)
        self.assertIn("## 预算", markdown)
        self.assertNotIn("候选池不得导出", markdown)
        self.assertNotIn("来源不得导出", markdown)
        self.assertNotIn("https://example.com/internal-source", markdown)
        self.assertNotIn("completed", markdown)
        self.assertNotIn("overdue", markdown)
        self.assertNotIn("## 参考来源", markdown)
        self.assertNotIn("## 下载", markdown)
        self.assertNotIn("![", markdown)

    def test_delivery_filename_is_windows_safe_and_bounded_by_utf8_bytes(self):
        filename = safe_delivery_filename(
            f"../{'东京大阪旅行方案' * 30}.pdf",
            fallback_title="旅行规划",
            revision=3,
            suffix=".pdf",
        )
        reserved = safe_delivery_filename(
            "CON.pdf",
            fallback_title="旅行规划",
            revision=3,
            suffix=".pdf",
        )

        self.assertLessEqual(len(filename.encode("utf-8")), 120)
        self.assertTrue(filename.endswith(".pdf"))
        self.assertNotIn("/", filename)
        self.assertNotEqual("CON.pdf", reserved)

    def test_traveler_contract_allows_child_only_trip_but_rejects_zero_travelers(self):
        traveler_counts = TravelerCountV3(adults=0, children=1, seniors=0)

        self.assertEqual(1, traveler_counts.total)
        with self.assertRaises(ValidationError):
            TravelerCountV3(adults=0, children=0, seniors=0)

    def test_v2_adapter_preserves_traveler_breakdown_and_currency_budget_details(self):
        payload = build_legacy_v2_document()
        payload["intent"].update({"adult_count": 1, "child_count": 1, "senior_count": 1, "people_count": 3})
        payload["itinerary"]["days"][0]["activities"][0].update({
            "estimated_cost": 6000, "estimated_cost_currency": "JPY",
            "estimated_cost_cny_reference_amount": 300, "estimated_cost_exchange_rate_as_of": "2026-07-28",
            "estimated_cost_unit": "per_traveler", "official_traveler_prices": {"child": 3000},
        })
        payload["budget"] = {
            "currency": "CNY",
            "estimated_total": 750,
            "currency_breakdown": {
                "categories": [
                    {
                        "category": "activities",
                        "amount": {
                            "amount": 15000,
                            "currency": "JPY",
                            "cny_reference_amount": 750,
                            "exchange_rate_as_of": "2026-07-28",
                            "source_status": "official_reference",
                        },
                    },
                ],
                "traveler_costs": [
                    {
                        "traveler_type": "child",
                        "count": 1,
                        "estimated_total": {"amount": 250, "currency": "CNY"},
                        "pricing_status": "official_discount_verified",
                    },
                ],
            },
        }

        document = adapt_v2_to_v3(payload)

        self.assertEqual((1, 1, 1), (
            document.intent.travelers.adults,
            document.intent.travelers.children,
            document.intent.travelers.seniors,
        ))
        self.assertNotIn("LEGACY_TRAVELER_BREAKDOWN_MISSING", {issue.code for issue in document.validation.issues})
        self.assertEqual("JPY", document.budget.categories[0].amount.currency)
        self.assertEqual(750, sum(item.amount.cny_reference_amount for item in document.budget.categories))
        child = next(item for item in document.budget.traveler_costs if item.traveler_type == "child")
        self.assertEqual("official_discount_verified", child.pricing_status)
        self.assertEqual(150, child.estimated_total.amount)

    def test_specific_transport_schedule_requires_realtime_verification(self):
        payload = load_fixture("trip_v3_domestic_3d.json")["outbound_transport"]["options"][0]
        payload["source_status"] = "official_reference"

        with self.assertRaises(ValidationError):
            TransportOptionV3.model_validate(payload)

    def test_candidate_cannot_keep_schedule_or_fixed_time(self):
        with self.assertRaises(ValidationError):
            TripActivityV3.model_validate(
                {
                    "activity_id": "candidate_invalid",
                    "kind": "free_time",
                    "title": "无效候选",
                    "start_at": "10:00",
                    "fixed_time": True,
                }
            )

    def test_v2_adapter_keeps_up_to_fifteen_verified_candidate_places_with_slots(self):
        payload = build_legacy_v2_document()
        payload["candidate_places"] = [
            {
                "name": f"杭州候选地点 {index}",
                "category": "景点",
                "poi_id": f"poi_candidate_{index}",
                "lat": 30.26 + index / 10_000,
                "lng": 120.16 + index / 10_000,
                "opening_hours": "09:00-18:00",
                "price": 20,
                "data_type": "confirmed_live_data",
            }
            for index in range(1, 8)
        ]

        document = adapt_v2_to_v3(payload)

        self.assertEqual(7, len(document.candidate_pool))
        self.assertTrue(all(candidate.place and candidate.place.poi_id for candidate in document.candidate_pool))
        self.assertTrue(all(candidate.insertion_options for candidate in document.candidate_pool))

    def test_v2_adapter_keeps_map_restaurant_category_in_candidate_pool(self):
        payload = build_legacy_v2_document()
        payload["candidate_places"] = [{
            "name": "杭州本地餐厅",
            "category": "餐厅",
            "poi_id": "poi_candidate_restaurant",
            "lat": 30.261,
            "lng": 120.161,
            "opening_hours": "10:00-22:00",
            "data_type": "confirmed_live_data",
        }]

        document = adapt_v2_to_v3(payload)

        self.assertEqual(1, len(document.candidate_pool))
        self.assertEqual("food", document.candidate_pool[0].kind)
        self.assertEqual("poi_candidate_restaurant", document.candidate_pool[0].place.poi_id)

    def test_meal_type_is_limited_to_food_and_is_exported(self):
        with self.assertRaises(ValidationError):
            TripActivityV3.model_validate(
                {
                    "activity_id": "invalid-meal",
                    "kind": "attraction",
                    "title": "不应带餐次的景点",
                    "meal_type": "lunch",
                }
            )

        payload = load_fixture("trip_v3_domestic_3d.json")
        activity = payload["itinerary"]["days"][0]["activities"][0]
        activity["kind"] = "food"
        activity["title"] = "测试午餐"
        activity["meal_type"] = "lunch"
        activity["place"]["category"] = "food"
        document = TravelPlanDocumentV3.model_validate(payload)

        self.assertIn("午餐：测试午餐", export_travel_plan_v3_markdown(document))

    def test_export_image_cannot_require_attribution(self):
        with self.assertRaises(ValidationError):
            ImageAssetV3(
                image_id="img_invalid",
                url="https://example.invalid/image.jpg",
                export_allowed=True,
                attribution_required=True,
            )

    def test_v2_unsplash_cover_migrates_with_attribution_and_a_resolved_source(self):
        payload = build_legacy_v2_document()
        payload["sources"][0]["reference_id"] = "source_cover_unsplash"
        payload["destination_overview"]["cover_image"] = {
            "url": "https://images.unsplash.com/photo-123",
            "alt": "杭州西湖",
            "photographer_name": "摄影师甲",
            "photographer_url": "https://unsplash.com/@photographer-a",
            "unsplash_url": "https://unsplash.com/photos/example",
            "download_location": "https://api.unsplash.com/photos/example/download",
            "source_reference_id": "source_cover_unsplash",
        }

        document = adapt_v2_to_v3(payload)
        cover = document.destination_overview.cover_image

        self.assertIsNotNone(cover)
        assert cover is not None
        self.assertEqual("Unsplash", cover.provider_name)
        self.assertFalse(cover.export_allowed)
        self.assertTrue(cover.attribution_required)
        self.assertEqual(document.sources[0].source_id, cover.source_ref)

    def test_v2_activity_image_asset_migrates_with_cover_reference(self):
        payload = build_legacy_v2_document()
        payload["sources"].append({
            "reference_id": "source_image_wikimedia_west_lake",
            "title": "西湖活动图片",
            "source": "Wikimedia Commons",
            "url": "https://commons.wikimedia.org/wiki/File:West_Lake.jpg",
            "data_type": "reference_data",
        })
        payload["itinerary"]["days"][0]["activities"][0]["place"]["image_assets"] = [{
            "image_id": "img_wikimedia_west_lake",
            "url": "https://upload.wikimedia.org/example/West_Lake.jpg",
            "alt": "西湖参考图片",
            "mime_type": "image/jpeg",
            "display_allowed": True,
            "export_allowed": False,
            "attribution_required": True,
            "attribution_text": "摄影师甲",
            "attribution_url": "https://commons.wikimedia.org/wiki/File:West_Lake.jpg",
            "provider_name": "Wikimedia Commons",
            "provider_url": "https://commons.wikimedia.org/wiki/File:West_Lake.jpg",
            "source_ref": "source_image_wikimedia_west_lake",
        }]

        document = adapt_v2_to_v3(payload)
        activity = document.itinerary.days[0].activities[0]

        self.assertEqual(["img_wikimedia_west_lake"], [image.image_id for image in activity.images])
        self.assertEqual("img_wikimedia_west_lake", activity.cover_image_id)
        self.assertIn(activity.images[0].source_ref, {source.source_id for source in document.sources})

    def test_map_locations_must_match_current_formal_revision(self):
        payload = load_fixture("trip_v3_domestic_3d.json")
        payload["map_guidance"]["formal_location_ids"].append("stale_activity")

        with self.assertRaises(ValidationError):
            TravelPlanDocumentV3.model_validate(payload)

    def test_status_labels_are_complete_and_user_visible_in_chinese(self):
        self.assertEqual("规划中", USER_VISIBLE_STATUS_LABELS["planning"])
        self.assertEqual("已完成，部分信息待确认", USER_VISIBLE_STATUS_LABELS["completed_degraded"])
        self.assertEqual(10, len(USER_VISIBLE_STATUS_LABELS))

    def test_planning_event_rejects_payload_for_another_event_type(self):
        with self.assertRaises(ValidationError):
            PlanningEventEnvelope(
                event_id="evt_1",
                run_id="run_1",
                request_id="req_1",
                sequence=1,
                occurred_at=datetime.fromisoformat("2026-07-17T10:00:00+08:00"),
                type="run_started",
                payload=AgentStagePayload(
                    stage="requirements_analysis",
                    agent_name="需求分析 Agent",
                    task="整理行程约束",
                    status="running",
                ),
            )

        event = PlanningEventEnvelope(
            event_id="evt_2",
            run_id="run_1",
            request_id="req_1",
            sequence=2,
            occurred_at=datetime.fromisoformat("2026-07-17T10:00:01+08:00"),
            type="run_started",
            payload=RunStartedPayload(message="开始规划"),
        )
        self.assertEqual("planning", event.payload.status)

        queue_event = PlanningEventEnvelope(
            event_id="evt_queue_1",
            run_id="run_1",
            request_id="req_1",
            sequence=3,
            occurred_at=datetime.fromisoformat("2026-07-17T10:00:02+08:00"),
            type="provider_queue_waiting",
            payload=ProviderQueueWaitingPayload(
                provider="baidu-map",
                operation="map_search_places",
                priority="formal",
                queue_position=1,
            ),
        )
        self.assertEqual("地点核验排队中", queue_event.payload.message)

        with self.assertRaises(ValidationError):
            PlanningEventEnvelope(
                event_id="evt_queue_2",
                run_id="run_1",
                request_id="req_1",
                sequence=4,
                occurred_at=datetime.fromisoformat("2026-07-17T10:00:03+08:00"),
                type="provider_queue_waiting",
                payload=RunStartedPayload(message="开始规划"),
            )

    def test_v2_adapter_is_deterministic_and_never_promotes_legacy_uncertainty(self):
        payload = build_legacy_v2_document()
        first = adapt_v2_to_v3(copy.deepcopy(payload))
        second = adapt_v2_to_v3(copy.deepcopy(payload))

        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))
        self.assertEqual(calculate_document_checksum(first), calculate_document_checksum(second))
        self.assertEqual("degraded", first.status)
        self.assertEqual([], first.outbound_transport.options)
        self.assertTrue(first.validation.degraded)
        self.assertIn(
            "LEGACY_TRAVELER_BREAKDOWN_MISSING",
            {issue.code for issue in first.validation.issues},
        )

    def test_v2_adapter_accepts_real_chinese_start_date_and_infers_three_days(self):
        payload = build_legacy_v2_document()
        payload["intent"]["date_range"] = "2026年8月15日"
        payload["intent"]["days"] = 3

        document = adapt_v2_to_v3(payload)

        self.assertEqual(document.intent.start_date.isoformat(), "2026-08-15")
        self.assertEqual(document.intent.end_date.isoformat(), "2026-08-17")
        self.assertEqual(document.intent.days, 3)
        self.assertEqual(
            [day.date.isoformat() for day in document.itinerary.days],
            ["2026-08-15", "2026-08-16", "2026-08-17"],
        )

    def test_v2_adapter_keeps_flexible_dates_without_fake_calendar_values(self):
        payload = build_legacy_v2_document()
        payload["intent"]["date_range"] = "日期暂未确定"
        payload["intent"]["days"] = 3

        document = adapt_v2_to_v3(payload)

        self.assertEqual(document.intent.date_mode, "flexible")
        self.assertIsNone(document.intent.start_date)
        self.assertIsNone(document.intent.end_date)
        self.assertEqual([day.date for day in document.itinerary.days], [None, None, None])
        self.assertEqual(document.outbound_transport.options, [])
        self.assertEqual(document.return_transport.options, [])
        self.assertEqual(document.outbound_transport.status, "unavailable")

    def test_v2_adapter_derives_total_budget_from_per_person_budget(self):
        payload = build_legacy_v2_document()
        payload["intent"].pop("budget_total")
        payload["intent"]["budget_per_person"] = 2000
        payload["intent"]["people_count"] = 2

        document = adapt_v2_to_v3(payload)

        self.assertEqual(document.budget.total_budget.amount, 4000)
        self.assertNotIn(
            "LEGACY_BUDGET_MISSING",
            {issue.code for issue in document.validation.issues},
        )

    def test_v2_adapter_preserves_realtime_seat_classes_and_remaining_tickets(self):
        payload = build_legacy_v2_document()
        payload["outbound_transport"] = {
            "direction": "outbound",
            "scope": "domestic",
            "status": "ready",
            "travel_date": "2026-08-01",
            "recommended_option_id": "transport-1",
            "options": [{
                "option_id": "transport-1",
                "mode": "train",
                "service_number": "G123",
                "departure_station": "上海虹桥",
                "arrival_station": "杭州东",
                "departure_time": "08:00",
                "arrival_time": "09:00",
                "availability": "available",
                "seat_options": [
                    {"name": "一等座", "availability": "limited", "remaining_text": "2", "price": 180, "currency": "CNY"},
                    {"name": "二等座", "availability": "available", "remaining_text": "18", "price": 112, "currency": "CNY"},
                ],
                "data_type": "confirmed_live_data",
                "queried_at": "2026-07-26T12:00:00+08:00",
            }],
        }

        document = adapt_v2_to_v3(payload)

        self.assertEqual(document.outbound_transport.options[0].seat_options[0].name, "一等座")
        self.assertEqual(document.outbound_transport.options[0].seat_options[0].remaining_text, "2")
        self.assertEqual(document.outbound_transport.options[0].seat_options[1].availability, "available")

    def test_v2_adapter_adds_verified_lodging_and_city_side_transport_anchors(self):
        payload = build_legacy_v2_document()
        payload["intent"].update({"date_range": "2026-08-01 至 2026-08-03", "days": 3})
        for day_number in (2, 3):
            activity = copy.deepcopy(payload["itinerary"]["days"][0]["activities"][0])
            activity.update({
                "activity_id": f"legacy_act_{day_number}",
                "day": day_number,
                "title": f"V2 契约地点 {day_number}",
                "place": {
                    **activity["place"],
                    "poi_id": f"poi_legacy_{day_number}",
                    "name": f"V2 契约地点 {day_number}",
                    "lat": 30.25 + day_number / 100,
                },
            })
            payload["itinerary"]["days"].append({
                "day": day_number,
                "date": f"2026-08-0{day_number}",
                "theme": "契约测试",
                "activities": [activity],
            })
        payload["map_guidance"]["location_ids"] = ["poi_legacy_1", "poi_legacy_2", "poi_legacy_3"]
        payload["hotel_recommendations"] = {
            "status": "needs_confirmation",
            "recommendations": [{
                "hotel_id": "lodging-west-lake",
                "name": "西湖规划住宿点",
                "area": "西湖区",
                "place": {
                    "poi_id": "poi-lodging-west-lake",
                    "name": "西湖规划住宿点",
                    "category": "住宿",
                    "lat": 30.245,
                    "lng": 120.145,
                    "data_type": "reference_data",
                },
            }],
        }
        payload["outbound_transport"] = {
            "direction": "outbound",
            "scope": "domestic",
            "status": "needs_confirmation",
            "status_reason": "具体到达班次待官方渠道确认",
            "recommended_option_id": "arrival-option",
            "options": [{
                "option_id": "arrival-option",
                "mode": "train",
                "arrival_station": "杭州东站",
                "arrival_hub": {
                    "poi_id": "poi-hangzhou-east",
                    "name": "杭州东站",
                    "category": "交通",
                    "lat": 30.294,
                    "lng": 120.212,
                    "data_type": "reference_data",
                },
            }],
        }
        payload["return_transport"] = {
            "direction": "return",
            "scope": "domestic",
            "status": "needs_confirmation",
            "status_reason": "具体返程班次待官方渠道确认",
            "recommended_option_id": "departure-option",
            "options": [{
                "option_id": "departure-option",
                "mode": "train",
                "departure_station": "杭州东站",
                "departure_hub": {
                    "poi_id": "poi-hangzhou-east",
                    "name": "杭州东站",
                    "category": "交通",
                    "lat": 30.294,
                    "lng": 120.212,
                    "data_type": "reference_data",
                },
            }],
        }

        document = adapt_v2_to_v3(payload)
        anchors = [anchor for day in document.itinerary.days for anchor in day.anchors]

        self.assertEqual("lodging-west-lake", document.lodging_plan.planning_lodging_id)
        self.assertEqual(
            ["arrival_hub", "lodging_return", "lodging_departure", "lodging_return", "lodging_departure", "departure_hub"],
            [anchor.kind for anchor in anchors],
        )
        self.assertTrue(all(anchor.anchor_id in document.map_guidance.formal_location_ids for anchor in anchors))
        self.assertEqual([], document.map_guidance.day_routes)

    def test_v2_adapter_omits_anchors_without_stable_poi_and_coordinates(self):
        payload = build_legacy_v2_document()
        payload["intent"].update({"date_range": "2026-08-01 至 2026-08-02", "days": 2})
        activity = copy.deepcopy(payload["itinerary"]["days"][0]["activities"][0])
        activity.update({"activity_id": "legacy_act_2", "day": 2})
        payload["itinerary"]["days"].append({"day": 2, "activities": [activity]})
        payload["map_guidance"]["location_ids"] = ["poi_legacy_1", "poi_legacy_1"]
        payload["hotel_recommendations"] = {
            "status": "needs_confirmation",
            "recommendations": [{"hotel_id": "lodging-unresolved", "name": "待确认住宿"}],
        }

        document = adapt_v2_to_v3(payload)

        self.assertEqual("lodging-unresolved", document.lodging_plan.planning_lodging_id)
        self.assertEqual([], [anchor for day in document.itinerary.days for anchor in day.anchors])

    def test_ready_route_requires_renderable_provider_geometry(self):
        with self.assertRaises(ValidationError):
            RouteLegV3(
                from_id="legacy_act_1",
                to_id="legacy_act_2",
                mode="walking",
                provider="openrouteservice",
                coordinate_system="WGS84",
                geometry={"type": "LineString", "coordinates": [[120.16, 30.25]]},
                status="ready",
            )

    def test_v2_adapter_downgrades_malformed_route_to_unavailable(self):
        payload = build_legacy_v2_document()
        next_activity = copy.deepcopy(payload["itinerary"]["days"][0]["activities"][0])
        next_activity.update({
            "activity_id": "legacy_act_2",
            "title": "第二个契约地点",
            "place": {**next_activity["place"], "poi_id": "poi_legacy_2", "lng": 120.18},
        })
        payload["itinerary"]["days"][0]["activities"].append(next_activity)
        payload["map_guidance"].update({
            "status": "ready",
            "location_ids": ["poi_legacy_1", "poi_legacy_2"],
            "day_routes": [{
                "day": 1,
                "plan_version": 2,
                "provider": "openrouteservice",
                "coordinate_system": "WGS84",
                "status": "ready",
                "legs": [{
                    "from_activity_id": "legacy_act_1",
                    "to_activity_id": "legacy_act_2",
                    "mode": "walking",
                    "provider": "openrouteservice",
                    "coordinate_system": "WGS84",
                    "geometry": {"type": "LineString", "coordinates": [[120.16, 30.25]]},
                    "status": "ready",
                }],
            }],
        })

        document = adapt_v2_to_v3(payload)
        route = document.map_guidance.day_routes[0]

        self.assertEqual("unavailable", route.status)
        self.assertEqual("unavailable", route.legs[0].status)
        self.assertIsNone(route.legs[0].geometry)
        self.assertEqual(
            "unavailable",
            document.itinerary.days[0].activities[0].route_to_next.status,
        )

    def test_v2_adapter_writes_ready_day_route_back_to_origin_activity(self):
        payload = build_legacy_v2_document()
        next_activity = copy.deepcopy(payload["itinerary"]["days"][0]["activities"][0])
        next_activity.update({
            "activity_id": "legacy_act_2",
            "title": "第二个契约地点",
            "place": {**next_activity["place"], "poi_id": "poi_legacy_2", "lng": 120.18},
        })
        payload["itinerary"]["days"][0]["activities"].append(next_activity)
        payload["map_guidance"].update({
            "status": "ready",
            "location_ids": ["poi_legacy_1", "poi_legacy_2"],
            "day_routes": [{
                "day": 1,
                "plan_version": 2,
                "provider": "openrouteservice",
                "coordinate_system": "WGS84",
                "status": "ready",
                "legs": [{
                    "from_activity_id": "legacy_act_1",
                    "to_activity_id": "legacy_act_2",
                    "mode": "walking",
                    "distance_meters": 1800,
                    "duration_minutes": 24,
                    "provider": "openrouteservice",
                    "coordinate_system": "WGS84",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[120.16, 30.25], [120.18, 30.25]],
                    },
                    "status": "ready",
                }],
            }],
        })

        document = adapt_v2_to_v3(payload)
        route = document.itinerary.days[0].activities[0].route_to_next

        self.assertIsNotNone(route)
        self.assertEqual("ready", route.status)
        self.assertEqual("legacy_act_2", route.to_id)
        self.assertEqual(24, route.duration_minutes)

    def test_v2_adapter_sorts_activities_and_discards_non_adjacent_stale_route(self):
        payload = build_legacy_v2_document()
        morning = copy.deepcopy(payload["itinerary"]["days"][0]["activities"][0])
        morning.update({
            "activity_id": "morning",
            "title": "上午地点",
            "start_time": "09:00",
            "end_time": "10:00",
            "place": {**morning["place"], "poi_id": "poi-morning", "lng": 120.14},
        })
        lunch = copy.deepcopy(morning)
        lunch.update({
            "activity_id": "lunch",
            "title": "午餐地点",
            "start_time": "11:30",
            "end_time": "13:00",
            "place": {**lunch["place"], "poi_id": "poi-lunch", "lng": 120.18},
        })
        payload["itinerary"]["days"][0]["activities"] = [lunch, morning]
        payload["map_guidance"]["location_ids"] = ["poi-lunch", "poi-morning"]
        payload["map_guidance"]["day_routes"] = [{
            "day": 1,
            "plan_version": 2,
            "status": "ready",
            "provider": "openrouteservice",
            "coordinate_system": "WGS84",
            "legs": [{
                "from_activity_id": "lunch",
                "to_activity_id": "morning",
                "mode": "walking",
                "provider": "openrouteservice",
                "coordinate_system": "WGS84",
                "geometry": {"type": "LineString", "coordinates": [[120.18, 30.25], [120.14, 30.25]]},
                "status": "ready",
            }],
        }]

        document = adapt_v2_to_v3(payload)

        self.assertEqual(["morning", "lunch"], [item.activity_id for item in document.itinerary.days[0].activities])
        self.assertEqual([1, 2], [item.order for item in document.itinerary.days[0].activities])
        self.assertEqual([], document.map_guidance.day_routes[0].legs)
        self.assertIsNone(document.itinerary.days[0].activities[0].route_to_next)

    def test_v2_adapter_marks_domestic_overnight_arrival_with_next_day_offset(self):
        payload = build_legacy_v2_document()
        payload["outbound_transport"] = {
            "direction": "outbound",
            "scope": "domestic",
            "status": "ready",
            "travel_date": "2026-08-01",
            "recommended_option_id": "transport-overnight",
            "options": [{
                "option_id": "transport-overnight",
                "mode": "train",
                "service_number": "D999",
                "departure_station": "上海站",
                "arrival_station": "杭州东站",
                "departure_time": "23:40",
                "arrival_time": "00:20",
                "duration_minutes": 40,
                "data_type": "confirmed_live_data",
                "queried_at": "2026-07-26T12:00:00+08:00",
            }],
        }

        document = adapt_v2_to_v3(payload)
        option = document.outbound_transport.options[0]

        self.assertEqual(option.departure_time.local_iso.date().isoformat(), "2026-08-01")
        self.assertEqual(option.arrival_time.local_iso.date().isoformat(), "2026-08-02")
        self.assertEqual(option.arrival_time.day_offset, 1)
        self.assertGreater(option.arrival_time.utc, option.departure_time.utc)

    def test_v2_adapter_normalizes_international_bare_clock_with_dual_timezones(self):
        payload = build_legacy_v2_document()
        payload["intent"]["destination"] = "东京"
        payload["destination_overview"].update({
            "name_zh": "东京",
            "country_code": "JP",
            "timezone": "Asia/Tokyo",
            "currency": "JPY",
        })
        payload["budget"] = {
            "currency": "CNY",
            "estimated_total": 1200,
            "currency_breakdown": {
                "categories": [{
                    "category": "activities",
                    "amount": {
                        "amount": 28000,
                        "currency": "JPY",
                        "cny_reference_amount": 1200,
                        "exchange_rate_as_of": "2026-08-19",
                        "source_status": "user_confirmation_required",
                    },
                }],
            },
        }
        payload["outbound_transport"] = {
            "direction": "outbound",
            "scope": "international",
            "status": "ready",
            "travel_date": "2026-08-01",
            "recommended_option_id": "flight-raw-clock",
            "options": [{
                "option_id": "flight-raw-clock",
                "mode": "flight",
                "service_number": "TEST123",
                "departure_station": "上海浦东机场",
                "arrival_station": "东京成田机场",
                "departure_time": "09:00",
                "arrival_time": "13:00",
                "duration_minutes": 180,
                "data_type": "confirmed_live_data",
                "queried_at": "2026-07-26T12:00:00+08:00",
            }],
        }

        document = adapt_v2_to_v3(payload)
        option = document.outbound_transport.options[0]

        self.assertEqual(option.departure_time.display_text, "09:00")
        self.assertEqual(option.departure_time.timezone, "Asia/Shanghai")
        self.assertEqual(option.arrival_time.timezone, "Asia/Tokyo")
        self.assertIsNotNone(option.departure_time.utc)
        self.assertIsNotNone(option.arrival_time.local_iso)
        self.assertEqual(option.arrival_time.day_offset, 0)

    def test_transport_contract_rejects_inconsistent_arrival_day_offset(self):
        with self.assertRaises(ValidationError):
            TransportOptionV3.model_validate({
                "option_id": "transport-invalid-offset",
                "mode": "flight",
                "source_status": "realtime_verified",
                "departure_time": {
                    "display_text": "23:40",
                    "utc": "2026-08-01T15:40:00Z",
                    "local_iso": "2026-08-01T23:40:00+08:00",
                    "timezone": "Asia/Shanghai",
                    "beijing_iso": "2026-08-01T23:40:00+08:00",
                },
                "arrival_time": {
                    "display_text": "00:20",
                    "utc": "2026-08-01T16:20:00Z",
                    "local_iso": "2026-08-02T00:20:00+08:00",
                    "timezone": "Asia/Shanghai",
                    "beijing_iso": "2026-08-02T00:20:00+08:00",
                    "day_offset": 0,
                },
            })

    def test_json_schema_matches_committed_snapshot(self):
        expected = json.loads(SCHEMA_SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(expected, travel_plan_v3_json_schema())

        expected_event = json.loads(EVENT_SCHEMA_SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(expected_event, planning_event_json_schema())


if __name__ == "__main__":
    unittest.main()


def test_v3_transport_limit_keeps_recommended_option_beyond_first_three():
    from services.ticket_search_service import transport_section_from_bundle
    legacy = build_legacy_v2_document()
    legacy['return_transport'] = transport_section_from_bundle({
        'direct_source': '12306',
        'direct_rows': [{'trip_no': f'G{index}', 'route': '杭州 -> 上海',
                         'depart': f'2026-08-01 {hour}:00', 'arrive': f'2026-08-01 {hour}:50',
                         'duration': '50分钟', 'price': 10 + index}
                        for index, hour in enumerate(('13', '14', '15', '20'))],
    }, 'return', 'domestic', '2026-08-01')
    expected = legacy['return_transport']['recommended_option_id']
    assert expected not in [item['option_id'] for item in legacy['return_transport']['options'][:3]]
    document = adapt_v2_to_v3(legacy)
    assert len(document.return_transport.options) == 3
    assert document.return_transport.selected_option_id == expected
    selected = next(item for item in document.return_transport.options if item.option_id == expected)
    assert selected.departure_time.local_iso.hour == 20
