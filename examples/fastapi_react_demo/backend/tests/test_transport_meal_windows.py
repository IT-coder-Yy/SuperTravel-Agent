"""交通窗口收缩时优先保留可行餐次，不越过真实返程时间。"""
import json
from pathlib import Path

from backend.schemas.trip_v3_models import TravelPlanDocumentV3
from backend.services.travel_document_service import _apply_v3_transport_windows


def document_with_last_day(*, fixed=False, meal_start="11:30", meal_end="13:00"):
    payload = json.loads((Path(__file__).parent / "fixtures/trip_v3_domestic_3d.json").read_text(encoding="utf-8"))
    document = TravelPlanDocumentV3.model_validate(payload)
    template = document.itinerary.days[0].activities[0]
    document.itinerary.days[-1].activities = [
        template.model_copy(deep=True, update={"activity_id": "long-visit", "day": 3,
            "start_at": "09:00", "end_at": "15:00", "duration_minutes": 360, "fixed_time": fixed}),
        template.model_copy(deep=True, update={"activity_id": "real-meal", "day": 3,
            "kind": "food", "meal_type": "lunch", "start_at": meal_start, "end_at": meal_end,
            "duration_minutes": 90}),
    ]
    return document


def apply(document):
    candidates, issues = [], []
    _apply_v3_transport_windows(document.itinerary, candidates,
        outbound=document.outbound_transport, returning=document.return_transport,
        migration_issues=issues)
    return document.itinerary.days[-1].activities, candidates


def test_flexible_visit_moves_to_candidate_before_losing_a_feasible_meal():
    document = document_with_last_day()
    original_place = document.itinerary.days[-1].activities[1].place.model_dump()
    kept, candidates = apply(document)
    assert [item.activity_id for item in kept] == ["real-meal"]
    assert (kept[0].start_at, kept[0].end_at) == ("11:30", "13:00")
    assert kept[0].place.model_dump() == original_place
    assert [item.activity_id for item in candidates] == ["long-visit"]
    assert candidates[0].day is None


def test_fixed_visit_is_not_removed_to_force_a_meal():
    kept, candidates = apply(document_with_last_day(fixed=True))
    assert [item.activity_id for item in kept] == ["long-visit"]
    assert [item.activity_id for item in candidates] == ["real-meal"]


def test_meal_outside_return_window_is_not_forced_into_the_trip():
    kept, candidates = apply(document_with_last_day(meal_start="17:30", meal_end="19:00"))
    assert [item.activity_id for item in kept] == ["long-visit"]
    assert [item.activity_id for item in candidates] == ["real-meal"]
