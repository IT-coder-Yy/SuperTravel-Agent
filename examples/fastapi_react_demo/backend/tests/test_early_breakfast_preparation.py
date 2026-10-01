from copy import deepcopy
import asyncio
from unittest.mock import patch

from backend.tests.test_transport_meal_windows import document_with_last_day
from backend.services.meal_schedule_service import (
    early_breakfast_preparation_covers, meal_targets_by_day, prepare_early_departure_breakfast,
)


def early_document(hours=None):
    document = document_with_last_day().model_dump(mode="json")
    day = document["itinerary"]["days"][-1]
    meal = day["activities"][-1]
    meal.update(day=None, order=None, start_at=None, end_at=None, meal_type="breakfast")
    meal["place"]["opening_hours"] = hours
    document["candidate_pool"] = [meal]
    day["activities"] = []
    time = document["return_transport"]["options"][0]["departure_time"]
    time["local_iso"] = f"{day['date']}T05:30:00+08:00"
    return document


def test_early_train_without_verified_open_restaurant_has_explicit_preparation():
    document = early_document()
    assert prepare_early_departure_breakfast(document)
    assert early_breakfast_preparation_covers(document, 3)
    assert not early_breakfast_preparation_covers(document, 2)
    assert not document["itinerary"]["days"][-1]["activities"]
    assert "05:30" in document["action_items"][-1]["text"]
    assert "提前一晚准备" in document["action_items"][-1]["text"]
    assert document["action_items"][-1]["status"] == "pending"


def test_verified_overnight_open_restaurant_keeps_real_poi_and_predeparture_window():
    document = early_document("周一至周日 00:00-24:00")
    original = deepcopy(document["candidate_pool"][0]["place"])
    assert prepare_early_departure_breakfast(document)
    breakfast = document["itinerary"]["days"][-1]["activities"][0]
    assert breakfast["place"] == original
    assert (breakfast["start_at"], breakfast["end_at"]) == ("03:30", "05:00")
    assert not early_breakfast_preparation_covers(document, 3)


def test_known_closed_early_restaurant_cannot_be_scheduled_before_opening():
    document = early_document("周一至周日 07:00-22:00")
    prepare_early_departure_breakfast(document)
    assert early_breakfast_preparation_covers(document, 3)
    assert not document["itinerary"]["days"][-1]["activities"]


def test_actual_route_conflict_replaces_only_early_breakfast_with_preparation():
    document = early_document("周一至周日 00:00-24:00")
    prepare_early_departure_breakfast(document)
    breakfast = document["itinerary"]["days"][-1]["activities"][0]
    document["candidate_pool"] = [{**deepcopy(breakfast), "activity_id": f"candidate-{i}",
        "day": None, "order": None, "start_at": None, "end_at": None} for i in range(15)]
    document["validation"]["issues"].extend([
        {"code": "INITIAL_MEAL_WINDOW_CONFLICT", "severity": "error", "target_id": breakfast["activity_id"], "message": "真实接驳无法赶上返程"},
        {"code": "UNRELATED", "severity": "error", "target_id": "other", "message": "其他活动错误"},
    ])
    assert prepare_early_departure_breakfast(document, after_routes=True)
    assert early_breakfast_preparation_covers(document, 3)
    assert any(i["code"] == "UNRELATED" for i in document["validation"]["issues"])
    assert not any(i["code"] == "INITIAL_MEAL_WINDOW_CONFLICT" for i in document["validation"]["issues"])
    assert len(document["candidate_pool"]) == 15
    assert any(item["activity_id"] == breakfast["activity_id"] for item in document["candidate_pool"])


def test_final_route_reconciliation_does_not_push_early_breakfast_to_seven():
    from backend.services.formal_consistency_service import finalize_initial_schedule
    document = early_document("周一至周日 00:00-24:00")
    last_day = document["itinerary"]["days"][-1]
    hub = deepcopy(document["candidate_pool"][0]["place"])
    hub.update(poi_id="verified-station", name="车站", category="transport")
    document["return_transport"]["options"][0]["departure_hub"] = hub
    prepare_early_departure_breakfast(document)

    async def route(**kwargs):
        activities = kwargs["activities"]
        return {"status": "ready", "legs": [{"from_activity_id": left["activity_id"],
            "to_activity_id": right["activity_id"], "status": "ready", "duration_minutes": 10,
            "mode": "walking", "provider": "test-route", "geometry": None}
            for left, right in zip(activities, activities[1:])]}

    with patch('services.route_geometry_service.build_day_route', side_effect=route):
        asyncio.run(finalize_initial_schedule(document))
    assert (last_day["activities"][0]["start_at"], last_day["activities"][0]["end_at"]) == ("03:30", "05:00")
    assert any(leg["duration_minutes"] == 10 for leg in document["map_guidance"]["day_routes"][-1]["legs"])
    assert not prepare_early_departure_breakfast(document, after_routes=True)
    assert not early_breakfast_preparation_covers(document, 3)


def test_normal_return_and_late_arrival_cannot_claim_early_breakfast_coverage():
    document = early_document()
    prepare_early_departure_breakfast(document)
    time = document["return_transport"]["options"][0]["departure_time"]
    time["local_iso"] = time["local_iso"].replace("05:30", "21:00")
    assert not early_breakfast_preparation_covers(document, 3)
    assert not prepare_early_departure_breakfast(document)
    assert meal_targets_by_day(days=3, date_mode="fixed", outbound_arrival="2026-08-01T19:00:00+08:00",
        return_departure=time["local_iso"]) == {1: ["dinner"], 2: ["lunch", "dinner"], 3: ["lunch", "dinner"]}
