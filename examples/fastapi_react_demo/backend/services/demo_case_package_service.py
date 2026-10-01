"""将已验收的真实规划运行转换为可回放、脱敏的案例包。"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from backend.schemas.trip_v3_models import TravelPlanDocumentV3


DEMO_CASE_PACKAGE_VERSION = 1
DEMO_CASE_KIND = "supertravelagent_demo_case"
DEMO_TIMESTAMP = "2000-01-01T00:00:00+00:00"
DEMO_CHAT_PLACEHOLDER = "已录制的过程摘要；正式方案将按章节展示。"
REQUIRED_AGENT_STAGES = {
    "requirements_analysis",
    "research",
    "route_planning",
    "realtime_verification",
    "validation_completed",
}
_CASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_CN_ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_SECRET_RE = re.compile(
    r"(?:\b(?:sk|rk|pk)_[A-Za-z0-9_-]{16,}\b|\b(?:api[_ -]?key|authorization)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)


class DemoCasePackageError(ValueError):
    """案例不是可公开回放的真实、脱敏验收包。"""


@dataclass(frozen=True)
class RecordedRun:
    run_id: str
    status: str
    events: Sequence[Mapping[str, Any]]


def _public_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return date.today().isoformat()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError as exc:
        raise DemoCasePackageError("验收日期必须是 ISO 日期") from exc


def _redact_text(value: str) -> str:
    if _SECRET_RE.search(value):
        raise DemoCasePackageError("案例包包含疑似密钥或授权信息，已拒绝写入")
    value = _EMAIL_RE.sub("[邮箱已隐藏]", value)
    value = _PHONE_RE.sub("[手机号已隐藏]", value)
    return _CN_ID_RE.sub("[证件信息已隐藏]", value)


def _ensure_public_label(name: str, value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DemoCasePackageError(f"{name}不能为空")
    redacted = _redact_text(text)
    if redacted != text:
        raise DemoCasePackageError(f"{name}不能包含个人信息")
    return text


def _sanitize_document_payload(document: Mapping[str, Any], case_id: str) -> dict[str, Any]:
    """移除会过期的价格、外链和图片，同时保持 V3 文档可校验。"""
    payload = copy.deepcopy(dict(document))

    def walk(value: Any) -> Any:
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value

        current = {str(key): walk(item) for key, item in value.items()}
        for key in (
            "price",
            "nightly_price",
            "total_price",
            "estimated_cost",
            "estimated_total",
            "overrun_amount",
            "booking_url",
            "official_query_url",
            "download_location",
            "attribution_url",
            "provider_url",
            "url",
        ):
            current.pop(key, None)
        if "images" in current:
            current["images"] = []
            current["cover_image_id"] = None
        current.pop("cover_image", None)
        current.pop("reservation", None)
        if "phone" in current:
            current["phone"] = None
        if "amount" in current and "currency" in current:
            current.pop("cny_reference_amount", None)
            current.pop("exchange_rate_as_of", None)
            current["source_status"] = "user_confirmation_required"
        for key in ("updated_at", "checked_at", "verified_at", "fetched_at", "generated_at"):
            if key in current:
                current[key] = DEMO_TIMESTAMP
        return current

    payload = walk(payload)
    payload["plan_id"] = f"demo-{case_id}"
    payload["generated_at"] = DEMO_TIMESTAMP
    budget = payload.get("budget")
    if isinstance(budget, dict):
        budget["estimated_total"] = None
        budget["categories"] = []
        budget["over_budget"] = False
        budget["overrun_amount"] = None
        budget["unknown_cost_count"] = max(1, int(budget.get("unknown_cost_count") or 0))
        budget["warnings"] = ["示例不包含实时价格，实际费用请重新确认。"]
        for cost in budget.get("traveler_costs") or []:
            if isinstance(cost, dict):
                cost["estimated_total"] = None
                cost["pricing_status"] = "not_applicable"

    sanitized = _sanitize_runtime_value(payload, case_id=case_id, sequence=0)
    if not isinstance(sanitized, dict):  # pragma: no cover - 防御式保护
        raise DemoCasePackageError("正式 V3 文档脱敏失败")
    try:
        return TravelPlanDocumentV3.model_validate(sanitized).model_dump(mode="json")
    except Exception as exc:
        raise DemoCasePackageError(f"脱敏后的正式 V3 文档不合法：{exc}") from exc


def _sanitize_runtime_value(value: Any, *, case_id: str, sequence: int) -> Any:
    if isinstance(value, list):
        return [_sanitize_runtime_value(item, case_id=case_id, sequence=sequence) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if not isinstance(value, dict):
        return value

    sanitized: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if key in {"device_id", "session_id", "user_id", "cookie", "authorization", "api_key"}:
            continue
        if key == "event_id":
            sanitized[key] = f"demo:{case_id}:event:{sequence:04d}"
            continue
        if key in {"run_id", "request_id"}:
            sanitized[key] = f"demo:{case_id}:run"
            continue
        if key == "plan_id":
            sanitized[key] = f"demo-{case_id}"
            continue
        if key == "operation_id":
            sanitized[key] = f"demo:{case_id}:operation:{sequence:04d}"
            continue
        if key in {"message_id", "linked_user_message_id"}:
            sanitized[key] = f"demo:{case_id}:message:{sequence:04d}"
            continue
        if key == "occurred_at":
            sanitized[key] = DEMO_TIMESTAMP
            continue
        if key in {"updated_at", "checked_at", "verified_at", "fetched_at", "generated_at", "created_at", "completed_at"}:
            sanitized[key] = DEMO_TIMESTAMP
            continue
        if key in {"booking_url", "official_query_url", "download_location", "attribution_url", "provider_url", "url"}:
            continue
        if key == "phone":
            sanitized[key] = None
            continue
        sanitized[key] = _sanitize_runtime_value(raw_value, case_id=case_id, sequence=sequence)
    return sanitized


def _document_sections(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "destination_overview": {
            "title": document["title"],
            "intent": document["intent"],
            "destination_overview": document["destination_overview"],
        },
        "outbound_transport": {
            "outbound_transport": document["outbound_transport"],
            "return_transport": document["return_transport"],
        },
        "lodging_plan": document["lodging_plan"],
        "itinerary": document["itinerary"],
        "budget_and_reminders": {
            "budget": document["budget"],
            "candidate_pool": document["candidate_pool"],
            "action_items": document["action_items"],
            "notes": document["notes"],
        },
        "map_guidance": document["map_guidance"],
        "delivery": document["delivery"],
        "sources": {"sources": document["sources"]},
    }


def _validate_recorded_runs(recorded_runs: Sequence[RecordedRun]) -> list[Mapping[str, Any]]:
    if not recorded_runs:
        raise DemoCasePackageError("至少需要一段真实规划运行")
    flattened: list[Mapping[str, Any]] = []
    for run in recorded_runs:
        if run.status not in {"completed", "degraded"}:
            raise DemoCasePackageError(f"运行 {run.run_id} 未成功结束，不能收录为案例")
        previous_sequence = 0
        if not run.events:
            raise DemoCasePackageError(f"运行 {run.run_id} 的事件已过期或不存在，请在五分钟内录制")
        for event in run.events:
            sequence = int(event.get("sequence") or 0)
            if sequence <= previous_sequence:
                raise DemoCasePackageError(f"运行 {run.run_id} 的 SSE 事件顺序不完整")
            previous_sequence = sequence
            flattened.append(event)
    types = {str(event.get("type") or "") for event in flattened}
    if "trip_plan_completed" not in types:
        raise DemoCasePackageError("案例缺少正式方案完成事件，不能标记为可回放")
    if "final_plan_section" not in types:
        raise DemoCasePackageError("案例缺少正式方案章节事件，不能标记为可回放")
    stages = {
        str((event.get("payload") or {}).get("stage") or "")
        for event in flattened
        if event.get("type") == "agent_stage_started"
    }
    missing = REQUIRED_AGENT_STAGES - stages
    if missing:
        raise DemoCasePackageError(f"案例缺少 Agent 阶段：{'、'.join(sorted(missing))}")
    return flattened


class DemoCasePackageService:
    """只接受运行时持久化的 SSE 数据，不把测试夹具伪装成真实案例。"""

    def build_package(
        self,
        *,
        case_id: str,
        title: str,
        summary: str,
        traveler_label: str,
        recorded_runs: Sequence[RecordedRun],
        final_document: Mapping[str, Any],
        accepted_on: Any,
    ) -> dict[str, Any]:
        if not _CASE_ID_RE.fullmatch(case_id):
            raise DemoCasePackageError("案例 ID 只能使用小写字母、数字和连字符")
        title = _ensure_public_label("案例标题", title)
        summary = _ensure_public_label("案例简介", summary)
        traveler_label = _ensure_public_label("出行人说明", traveler_label)
        flattened = _validate_recorded_runs(recorded_runs)
        document = _sanitize_document_payload(final_document, case_id)
        sections = _document_sections(document)

        events: list[dict[str, Any]] = []
        for playback_sequence, raw_event in enumerate(flattened, start=1):
            event = _sanitize_runtime_value(copy.deepcopy(dict(raw_event)), case_id=case_id, sequence=playback_sequence)
            if not isinstance(event, dict):  # pragma: no cover - 由输入类型保证
                raise DemoCasePackageError("SSE 事件格式无效")
            event["sequence"] = playback_sequence
            event["event_id"] = f"demo:{case_id}:event:{playback_sequence:04d}"
            event["run_id"] = f"demo:{case_id}:run"
            event["request_id"] = f"demo:{case_id}:run"
            event["occurred_at"] = DEMO_TIMESTAMP
            payload = event.get("payload")
            if event.get("type") == "final_plan_section" and isinstance(payload, dict):
                section = str(payload.get("section") or "")
                if section in sections:
                    payload["plan_id"] = document["plan_id"]
                    payload["revision"] = document["revision"]
                    payload["content"] = copy.deepcopy(sections[section])
            if event.get("type") == "trip_plan" and isinstance(event.get("document"), dict):
                event["document"] = copy.deepcopy(document)
            if event.get("type") == "chat_chunk":
                event["content"] = DEMO_CHAT_PLACEHOLDER
                event["show_content"] = DEMO_CHAT_PLACEHOLDER
            events.append(event)

        intent = document["intent"]
        accepted_on_text = _public_date(accepted_on)
        return {
            "kind": DEMO_CASE_KIND,
            "package_version": DEMO_CASE_PACKAGE_VERSION,
            "case": {
                "id": case_id,
                "title": title,
                "summary": summary,
                "days": intent["days"],
                "traveler_label": traveler_label,
                "origin": intent["origin"],
                "destination": intent["destination"],
                "preferences": intent["preferences"],
                "recording_status": "ready",
            },
            "recording": {
                "source": "real_sse",
                "accepted": True,
                "accepted_on": accepted_on_text,
                "run_count": len(recorded_runs),
                "event_count": len(events),
                "notice": "案例录制时的信息，仅用于演示。",
            },
            "events": events,
            "document": document,
            "map_data": copy.deepcopy(document["map_guidance"]),
        }

    def build_from_repository(
        self,
        *,
        repository: Any,
        device_id: str,
        run_ids: Sequence[str],
        case_id: str,
        title: str,
        summary: str,
        traveler_label: str,
        accepted_on: Any,
    ) -> dict[str, Any]:
        normalized_run_ids = [str(run_id or "").strip() for run_id in run_ids]
        if not normalized_run_ids or any(not run_id for run_id in normalized_run_ids):
            raise DemoCasePackageError("必须提供至少一个真实 run_id")
        recorded_runs: list[RecordedRun] = []
        trip_id = ""
        for run_id in normalized_run_ids:
            run = repository.planning_run(device_id, run_id)
            if run is None:
                raise DemoCasePackageError("找不到当前匿名设备下的规划运行")
            current_trip_id = str(run.get("trip_id") or "")
            if trip_id and current_trip_id != trip_id:
                raise DemoCasePackageError("同一案例的多段运行必须属于同一旅程")
            trip_id = current_trip_id
            persisted_events = repository.recent_run_events(run_id)
            recorded_runs.append(
                RecordedRun(
                    run_id=run_id,
                    status=str(run.get("status") or ""),
                    events=[
                        item.get("event") if isinstance(item, Mapping) and isinstance(item.get("event"), Mapping) else item
                        for item in persisted_events
                    ],
                )
            )
        trip = repository.get_trip(device_id, trip_id)
        current = ((trip.get("formalSnapshots") or {}).get("current") or {}).get("document")
        if not isinstance(current, dict):
            raise DemoCasePackageError("旅程没有已保存的正式 V3 方案")
        return self.build_package(
            case_id=case_id,
            title=title,
            summary=summary,
            traveler_label=traveler_label,
            recorded_runs=recorded_runs,
            final_document=current,
            accepted_on=accepted_on,
        )

    @staticmethod
    def write_package(package: Mapping[str, Any], output_dir: Path | str) -> Path:
        case = package.get("case") if isinstance(package, Mapping) else None
        case_id = str((case or {}).get("id") or "")
        if not _CASE_ID_RE.fullmatch(case_id):
            raise DemoCasePackageError("案例包缺少合法案例 ID")
        destination = Path(output_dir).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / f"{case_id}.json"
        target.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target


def iter_ready_demo_packages(directory: Path | str) -> Iterable[dict[str, Any]]:
    """供 P7-06 读取：只返回经本服务写入的已就绪包。"""
    root = Path(directory)
    if not root.exists():
        return []
    packages: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("kind") == DEMO_CASE_KIND
            and payload.get("package_version") == DEMO_CASE_PACKAGE_VERSION
            and (payload.get("recording") or {}).get("accepted") is True
        ):
            packages.append(payload)
    return packages
