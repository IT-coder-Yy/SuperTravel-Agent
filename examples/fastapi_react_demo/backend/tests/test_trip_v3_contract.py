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
    RunStartedPayload,
    TransportOptionV3,
    TravelPlanDocumentV3,
    TripActivityV3,
    USER_VISIBLE_STATUS_LABELS,
)
from backend.services.travel_document_service import (
    adapt_v2_to_v3,
    calculate_document_checksum,
    planning_event_json_schema,
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

    def test_export_image_cannot_require_attribution(self):
        with self.assertRaises(ValidationError):
            ImageAssetV3(
                image_id="img_invalid",
                url="https://example.invalid/image.jpg",
                export_allowed=True,
                attribution_required=True,
            )

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

    def test_json_schema_matches_committed_snapshot(self):
        expected = json.loads(SCHEMA_SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(expected, travel_plan_v3_json_schema())

        expected_event = json.loads(EVENT_SCHEMA_SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(expected_event, planning_event_json_schema())


if __name__ == "__main__":
    unittest.main()
