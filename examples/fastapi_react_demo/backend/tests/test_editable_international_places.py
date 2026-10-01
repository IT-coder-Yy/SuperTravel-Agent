from copy import deepcopy
from pathlib import Path
import sys
from unittest.mock import Mock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parents[4]))
from services import editable_place_service as editable
from services import international_place_search_service as international


def osm_place():
    return {"osm_type": "way", "osm_id": 173154847, "lat": "35.7134032", "lon": "139.7955265",
            "category": "amenity", "type": "place_of_worship", "name": "淺草寺",
            "display_name": "淺草寺, Asakusa, 臺東區, 东京都/東京都, 日本",
            "address": {"city": "臺東區", "country_code": "jp"},
            "namedetails": {"name": "浅草寺", "name:zh": "淺草寺"},
            "extratags": {"opening_hours": "Mo-Su 06:00-17:00", "tourism": "attraction"}}


@pytest.fixture(autouse=True)
def clear_cache():
    international._fetch_places.cache_clear()
    editable._selections.clear()
    with patch.object(international.time, "sleep"):
        yield
    international._fetch_places.cache_clear()
    editable._selections.clear()


def test_real_provider_shape_retains_coordinates_identity_and_source_with_cached_search():
    response = Mock()
    response.json.return_value = [osm_place(), osm_place()]
    with patch.object(international.requests, "get", return_value=response) as get:
        result = international.search_international_places("浅草寺", "东京", "attraction")
        again = international.search_international_places("浅草寺", "Tokyo", "attraction")
    assert len(result) == 1 and result == again
    assert result[0]["poi_id"] == "osm_way_173154847"
    assert result[0]["lat"] == 35.7134032 and result[0]["lng"] == 139.7955265
    assert result[0]["country_code"] == "JP" and result[0]["coordinate_system"] == "WGS84"
    assert result[0]["source_url"] == "https://www.openstreetmap.org/way/173154847"
    assert result[0]["name"] == "浅草寺"
    assert result[0]["opening_hours"] == "Mo-Su 06:00-17:00"
    get.assert_called_once()
    assert get.call_args.kwargs["params"]["countrycodes"] == "jp"


@pytest.mark.parametrize("replacement", [
    {"address": {"city": "Tokyo", "country_code": "kr"}},
    {"address": {"city": "Yokohama", "country_code": "jp"}, "display_name": "Tokyo Cafe, Yokohama, Japan"},
    {"lat": "34.6937", "lon": "135.5023"},
    {"lat": "NaN"}, {"lon": "Infinity"}, {"lat": None},
    {"osm_type": "administrative"}, {"osm_id": None},
    {"type": "restaurant", "extratags": {}},
    {"extratags": {"disused": "yes"}},
])
def test_rejects_wrong_destination_invalid_coordinate_identity_and_wrong_kind(replacement):
    raw = {**osm_place(), **replacement}
    with patch.object(international, "_fetch_places", return_value=(raw,)):
        assert international.search_international_places("浅草寺", "东京", "attraction") == []


def test_failed_request_can_retry_and_domestic_search_does_not_call_osm():
    response = Mock()
    response.json.return_value = [osm_place()]
    with patch.object(international.requests, "get", side_effect=[requests.Timeout(), response]) as get:
        assert international.search_international_places("浅草寺", "东京", "attraction") == []
        assert international.search_international_places("浅草寺", "东京", "attraction")
        assert international.search_international_places("西湖", "杭州", "attraction") == []
        assert get.call_count == 2


def test_hotel_lookup_returns_production_provider_and_destination_evidence():
    raw = {**osm_place(), "osm_id": 234567, "category": "tourism", "type": "hotel",
           "name": "Tokyo Test Hotel", "namedetails": {}, "extratags": {}}
    with patch.object(international, "_fetch_places", return_value=(raw,)):
        items = international.search_international_places("hotel", "东京", "hotel")
    assert len(items) == 1
    hotel = items[0]
    assert hotel["id"] == hotel["poi_id"] == hotel["place_id"] == hotel["provider_place_id"] == "osm_way_234567"
    assert hotel["requested_destination"] == "东京" and hotel["destination_bound"]
    assert hotel["provider_coordinate_system"] == "WGS84"
    assert hotel["source_reference_id"] in hotel["evidence_refs"]
    assert hotel["field_evidence"]["coordinates"]["source_reference_id"] == hotel["source_reference_id"]


def test_edit_search_uses_international_fallback_and_destination_bound_expiring_token():
    from services import chat_service as chat
    with patch.object(chat, "_available_tool_names", return_value=[]), patch.object(international, "_fetch_places", return_value=(osm_place(),)):
        result = editable.search_editable_places("浅草寺", "东京", "attraction", Mock())
    assert len(result) == 1
    place = editable.resolve_place(result[0]["selection_id"], "东京")
    assert place["coordinates"] == {"latitude": 35.7134032, "longitude": 139.7955265, "coordinate_system": "WGS84"}
    assert "https://www.openstreetmap.org/way/173154847" in place["summary"]
    place["name"] = "客户端篡改"
    assert editable.resolve_place(result[0]["selection_id"], "东京")["name"] == "浅草寺"
    with pytest.raises(ValueError):
        editable.resolve_place(result[0]["selection_id"], "京都")
    with patch.object(editable.time, "monotonic", return_value=editable.time.monotonic() + 601):
        with pytest.raises(ValueError):
            editable.resolve_place(result[0]["selection_id"], "东京")


def test_existing_domestic_provider_result_keeps_original_search_path():
    from services import chat_service as chat
    row = {"name": "西湖", "category": "attraction", "poi_id": "provider_xihu", "city": "杭州",
           "lat": 30.25, "lng": 120.14, "coordinate_system": "BD09LL"}
    with patch.object(chat, "_available_tool_names", return_value=["map_search_places"]), \
         patch.object(chat, "_run_tool_with_arg_candidates", return_value={}), \
         patch.object(chat, "_normalize_map_locations", return_value=[deepcopy(row)]), \
         patch.object(international, "search_international_places") as fallback:
        result = editable.search_editable_places("西湖", "杭州", "attraction", Mock())
    fallback.assert_not_called()
    assert result[0]["place"]["poi_id"] == "provider_xihu"
    assert result[0]["place"]["coordinates"]["coordinate_system"] == "BD09LL"
