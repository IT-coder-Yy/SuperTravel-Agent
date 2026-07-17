from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import ValidationError

try:
    from backend.schemas.trip_persistence_models import (
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
except ModuleNotFoundError:  # Runtime entrypoint executes from backend/.
    from schemas.trip_persistence_models import (
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


def _raise_repository_http_error(error: TripRepositoryError) -> None:
    status_code = 422
    if isinstance(error, TripNotFoundError):
        status_code = 404
    elif isinstance(error, (ActivePlanningRunError, RevisionConflictError)):
        status_code = 409
    elif error.code == "PREVIOUS_REVISION_NOT_FOUND":
        status_code = 404
    raise HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": str(error)},
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
        except TripRepositoryError as error:
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
        return repository.upsert_trip(
            identity.device_id,
            trip_id,
            title=payload.title,
            messages=payload.messages if "messages" in fields else UNSET,
            trip_plan=payload.trip_plan if "trip_plan" in fields else UNSET,
            trip_document=payload.trip_document if "trip_document" in fields else UNSET,
            trip_workspace=payload.trip_workspace if "trip_workspace" in fields else UNSET,
            change_reason=payload.change_reason,
        )

    @router.get("/trips/{trip_id}")
    async def get_trip(trip_id: str, request: Request, response: Response):
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        try:
            return repository.get_trip(identity.device_id, trip_id)
        except TripRepositoryError as error:
            _raise_repository_http_error(error)

    @router.delete("/trips/{trip_id}")
    async def delete_trip(trip_id: str, request: Request, response: Response):
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        try:
            return repository.delete_trip(identity.device_id, trip_id)
        except TripRepositoryError as error:
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
        except TripRepositoryError as error:
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
        except TripRepositoryError as error:
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
        except TripRepositoryError as error:
            _raise_repository_http_error(error)

    @router.get("/planning-runs/active")
    async def get_active_planning_run(request: Request, response: Response):
        identity = _device(request, response, repository_getter)
        repository = _repository_or_503(repository_getter)
        return {"run": repository.active_planning_run(identity.device_id)}

    return router
