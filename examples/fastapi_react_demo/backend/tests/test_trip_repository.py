import copy
import json
import tempfile
import unittest
from pathlib import Path

from backend.services.anonymous_device_service import hash_device_token, resolve_anonymous_device
from backend.services.persistence_sanitizer import REDACTED, sanitize_for_persistence
from backend.services.trip_repository import (
    ActivePlanningRunError,
    RevisionConflictError,
    TripNotFoundError,
    TripRepository,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


class TripRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="sta-trip-repo-")
        self.database_path = Path(self.temp_dir.name) / "travel.sqlite3"
        self.repository = TripRepository(self.database_path)
        self.repository.initialize()
        self.device_id = self.repository.get_or_create_device(hash_device_token("device-token-1"))
        self.fixture = json.loads(
            (FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8")
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_initializes_wal_and_reuses_hashed_anonymous_device(self):
        info = self.repository.database_info()
        same_device = self.repository.get_or_create_device(hash_device_token("device-token-1"))
        identity = resolve_anonymous_device(self.repository, "device-token-1")

        self.assertEqual("wal", info["journal_mode"])
        self.assertEqual(2, info["schema_version"])
        self.assertEqual(self.device_id, same_device)
        self.assertEqual(self.device_id, identity.device_id)
        self.assertFalse(identity.should_set_cookie)

    def test_history_sorting_uses_content_update_and_system_save_does_not_reorder(self):
        first = self.repository.upsert_trip(
            self.device_id,
            "trip-1",
            title="杭州",
            messages=[{"role": "user", "content": "杭州三日游"}],
            change_reason="user_message",
            created_at="2026-07-15T08:00:00+00:00",
            content_updated_at="2026-07-15T08:00:00+00:00",
        )
        self.repository.upsert_trip(
            self.device_id,
            "trip-2",
            title="东京",
            messages=[{"role": "user", "content": "东京五日游"}],
            change_reason="user_message",
            created_at="2026-07-15T09:00:00+00:00",
            content_updated_at="2026-07-15T09:00:00+00:00",
        )
        repeated = self.repository.upsert_trip(
            self.device_id,
            "trip-1",
            title="杭州",
            messages=[{"role": "user", "content": "杭州三日游"}],
            change_reason="system",
        )

        page = self.repository.list_trips(self.device_id)
        self.assertEqual(first["contentUpdatedAt"], repeated["contentUpdatedAt"])
        self.assertEqual(["trip-2", "trip-1"], [item["id"] for item in page["items"]])
        self.assertEqual("东京五日游", page["items"][0]["preview"])

    def test_legacy_migration_is_transactional_and_idempotent(self):
        item = {
            "id": "legacy-1",
            "title": "旧杭州行程",
            "messages": [{"role": "user", "content": "旧行程"}],
            "createdAt": "2026-07-01T08:00:00Z",
            "contentUpdatedAt": "2026-07-01T09:00:00Z",
            "tripPlan": {"version": 2},
        }

        first = self.repository.migrate_legacy_trips(self.device_id, [item])
        second = self.repository.migrate_legacy_trips(self.device_id, [item])

        self.assertEqual({"imported": 1, "skipped": 0}, first)
        self.assertEqual({"imported": 0, "skipped": 1}, second)
        self.assertEqual(2, self.repository.get_trip(self.device_id, "legacy-1")["tripPlan"]["version"])

    def test_only_one_draft_exists_per_anonymous_device(self):
        self.repository.ensure_trip(self.device_id, "trip-1")
        self.repository.ensure_trip(self.device_id, "trip-2")
        self.repository.save_draft(self.device_id, "trip-1", {"title": "草稿一"})
        self.repository.save_draft(self.device_id, "trip-2", {"title": "草稿二"})

        self.assertIsNone(self.repository.get_trip(self.device_id, "trip-1")["draft"])
        self.assertEqual(
            "草稿二",
            self.repository.get_trip(self.device_id, "trip-2")["draft"]["document"]["title"],
        )

    def test_formal_apply_is_idempotent_and_keeps_only_current_and_previous(self):
        self.repository.ensure_trip(self.device_id, "trip-1")
        first_document = copy.deepcopy(self.fixture)
        first_document["revision"] = 1
        first = self.repository.apply_formal_snapshot(
            self.device_id, "trip-1", "op-1", first_document
        )
        replay = self.repository.apply_formal_snapshot(
            self.device_id, "trip-1", "op-1", first_document
        )
        second_document = copy.deepcopy(first_document)
        second_document["revision"] = 2
        second_document["title"] = "第二版"
        self.repository.apply_formal_snapshot(
            self.device_id, "trip-1", "op-2", second_document
        )

        stored = self.repository.get_trip(self.device_id, "trip-1")
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(2, stored["formalSnapshots"]["current"]["revision"])
        self.assertEqual(1, stored["formalSnapshots"]["previous"]["revision"])

    def test_restore_creates_monotonic_revision_and_removes_redo_slot(self):
        self.repository.ensure_trip(self.device_id, "trip-1")
        first_document = copy.deepcopy(self.fixture)
        first_document["revision"] = 1
        self.repository.apply_formal_snapshot(self.device_id, "trip-1", "op-1", first_document)
        second_document = copy.deepcopy(first_document)
        second_document["revision"] = 2
        second_document["title"] = "不再保留的第二版"
        self.repository.apply_formal_snapshot(self.device_id, "trip-1", "op-2", second_document)

        restored = self.repository.restore_previous_snapshot(
            self.device_id, "trip-1", "op-restore", expected_current_revision=2
        )
        stored = self.repository.get_trip(self.device_id, "trip-1")

        self.assertEqual(3, restored["revision"])
        self.assertEqual(3, stored["formalSnapshots"]["current"]["revision"])
        self.assertNotIn("previous", stored["formalSnapshots"])
        self.assertEqual(first_document["title"], stored["formalSnapshots"]["current"]["document"]["title"])

    def test_revision_conflict_rolls_back_without_replacing_current_snapshot(self):
        self.repository.ensure_trip(self.device_id, "trip-1")
        first_document = copy.deepcopy(self.fixture)
        first_document["revision"] = 1
        self.repository.apply_formal_snapshot(self.device_id, "trip-1", "op-1", first_document)
        invalid = copy.deepcopy(first_document)
        invalid["revision"] = 3

        with self.assertRaises(RevisionConflictError):
            self.repository.apply_formal_snapshot(self.device_id, "trip-1", "op-bad", invalid)

        stored = self.repository.get_trip(self.device_id, "trip-1")
        self.assertEqual(1, stored["formalSnapshots"]["current"]["revision"])
        self.assertNotIn("previous", stored["formalSnapshots"])

    def test_running_trip_blocks_single_and_bulk_delete_then_cascades_after_finish(self):
        self.repository.ensure_trip(self.device_id, "trip-1")
        self.repository.begin_planning_run(
            self.device_id, "trip-1", "run-1", "request-1"
        )
        self.assertEqual("planning", self.repository.get_trip(self.device_id, "trip-1")["status"])

        with self.assertRaises(ActivePlanningRunError):
            self.repository.delete_trip(self.device_id, "trip-1")
        with self.assertRaises(ActivePlanningRunError):
            self.repository.delete_all_trips(self.device_id)

        self.repository.finish_planning_run(self.device_id, "run-1", "cancelled")
        self.assertEqual("draft", self.repository.get_trip(self.device_id, "trip-1")["status"])
        self.assertEqual(1, self.repository.delete_all_trips(self.device_id)["deleted"])
        with self.assertRaises(TripNotFoundError):
            self.repository.get_trip(self.device_id, "trip-1")

    def test_sensitive_values_are_removed_before_storage(self):
        sanitized = sanitize_for_persistence({
            "passport_number": "E12345678",
            "message": "身份证号 11010519491231002X，验证码 123456",
            "card_in_text": "请核对 4111111111111111",
            "source_url": "https://example.com/article/1951599784315064475",
            "nested": {"payment": {"card": "6222021234567890"}},
        })

        self.assertEqual(REDACTED, sanitized["passport_number"])
        self.assertNotIn("11010519491231002X", sanitized["message"])
        self.assertNotIn("123456", sanitized["message"])
        self.assertNotIn("4111111111111111", sanitized["card_in_text"])
        self.assertEqual(
            "https://example.com/article/1951599784315064475",
            sanitized["source_url"],
        )
        self.assertEqual(REDACTED, sanitized["nested"]["payment"])


if __name__ == "__main__":
    unittest.main()
