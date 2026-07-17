from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


TripChangeReason = Literal[
    "user_message",
    "final_answer",
    "trip_edit",
    "import",
    "checklist",
    "note",
    "rename",
    "system",
]


class TripUpsertRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    messages: Optional[List[Dict[str, Any]]] = None
    change_reason: TripChangeReason = "system"
    trip_plan: Optional[Dict[str, Any]] = None
    trip_document: Optional[Dict[str, Any]] = None
    trip_workspace: Optional[Dict[str, Any]] = None


class LegacyTripMigrationRequest(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list, max_length=100)


class TripDraftRequest(BaseModel):
    document: Dict[str, Any]
    operations: List[Dict[str, Any]] = Field(default_factory=list)


class FormalSnapshotApplyRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=120)
    document: Dict[str, Any]


class FormalSnapshotRestoreRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=120)
    expected_current_revision: int = Field(ge=1)
