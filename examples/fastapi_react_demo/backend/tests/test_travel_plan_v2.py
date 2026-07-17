import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
for path in (BACKEND_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from services.trip_product_service import export_trip_markdown, import_trip_document, validate_trip_document


def legacy_document():
    return {"schema_version": "1.0", "plan": {
        "plan_id": "p-v2", "version": 3, "title": "东京三日游",
        "intent": {"origin": "北京", "destination": "东京", "days": 3}, "days": 3,
        "trip_days": [{"day": 1, "activities": [{
            "activity_id": "a1", "day": 1, "title": "浅草寺", "activity_type": "attraction",
            "place": {"name": "浅草寺", "category": "景点", "poi_id": "poi-1", "lat": 35.7148, "lng": 139.7967},
        }]}],
    }}


def test_v1_migrates_to_complete_v2_without_fabricating_tickets():
    document = import_trip_document("json", legacy_document())
    assert document["schema_version"] == "2.0"
    assert document["plan_id"] == "p-v2"
    assert document["outbound_transport"]["status"] == "needs_date"
    assert document["return_transport"]["options"] == []
    assert document["map_guidance"]["location_ids"] == ["poi-1"]


def test_markdown_has_deterministic_eight_section_order():
    markdown = export_trip_markdown(legacy_document())
    headings = [
        "## 1. 目的地介绍", "## 2. 出发地到目的地的车票", "## 3. 酒店推荐",
        "## 4. 日程规划（景点与美食）", "## 5. 目的地返回出发地的车票",
        "## 6. 友情提醒", "## 7. 景点和地图提醒", "## 8. 下载与分享",
    ]
    indexes = [markdown.index(heading) for heading in headings]
    assert indexes == sorted(indexes)


def test_v2_rejects_map_locations_not_in_current_itinerary():
    document = import_trip_document("json", legacy_document())
    document.pop("plan", None)
    document.pop("imported_at", None)
    document["map_guidance"]["location_ids"] = ["unrelated-poi"]
    with pytest.raises(ValueError, match="结构无效"):
        validate_trip_document(document)
