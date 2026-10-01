import asyncio
from copy import deepcopy
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.transport_hub_service import enrich_selected_transport_hubs
from backend.tests.test_empty_transport_anchors import document


def missing_hubs():
    doc = document()
    doc["intent"]["date_mode"] = "fixed"
    for key, field, name in (("outbound", "arrival", "杭州"), ("return", "departure", "杭州南")):
        option = doc[f"{key}_transport"]["options"][0]
        option[f"{field}_hub"] = None
        option[f"{field}_place"] = name
    doc["itinerary"]["days"][-1]["activities"] = []
    return doc


def result_place(doc, name):
    place = deepcopy(doc["lodging_plan"]["options"][0]["place"])
    place.update(name=name, category="transport", poi_id="real-provider-" + name)
    return {"place": place}


def test_selected_station_exact_match_and_empty_return_day_transfer():
    doc = missing_hubs()
    def search(query, destination, kind, manager):
        assert destination == "杭州" and kind == "transport"
        return [result_place(doc, "杭州东站"), result_place(doc, query + "-进站口"), result_place(doc, query)]
    with patch("services.transport_hub_service.search_editable_places", side_effect=search) as lookup:
        asyncio.run(enrich_selected_transport_hubs(doc, object()))
    assert [call.args[0] for call in lookup.call_args_list] == ["杭州站", "杭州南站"]
    assert doc["outbound_transport"]["options"][0]["arrival_hub"]["name"] == "杭州站"
    assert doc["return_transport"]["options"][0]["departure_hub"]["name"] == "杭州南站"
    assert [a["kind"] for a in doc["itinerary"]["days"][-1]["anchors"]] == ["lodging_departure", "departure_hub"]
    assert not doc["itinerary"]["days"][-1]["activities"]
    ids = {source["source_id"] for source in doc["sources"]}
    assert set(doc["return_transport"]["options"][0]["departure_hub"]["evidence_refs"]) <= ids


def test_no_exact_station_does_not_use_neighbor_or_city_center():
    doc = missing_hubs()
    with patch("services.transport_hub_service.search_editable_places", return_value=[result_place(doc, "杭州东站")]):
        asyncio.run(enrich_selected_transport_hubs(doc, object()))
    assert doc["return_transport"]["options"][0]["departure_hub"] is None
    assert doc["itinerary"]["days"][-1]["anchors"] == []
    assert doc["status"] == "degraded"
    assert len([i for i in doc["validation"]["issues"] if i["code"] == "TRANSPORT_HUB_UNVERIFIED"]) == 2


def test_transport_edit_only_queries_selected_direction_and_existing_hubs_are_reused():
    doc = missing_hubs()
    with patch("services.transport_hub_service.search_editable_places", return_value=[result_place(doc, "杭州南站")]) as lookup:
        asyncio.run(enrich_selected_transport_hubs(doc, object(), directions=("return",)))
        asyncio.run(enrich_selected_transport_hubs(doc, object(), directions=("return",)))
    assert lookup.call_count == 1
    assert doc["outbound_transport"]["options"][0]["arrival_hub"] is None


def test_flexible_dates_and_missing_selected_option_do_not_query_stations():
    doc = missing_hubs()
    with patch("services.transport_hub_service.search_editable_places") as lookup:
        doc["intent"]["date_mode"] = "flexible"
        asyncio.run(enrich_selected_transport_hubs(doc, object()))
        doc["intent"]["date_mode"] = "fixed"
        for key in ("outbound_transport", "return_transport"):
            doc[key]["selected_option_id"] = None
        asyncio.run(enrich_selected_transport_hubs(doc, object()))
    lookup.assert_not_called()
