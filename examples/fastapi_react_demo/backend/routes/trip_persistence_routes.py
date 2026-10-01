from typing import Any, Callable

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

try:
    from schemas.trip_persistence_models import (
        DraftFormalSnapshotApplyRequest,
        FormalSnapshotApplyRequest,
        FormalSnapshotRestoreRequest,
        LegacyTripMigrationRequest,
        TripDraftRequest,
        TripUpsertRequest,
    )
    from schemas.trip_v3_models import TravelPlanDocumentV3
    from services.anonymous_device_service import (
        DEVICE_COOKIE_NAME,
        AnonymousDeviceIdentity,
        resolve_anonymous_device,
        set_anonymous_device_cookie,
    )
    from services.trip_repository import (
        ActivePlanningRunError,
        RevisionConflictError,
        TripNotFoundError,
        TripRepositoryError,
        UNSET,
    )
    from services.revision_event_service import (
        build_revision_events,
        encode_revision_event_stream,
    )
except ModuleNotFoundError:  # Package-style imports used by tests and ``python -m``.
    from backend.schemas.trip_persistence_models import (
        DraftFormalSnapshotApplyRequest,
        FormalSnapshotApplyRequest,
        FormalSnapshotRestoreRequest,
        LegacyTripMigrationRequest,
        TripDraftRequest,
        TripUpsertRequest,
    )
    from backend.schemas.trip_v3_models import TravelPlanDocumentV3
    from backend.services.anonymous_device_service import (
        DEVICE_COOKIE_NAME,
        AnonymousDeviceIdentity,
        resolve_anonymous_device,
        set_anonymous_device_cookie,
    )
    from backend.services.trip_repository import (
        ActivePlanningRunError,
        RevisionConflictError,
        TripNotFoundError,
        TripRepositoryError,
        UNSET,
    )
    from backend.services.revision_event_service import (
        build_revision_events,
        encode_revision_event_stream,
    )


def _repository_or_503(repository_getter: Callable[[], Any]):
    repository = repository_getter()
    if repository is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "TRIP_STORAGE_UNAVAILABLE", "message": "旅程存储暂不可用"},
        )
    return repository


def _device(
    request: Request,
    response: Response,
    repository_getter: Callable[[], Any],
) -> AnonymousDeviceIdentity:
    repository = _repository_or_503(repository_getter)
    identity = resolve_anonymous_device(
        repository,
        request.cookies.get(DEVICE_COOKIE_NAME),
    )
    set_anonymous_device_cookie(
        response,
        identity,
        secure=request.url.scheme == "https",
    )
    return identity


def _raise_repository_http_error(error: Exception) -> None:
    code = str(getattr(error, "code", "") or "")
    if not code:
        raise error
    status_code = 422
    if code in {"TRIP_NOT_FOUND", "PREVIOUS_REVISION_NOT_FOUND"}:
        status_code = 404
    elif code in {"ACTIVE_PLANNING_RUN", "REVISION_CONFLICT"}:
        status_code = 409
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": str(error)},
    ) from None


def create_trip_persistence_router(repository_getter: Callable[[], Any]) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["trip-persistence"])

    @router.get("/device")
    async def bootstrap_device(request: Request, response: Response):
        identity = _device(request, response, repository_getter)
        return {"status": "ready", "device_id": identity.device_id}

    @router.post("/trips/migrate")
    async def migrate_legacy_trips(
        payload: LegacyTripMigrationRequest,
        request: Request,
        response: Response,
    ):
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        return repository.migrate_legacy_trips(identity.device_id, payload.items)

    @router.get("/trips")
    async def list_trips(
        request: Request,
        response: Response,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=20),
    ):
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        return repository.list_trips(identity.device_id, page=page, page_size=page_size)

    @router.delete("/trips")
    async def delete_all_trips(request: Request, response: Response):
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        try:
            return repository.delete_all_trips(identity.device_id)
        except Exception as error:
            _raise_repository_http_error(error)

    @router.put("/trips/{trip_id}")
    async def upsert_trip(
        trip_id: str,
        payload: TripUpsertRequest,
        request: Request,
        response: Response,
    ):
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        fields = payload.model_fields_set
        values = {
            field: getattr(payload, field)
            for field in ("messages", "trip_plan", "trip_document", "trip_workspace", "agent_timeline")
            if field in fields
        }
        return repository.upsert_trip(
            identity.device_id,
            trip_id,
            title=payload.title,
            change_reason=payload.change_reason,
            **values,
        )

    @router.get("/trips/{trip_id}")
    async def get_trip(trip_id: str, request: Request, response: Response):
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        try:
            return repository.get_trip(identity.device_id, trip_id)
        except Exception as error:
            _raise_repository_http_error(error)

    @router.delete("/trips/{trip_id}")
    async def delete_trip(trip_id: str, request: Request, response: Response):
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        try:
            return repository.delete_trip(identity.device_id, trip_id)
        except Exception as error:
            _raise_repository_http_error(error)

    @router.put("/trips/{trip_id}/draft")
    async def save_draft(
        trip_id: str,
        payload: TripDraftRequest,
        request: Request,
        response: Response,
    ):
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        try:
            return repository.save_draft(
                identity.device_id,
                trip_id,
                payload.document,
                payload.operations,
            )
        except Exception as error:
            _raise_repository_http_error(error)

    @router.delete("/trips/{trip_id}/draft")
    async def discard_draft(trip_id: str, request: Request, response: Response):
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        return {"discarded": repository.discard_draft(identity.device_id, trip_id)}

    @router.post("/trips/{trip_id}/formal")
    async def apply_formal_snapshot(
        trip_id: str,
        payload: FormalSnapshotApplyRequest,
        request: Request,
        response: Response,
    ):
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        try:
            document = TravelPlanDocumentV3.model_validate(payload.document).model_dump(mode="json")
            return repository.apply_formal_snapshot(
                identity.device_id,
                trip_id,
                payload.operation_id,
                document,
            )
        except ValidationError:
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_V3_DOCUMENT", "message": "正式行程未通过 V3 契约校验"},
            ) from None
        except Exception as error:
            _raise_repository_http_error(error)

    @router.post("/trips/{trip_id}/draft/apply")
    async def apply_persisted_draft(
        trip_id: str,
        payload: DraftFormalSnapshotApplyRequest,
        request: Request,
        response: Response,
    ):
        """只应用服务端当前草稿，避免客户端提交过期或伪造的正式文档。"""
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        try:
            return repository.apply_draft_as_formal(
                identity.device_id,
                trip_id,
                payload.operation_id,
                payload.expected_draft_revision,
            )
        except Exception as error:
            _raise_repository_http_error(error)

    @router.post("/trips/{trip_id}/restore")
    async def restore_formal_snapshot(
        trip_id: str,
        payload: FormalSnapshotRestoreRequest,
        request: Request,
        response: Response,
    ):
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        try:
            return repository.restore_previous_snapshot(
                identity.device_id,
                trip_id,
                payload.operation_id,
                payload.expected_current_revision,
            )
        except Exception as error:
            _raise_repository_http_error(error)

    @router.get("/trips/{trip_id}/revisions/{operation_id}/events")
    async def stream_formal_revision(
        trip_id: str,
        operation_id: str,
        request: Request,
        response: Response,
        after_sequence: int = Query(default=0, ge=0),
    ):
        """从已提交回执重放完整正式修订；断线可按 sequence 安全续传。"""
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        try:
            revision = repository.current_revision_operation(
                identity.device_id,
                trip_id,
                operation_id,
            )
            events = build_revision_events(
                revision["document"],
                operation_id=operation_id,
                checksum=revision["checksum"],
                occurred_at=datetime.fromisoformat(revision["occurred_at"]),
            )
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=500,
                detail={"code": "REVISION_STREAM_UNAVAILABLE", "message": "正式版本同步暂不可用，请刷新后查看当前版本"},
            ) from None
        except Exception as error:
            _raise_repository_http_error(error)

        stream_response = StreamingResponse(
            encode_revision_event_stream(events, after_sequence=after_sequence),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
        set_anonymous_device_cookie(
            stream_response,
            identity,
            secure=request.url.scheme == "https",
        )
        return stream_response

    @router.get("/planning-runs/active")
    async def get_active_planning_run(request: Request, response: Response):
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        response.headers["Cache-Control"] = "no-store"
        return {"active_run": repository.active_planning_run(identity.device_id)}

    return router
