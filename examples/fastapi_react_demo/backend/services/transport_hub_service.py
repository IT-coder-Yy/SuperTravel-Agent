"""从真实地点检索补齐所选班次在目的地的抵离站点。"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from hashlib import sha1
import re
from typing import Any, Iterable

from services.editable_place_service import search_editable_places


def _hub_name(name: str, mode: str) -> str:
    value = re.sub(r"\s+", "", name).casefold()
    return re.sub(r"(?:火车站|高铁站|铁路车站|站)$", "", value) if mode == "train" else value


async def enrich_selected_transport_hubs(
    document: dict, tool_manager: Any, *, directions: Iterable[str] = ("outbound", "return"),
) -> None:
    """只绑定准确站名；相邻车站、进出口与城市中心不能代替所选车站。"""
    if document["intent"]["date_mode"] != "fixed":
        return
    destination = document["intent"]["destination"]
    for direction in directions:
        section = document[f"{direction}_transport"]
        option = next((item for item in section["options"]
                       if item["option_id"] == section.get("selected_option_id")), None)
        if not option:
            continue
        field = "arrival_hub" if direction == "outbound" else "departure_hub"
        existing = option.get(field)
        if existing and existing.get("poi_id") and existing.get("coordinates"):
            continue
        name = str(option.get("arrival_place" if direction == "outbound" else "departure_place") or "").strip()
        if not name:
            continue
        mode = option["mode"]
        query = name + "站" if mode == "train" and not name.endswith("站") else name
        try:
            results = await asyncio.to_thread(search_editable_places, query, destination, "transport", tool_manager)
        except Exception:
            results = []
        place = next((row["place"] for row in results
                      if row.get("place", {}).get("poi_id") and row["place"].get("coordinates")
                      and _hub_name(row["place"]["name"], mode) == _hub_name(name, mode)), None)
        issues = document["validation"]["issues"]
        issues[:] = [item for item in issues if not (
            item.get("code") == "TRANSPORT_HUB_UNVERIFIED" and item.get("target_id") == option["option_id"])]
        if place is None:
            issues.append({"code": "TRANSPORT_HUB_UNVERIFIED", "severity": "warning",
                "target_id": option["option_id"], "message": f"{name}的准确站点坐标尚未核验，市内接驳待确认。"})
            document["status"] = "degraded"
            document["validation"]["degraded"] = True
            continue
        place = deepcopy(place)
        source_id = "src_hub_" + sha1(place["poi_id"].encode()).hexdigest()[:16]
        place["evidence_refs"] = [source_id]
        if not any(source["source_id"] == source_id for source in document["sources"]):
            document["sources"].append({"source_id": source_id, "title": f"{place['name']}站点位置",
                "source_name": "地图地点检索", "status": "map_reference",
                "related_fields": [f"{direction}_transport"], "related_place_ids": [place["poi_id"]]})
        option[field] = place
    from services.trip_edit_service import _v3_refresh_anchors
    _v3_refresh_anchors(document)
