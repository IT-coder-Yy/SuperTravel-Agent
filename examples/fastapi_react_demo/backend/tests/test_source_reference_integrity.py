"""来源引用不能因同页多图去重或字符串子串匹配而串到其它来源。"""

import copy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.chat_service import _travel_bundle_to_trip_plan
from backend.services.travel_document_service import _map_ref, adapt_v2_to_v3
from backend.tests.test_trip_v3_contract import build_legacy_v2_document


def source(reference_id, url="https://example.com/place"):
    return {
        "reference_id": reference_id,
        "type": "image",
        "title": "西湖实景图片",
        "source": "图片来源网站",
        "url": url,
        "related_fields": ["images"],
        "related_places": ["西湖"],
    }


def image(reference_id, index):
    return {
        "image_id": f"image_{index}",
        "url": f"https://example.com/photo-{index}.jpg",
        "alt": "西湖实景",
        "display_allowed": True,
        "export_allowed": True,
        "attribution_required": False,
        "provider_name": "图片来源网站",
        "provider_url": "https://example.com/place",
        "source_ref": reference_id,
    }


def test_unknown_hash_does_not_match_numeric_source_or_identifier_prefix():
    lookup = {"1": "official", "source_image_a": "other_image"}
    assert _map_ref("source_image_a98112", lookup) is None
    assert _map_ref("unrelated https://example.com/page", {"https://example.com": "root"}) is None
    assert _map_ref("1", lookup) == "official"
    assert _map_ref(1, lookup) == "official"
    assert _map_ref("source_image_a", lookup) == "other_image"


def test_same_page_distinct_image_references_survive_planning_and_v3_adaptation():
    references = ["source_image_1abc", "source_image_21def", "source_image_31f00"]
    image_sources = [source(reference) for reference in references]
    plan = _travel_bundle_to_trip_plan({
        "query": "杭州资料",
        "trip_intent": {"destination": "杭州", "days": 1},
        "activity_image_sources": image_sources + [copy.deepcopy(image_sources[0])],
    })
    kept = [item for item in plan.source_references if item.type == "image"]
    assert [item.reference_id for item in kept] == references
    payload = build_legacy_v2_document()
    payload["sources"] += [item.model_dump(mode="json") for item in kept]
    payload["itinerary"]["days"][0]["activities"][0]["place"]["image_assets"] = [
        image(reference, index) for index, reference in enumerate(references)
    ]
    document = adapt_v2_to_v3(payload)
    assets = document.itinerary.days[0].activities[0].images
    assert len(assets) == 3
    by_id = {item.source_id: item for item in document.sources}
    assert all(by_id[asset.source_ref].url == asset.provider_url for asset in assets)


def test_unresolved_image_reference_is_omitted_instead_of_attached_to_source_one():
    payload = build_legacy_v2_document()
    payload["itinerary"]["days"][0]["activities"][0]["place"]["image_assets"] = [
        image("source_image_31f00", 1)
    ]
    document = adapt_v2_to_v3(payload)
    assert document.itinerary.days[0].activities[0].images == []


def test_unidentified_duplicate_web_sources_still_deduplicate():
    row = {"title": "西湖介绍", "url": "https://example.com/place", "source": "网站"}
    plan = _travel_bundle_to_trip_plan({
        "query": "杭州资料",
        "trip_intent": {"destination": "杭州", "days": 1},
        "web_rows": [row, dict(row)],
    })
    assert len([item for item in plan.source_references if item.type == "web"]) == 1
