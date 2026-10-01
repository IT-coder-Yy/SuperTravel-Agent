"""Transport-only days retain verified transfer endpoints without inventing visits."""

import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.formal_consistency_service import daily_route_nodes, rebuild_formal_routes
from services.travel_document_service import ItineraryV3, TripPlaceV3, _append_trip_anchors
from services.trip_edit_service import _v3_refresh_anchors


def document():
    path = Path(__file__).parent / "fixtures" / "trip_v3_domestic_3d.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    hub = deepcopy(doc["lodging_plan"]["options"][0]["place"])
    hub.update(poi_id="poi_station_fixture", name="杭州东站", category="transport")
    hub["coordinates"].update(latitude=30.294, longitude=120.212)
    for section, field in (("outbound_transport", "arrival_hub"), ("return_transport", "departure_hub")):
        doc[section]["options"][0][field] = deepcopy(hub)
    doc["outbound_transport"]["options"][0]["arrival_time"]["local_iso"] = "2026-08-01T23:00:00+08:00"
    doc["return_transport"]["options"][0]["departure_time"]["local_iso"] = "2026-08-03T05:30:00+08:00"
    for day in doc["itinerary"]["days"]:
        day["anchors"] = []
    return doc


def generate(doc, stage):
    if stage == "edit":
        _v3_refresh_anchors(doc)
        return
    itinerary = ItineraryV3.model_validate(doc["itinerary"])
    lodging = doc["lodging_plan"]["options"][0].get("place")
    arrival = doc["outbound_transport"]["options"][0].get("arrival_hub")
    departure = doc["return_transport"]["options"][0].get("departure_hub")
    mapped = lambda place: TripPlaceV3.model_validate(place) if place and place.get("coordinates") else None
    ids = _append_trip_anchors(itinerary, plan_id=doc["plan_id"], planning_lodging=mapped(lodging),
                              arrival_hub=mapped(arrival), departure_hub=mapped(departure))
    doc["itinerary"] = itinerary.model_dump(mode="json")
    doc["map_guidance"]["formal_location_ids"] = ids


@pytest.mark.parametrize("stage", ["initial", "edit"])
@pytest.mark.parametrize("day_index,kinds", [
    (0, ["arrival_hub", "lodging_return"]),
    (2, ["lodging_departure", "departure_hub"]),
])
def test_empty_boundary_day_retains_real_transfer(stage, day_index, kinds):
    doc = document()
    doc["itinerary"]["days"][day_index]["activities"] = []
    generate(doc, stage)
    day = doc["itinerary"]["days"][day_index]
    assert day["activities"] == []
    assert [a["kind"] for a in day["anchors"]] == kinds
    assert [n["activity_id"] for n in daily_route_nodes(day)] == [a["anchor_id"] for a in day["anchors"]]
    assert all(a["anchor_id"] in doc["map_guidance"]["formal_location_ids"] for a in day["anchors"])


@pytest.mark.parametrize("stage", ["initial", "edit"])
@pytest.mark.parametrize("missing", ["arrival", "departure", "lodging"])
def test_empty_day_without_both_verified_endpoints_does_not_invent_route(stage, missing):
    doc = document()
    for day in doc["itinerary"]["days"]:
        day["activities"] = []
    if missing == "lodging":
        doc["lodging_plan"]["options"][0]["place"]["coordinates"] = None
    else:
        section, field = ("outbound_transport", "arrival_hub") if missing == "arrival" else ("return_transport", "departure_hub")
        doc[section]["options"][0].pop(field)
    generate(doc, stage)
    days = doc["itinerary"]["days"]
    assert days[1]["anchors"] == []  # No lodging self-loop on an empty middle day.
    if missing in {"arrival", "lodging"}:
        assert days[0]["anchors"] == []
    if missing in {"departure", "lodging"}:
        assert days[-1]["anchors"] == []


@pytest.mark.parametrize("stage", ["initial", "edit"])
def test_normal_days_keep_one_anchor_per_kind(stage):
    doc = document()
    generate(doc, stage)
    assert [[a["kind"] for a in day["anchors"]] for day in doc["itinerary"]["days"]] == [
        ["arrival_hub", "lodging_return"], ["lodging_departure", "lodging_return"],
        ["lodging_departure", "departure_hub"],
    ]
    if stage == "edit":
        before = deepcopy(doc["itinerary"])
        generate(doc, stage)
        assert doc["itinerary"] == before


def test_empty_departure_day_requests_provider_route_and_preserves_unavailability():
    doc = document()
    doc["itinerary"]["days"][-1]["activities"] = []
    _v3_refresh_anchors(doc)
    calls = []

    async def unavailable_route(**kwargs):
        calls.append(kwargs)
        nodes = kwargs["activities"]
        return {"status": "unavailable", "legs": [
            {"from_activity_id": a["activity_id"], "to_activity_id": b["activity_id"],
             "mode": "transit", "status": "unavailable"}
            for a, b in zip(nodes, nodes[1:])
        ]}

    with patch("services.route_geometry_service.build_day_route", side_effect=unavailable_route):
        asyncio.run(rebuild_formal_routes(doc))
    assert calls[-1]["day"] == 3
    assert len(calls[-1]["activities"]) == 2
    route = doc["map_guidance"]["day_routes"][-1]
    assert route["status"] == "unavailable"
    assert len(route["legs"]) == 1
    assert route["legs"][0]["status"] == "unavailable"
    assert not route["legs"][0].get("geometry")
