"""POI 搜索选择凭据：正式新增与替换只接纳服务端检索过的真实地点。"""
from copy import deepcopy
import time
import uuid
from typing import Any

_selections: dict[str, tuple[float, str, dict]] = {}


def remember_place(destination: str, place: dict) -> str:
    now = time.monotonic()
    for key, value in list(_selections.items()):
        if now - value[0] > 600:
            _selections.pop(key, None)
    token = str(uuid.uuid4())
    _selections[token] = (now, destination, deepcopy(place))
    while len(_selections) > 500:
        _selections.pop(next(iter(_selections)))
    return token


def resolve_place(token: Any, destination: str) -> dict:
    value = _selections.get(str(token))
    if value is None or time.monotonic() - value[0] > 600 or value[1] != destination:
        raise ValueError("地点选择已过期或不属于当前目的地，请重新搜索选择。")
    return deepcopy(value[2])


def search_editable_places(query: str, destination: str, kind: str, tool_manager: Any) -> list[dict]:
    from services import chat_service as c
    from schemas.trip_models import TripPlace
    from services.travel_document_service import _map_place
    session = f"poi-edit-{uuid.uuid4()}"
    rows = []
    for tool in c._order_map_provider_tools(c._available_tool_names(tool_manager,
        ["maps_text_search", "map_search_places", "map_place_search"]), destination):
        args = [{"keywords": query, "city": destination}] if tool == "maps_text_search" else [
            {"query": query, "region": destination, "count": 10}, {"query": query, "region": destination}]
        payload = c._run_tool_with_arg_candidates(tool_manager=tool_manager, tool_name=tool, message_history=[],
            session_id=session, arg_candidates=args, request_priority="candidate")
        rows.extend(c._normalize_map_locations(payload, max_rows=10, require_coordinates=True, source_tool=tool))
    rows = c._filter_quality_map_locations(rows, destination, require_positive_destination=True)
    if not rows:
        from services.international_place_search_service import search_international_places
        rows = search_international_places(query, destination, kind)
    results = []
    for row in rows[:10]:
        legacy = TripPlace.model_validate({**row, "name": row["name"], "poi_id": row.get("poi_id") or row.get("place_id") or row.get("id")})
        place = _map_place(legacy, kind, {})
        if place is not None and place.coordinates is not None:
            value = place.model_dump(mode="json")
            results.append({"selection_id": remember_place(destination, value), "place": value})
    return results
