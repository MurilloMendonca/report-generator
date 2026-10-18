import importlib.util
import io
import os
import shutil
import tarfile
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.services import deployment_service, session_manager
from app.services.cleanup_service import delete_entire_session, get_session_generated_paths
from app.services.executor_service import execute_script
from app.utils.report_paths import LegacyStorageError, ReportPaths
from tests.auth_helpers import create_session, make_user


class DeploymentTests(unittest.TestCase):
    def test_launch_checks_do_not_accept_arbitrary_access_issuers(self):
        from app.services.cloudflare_access import configuration
        with patch.dict(os.environ, {"CLOUDFLARE_ACCESS_ISSUER": "https://example.com/jwks", "CLOUDFLARE_ACCESS_AUD": "aud"}):
            self.assertFalse(configuration()[2])
        with patch.dict(os.environ, {"CLOUDFLARE_ACCESS_ISSUER": "https://repgen.cloudflareaccess.com/", "CLOUDFLARE_ACCESS_AUD": "aud"}):
            self.assertTrue(configuration()[2])

    def test_production_cannot_mount_shared_folders(self):
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            with self.assertRaises(LegacyStorageError):
                ReportPaths("test-old", None).ensure()
            with self.assertRaises(LegacyStorageError):
                execute_script("print('never')", "workspace/scripts/test-never.py")
        self.assertFalse(Path("workspace/scripts/test-never.py").exists())

    def test_fresh_production_sandbox_probe_uses_isolated_temporary_folders(self):
        from app.services import executor_service
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            available, reason = executor_service.probe_bubblewrap(force=True)
        self.assertTrue(available, reason)

    def test_production_refuses_unsafe_settings_and_unprotected_signup(self):
        import app.security as security
        with patch.dict(os.environ, {"APP_ENV": "production", "PDF_REPORT_ALLOW_UNSAFE_SANDBOX": "1"}), patch.object(security, "COOKIE_SECURE", True):
            with self.assertRaises(RuntimeError):
                deployment_service.validate_runtime_configuration()
        with patch.dict(os.environ, {"APP_ENV": "production", "PDF_REPORT_ALLOW_UNSAFE_SANDBOX": "0", "PDF_REPORT_SIGNUP_MODE": "open", "SMTP_HOST": "", "TURNSTILE_SITE_KEY": "", "TURNSTILE_SECRET_KEY": ""}), patch.object(security, "COOKIE_SECURE", True):
            with self.assertRaises(RuntimeError):
                deployment_service.validate_runtime_configuration()

    def test_existing_db_report_storage_migrates_without_removing_original(self):
        user = make_user()
        job = f"test-migrate-{uuid.uuid4().hex}"
        source = Path("workspace/uploads") / f"{job}_source.csv"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("value\n42\n")
        self.addCleanup(source.unlink, missing_ok=True)
        create_session(job, {"job_id": job, "current_step": "completed", "files": [{"path": str(source), "original_filename": "source.csv"}]}, owner=user)
        self.addCleanup(delete_entire_session, job)
        self.assertEqual(session_manager.migrate_report_storage(job, dry_run=True), "migrated")
        self.assertTrue(ReportPaths.from_session(session_manager.get_session(job)).is_legacy)
        self.assertEqual(session_manager.migrate_report_storage(job), "migrated")
        state = session_manager.get_session(job)
        paths = ReportPaths.from_session(state)
        self.assertEqual(paths.root.parent.parent.name, str(user.id))
        self.assertEqual(Path(state["files"][0]["path"]).read_text(), source.read_text())
        self.assertTrue(source.exists())
        self.assertEqual(session_manager.migrate_report_storage(job), "already_isolated")
        self.assertGreater(session_manager.storage_used(user.id), 0)

    def test_cleanup_does_not_match_a_longer_report_id(self):
        job = f"test-prefix-{uuid.uuid4().hex}"
        source = Path("workspace/scripts") / f"preprocess_{job}extra.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# another report")
        self.addCleanup(source.unlink, missing_ok=True)
        self.assertNotIn(str(source), get_session_generated_paths(job, {}))

    def test_missing_source_does_not_commit_migration(self):
        job = f"test-missing-{uuid.uuid4().hex}"
        create_session(job, {"job_id": job, "files": [{"path": f"workspace/uploads/{job}.csv", "original_filename": "missing.csv"}]})
        self.addCleanup(delete_entire_session, job)
        self.assertEqual(session_manager.migrate_report_storage(job), "missing_files")
        self.assertTrue(ReportPaths.from_session(session_manager.get_session(job)).is_legacy)

    def test_restore_archive_rejects_traversal_and_links(self):
        spec = importlib.util.spec_from_file_location("restore_drill", "scripts/restore-drill.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, kind in [("../outside", tarfile.REGTYPE), ("workspace/link", tarfile.SYMTYPE), ("secrets/keyring", tarfile.REGTYPE)]:
                archive = root / "bad.tar.gz"
                with tarfile.open(archive, "w:gz") as tar:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
                    tar.addfile(member, io.BytesIO())
                with self.assertRaises(ValueError):
                    module.checked_archive(archive, root / "unpack")
