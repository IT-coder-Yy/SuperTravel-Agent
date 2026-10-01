import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

try:
    from backend.services.persistence_sanitizer import sanitize_for_persistence, sanitize_persisted_text
    from backend.services.trip_draft_validation_service import validate_trip_draft
    from backend.schemas.trip_v3_models import TravelPlanDocumentV3
    from backend.services.anonymous_device_service import (
        LOCAL_SHARED_DEVICE_ID,
        LOCAL_SHARED_DEVICE_TOKEN,
        hash_device_token,
    )
except ModuleNotFoundError:  # Runtime entrypoint executes from backend/.
    from services.persistence_sanitizer import sanitize_for_persistence, sanitize_persisted_text
    from services.trip_draft_validation_service import validate_trip_draft
    from schemas.trip_v3_models import TravelPlanDocumentV3
    from services.anonymous_device_service import (
        LOCAL_SHARED_DEVICE_ID,
        LOCAL_SHARED_DEVICE_TOKEN,
        hash_device_token,
    )


UNSET = object()
CONTENT_CHANGE_REASONS = {
    "user_message",
    "final_answer",
    "trip_edit",
    "import",
    "checklist",
    "note",
    "rename",
}


class TripRepositoryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TripNotFoundError(TripRepositoryError):
    def __init__(self):
        super().__init__("TRIP_NOT_FOUND", "未找到该旅程")


class ActivePlanningRunError(TripRepositoryError):
    def __init__(self):
        super().__init__("ACTIVE_PLANNING_RUN", "当前有行程正在规划，请先停止当前任务")


class RevisionConflictError(TripRepositoryError):
    def __init__(self, message: str = "行程版本已变化，请刷新后重试"):
        super().__init__("REVISION_CONFLICT", message)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_trip_database_path() -> Path:
    configured = os.getenv("SUPERTRAVEL_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "data" / "supertravelagent.sqlite3"


def _json_dumps(value: Any) -> str:
    return json.dumps(
        sanitize_for_persistence(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_loads(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _checksum(value: Dict[str, Any]) -> str:
    return hashlib.sha256(_json_dumps(value).encode("utf-8")).hexdigest()


def _normalized_iso(value: Any, fallback: str) -> str:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return fallback


class TripRepository:
    """SQLite-backed anonymous trip store with explicit transaction boundaries."""

    SCHEMA_VERSION = 4

    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                int(row["version"])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            if 1 not in applied:
                self._apply_v1(connection)
                applied.add(1)
            if 2 not in applied:
                self._apply_v2(connection)
                applied.add(2)
            if 3 not in applied:
                self._apply_v3(connection)
                applied.add(3)
            if 4 not in applied:
                self._apply_v4(connection)
        # Planning workers and their SSE buffers are process-local. Any run that
        # survived in SQLite across a backend restart cannot resume safely and
        # must release the per-device planning lock before the UI reconnects.
        self.recover_interrupted_planning_runs()

    def recover_interrupted_planning_runs(self) -> int:
        now = utc_now_iso()
        with self._transaction() as connection:
            active_runs = connection.execute(
                "SELECT run_id, trip_id, previous_trip_status FROM planning_runs "
                "WHERE status IN ('running', 'retrying')"
            ).fetchall()
            for run in active_runs:
                connection.execute(
                    "UPDATE trips SET status = 'cancelled', updated_at = ? WHERE trip_id = ?",
                    (now, run["trip_id"]),
                )
            connection.execute(
                "UPDATE planning_runs SET status = 'cancelled', updated_at = ?, completed_at = ? "
                "WHERE status IN ('running', 'retrying')",
                (now, now),
            )
        return len(active_runs)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.database_path),
            timeout=5,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _apply_v1(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE anonymous_devices (
                device_id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            CREATE TABLE trips (
                trip_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES anonymous_devices(device_id) ON DELETE CASCADE,
                plan_id TEXT,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                messages_json TEXT NOT NULL DEFAULT '[]',
                legacy_plan_json TEXT,
                legacy_document_json TEXT,
                workspace_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                content_updated_at TEXT NOT NULL,
                current_revision INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX idx_trips_device_content
                ON trips(device_id, content_updated_at DESC, trip_id DESC);
            CREATE TABLE formal_snapshots (
                trip_id TEXT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
                slot TEXT NOT NULL CHECK(slot IN ('current', 'previous')),
                revision INTEGER NOT NULL,
                document_json TEXT NOT NULL,
                checksum TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(trip_id, slot)
            );
            CREATE TABLE drafts (
                device_id TEXT PRIMARY KEY REFERENCES anonymous_devices(device_id) ON DELETE CASCADE,
                trip_id TEXT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
                document_json TEXT NOT NULL,
                operation_set_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE operation_receipts (
                device_id TEXT NOT NULL REFERENCES anonymous_devices(device_id) ON DELETE CASCADE,
                operation_id TEXT NOT NULL,
                trip_id TEXT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
                operation_type TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(device_id, operation_id)
            );
            CREATE TABLE planning_runs (
                run_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES anonymous_devices(device_id) ON DELETE CASCADE,
                trip_id TEXT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
                request_id TEXT NOT NULL,
                status TEXT NOT NULL,
                last_sequence INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE UNIQUE INDEX idx_one_active_run_per_device
                ON planning_runs(device_id)
                WHERE status IN ('running', 'retrying');
            CREATE TABLE run_events (
                run_id TEXT NOT NULL REFERENCES planning_runs(run_id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                event_json TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY(run_id, sequence),
                UNIQUE(run_id, event_id)
            );
            INSERT INTO schema_migrations(version, applied_at)
                VALUES (1, CURRENT_TIMESTAMP);
            COMMIT;
            """
        )

    def _apply_v2(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE planning_runs
                ADD COLUMN previous_trip_status TEXT NOT NULL DEFAULT 'draft';
            INSERT INTO schema_migrations(version, applied_at)
                VALUES (2, CURRENT_TIMESTAMP);
            COMMIT;
            """
        )

    @staticmethod
    def _consolidate_local_history(connection: sqlite3.Connection) -> Dict[str, int]:
        now = utc_now_iso()
        local_token_hash = hash_device_token(LOCAL_SHARED_DEVICE_TOKEN)
        connection.execute(
            "INSERT OR IGNORE INTO anonymous_devices(device_id, token_hash, created_at, last_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (LOCAL_SHARED_DEVICE_ID, local_token_hash, now, now),
        )
        connection.execute(
            "UPDATE anonymous_devices SET last_seen_at = ? WHERE device_id = ?",
            (now, LOCAL_SHARED_DEVICE_ID),
        )

        active_runs = connection.execute(
            "SELECT run_id, trip_id, previous_trip_status FROM planning_runs "
            "WHERE status IN ('running', 'retrying') ORDER BY started_at DESC"
        ).fetchall()
        restored_trips: set[str] = set()
        for run in active_runs:
            trip_id = str(run["trip_id"])
            if trip_id in restored_trips:
                continue
            connection.execute(
                "UPDATE trips SET status = ?, updated_at = ? WHERE trip_id = ?",
                (run["previous_trip_status"], now, trip_id),
            )
            restored_trips.add(trip_id)
        connection.execute(
            "UPDATE planning_runs SET status = 'cancelled', updated_at = ?, completed_at = ? "
            "WHERE status IN ('running', 'retrying')",
            (now, now),
        )

        latest_draft = connection.execute(
            "SELECT trip_id, document_json, operation_set_json, updated_at FROM drafts "
            "ORDER BY updated_at DESC, trip_id DESC LIMIT 1"
        ).fetchone()
        receipts = connection.execute(
            "SELECT operation_id, trip_id, operation_type, response_json, created_at "
            "FROM operation_receipts ORDER BY created_at ASC"
        ).fetchall()
        trip_count = int(connection.execute("SELECT COUNT(*) FROM trips").fetchone()[0])
        source_device_count = int(connection.execute(
            "SELECT COUNT(DISTINCT device_id) FROM trips WHERE device_id <> ?",
            (LOCAL_SHARED_DEVICE_ID,),
        ).fetchone()[0])

        connection.execute("DELETE FROM drafts")
        connection.execute("DELETE FROM operation_receipts")
        connection.execute(
            "UPDATE trips SET device_id = ? WHERE device_id <> ?",
            (LOCAL_SHARED_DEVICE_ID, LOCAL_SHARED_DEVICE_ID),
        )
        connection.execute(
            "UPDATE planning_runs SET device_id = ? WHERE device_id <> ?",
            (LOCAL_SHARED_DEVICE_ID, LOCAL_SHARED_DEVICE_ID),
        )
        for receipt in receipts:
            connection.execute(
                "INSERT OR IGNORE INTO operation_receipts("
                "device_id, operation_id, trip_id, operation_type, response_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    LOCAL_SHARED_DEVICE_ID,
                    receipt["operation_id"],
                    receipt["trip_id"],
                    receipt["operation_type"],
                    receipt["response_json"],
                    receipt["created_at"],
                ),
            )
        if latest_draft is not None:
            connection.execute(
                "INSERT INTO drafts(device_id, trip_id, document_json, operation_set_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    LOCAL_SHARED_DEVICE_ID,
                    latest_draft["trip_id"],
                    latest_draft["document_json"],
                    latest_draft["operation_set_json"],
                    latest_draft["updated_at"],
                ),
            )
        return {
            "devices_merged": source_device_count,
            "trips_preserved": trip_count,
            "drafts_preserved": 1 if latest_draft is not None else 0,
            "runs_cancelled": len(active_runs),
        }

    def _apply_v3(self, connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._consolidate_local_history(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (3, ?)",
                (utc_now_iso(),),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _apply_v4(self, connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("ALTER TABLE trips ADD COLUMN agent_timeline_json TEXT")
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (4, ?)",
                (utc_now_iso(),),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def consolidate_local_history(self) -> Dict[str, int]:
        with self._transaction() as connection:
            return self._consolidate_local_history(connection)

    def claim_legacy_shared_history(self, device_id: str) -> int:
        """Move the one-time pre-device history to the browser replacing its legacy cookie."""
        if not device_id or device_id == LOCAL_SHARED_DEVICE_ID:
            return 0
        with self._transaction() as connection:
            count = int(connection.execute(
                "SELECT COUNT(*) FROM trips WHERE device_id = ?",
                (LOCAL_SHARED_DEVICE_ID,),
            ).fetchone()[0])
            if count == 0:
                return 0
            connection.execute(
                "UPDATE trips SET device_id = ? WHERE device_id = ?",
                (device_id, LOCAL_SHARED_DEVICE_ID),
            )
            connection.execute(
                "UPDATE planning_runs SET device_id = ? WHERE device_id = ?",
                (device_id, LOCAL_SHARED_DEVICE_ID),
            )
            connection.execute(
                "UPDATE drafts SET device_id = ? WHERE device_id = ?",
                (device_id, LOCAL_SHARED_DEVICE_ID),
            )
            connection.execute(
                "UPDATE operation_receipts SET device_id = ? WHERE device_id = ?",
                (device_id, LOCAL_SHARED_DEVICE_ID),
            )
            return count

    def database_info(self) -> Dict[str, Any]:
        with self._connection() as connection:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
        return {"journal_mode": str(mode).lower(), "schema_version": int(version)}

    def get_or_create_device(self, token_hash: str) -> str:
        now = utc_now_iso()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT device_id FROM anonymous_devices WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE anonymous_devices SET last_seen_at = ? WHERE device_id = ?",
                    (now, existing["device_id"]),
                )
                return str(existing["device_id"])
            device_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO anonymous_devices(device_id, token_hash, created_at, last_seen_at) "
                "VALUES (?, ?, ?, ?)",
                (device_id, token_hash, now, now),
            )
            return device_id

    @staticmethod
    def _owned_trip(connection: sqlite3.Connection, device_id: str, trip_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM trips WHERE trip_id = ? AND device_id = ?",
            (trip_id, device_id),
        ).fetchone()
        if row is None:
            raise TripNotFoundError()
        return row

    def ensure_trip(self, device_id: str, trip_id: str, title: str = "新旅程") -> Dict[str, Any]:
        now = utc_now_iso()
        safe_title = sanitize_persisted_text(title.strip() or "新旅程")
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO trips("
                "trip_id, device_id, title, created_at, updated_at, content_updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (trip_id, device_id, safe_title, now, now, now),
            )
            row = self._owned_trip(connection, device_id, trip_id)
        return self._decode_trip(row)

    def upsert_trip(
        self,
        device_id: str,
        trip_id: str,
        *,
        title: str,
        messages: Any = UNSET,
        trip_plan: Any = UNSET,
        trip_document: Any = UNSET,
        trip_workspace: Any = UNSET,
        agent_timeline: Any = UNSET,
        change_reason: str = "system",
        created_at: Optional[str] = None,
        content_updated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        safe_title = sanitize_persisted_text(title.strip() or "新旅程")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM trips WHERE trip_id = ? AND device_id = ?",
                (trip_id, device_id),
            ).fetchone()
            if existing is None:
                initial_created = _normalized_iso(created_at, now)
                initial_content = _normalized_iso(content_updated_at, now)
                connection.execute(
                    "INSERT INTO trips("
                    "trip_id, device_id, title, messages_json, legacy_plan_json, "
                    "legacy_document_json, workspace_json, agent_timeline_json, created_at, updated_at, content_updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        trip_id,
                        device_id,
                        safe_title,
                        _json_dumps([] if messages is UNSET else messages),
                        None if trip_plan is UNSET or trip_plan is None else _json_dumps(trip_plan),
                        None if trip_document is UNSET or trip_document is None else _json_dumps(trip_document),
                        None if trip_workspace is UNSET or trip_workspace is None else _json_dumps(trip_workspace),
                        None if agent_timeline is UNSET or agent_timeline is None else _json_dumps(agent_timeline),
                        initial_created,
                        now,
                        initial_content,
                    ),
                )
            else:
                next_messages = existing["messages_json"] if messages is UNSET else _json_dumps(messages)
                next_plan = existing["legacy_plan_json"] if trip_plan is UNSET else (
                    None if trip_plan is None else _json_dumps(trip_plan)
                )
                next_document = existing["legacy_document_json"] if trip_document is UNSET else (
                    None if trip_document is None else _json_dumps(trip_document)
                )
                next_workspace = existing["workspace_json"] if trip_workspace is UNSET else (
                    None if trip_workspace is None else _json_dumps(trip_workspace)
                )
                next_agent_timeline = existing["agent_timeline_json"] if agent_timeline is UNSET else (
                    None if agent_timeline is None else _json_dumps(agent_timeline)
                )
                content_time = now if change_reason in CONTENT_CHANGE_REASONS else existing["content_updated_at"]
                connection.execute(
                    "UPDATE trips SET title = ?, messages_json = ?, legacy_plan_json = ?, "
                    "legacy_document_json = ?, workspace_json = ?, agent_timeline_json = ?, updated_at = ?, "
                    "content_updated_at = ? WHERE trip_id = ? AND device_id = ?",
                    (
                        safe_title,
                        next_messages,
                        next_plan,
                        next_document,
                        next_workspace,
                        next_agent_timeline,
                        now,
                        content_time,
                        trip_id,
                        device_id,
                    ),
                )
            row = self._owned_trip(connection, device_id, trip_id)
        return self._decode_trip(row)

    def migrate_legacy_trips(self, device_id: str, items: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        now = utc_now_iso()
        imported = 0
        skipped = 0
        with self._transaction() as connection:
            for item in items:
                trip_id = str(item.get("id") or "").strip()
                if not trip_id:
                    skipped += 1
                    continue
                exists = connection.execute(
                    "SELECT 1 FROM trips WHERE trip_id = ? AND device_id = ?",
                    (trip_id, device_id),
                ).fetchone()
                if exists:
                    skipped += 1
                    continue
                created = _normalized_iso(item.get("createdAt"), now)
                content_updated = _normalized_iso(
                    item.get("contentUpdatedAt") or item.get("updatedAt"),
                    created,
                )
                connection.execute(
                    "INSERT INTO trips("
                    "trip_id, device_id, title, messages_json, legacy_plan_json, "
                    "legacy_document_json, workspace_json, agent_timeline_json, created_at, updated_at, content_updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        trip_id,
                        device_id,
                        sanitize_persisted_text(str(item.get("title") or "新旅程")),
                        _json_dumps(item.get("messages") or []),
                        None if item.get("tripPlan") is None else _json_dumps(item.get("tripPlan")),
                        None if item.get("tripDocument") is None else _json_dumps(item.get("tripDocument")),
                        None if item.get("tripWorkspace") is None else _json_dumps(item.get("tripWorkspace")),
                        None if item.get("agentTimeline") is None else _json_dumps(item.get("agentTimeline")),
                        created,
                        now,
                        content_updated,
                    ),
                )
                imported += 1
        return {"imported": imported, "skipped": skipped}

    def list_trips(self, device_id: str, *, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        safe_page = max(1, page)
        safe_size = min(20, max(1, page_size))
        offset = (safe_page - 1) * safe_size
        with self._connection() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM trips WHERE device_id = ?",
                (device_id,),
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT trip_id, title, status, created_at, updated_at, content_updated_at, "
                "current_revision, messages_json FROM trips WHERE device_id = ? "
                "ORDER BY content_updated_at DESC, trip_id DESC LIMIT ? OFFSET ?",
                (device_id, safe_size, offset),
            ).fetchall()
        items = []
        for row in rows:
            messages = _json_loads(row["messages_json"], [])
            preview = ""
            for message in reversed(messages):
                if isinstance(message, dict) and message.get("role") == "user":
                    preview = str(message.get("displayContent") or message.get("content") or "")[:120]
                    break
            items.append({
                "id": row["trip_id"],
                "title": row["title"],
                "status": row["status"],
                "preview": preview,
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
                "contentUpdatedAt": row["content_updated_at"],
                "currentRevision": int(row["current_revision"]),
            })
        return {
            "items": items,
            "page": safe_page,
            "page_size": safe_size,
            "total": total,
            "has_more": offset + len(items) < total,
        }

    def get_trip(self, device_id: str, trip_id: str) -> Dict[str, Any]:
        with self._connection() as connection:
            row = self._owned_trip(connection, device_id, trip_id)
            snapshots = connection.execute(
                "SELECT slot, revision, document_json, checksum, created_at "
                "FROM formal_snapshots WHERE trip_id = ?",
                (trip_id,),
            ).fetchall()
            draft = connection.execute(
                "SELECT document_json, operation_set_json, updated_at FROM drafts "
                "WHERE device_id = ? AND trip_id = ?",
                (device_id, trip_id),
            ).fetchone()
        payload = self._decode_trip(row)
        payload["formalSnapshots"] = {
            item["slot"]: {
                "revision": int(item["revision"]),
                "document": _json_loads(item["document_json"], {}),
                "checksum": item["checksum"],
                "createdAt": item["created_at"],
            }
            for item in snapshots
        }
        payload["draft"] = None if draft is None else {
            "document": _json_loads(draft["document_json"], {}),
            "operations": _json_loads(draft["operation_set_json"], []),
            "updatedAt": draft["updated_at"],
        }
        return payload

    @staticmethod
    def _decode_trip(row: sqlite3.Row) -> Dict[str, Any]:
        messages = _json_loads(row["messages_json"], [])
        preview = ""
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                preview = str(message.get("displayContent") or message.get("content") or "")[:120]
                break
        return {
            "id": row["trip_id"],
            "title": row["title"],
            "status": row["status"],
            "preview": preview,
            "messages": messages,
            "tripPlan": _json_loads(row["legacy_plan_json"], None),
            "tripDocument": _json_loads(row["legacy_document_json"], None),
            "tripWorkspace": _json_loads(row["workspace_json"], None),
            "agentTimeline": _json_loads(row["agent_timeline_json"], None),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "contentUpdatedAt": row["content_updated_at"],
            "currentRevision": int(row["current_revision"]),
        }

    def save_draft(
        self,
        device_id: str,
        trip_id: str,
        document: Dict[str, Any],
        operations: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self._transaction() as connection:
            trip = self._owned_trip(connection, device_id, trip_id)
            connection.execute(
                "INSERT INTO drafts(device_id, trip_id, document_json, operation_set_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET "
                "trip_id=excluded.trip_id, document_json=excluded.document_json, "
                "operation_set_json=excluded.operation_set_json, updated_at=excluded.updated_at",
                (device_id, trip_id, _json_dumps(document), _json_dumps(operations or []), now),
            )
        return {"trip_id": trip_id, "document": sanitize_for_persistence(document), "updated_at": now}

    def discard_draft(self, device_id: str, trip_id: str) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM drafts WHERE device_id = ? AND trip_id = ?",
                (device_id, trip_id),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _existing_receipt(
        connection: sqlite3.Connection,
        device_id: str,
        operation_id: str,
    ) -> Optional[Dict[str, Any]]:
        row = connection.execute(
            "SELECT response_json FROM operation_receipts WHERE device_id = ? AND operation_id = ?",
            (device_id, operation_id),
        ).fetchone()
        return None if row is None else _json_loads(row["response_json"], {})

    def current_revision_operation(
        self,
        device_id: str,
        trip_id: str,
        operation_id: str,
    ) -> Dict[str, Any]:
        """返回仍是当前正式版本的应用/恢复回执，供可重连 SSE 修订流读取。"""
        with self._connection() as connection:
            trip = self._owned_trip(connection, device_id, trip_id)
            receipt = connection.execute(
                "SELECT trip_id, operation_type, response_json, created_at FROM operation_receipts "
                "WHERE device_id = ? AND operation_id = ?",
                (device_id, operation_id),
            ).fetchone()
            if receipt is None or receipt["trip_id"] != trip_id:
                raise TripRepositoryError("REVISION_OPERATION_NOT_FOUND", "未找到该正式版本修订记录")
            if receipt["operation_type"] not in {"apply_draft_formal", "restore_previous"}:
                raise TripRepositoryError("REVISION_OPERATION_NOT_FOUND", "该操作不包含可发布的正式版本")
            result = _json_loads(receipt["response_json"], {})
            document = result.get("document") if isinstance(result, dict) else None
            revision = result.get("revision") if isinstance(result, dict) else None
            checksum = result.get("checksum") if isinstance(result, dict) else None
            if not isinstance(document, dict) or not isinstance(revision, int) or not isinstance(checksum, str):
                raise TripRepositoryError("REVISION_OPERATION_INVALID", "正式版本修订记录不完整")
            current = connection.execute(
                "SELECT revision, checksum FROM formal_snapshots WHERE trip_id = ? AND slot = 'current'",
                (trip_id,),
            ).fetchone()
            if (
                current is None
                or int(current["revision"]) != int(trip["current_revision"])
                or int(current["revision"]) != revision
                or current["checksum"] != checksum
            ):
                raise RevisionConflictError("该正式版本已被后续修订替代，请刷新后查看当前版本")
        return {
            "operation_id": operation_id,
            "trip_id": trip_id,
            "revision": revision,
            "checksum": checksum,
            "status": result.get("status"),
            "occurred_at": str(receipt["created_at"]),
            "document": sanitize_for_persistence(document),
        }

    def _replace_formal_snapshot(
        self,
        connection: sqlite3.Connection,
        device_id: str,
        trip: sqlite3.Row,
        document: Dict[str, Any],
        now: str,
    ) -> tuple[int, str]:
        """在当前事务中替换 current/previous 两个正式快照并清除唯一草稿。"""
        current_revision = int(trip["current_revision"])
        requested_revision = int(document.get("revision") or 0)
        if requested_revision != current_revision + 1:
            raise RevisionConflictError()
        bound_plan_id = str(trip["plan_id"] or "")
        requested_plan_id = str(document.get("plan_id") or "")
        if bound_plan_id and requested_plan_id != bound_plan_id:
            raise RevisionConflictError("正式方案 plan_id 与当前旅程不一致")

        current = connection.execute(
            "SELECT * FROM formal_snapshots WHERE trip_id = ? AND slot = 'current'",
            (trip["trip_id"],),
        ).fetchone()
        connection.execute(
            "DELETE FROM formal_snapshots WHERE trip_id = ? AND slot = 'previous'",
            (trip["trip_id"],),
        )
        if current is not None:
            connection.execute(
                "INSERT INTO formal_snapshots("
                "trip_id, slot, revision, document_json, checksum, created_at"
                ") VALUES (?, 'previous', ?, ?, ?, ?)",
                (
                    trip["trip_id"],
                    current["revision"],
                    current["document_json"],
                    current["checksum"],
                    current["created_at"],
                ),
            )
            connection.execute(
                "DELETE FROM formal_snapshots WHERE trip_id = ? AND slot = 'current'",
                (trip["trip_id"],),
            )

        sanitized_document = sanitize_for_persistence(document)
        checksum = _checksum(sanitized_document)
        connection.execute(
            "INSERT INTO formal_snapshots("
            "trip_id, slot, revision, document_json, checksum, created_at"
            ") VALUES (?, 'current', ?, ?, ?, ?)",
            (trip["trip_id"], requested_revision, _json_dumps(sanitized_document), checksum, now),
        )
        connection.execute(
            "UPDATE trips SET plan_id = ?, current_revision = ?, status = ?, "
            "updated_at = ?, content_updated_at = ? WHERE trip_id = ? AND device_id = ?",
            (
                requested_plan_id,
                requested_revision,
                "completed_degraded" if document.get("status") == "degraded" else "completed",
                now,
                now,
                trip["trip_id"],
                device_id,
            ),
        )
        connection.execute("DELETE FROM drafts WHERE device_id = ?", (device_id,))
        return requested_revision, checksum

    def apply_formal_snapshot(
        self,
        device_id: str,
        trip_id: str,
        operation_id: str,
        document: Dict[str, Any],
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self._transaction() as connection:
            receipt = self._existing_receipt(connection, device_id, operation_id)
            if receipt is not None:
                return {**receipt, "idempotent_replay": True}
            trip = self._owned_trip(connection, device_id, trip_id)
            requested_revision, checksum = self._replace_formal_snapshot(
                connection, device_id, trip, document, now
            )
            response = {
                "operation_id": operation_id,
                "trip_id": trip_id,
                "revision": requested_revision,
                "checksum": checksum,
                "status": "applied",
                "idempotent_replay": False,
            }
            connection.execute(
                "INSERT INTO operation_receipts("
                "device_id, operation_id, trip_id, operation_type, response_json, created_at"
                ") VALUES (?, ?, ?, 'apply_formal', ?, ?)",
                (device_id, operation_id, trip_id, _json_dumps(response), now),
            )
            return response

    def apply_draft_as_formal(
        self,
        device_id: str,
        trip_id: str,
        operation_id: str,
        expected_draft_revision: int,
    ) -> Dict[str, Any]:
        """原子校验并应用当前唯一草稿，正式 revision 始终只递增一次。"""
        now = utc_now_iso()
        with self._transaction() as connection:
            receipt = self._existing_receipt(connection, device_id, operation_id)
            if receipt is not None:
                return {**receipt, "idempotent_replay": True}
            trip = self._owned_trip(connection, device_id, trip_id)
            draft = connection.execute(
                "SELECT document_json FROM drafts WHERE device_id = ? AND trip_id = ?",
                (device_id, trip_id),
            ).fetchone()
            if draft is None:
                raise TripRepositoryError("DRAFT_NOT_FOUND", "没有可应用的未应用修改")
            draft_document = _json_loads(draft["document_json"], {})
            draft_revision = draft_document.get("revision") if isinstance(draft_document, dict) else None
            if not isinstance(draft_revision, int) or isinstance(draft_revision, bool):
                raise TripRepositoryError("INVALID_DRAFT_DOCUMENT", "草稿版本无效，请重新编辑后再应用")
            if draft_revision != expected_draft_revision:
                raise RevisionConflictError("草稿已变化，请刷新后再应用")
            if draft_revision <= int(trip["current_revision"]):
                raise RevisionConflictError("草稿已过期，请刷新后继续编辑")
            current_snapshot = connection.execute(
                "SELECT revision FROM formal_snapshots WHERE trip_id = ? AND slot = 'current'",
                (trip_id,),
            ).fetchone()
            if current_snapshot is None or int(current_snapshot["revision"]) != int(trip["current_revision"]):
                raise TripRepositoryError("FORMAL_SNAPSHOT_NOT_FOUND", "当前正式版本不可用，请刷新后再应用")

            from services.formal_consistency_service import recalculate_formal_budget, refresh_schedule_validation
            validation = validate_trip_draft(draft_document)
            if not validation.get("can_apply"):
                raise TripRepositoryError("DRAFT_VALIDATION_BLOCKED", "草稿存在必须修正的问题，无法应用")
            recalculate_formal_budget(draft_document)
            refresh_schedule_validation(draft_document)
            validation = validate_trip_draft(draft_document)
            if not validation.get("can_apply"):
                raise TripRepositoryError("DRAFT_VALIDATION_BLOCKED", "草稿存在必须修正的问题，无法应用")
            formal_document = dict(draft_document)
            formal_document["revision"] = int(trip["current_revision"]) + 1
            formal_document["status"] = "degraded" if validation.get("soft_warnings") else "formal"
            try:
                formal_document = TravelPlanDocumentV3.model_validate(formal_document).model_dump(mode="json")
            except (TypeError, ValueError):
                raise TripRepositoryError("INVALID_DRAFT_DOCUMENT", "草稿结构无效，请重新编辑后再应用") from None

            revision, checksum = self._replace_formal_snapshot(
                connection, device_id, trip, formal_document, now
            )
            response = {
                "operation_id": operation_id,
                "trip_id": trip_id,
                "revision": revision,
                "checksum": checksum,
                "status": "applied",
                "document": formal_document,
                "validation": validation,
                "idempotent_replay": False,
            }
            connection.execute(
                "INSERT INTO operation_receipts("
                "device_id, operation_id, trip_id, operation_type, response_json, created_at"
                ") VALUES (?, ?, ?, 'apply_draft_formal', ?, ?)",
                (device_id, operation_id, trip_id, _json_dumps(response), now),
            )
            return response

    def restore_previous_snapshot(
        self,
        device_id: str,
        trip_id: str,
        operation_id: str,
        expected_current_revision: int,
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self._transaction() as connection:
            receipt = self._existing_receipt(connection, device_id, operation_id)
            if receipt is not None:
                return {**receipt, "idempotent_replay": True}
            trip = self._owned_trip(connection, device_id, trip_id)
            current_revision = int(trip["current_revision"])
            if expected_current_revision != current_revision:
                raise RevisionConflictError()
            if connection.execute(
                "SELECT 1 FROM drafts WHERE device_id = ?", (device_id,)
            ).fetchone() is not None:
                raise RevisionConflictError("请先应用或放弃未应用修改，再恢复上一正式版本")
            previous = connection.execute(
                "SELECT * FROM formal_snapshots WHERE trip_id = ? AND slot = 'previous'",
                (trip_id,),
            ).fetchone()
            if previous is None:
                raise TripRepositoryError("PREVIOUS_REVISION_NOT_FOUND", "没有可以恢复的上一正式版本")
            restored_document = _json_loads(previous["document_json"], {})
            restored_document["revision"] = current_revision + 1
            validated = TravelPlanDocumentV3.model_validate(restored_document).model_dump(mode="json")
            checksum = _checksum(validated)
            connection.execute(
                "DELETE FROM formal_snapshots WHERE trip_id = ?",
                (trip_id,),
            )
            connection.execute(
                "INSERT INTO formal_snapshots("
                "trip_id, slot, revision, document_json, checksum, created_at"
                ") VALUES (?, 'current', ?, ?, ?, ?)",
                (trip_id, current_revision + 1, _json_dumps(validated), checksum, now),
            )
            connection.execute(
                "UPDATE trips SET current_revision = ?, status = ?, updated_at = ?, content_updated_at = ? "
                "WHERE trip_id = ? AND device_id = ?",
                (
                    current_revision + 1,
                    "completed_degraded" if validated.get("status") == "degraded" else "completed",
                    now,
                    now,
                    trip_id,
                    device_id,
                ),
            )
            response = {
                "operation_id": operation_id,
                "trip_id": trip_id,
                "revision": current_revision + 1,
                "checksum": checksum,
                "status": "restored",
                "document": validated,
                "idempotent_replay": False,
            }
            connection.execute(
                "INSERT INTO operation_receipts("
                "device_id, operation_id, trip_id, operation_type, response_json, created_at"
                ") VALUES (?, ?, ?, 'restore_previous', ?, ?)",
                (device_id, operation_id, trip_id, _json_dumps(response), now),
            )
            return response

    def begin_planning_run(
        self,
        device_id: str,
        trip_id: str,
        run_id: str,
        request_id: str,
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self._transaction() as connection:
            trip = self._owned_trip(connection, device_id, trip_id)
            same_run = connection.execute(
                "SELECT * FROM planning_runs WHERE run_id = ? AND device_id = ?",
                (run_id, device_id),
            ).fetchone()
            if same_run:
                if same_run["status"] in {"running", "retrying"}:
                    raise ActivePlanningRunError()
                raise TripRepositoryError("RUN_ID_REUSED", "本次规划请求已经结束，请重新发起")
            active = connection.execute(
                "SELECT run_id FROM planning_runs WHERE device_id = ? "
                "AND status IN ('running', 'retrying')",
                (device_id,),
            ).fetchone()
            if active:
                raise ActivePlanningRunError()
            connection.execute(
                "INSERT INTO planning_runs("
                "run_id, device_id, trip_id, request_id, status, started_at, updated_at, "
                "previous_trip_status"
                ") VALUES (?, ?, ?, ?, 'running', ?, ?, ?)",
                (run_id, device_id, trip_id, request_id, now, now, trip["status"]),
            )
            connection.execute(
                "UPDATE trips SET status = 'planning', updated_at = ? "
                "WHERE trip_id = ? AND device_id = ?",
                (now, trip_id, device_id),
            )
        return {"run_id": run_id, "trip_id": trip_id, "status": "running", "started_at": now}

    def finish_planning_run(self, device_id: str, run_id: str, status: str) -> None:
        if status not in {"completed", "degraded", "failed", "cancelled"}:
            raise ValueError("invalid terminal planning status")
        now = utc_now_iso()
        with self._transaction() as connection:
            run = connection.execute(
                "SELECT trip_id, previous_trip_status FROM planning_runs "
                "WHERE run_id = ? AND device_id = ? AND status IN ('running', 'retrying')",
                (run_id, device_id),
            ).fetchone()
            if run is None:
                return
            connection.execute(
                "UPDATE planning_runs SET status = ?, updated_at = ?, completed_at = ? "
                "WHERE run_id = ? AND device_id = ? AND status IN ('running', 'retrying')",
                (status, now, now, run_id, device_id),
            )
            if status == "degraded":
                trip_status = "completed_degraded"
            else:
                trip_status = status
            connection.execute(
                "UPDATE trips SET status = ?, updated_at = ? WHERE trip_id = ? AND device_id = ?",
                (trip_status, now, run["trip_id"], device_id),
            )

    def active_planning_run(self, device_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT run_id, trip_id, request_id, status, started_at, updated_at "
                "FROM planning_runs WHERE device_id = ? AND status IN ('running', 'retrying')",
                (device_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def append_run_event(
        self,
        run_id: str,
        sequence: int,
        event_id: str,
        event: Dict[str, Any],
        occurred_at: Optional[str] = None,
    ) -> bool:
        persisted_at = utc_now_iso()
        event_time = _normalized_iso(occurred_at, persisted_at)
        expires = (datetime.fromisoformat(persisted_at) + timedelta(minutes=5)).isoformat()
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO run_events("
                "run_id, sequence, event_id, event_json, occurred_at, expires_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, sequence, event_id, _json_dumps(event), event_time, expires),
            )
            connection.execute(
                "UPDATE planning_runs SET last_sequence = MAX(last_sequence, ?), updated_at = ? "
                "WHERE run_id = ?",
                (sequence, utc_now_iso(), run_id),
            )
            connection.execute("DELETE FROM run_events WHERE expires_at < ?", (utc_now_iso(),))
        return cursor.rowcount > 0

    def recent_run_events(self, run_id: str, after_sequence: int = 0) -> List[Dict[str, Any]]:
        now = utc_now_iso()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT sequence, event_id, event_json, occurred_at FROM run_events "
                "WHERE run_id = ? AND sequence > ? AND expires_at >= ? ORDER BY sequence",
                (run_id, after_sequence, now),
            ).fetchall()
        return [
            {
                "sequence": int(row["sequence"]),
                "event_id": row["event_id"],
                "event": _json_loads(row["event_json"], {}),
                "occurred_at": row["occurred_at"],
            }
            for row in rows
        ]

    def planning_run(self, device_id: str, run_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT run_id, device_id, trip_id, request_id, status, started_at, "
                "updated_at, completed_at, last_sequence FROM planning_runs "
                "WHERE run_id = ? AND device_id = ?",
                (run_id, device_id),
            ).fetchone()
        return None if row is None else dict(row)

    def clear_run_events(self, device_id: str, run_id: str) -> int:
        with self._transaction() as connection:
            owned = connection.execute(
                "SELECT 1 FROM planning_runs WHERE run_id = ? AND device_id = ?",
                (run_id, device_id),
            ).fetchone()
            if owned is None:
                raise TripRepositoryError("PLANNING_RUN_NOT_FOUND", "规划任务不存在")
            cursor = connection.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
        return int(cursor.rowcount)

    def delete_trip(self, device_id: str, trip_id: str) -> Dict[str, Any]:
        with self._transaction() as connection:
            self._owned_trip(connection, device_id, trip_id)
            active = connection.execute(
                "SELECT 1 FROM planning_runs WHERE device_id = ? AND trip_id = ? "
                "AND status IN ('running', 'retrying')",
                (device_id, trip_id),
            ).fetchone()
            if active:
                raise ActivePlanningRunError()
            connection.execute(
                "DELETE FROM trips WHERE trip_id = ? AND device_id = ?",
                (trip_id, device_id),
            )
        return {"deleted": 1, "trip_id": trip_id}

    def delete_all_trips(self, device_id: str) -> Dict[str, Any]:
        with self._transaction() as connection:
            active = connection.execute(
                "SELECT 1 FROM planning_runs WHERE device_id = ? "
                "AND status IN ('running', 'retrying')",
                (device_id,),
            ).fetchone()
            if active:
                raise ActivePlanningRunError()
            count = int(connection.execute(
                "SELECT COUNT(*) FROM trips WHERE device_id = ?",
                (device_id,),
            ).fetchone()[0])
            connection.execute("DELETE FROM trips WHERE device_id = ?", (device_id,))
        return {"deleted": count}
