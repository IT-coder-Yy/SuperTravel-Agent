"""已验收案例包的只读目录与 SSE 回放流。"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Mapping

from .demo_case_package_service import (
    DEMO_CASE_KIND,
    DEMO_CASE_PACKAGE_VERSION,
    DemoCasePackageError,
    RecordedRun,
    _CASE_ID_RE,
    _validate_recorded_runs,
)
from backend.schemas.trip_v3_models import TravelPlanDocumentV3


DEMO_REPLAY_EVENT_DELAY_SECONDS = 0.2
DEMO_REPLAY_SPEEDS = {1.0, 2.0}


def default_demo_case_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "trip_demos"


def _as_ready_demo_package(payload: Any, *, expected_case_id: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DemoCasePackageError("案例包格式无效")
    case = payload.get("case")
    recording = payload.get("recording")
    events = payload.get("events")
    document = payload.get("document")
    case_id = str((case or {}).get("id") or "")
    if not _CASE_ID_RE.fullmatch(case_id) or (expected_case_id and case_id != expected_case_id):
        raise DemoCasePackageError("案例 ID 无效")
    if payload.get("kind") != DEMO_CASE_KIND or payload.get("package_version") != DEMO_CASE_PACKAGE_VERSION:
        raise DemoCasePackageError("案例包版本不受支持")
    if not isinstance(case, dict) or case.get("recording_status") != "ready":
        raise DemoCasePackageError("案例尚未就绪")
    if not isinstance(recording, dict) or recording.get("accepted") is not True or recording.get("source") != "real_sse":
        raise DemoCasePackageError("案例未通过真实录制验收")
    if not isinstance(events, list):
        raise DemoCasePackageError("案例缺少回放事件")
    _validate_recorded_runs([RecordedRun(run_id=f"demo:{case_id}", status="completed", events=events)])
    if not isinstance(document, dict):
        raise DemoCasePackageError("案例缺少正式方案")
    try:
        TravelPlanDocumentV3.model_validate(document)
    except Exception as exc:
        raise DemoCasePackageError("案例正式方案不合法") from exc
    return payload


def load_ready_demo_case_package(directory: Path | str, case_id: str) -> dict[str, Any]:
    normalized_case_id = str(case_id or "").strip()
    if not _CASE_ID_RE.fullmatch(normalized_case_id):
        raise DemoCasePackageError("案例不存在")
    path = Path(directory) / f"{normalized_case_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoCasePackageError("案例不存在") from exc
    return _as_ready_demo_package(payload, expected_case_id=normalized_case_id)


def iter_valid_demo_case_packages(directory: Path | str) -> Iterable[dict[str, Any]]:
    root = Path(directory)
    if not root.exists():
        return []
    packages: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            packages.append(_as_ready_demo_package(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, DemoCasePackageError):
            continue
    return packages


def demo_case_summary(package: Mapping[str, Any]) -> dict[str, Any]:
    case = package["case"]
    document = package["document"]
    intent = document["intent"]
    travelers = intent.get("travelers") or {}
    return {
        "id": case["id"],
        "title": case["title"],
        "summary": case["summary"],
        "days": case["days"],
        "traveler_label": case["traveler_label"],
        "origin": case["origin"],
        "destination": case["destination"],
        "preferences": case.get("preferences") or [],
        "recording_status": "ready",
        "event_count": len(package["events"]),
        "notice": (package.get("recording") or {}).get("notice") or "案例录制时的信息，仅用于演示。",
        "template": {
            "origin": intent.get("origin") or case["origin"],
            "destination": intent.get("destination") or case["destination"],
            "days": intent.get("days") or case["days"],
            "adults": travelers.get("adults") or 0,
            "children": travelers.get("children") or 0,
            "seniors": travelers.get("seniors") or 0,
            "preferences": intent.get("preferences") or case.get("preferences") or [],
        },
    }


async def iter_demo_case_sse(
    package: Mapping[str, Any],
    *,
    after_sequence: int = 0,
    speed: float = 1.0,
    final_only: bool = False,
) -> AsyncIterator[str]:
    if speed not in DEMO_REPLAY_SPEEDS:
        raise DemoCasePackageError("回放速度仅支持 1 倍或 2 倍")
    emitted = False
    terminal_types = {"trip_plan_completed", "trip_plan", "chat_complete"}
    for raw_event in package["events"]:
        sequence = int(raw_event.get("sequence") or 0)
        if sequence <= after_sequence:
            continue
        if final_only and raw_event.get("type") not in terminal_types:
            continue
        if emitted:
            await asyncio.sleep(DEMO_REPLAY_EVENT_DELAY_SECONDS / speed)
        emitted = True
        event = copy.deepcopy(raw_event)
        yield f"id: {sequence}\ndata: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
