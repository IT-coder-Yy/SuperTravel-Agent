"""Regressions from the live Hangzhou export, independent of network providers."""

import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
for path in (BACKEND_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from backend.schemas.trip_v3_models import TravelPlanDocumentV3
from backend.services.chat_service import _build_destination_overview_summary
from backend.services.travel_document_service import (
    export_travel_plan_v3_markdown,
    export_travel_plan_v3_plain_markdown,
)


@pytest.mark.parametrize("source", ["selected", "rag", "web"])
def test_destination_description_does_not_promise_generic_three_day_schedule(source):
    guide = (
        "杭州旅游攻略建议安排2到3天弹性行程。"
        "杭州以西湖山水和茶文化闻名。"
        "可优先围绕城区地标组织路线：第一天看城区核心景点，"
        "第二天串联近郊，第三天用于慢游、美食和周边补充。"
        "旺季提前核对门票、开放时间和交通接驳。"
    )
    bundle = {
        "selected": {"selected_knowledge_context": [{"snippet": guide}]},
        "rag": {"rag_context": guide},
        "web": {"web_rows": [{"snippet": guide}]},
    }[source]
    summary = _build_destination_overview_summary(bundle)
    assert summary == "杭州以西湖山水和茶文化闻名。旺季提前核对门票、开放时间和交通接驳。"


def test_schedule_only_research_falls_through_to_destination_facts():
    summary = _build_destination_overview_summary({
        "selected_knowledge_context": [{"snippet": "Day 1: visit the lake. Day 2: museums."}],
        "web_rows": [{"snippet": "杭州拥有三面云山一面城的湖山景观。"}],
    })
    assert summary == "杭州拥有三面云山一面城的湖山景观。"


@pytest.mark.parametrize("renderer", [export_travel_plan_v3_markdown, export_travel_plan_v3_plain_markdown])
def test_zero_travelers_have_no_unknown_cost_line_but_present_travelers_keep_it(renderer):
    payload = json.loads((Path(__file__).parent / "fixtures/trip_v3_domestic_3d.json").read_text(encoding="utf-8"))
    payload["budget"]["traveler_costs"] = [
        {"traveler_type": "adult", "count": 2, "pricing_status": "standard_price"},
        {"traveler_type": "child", "count": 0, "pricing_status": "not_applicable"},
        {"traveler_type": "senior", "count": 0, "pricing_status": "not_applicable"},
    ]
    document = TravelPlanDocumentV3.model_validate(payload)
    text = renderer(document)
    assert "- 成人 2 人：费用待确认" in text
    assert "- 儿童 0 人：" not in text
    assert "- 老人 0 人：" not in text
    assert "儿童 0、老人 0" in text  # The trip's traveler composition remains explicit.
    assert len(document.budget.traveler_costs) == 3

    payload["intent"]["travelers"]["children"] = 1
    payload["budget"]["traveler_costs"][1]["count"] = 1
    text = renderer(TravelPlanDocumentV3.model_validate(payload))
    assert "- 儿童 1 人：费用待确认" in text
