"""Generate a safe, printable PDF from one validated formal V3 revision."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from io import BytesIO
import ipaddress
from pathlib import Path
import socket
from typing import Dict, Iterable, Mapping, Optional
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image as PilImage
from PIL import ImageOps
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer

from backend.schemas.trip_v3_models import ImageAssetV3, TravelPlanDocumentV3, TripActivityV3
from backend.services.travel_document_service import export_travel_plan_v3_plain_markdown


PDF_FONT_NAME = "SuperTravelAgentCJK"
PDF_IMAGE_WIDTH = 150 * mm
PDF_IMAGE_HEIGHT = 112.5 * mm
MAX_IMAGE_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 6 * 1024 * 1024
PDF_IMAGE_TIMEOUT_SECONDS = 12.0
MAX_IMAGE_REDIRECTS = 3
SYNTHETIC_EGRESS_NETWORK = ipaddress.ip_network("198.18.0.0/15")
CLOUDFLARE_DOH_URL = "https://cloudflare-dns.com/dns-query"


@dataclass(frozen=True)
class PdfImageSelection:
    cover: Optional[ImageAssetV3]
    activities: Dict[str, ImageAssetV3]


def _ensure_chinese_font() -> str:
    """Register a locally installed Chinese font, with a CID fallback for CI."""

    if PDF_FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return PDF_FONT_NAME
    for font_path in (
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\msyh.ttf"),
    ):
        if font_path.is_file():
            pdfmetrics.registerFont(TTFont(PDF_FONT_NAME, str(font_path)))
            return PDF_FONT_NAME
    fallback = "STSong-Light"
    if fallback not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(fallback))
    return fallback


def _is_exportable_image(image: Optional[ImageAssetV3]) -> bool:
    if image is None or not image.display_allowed or not image.export_allowed or image.attribution_required:
        return False
    parsed = urlsplit(image.url)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def _activity_cover_image(activity: TripActivityV3) -> Optional[ImageAssetV3]:
    images = [image for image in activity.images if _is_exportable_image(image)]
    if not images:
        return None
    if activity.cover_image_id:
        return next((image for image in images if image.image_id == activity.cover_image_id), images[0])
    return images[0]


def select_pdf_export_images(document: TravelPlanDocumentV3) -> PdfImageSelection:
    """Select at most one explicitly exportable image for the cover and each formal activity."""

    cover = document.destination_overview.cover_image
    activity_images = {
        activity.activity_id: image
        for day in document.itinerary.days
        for activity in day.activities
        if (image := _activity_cover_image(activity)) is not None
    }
    return PdfImageSelection(
        cover=cover if _is_exportable_image(cover) else None,
        activities=activity_images,
    )


def selected_pdf_image_assets(document: TravelPlanDocumentV3) -> Iterable[ImageAssetV3]:
    """Return distinct assets selected for this PDF, preserving document order."""

    selection = select_pdf_export_images(document)
    seen: set[str] = set()
    for image in (selection.cover, *selection.activities.values()):
        if image is not None and image.image_id not in seen:
            seen.add(image.image_id)
            yield image


def _is_global_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return False


def _is_synthetic_egress_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.version == 4 and address in SYNTHETIC_EGRESS_NETWORK


@lru_cache(maxsize=256)
def _public_doh_addresses(hostname: str) -> tuple[str, ...]:
    """Resolve through a fixed trusted DoH endpoint when local DNS is synthetic."""

    addresses: set[str] = set()
    try:
        for record_type in ("A", "AAAA"):
            response = httpx.get(
                CLOUDFLARE_DOH_URL,
                params={"name": hostname, "type": record_type},
                headers={"Accept": "application/dns-json"},
                timeout=5.0,
                follow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("Status") != 0:
                continue
            for answer in payload.get("Answer") or []:
                if not isinstance(answer, dict) or answer.get("type") not in {1, 28}:
                    continue
                value = str(answer.get("data") or "").strip()
                try:
                    ipaddress.ip_address(value)
                except ValueError:
                    continue
                addresses.add(value)
    except (httpx.HTTPError, TypeError, ValueError):
        return ()
    return tuple(sorted(addresses))


def _is_public_host(hostname: str) -> bool:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        return literal.is_global
    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    if not addresses:
        return False
    local_addresses = {str(address[4][0]) for address in addresses}
    if local_addresses and all(_is_global_ip(address) for address in local_addresses):
        return True
    if not local_addresses or not all(_is_synthetic_egress_ip(address) for address in local_addresses):
        return False
    public_addresses = _public_doh_addresses(hostname)
    return bool(public_addresses) and all(_is_global_ip(address) for address in public_addresses)


async def _fetch_exportable_image(client: httpx.AsyncClient, image: ImageAssetV3) -> Optional[bytes]:
    if not _is_exportable_image(image):
        return None
    current_url = image.url
    for redirect_count in range(MAX_IMAGE_REDIRECTS + 1):
        parsed = urlsplit(current_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or not await asyncio.to_thread(_is_public_host, parsed.hostname)
        ):
            return None
        try:
            async with client.stream("GET", current_url, follow_redirects=False) as response:
                if response.is_redirect:
                    if redirect_count >= MAX_IMAGE_REDIRECTS:
                        return None
                    location = response.headers.get("location", "").strip()
                    if not location:
                        return None
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                if not response.headers.get("content-type", "").lower().startswith("image/"):
                    return None
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > MAX_IMAGE_DOWNLOAD_BYTES:
                    return None
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_IMAGE_DOWNLOAD_BYTES:
                        return None
        except (httpx.HTTPError, TypeError, ValueError):
            return None
        return _normalise_pdf_image(bytes(content))
    return None


def _normalise_pdf_image(content: bytes) -> Optional[bytes]:
    """Produce a bounded 4:3 JPEG so one remote image cannot bloat the export."""

    try:
        with PilImage.open(BytesIO(content)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image = ImageOps.fit(image, (1200, 900), method=PilImage.Resampling.LANCZOS)
            result = BytesIO()
            image.save(result, format="JPEG", quality=82, optimize=True)
    except (OSError, ValueError):
        return None
    return result.getvalue()


async def fetch_pdf_export_images(document: TravelPlanDocumentV3) -> Dict[str, bytes]:
    """Fetch permitted public HTTPS images only; failures intentionally omit the image."""

    assets = list(selected_pdf_image_assets(document))
    if not assets:
        return {}
    timeout = httpx.Timeout(PDF_IMAGE_TIMEOUT_SECONDS, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"Accept": "image/*"}) as client:
        results = await asyncio.gather(*[_fetch_exportable_image(client, image) for image in assets])
    image_data: Dict[str, bytes] = {}
    total = 0
    for image, content in zip(assets, results):
        if content is None or total + len(content) > MAX_TOTAL_IMAGE_BYTES:
            continue
        image_data[image.image_id] = content
        total += len(content)
    return image_data


def _build_image_flowable(
    content: Optional[bytes],
    *,
    width: float = PDF_IMAGE_WIDTH,
    height: float = PDF_IMAGE_HEIGHT,
) -> Optional[Image]:
    if not content:
        return None
    try:
        image = Image(BytesIO(content))
        image.drawWidth = width
        image.drawHeight = height
        image.hAlign = "CENTER"
        return image
    except (OSError, ValueError):
        return None


def _draw_page_chrome(page_canvas, page_number: int) -> None:
    font_name = _ensure_chinese_font()
    page_canvas.saveState()
    page_canvas.setStrokeColor(HexColor("#E2E8F0"))
    page_canvas.line(22 * mm, A4[1] - 15 * mm, A4[0] - 22 * mm, A4[1] - 15 * mm)
    page_canvas.setFillColor(HexColor("#64748B"))
    page_canvas.setFont(font_name, 8)
    page_canvas.drawString(22 * mm, A4[1] - 11 * mm, "SuperTravelAgent · 已保存旅行方案")
    page_canvas.drawRightString(A4[0] - 22 * mm, 11 * mm, f"第 {page_number} 页")
    page_canvas.restoreState()


def _page_chrome(page_canvas, doc) -> None:
    _draw_page_chrome(page_canvas, doc.page)


def export_travel_plan_v3_pdf(
    document: TravelPlanDocumentV3,
    image_data: Optional[Mapping[str, bytes]] = None,
) -> bytes:
    """Render the same accepted content as Markdown, with permitted images when available."""

    font_name = _ensure_chinese_font()
    images = dict(image_data or {})
    selection = select_pdf_export_images(document)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TravelPdfTitle", parent=styles["Title"], fontName=font_name, fontSize=24,
        leading=32, alignment=TA_CENTER, textColor=HexColor("#1E1B4B"), spaceAfter=12,
    )
    subtitle_style = ParagraphStyle(
        "TravelPdfSubtitle", parent=styles["BodyText"], fontName=font_name, fontSize=11,
        leading=18, alignment=TA_CENTER, textColor=HexColor("#475569"), spaceAfter=18,
    )
    heading_style = ParagraphStyle(
        "TravelPdfHeading", parent=styles["Heading2"], fontName=font_name, fontSize=16,
        leading=24, textColor=HexColor("#312E81"), spaceBefore=14, spaceAfter=8,
    )
    subheading_style = ParagraphStyle(
        "TravelPdfSubheading", parent=styles["Heading3"], fontName=font_name, fontSize=12,
        leading=18, textColor=HexColor("#4338CA"), spaceBefore=10, spaceAfter=5,
    )
    body_style = ParagraphStyle(
        "TravelPdfBody", parent=styles["BodyText"], fontName=font_name, fontSize=10.5,
        leading=17, textColor=HexColor("#1F2937"), alignment=TA_LEFT, spaceAfter=4,
        wordWrap="CJK",
    )
    bullet_style = ParagraphStyle(
        "TravelPdfBullet", parent=body_style, leftIndent=12, firstLineIndent=-8, spaceAfter=4,
        wordWrap="CJK",
    )
    document_buffer = BytesIO()
    pdf = SimpleDocTemplate(
        document_buffer,
        pagesize=A4,
        leftMargin=22 * mm,
        rightMargin=22 * mm,
        topMargin=24 * mm,
        bottomMargin=20 * mm,
        title=document.title,
        author="SuperTravelAgent",
    )
    story = [
        Spacer(1, 20 * mm),
        Paragraph(escape(document.title), title_style),
        Paragraph(
            escape(f"{document.intent.origin} → {document.intent.destination} · 已保存旅行方案"),
            subtitle_style,
        ),
    ]
    if cover_image := _build_image_flowable(images.get(selection.cover.image_id) if selection.cover else None):
        story.extend([cover_image, Spacer(1, 10 * mm)])
    # Start formal content on a fresh page; the frame reserves room for its header.
    story.extend([PageBreak(), Spacer(1, 12 * mm)])

    itinerary_days = iter(document.itinerary.days)
    activity_image_queue = []
    in_itinerary = False
    pending_day_note = False
    note_by_id = {note.note_id: note for note in document.notes}
    pending_headings = []

    def append_content(*flowables) -> None:
        nonlocal pending_headings
        if pending_headings and flowables and isinstance(flowables[0], KeepTogether):
            # A nested image group can break ReportLab's keepWithNext chain.
            # Include section/day headings in that same indivisible group.
            flowables = (
                KeepTogether([*pending_headings, *flowables[0]._content]),
                *flowables[1:],
            )
        else:
            story.extend(pending_headings)
        pending_headings = []
        story.extend(flowables)

    for line in export_travel_plan_v3_plain_markdown(document).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("# "):
            if not stripped:
                if not pending_headings:
                    story.append(Spacer(1, 3 * mm))
            continue
        if stripped.startswith("## "):
            in_itinerary = stripped == "## 分日行程"
            if stripped == "## 分日行程":
                story.append(PageBreak())
            pending_headings.append(Paragraph(escape(stripped[3:]), heading_style))
            continue
        if stripped.startswith("### "):
            if in_itinerary:
                day = next(itinerary_days, None)
                day_note = note_by_id.get(day.note_id) if day else None
                pending_day_note = bool(day_note and day_note.content)
                activity_image_queue = [
                    selection.activities.get(activity.activity_id)
                    for activity in day.activities
                ] if day else []
            pending_headings.append(Paragraph(escape(stripped[4:]), subheading_style))
            continue
        if stripped.startswith("- "):
            content = stripped[2:]
            # The bundled Windows CJK fonts do not consistently contain U+2022.
            # U+00B7 is already used elsewhere in this PDF and renders reliably.
            flowables = [Paragraph(f"· {escape(content)}", bullet_style)]
            if in_itinerary and line.startswith("- ") and pending_day_note:
                pending_day_note = False
                append_content(*flowables)
                continue
            # Only top-level itinerary bullets describe activities. Consume every
            # activity, including missing images; names can overlap or repeat.
            if in_itinerary and line.startswith("- ") and activity_image_queue:
                asset = activity_image_queue.pop(0)
                if asset and (image := _build_image_flowable(
                    images.get(asset.image_id), width=115 * mm, height=86.25 * mm,
                )):
                    flowables = [KeepTogether([
                        *flowables, Spacer(1, 2 * mm), image, Spacer(1, 4 * mm),
                    ])]
            append_content(*flowables)
            continue
        append_content(Paragraph(escape(stripped), body_style))
    if pending_headings:
        story.extend(pending_headings)
    pdf.build(story, onFirstPage=_page_chrome, onLaterPages=_page_chrome)
    return document_buffer.getvalue()
