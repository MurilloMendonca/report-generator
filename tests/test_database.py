import threading
import unittest
import uuid

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app import db
from app.models.db import Report
from app.services.session_manager import (
    add_log, begin_exploration_turn, delete_session, get_session,
    list_recent_sessions, recover_interrupted_sessions, update_session,
)
from app.utils.template_library import (
    TemplateNotEditable, delete_layout_template, load_layout_templates, save_layout_templates, upsert_layout_template,
)
from tests.auth_helpers import create_session, default_user, make_user


def new_job(test, **state):
    job_id = f"test-db-{uuid.uuid4().hex}"
    create_session(job_id, {"job_id": job_id, **state})
    test.addCleanup(delete_session, job_id)
    return job_id


class ReportStorageTests(unittest.TestCase):
    def test_values_postgres_rejects_are_cleaned(self):
        job_id = new_job(self, status="a\x00b", calculated_metrics={"x": float("nan"), "y": float("inf"), "z": 1.5})
        session = get_session(job_id)
        self.assertEqual(session["status"], "ab")
        self.assertEqual(session["calculated_metrics"], {"x": None, "y": None, "z": 1.5})

    def test_concurrent_updates_are_not_lost(self):
        job_id = new_job(self)
        threads = [threading.Thread(target=add_log, args=(job_id, f"line {n}")) for n in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(get_session(job_id)["logs"]), 20)

    def test_columns_follow_the_document(self):
        job_id = new_job(self, initial_prompt="Vendas", settings={"title": "Relatório de vendas"})
        update_session(job_id, {"current_step": "script", "pdf_version": 2, "status": "Gerando"})
        with db.transaction() as tx:
            report = tx.get(Report, job_id)
            self.assertEqual((report.title, report.current_step, report.pdf_version, report.status),
                             ("Relatório de vendas", "script", 2, "Gerando"))

    def test_create_replaces_an_existing_document(self):
        job_id = new_job(self, initial_prompt="first")
        create_session(job_id, {"job_id": job_id, "initial_prompt": "second"})
        self.assertEqual(get_session(job_id)["initial_prompt"], "second")

    def test_missing_and_deleted_reports(self):
        self.assertIsNone(update_session("test-db-missing", {"status": "x"}))
        self.assertIsNone(add_log("test-db-missing", "x"))
        job_id = new_job(self)
        self.assertTrue(delete_session(job_id))
        self.assertIsNone(get_session(job_id))
        self.assertFalse(delete_session(job_id))

    def test_recent_list_is_newest_first(self):
        older = new_job(self, initial_prompt="older", files=[{"original_filename": "a.csv", "path": "a.csv"}])
        newer = new_job(self, initial_prompt="newer")
        update_session(older, {"status": "touched"})  # now the most recent
        recent = [item for item in list_recent_sessions(default_user().id, limit=50) if item["job_id"] in {older, newer}]
        self.assertEqual([item["job_id"] for item in recent], [older, newer])
        self.assertEqual(recent[0]["file_count"], 1)
        self.assertEqual(recent[0]["initial_prompt"], "older")

    def test_recovery_finds_pending_explorer_turns_in_finished_reports(self):
        job_id = new_job(self, current_step="completed")
        begin_exploration_turn(job_id, {"role": "user", "content": "q"}, "turn1")
        self.assertIn(job_id, recover_interrupted_sessions())
        session = get_session(job_id)
        self.assertEqual(session["explore_history"][-1]["status"], "error")
        self.assertEqual(session["current_step"], "completed")


class TemplateStorageTests(unittest.TestCase):
    def setUp(self):
        self.owner, self.other = make_user().id, make_user().id

    def test_built_ins_come_first_then_your_own(self):
        mine = upsert_layout_template({"name": "Mine", "slots": []}, self.owner)
        theirs = upsert_layout_template({"name": "Theirs", "slots": []}, self.other)
        templates = load_layout_templates(self.owner)
        ids = [template["id"] for template in templates]
        self.assertEqual(ids[:3], ["executive_standard", "data_heavy", "document_text_only"])
        self.assertTrue(all(template["builtin"] for template in templates[:3]))
        self.assertEqual(ids[-1], mine["id"])
        self.assertNotIn(theirs["id"], ids)
        self.assertFalse(templates[-1]["builtin"])

    def test_built_ins_cannot_be_changed_or_deleted(self):
        with self.assertRaises(TemplateNotEditable):
            upsert_layout_template({"id": "data_heavy", "name": "Edited", "slots": []}, self.owner)
        self.assertFalse(delete_layout_template("executive_standard", self.owner))
        self.assertEqual(load_layout_templates(self.owner)[1]["id"], "data_heavy")

    def test_someone_elses_template_is_invisible(self):
        theirs = upsert_layout_template({"name": "Theirs", "slots": []}, self.other)
        with self.assertRaises(LookupError):
            upsert_layout_template({"id": theirs["id"], "name": "Hijacked", "slots": []}, self.owner)
        self.assertFalse(delete_layout_template(theirs["id"], self.owner))
        self.assertEqual(load_layout_templates(self.other)[-1]["name"], "Theirs")
        with self.assertRaises(LookupError):
            upsert_layout_template({"id": "tpl_missing", "name": "x", "slots": []}, self.owner, must_exist=True)


class TestDatabaseGuardTests(unittest.TestCase):
    def test_refuses_a_database_not_named_for_tests(self):
        with self.assertRaises(RuntimeError):
            db._create_test_database(make_url("postgresql+psycopg://u:p@localhost/repgen"))

    def test_schema_is_at_the_latest_migration(self):
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from alembic.config import Config

        config = Config(str(db.ROOT_DIR / "alembic.ini"))
        head = ScriptDirectory.from_config(config).get_current_head()
        with db.get_engine().connect() as conn:
            self.assertEqual(MigrationContext.configure(conn).get_current_revision(), head)
            # And the models match the migrations (no forgotten `alembic revision`).
            from alembic.autogenerate import compare_metadata
            from app.models.db import Base
            self.assertEqual(compare_metadata(MigrationContext.configure(conn), Base.metadata), [])


if __name__ == "__main__":
    unittest.main()
