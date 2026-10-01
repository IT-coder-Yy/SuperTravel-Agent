import json
from io import BytesIO
from pathlib import Path
import re
import sys

import pytest
from pypdf import PdfReader

for directory in (Path(__file__).resolve().parents[4], Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(directory))

from backend.schemas.trip_v3_models import TravelPlanDocumentV3
from backend.services.chat_service import _transport_fallback_guidance
from backend.services.pdf_export_service import export_travel_plan_v3_pdf
from backend.services.ticket_search_service import transport_section_from_bundle
from backend.services.travel_document_service import export_travel_plan_v3_plain_markdown


@pytest.mark.parametrize("origin,destination", [("上海", "东京"), ("巴黎", "伦敦"), ("上海", "杭州")])
def test_unknown_route_does_not_invent_available_modes(origin, destination):
    bundle = {"trip_intent": {"origin": origin, "destination": destination}}
    for direction, route in (("outbound", f"{origin} → {destination}"), ("return", f"{destination} → {origin}")):
        text = _transport_fallback_guidance(bundle, direction)
        assert route in text
        assert "官方交通查询" in text and "接驳" in text
        assert not any(mode in text for mode in ("高铁", "航班", "长途客运", "席别"))


@pytest.mark.parametrize("bundle", [None, {"flight_rows": []}])
def test_international_ticket_default_is_neutral_when_results_are_missing(bundle):
    section = transport_section_from_bundle(bundle, "outbound", "international", "2026-10-03")
    assert section["options"] == []
    assert "官方交通查询" in section["status_reason"]
    assert "接驳" in section["status_reason"]
    assert "车次" not in section["status_reason"]


def test_missing_origin_omits_incomplete_route():
    text = _transport_fallback_guidance({"destination_city": "东京"}, "outbound")
    assert "→" not in text
    assert "官方交通查询" in text


def test_verified_flight_candidate_does_not_receive_unavailable_guidance():
    section = transport_section_from_bundle({
        "flight_source": "official api",
        "flight_rows": [{"trip_no": "QA123", "route": "甲地 -> 乙地",
                         "depart": "2026-10-03 08:00", "arrive": "2026-10-03 10:00"}],
    }, "outbound", "international", "2026-10-03")
    assert section["status"] == "ready"
    assert section["status_reason"] is None
    assert section["options"][0]["mode"] == "flight"


def test_new_guidance_reaches_markdown_and_pdf_without_a_separate_template():
    payload = json.loads((Path(__file__).parent / "fixtures/trip_v3_international_5d.json").read_text(encoding="utf-8"))
    bundle = {"trip_intent": {"origin": "上海", "destination": "东京"}}
    texts = []
    for direction in ("outbound", "return"):
        reason = _transport_fallback_guidance(bundle, direction)
        payload[f"{direction}_transport"].update(status_reason=reason)
        texts.append(reason)
    doc = TravelPlanDocumentV3.model_validate(payload)
    markdown = export_travel_plan_v3_plain_markdown(doc)
    pdf = PdfReader(BytesIO(export_travel_plan_v3_pdf(doc)))
    pdf_text = re.sub(r"\s+", "", "\n".join(page.extract_text() for page in pdf.pages))
    for reason in texts:
        assert reason in markdown
        assert re.sub(r"\s+", "", reason) in pdf_text
    assert "比较高铁" not in markdown
    assert "长途客运" not in pdf_text
