import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from pypdf import PdfReader
from reportlab.platypus import Image as PdfImage, KeepTogether, Paragraph


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
for path in (BACKEND_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from backend.schemas.trip_v3_models import TravelPlanDocumentV3
from backend.services.pdf_export_service import (
    _is_public_host,
    _public_doh_addresses,
    export_travel_plan_v3_pdf,
    select_pdf_export_images,
    selected_pdf_image_assets,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_document() -> dict:
    return json.loads((FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8"))


def png_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (1600, 900), color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def exportable_image(image_id: str, url: str) -> dict:
    return {
        "image_id": image_id,
        "url": url,
        "alt": image_id,
        "display_allowed": True,
        "export_allowed": True,
        "attribution_required": False,
    }


class PdfExportServiceTests(unittest.TestCase):
    def tearDown(self):
        _public_doh_addresses.cache_clear()

    @patch("backend.services.pdf_export_service.httpx.get")
    @patch("backend.services.pdf_export_service.socket.getaddrinfo")
    def test_synthetic_egress_dns_requires_independent_public_resolution(self, getaddrinfo, httpx_get):
        getaddrinfo.return_value = [(2, 1, 6, "", ("198.18.0.179", 443))]
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.side_effect = [
            {"Status": 0, "Answer": [{"type": 1, "data": "104.18.1.1"}]},
            {"Status": 0, "Answer": []},
        ]
        httpx_get.return_value = response

        self.assertTrue(_is_public_host("images.example.com"))

    @patch("backend.services.pdf_export_service.httpx.get")
    @patch("backend.services.pdf_export_service.socket.getaddrinfo")
    def test_synthetic_egress_dns_rejects_private_doh_answer(self, getaddrinfo, httpx_get):
        getaddrinfo.return_value = [(2, 1, 6, "", ("198.18.0.179", 443))]
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.side_effect = [
            {"Status": 0, "Answer": [{"type": 1, "data": "10.0.0.8"}]},
            {"Status": 0, "Answer": []},
        ]
        httpx_get.return_value = response

        self.assertFalse(_is_public_host("internal.example.com"))

    @patch("backend.services.pdf_export_service.socket.getaddrinfo")
    def test_private_local_dns_is_rejected_without_doh_bypass(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]

        self.assertFalse(_is_public_host("localhost.example"))

    def test_pdf_selects_only_permitted_cover_and_formal_activity_images(self):
        payload = load_document()
        payload["destination_overview"]["cover_image"] = exportable_image(
            "cover_allowed", "https://assets.example.com/cover.png"
        )
        activity = payload["itinerary"]["days"][0]["activities"][0]
        activity["images"] = [
            exportable_image("activity_allowed", "https://assets.example.com/activity.png"),
            {
                **exportable_image("activity_denied", "https://assets.example.com/denied.png"),
                "export_allowed": False,
                "attribution_required": True,
            },
        ]
        activity["cover_image_id"] = "activity_allowed"
        payload["candidate_pool"][0]["images"] = [
            exportable_image("candidate_disallowed", "https://assets.example.com/candidate.png")
        ]
        payload["candidate_pool"][0]["cover_image_id"] = "candidate_disallowed"
        document = TravelPlanDocumentV3.model_validate(payload)

        selection = select_pdf_export_images(document)
        self.assertEqual("cover_allowed", selection.cover.image_id)
        self.assertEqual("activity_allowed", selection.activities[activity["activity_id"]].image_id)
        self.assertEqual(
            ["cover_allowed", "activity_allowed"],
            [image.image_id for image in selected_pdf_image_assets(document)],
        )

        pdf = export_travel_plan_v3_pdf(
            document,
            {
                "cover_allowed": png_bytes((98, 80, 210)),
                "activity_allowed": png_bytes((14, 116, 144)),
                "candidate_disallowed": png_bytes((225, 29, 72)),
            },
        )
        reader = PdfReader(BytesIO(pdf))
        self.assertGreaterEqual(len(reader.pages), 2)
        self.assertEqual(document.title, reader.metadata.title)
        image_count = 0
        for page in reader.pages:
            xobjects = page.get("/Resources", {}).get("/XObject", {})
            image_count += sum(
                1
                for item in xobjects.values()
                if item.get_object().get("/Subtype") == "/Image"
            )
        self.assertGreaterEqual(image_count, 2)

    def test_pdf_omits_unavailable_images_without_blocking_text_export(self):
        document = TravelPlanDocumentV3.model_validate(load_document())

        pdf = export_travel_plan_v3_pdf(document, {})

        reader = PdfReader(BytesIO(pdf))
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreaterEqual(len(reader.pages), 2)
        self.assertEqual(document.title, reader.metadata.title)

    def test_activity_images_follow_activity_identity_not_title_substrings(self):
        payload = load_document()
        for day in payload["itinerary"]["days"]:
            for activity in day["activities"]:
                activity["images"] = []
                activity["cover_image_id"] = None
        payload["destination_overview"]["cover_image"] = None
        first = payload["itinerary"]["days"][0]["activities"][0]
        second = payload["itinerary"]["days"][1]["activities"][0]
        first["title"] = "午餐：新刘记(河坊街店)"
        second["title"] = "河坊街"
        second["images"] = [exportable_image("street", "https://assets.example.com/street.png")]
        second["cover_image_id"] = "street"
        day = payload["itinerary"]["days"][1]
        day["note_id"] = "pdf_day_note"
        payload["notes"].append({
            "note_id": "pdf_day_note", "scope": "day", "target_id": str(day["day"]),
            "content": "先确认集合时间，随后去河坊街。",
            "updated_at": payload["generated_at"],
        })
        document = TravelPlanDocumentV3.model_validate(payload)
        with patch("backend.services.pdf_export_service.SimpleDocTemplate.build") as build:
            export_travel_plan_v3_pdf(document, {"street": png_bytes((14, 116, 144))})
        story = build.call_args.args[0]
        preceding_text = None
        image_owners = []
        flattened = [child for item in story for child in (
            item._content if isinstance(item, KeepTogether) else [item]
        )]
        self.assertTrue(any(isinstance(item, KeepTogether) for item in story))
        for flowable in flattened:
            if isinstance(flowable, Paragraph):
                preceding_text = flowable.getPlainText()
            elif isinstance(flowable, PdfImage):
                image_owners.append(preceding_text)
        self.assertEqual(1, len(image_owners))
        self.assertTrue(image_owners[0].startswith("· 河坊街（"), image_owners)


if __name__ == "__main__":
    unittest.main()
