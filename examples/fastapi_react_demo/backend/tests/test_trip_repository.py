import copy
import json
import tempfile
import unittest
from pathlib import Path

from backend.services.anonymous_device_service import (
    LOCAL_SHARED_DEVICE_ID,
    LOCAL_SHARED_DEVICE_TOKEN,
    hash_device_token,
    resolve_anonymous_device,
)
from backend.services.persistence_sanitizer import REDACTED, sanitize_for_persistence
from backend.services.trip_repository import (
    ActivePlanningRunError,
    RevisionConflictError,
    TripNotFoundError,
    TripRepositoryError,
    TripRepository,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


class TripRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="sta-trip-repo-")
        self.database_path = Path(self.temp_dir.name) / "travel.sqlite3"
        self.repository = TripRepository(self.database_path)
        self.repository.initialize()
        self.device_token = "device-token-one-000000000000000000"
        self.device_id = self.repository.get_or_create_device(hash_device_token(self.device_token))
        self.fixture = json.loads(
            (FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8")
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_initializes_wal_and_reuses_hashed_anonymous_device(self):
        info = self.repository.database_info()
        same_device = self.repository.get_or_create_device(hash_device_token(self.device_token))
        identity = resolve_anonymous_device(self.repository, self.device_token)
        migrated_identity = resolve_anonymous_device(self.repository, LOCAL_SHARED_DEVICE_TOKEN)

        self.assertEqual("wal", info["journal_mode"])
        self.assertEqual(4, info["schema_version"])
        self.assertEqual(self.device_id, same_device)
        self.assertEqual(self.device_id, identity.device_id)
        self.assertNotEqual(identity.device_id, migrated_identity.device_id)
        self.assertFalse(identity.should_set_cookie)
        self.assertTrue(migrated_identity.should_set_cookie)

    def test_restart_marks_interrupted_planning_trip_cancelled_instead_of_draft(self):
        self.repository.ensure_trip(self.device_id, "trip-interrupted", "未完成规划")
        self.repository.begin_planning_run(
            self.device_id,
            "trip-interrupted",
            "run-interrupted",
            "request-interrupted",
        )

        restarted = TripRepository(self.database_path)
        restarted.initialize()

        self.assertEqual(
            "cancelled",
            restarted.get_trip(self.device_id, "trip-interrupted")["status"],
        )
        self.assertEqual(
            "cancelled",
            restarted.planning_run(self.device_id, "run-interrupted")["status"],
        )

    def test_consolidates_all_browser_histories_and_keeps_only_latest_draft(self):
        second_device = self.repository.get_or_create_device(hash_device_token("device-token-2"))
        self.repository.ensure_trip(self.device_id, "trip-browser-one", "浏览器一")
        self.repository.ensure_trip(second_device, "trip-browser-two", "浏览器二")
        self.repository.save_draft(self.device_id, "trip-browser-one", {"title": "旧草稿"})
        self.repository.save_draft(second_device, "trip-browser-two", {"title": "最新草稿"})
        self.repository.begin_planning_run(
            self.device_id,
            "trip-browser-one",
            "run-before-merge",
            "request-before-merge",
        )

        result = self.repository.consolidate_local_history()
        migrated_device = self.repository.get_or_create_device(hash_device_token("migrated-browser-token-000000000000"))
        claimed = self.repository.claim_legacy_shared_history(migrated_device)
        shared_device = migrated_device
        page = self.repository.list_trips(shared_device)

        self.assertEqual(2, result["devices_merged"])
        self.assertEqual(2, result["trips_preserved"])
        self.assertEqual(1, result["drafts_preserved"])
        self.assertEqual(1, result["runs_cancelled"])
        self.assertEqual(2, claimed)
        self.assertEqual(
            {"trip-browser-one", "trip-browser-two"},
            {item["id"] for item in page["items"]},
        )
        self.assertIsNone(self.repository.get_trip(shared_device, "trip-browser-one")["draft"])
        self.assertEqual(
            "最新草稿",
            self.repository.get_trip(shared_device, "trip-browser-two")["draft"]["document"]["title"],
        )
        self.assertIsNone(self.repository.active_planning_run(shared_device))
        self.assertEqual("draft", self.repository.get_trip(shared_device, "trip-browser-one")["status"])
        self.assertEqual(0, self.repository.list_trips(LOCAL_SHARED_DEVICE_ID)["total"])

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

    def test_persists_agent_stage_summary_independently_from_formal_snapshot(self):
        timeline = {
            "schema_version": 1,
            "request_id": "request-1",
            "status": "completed",
            "stages": {"research": {"summary": "已汇总公开资料", "status": "completed"}},
        }
        self.repository.upsert_trip(
            self.device_id,
            "trip-agent-timeline",
            title="杭州三日游",
            messages=[{"role": "user", "content": "杭州三日游"}],
            agent_timeline=timeline,
            change_reason="system",
        )
        formal_document = copy.deepcopy(self.fixture)
        self.repository.apply_formal_snapshot(
            self.device_id, "trip-agent-timeline", "op-agent-timeline", formal_document
        )

        stored = self.repository.get_trip(self.device_id, "trip-agent-timeline")

        self.assertEqual("completed", stored["agentTimeline"]["status"])
        self.assertEqual("已汇总公开资料", stored["agentTimeline"]["stages"]["research"]["summary"])
        self.assertEqual("3.0", stored["formalSnapshots"]["current"]["document"]["schema_version"])

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

    def test_apply_draft_is_atomic_uses_stored_draft_and_only_increments_formal_once(self):
        self.repository.ensure_trip(self.device_id, "trip-1")
        original = copy.deepcopy(self.fixture)
        original["revision"] = 1
        self.repository.apply_formal_snapshot(self.device_id, "trip-1", "op-initial", original)
        draft = copy.deepcopy(original)
        draft["revision"] = 3
        draft["title"] = "连续两次修改后的草稿"
        self.repository.save_draft(self.device_id, "trip-1", draft, [{"operation_id": "edit-2"}])

        applied = self.repository.apply_draft_as_formal(self.device_id, "trip-1", "op-apply", 3)
        replay = self.repository.apply_draft_as_formal(self.device_id, "trip-1", "op-apply", 3)
        stored = self.repository.get_trip(self.device_id, "trip-1")

        self.assertEqual(2, applied["revision"])
        self.assertEqual(2, applied["document"]["revision"])
        self.assertEqual("连续两次修改后的草稿", applied["document"]["title"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertIsNone(stored["draft"])
        self.assertEqual(2, stored["formalSnapshots"]["current"]["revision"])
        self.assertEqual(1, stored["formalSnapshots"]["previous"]["revision"])

    def test_apply_draft_blocks_hard_errors_without_touching_current_or_draft(self):
        self.repository.ensure_trip(self.device_id, "trip-1")
        original = copy.deepcopy(self.fixture)
        original["revision"] = 1
        self.repository.apply_formal_snapshot(self.device_id, "trip-1", "op-initial", original)
        invalid_draft = {"revision": 2, "plan_id": original["plan_id"]}
        self.repository.save_draft(self.device_id, "trip-1", invalid_draft)

        with self.assertRaises(TripRepositoryError) as raised:
            self.repository.apply_draft_as_formal(self.device_id, "trip-1", "op-apply-invalid", 2)

        stored = self.repository.get_trip(self.device_id, "trip-1")
        self.assertEqual("DRAFT_VALIDATION_BLOCKED", raised.exception.code)
        self.assertEqual(1, stored["formalSnapshots"]["current"]["revision"])
        self.assertEqual(invalid_draft, stored["draft"]["document"])

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

    def test_restore_refuses_to_skip_an_unapplied_draft(self):
        self.repository.ensure_trip(self.device_id, "trip-1")
        first_document = copy.deepcopy(self.fixture)
        first_document["revision"] = 1
        self.repository.apply_formal_snapshot(self.device_id, "trip-1", "op-1", first_document)
        second_document = copy.deepcopy(first_document)
        second_document["revision"] = 2
        self.repository.apply_formal_snapshot(self.device_id, "trip-1", "op-2", second_document)
        draft = copy.deepcopy(second_document)
        draft["revision"] = 3
        self.repository.save_draft(self.device_id, "trip-1", draft)

        with self.assertRaises(RevisionConflictError):
            self.repository.restore_previous_snapshot(self.device_id, "trip-1", "op-restore", 2)

    def test_revision_stream_receipt_only_exposes_the_current_formal_version(self):
        self.repository.ensure_trip(self.device_id, "trip-1")
        first_document = copy.deepcopy(self.fixture)
        first_document["revision"] = 1
        self.repository.apply_formal_snapshot(self.device_id, "trip-1", "op-initial", first_document)
        draft = copy.deepcopy(first_document)
        draft["revision"] = 2
        draft["title"] = "应用后通过修订流发布"
        self.repository.save_draft(self.device_id, "trip-1", draft)
        applied = self.repository.apply_draft_as_formal(self.device_id, "trip-1", "op-apply", 2)

        current = self.repository.current_revision_operation(self.device_id, "trip-1", "op-apply")
        restored = self.repository.restore_previous_snapshot(self.device_id, "trip-1", "op-restore", applied["revision"])

        self.assertEqual(applied["revision"], current["revision"])
        self.assertEqual("应用后通过修订流发布", current["document"]["title"])
        self.assertEqual(applied["revision"] + 1, restored["revision"])
        with self.assertRaises(RevisionConflictError):
            self.repository.current_revision_operation(self.device_id, "trip-1", "op-apply")

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
        self.assertEqual("cancelled", self.repository.get_trip(self.device_id, "trip-1")["status"])
        self.assertEqual(1, self.repository.delete_all_trips(self.device_id)["deleted"])
        with self.assertRaises(TripNotFoundError):
            self.repository.get_trip(self.device_id, "trip-1")

    def test_second_tab_cannot_replace_the_active_run(self):
        self.repository.ensure_trip(self.device_id, "trip-tab-a")
        self.repository.ensure_trip(self.device_id, "trip-tab-b")
        self.repository.begin_planning_run(
            self.device_id, "trip-tab-a", "run-tab-a", "request-tab-a"
        )

        with self.assertRaises(ActivePlanningRunError):
            self.repository.begin_planning_run(
                self.device_id, "trip-tab-b", "run-tab-b", "request-tab-b"
            )

        active = self.repository.active_planning_run(self.device_id)
        self.assertEqual("run-tab-a", active["run_id"])
        self.assertEqual("planning", self.repository.get_trip(self.device_id, "trip-tab-a")["status"])
        self.assertEqual("draft", self.repository.get_trip(self.device_id, "trip-tab-b")["status"])

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
