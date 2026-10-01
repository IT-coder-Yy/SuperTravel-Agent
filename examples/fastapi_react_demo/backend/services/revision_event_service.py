"""为已提交的正式版本生成可重连、可去重的完整 SSE 修订流。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple

try:
    from backend.schemas.trip_v3_models import (
        PlanCompletedPayload,
        PlanRevisionStartedPayload,
        PlanSectionPayload,
        PlanningEventEnvelope,
        TravelPlanDocumentV3,
    )
    from backend.services.planning_orchestrator import encode_sse
except ModuleNotFoundError:  # Runtime entrypoint executes from backend/.
    from schemas.trip_v3_models import (
        PlanCompletedPayload,
        PlanRevisionStartedPayload,
        PlanSectionPayload,
        PlanningEventEnvelope,
        TravelPlanDocumentV3,
    )
    from services.planning_orchestrator import encode_sse


def _document_sections(document: TravelPlanDocumentV3) -> List[Tuple[str, Dict[str, Any]]]:
    payload = document.model_dump(mode="json")
    return [
        ("destination_overview", {
            "title": payload["title"],
            "intent": payload["intent"],
            "destination_overview": payload["destination_overview"],
        }),
        ("outbound_transport", {
            "outbound_transport": payload["outbound_transport"],
            "return_transport": payload["return_transport"],
        }),
        ("lodging_plan", payload["lodging_plan"]),
        ("itinerary", payload["itinerary"]),
        ("budget_and_reminders", {
            "budget": payload["budget"],
            "candidate_pool": payload["candidate_pool"],
            "action_items": payload["action_items"],
            "notes": payload["notes"],
        }),
        ("map_guidance", payload["map_guidance"]),
        ("delivery", payload["delivery"]),
        ("sources", {"sources": payload["sources"]}),
    ]


def build_revision_events(
    document_value: Dict[str, Any],
    *,
    operation_id: str,
    checksum: str,
    occurred_at: datetime | None = None,
) -> List[Dict[str, Any]]:
    """事件内容和编号只由回执决定，客户端断线后可安全按 sequence 续传。"""
    document = TravelPlanDocumentV3.model_validate(document_value)
    run_id = f"revision:{document.plan_id}:{operation_id}"
    event_time = occurred_at or datetime.now(timezone.utc)
    sequence = 0

    def envelope(event_type: str, payload: Any) -> Dict[str, Any]:
        nonlocal sequence
        sequence += 1
        return PlanningEventEnvelope(
            event_id=f"evt_{run_id}_{sequence}",
            run_id=run_id,
            request_id=operation_id,
            sequence=sequence,
            occurred_at=event_time,
            type=event_type,
            payload=payload,
        ).model_dump(mode="json")

    events = [
        envelope(
            "plan_revision_started",
            PlanRevisionStartedPayload(
                plan_id=document.plan_id,
                revision=document.revision,
                operation_id=operation_id,
            ),
        ),
    ]
    for section, content in _document_sections(document):
        events.append(
            envelope(
                "plan_revision_section",
                PlanSectionPayload(
                    plan_id=document.plan_id,
                    revision=document.revision,
                    section=section,
                    content=content,
                ),
            )
        )
    events.append(
        envelope(
            "plan_revision_completed",
            PlanCompletedPayload(
                plan_id=document.plan_id,
                revision=document.revision,
                status="completed_degraded" if document.status == "degraded" else "completed",
                checksum=checksum,
            ),
        )
    )
    sequence += 1
    events.append({
        "event_id": f"evt_{run_id}_{sequence}",
        "run_id": run_id,
        "request_id": operation_id,
        "sequence": sequence,
        "occurred_at": event_time.isoformat(),
        "type": "trip_plan",
        "version": document.revision,
        "document": document.model_dump(mode="json"),
        "plan": {},
    })
    return events


def encode_revision_event_stream(
    events: Iterable[Dict[str, Any]],
    *,
    after_sequence: int = 0,
) -> Iterable[str]:
    cursor = max(0, int(after_sequence))
    for event in events:
        if int(event.get("sequence") or 0) > cursor:
            yield encode_sse(event)
