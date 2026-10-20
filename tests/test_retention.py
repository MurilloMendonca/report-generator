import os
import json
import unittest
import shutil
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

from app.services.cleanup_service import (
    delete_entire_session, 
    cleanup_expired_artifacts, 
    get_workspace_usage,
    WORKSPACE_DIRS
)
from app.services.session_manager import get_session
from tests.auth_helpers import create_session

class TestRetention(unittest.TestCase):
    def setUp(self):
        self.job_id = "test-retention-job"
        # Ensure directories exist
        for d in WORKSPACE_DIRS.values():
            os.makedirs(d, exist_ok=True)
            
    def tearDown(self):
        # Cleanup any remaining test files
        delete_entire_session(self.job_id)

    def test_delete_entire_session_removes_all_files(self):
        # 1. Create a mock session
        session_data = {
            "job_id": self.job_id,
            "files": [
                {
                    "original_filename": "data.csv",
                    "path": os.path.join(WORKSPACE_DIRS["uploads"], f"{self.job_id}_data.csv"),
                    "preprocessed_path": os.path.join(WORKSPACE_DIRS["filtered"], f"{self.job_id}_data_p.csv")
                }
            ],
            "script_path": os.path.join(WORKSPACE_DIRS["scripts"], f"{self.job_id}.py"),
            "pdf_path": os.path.join(WORKSPACE_DIRS["reports"], f"{self.job_id}.pdf")
        }
        create_session(self.job_id, session_data)
        
        # 2. Create physical files (except the session JSON which was created by create_session)
        files_to_check = [
            session_data["files"][0]["path"],
            session_data["files"][0]["preprocessed_path"],
            session_data["script_path"],
            session_data["pdf_path"],
            f"{session_data['pdf_path']}.manifest.json"
        ]
        
        for p in files_to_check:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write("test content")
        
        # 3. Add an exploratory script (prefix match)
        explore_script = os.path.join(WORKSPACE_DIRS["scripts"], f"explore_{self.job_id}.py")
        with open(explore_script, "w") as f:
            f.write("print('explore')")
        files_to_check.append(explore_script)

        # 4. Verify files exist
        for p in files_to_check:
            self.assertTrue(os.path.exists(p), f"File {p} should exist before deletion")

        # 5. Delete session
        removed_count = delete_entire_session(self.job_id)
        
        # 6. Verify files are gone
        for p in files_to_check:
            self.assertFalse(os.path.exists(p), f"File {p} should be deleted")
        
        self.assertIsNone(get_session(self.job_id))
        self.assertGreaterEqual(removed_count, len(files_to_check) + 1)  # files plus the session row

    def test_cleanup_expired_artifacts(self):
        # Create an old orphan file
        old_file = os.path.join(WORKSPACE_DIRS["uploads"], "old_orphan.csv")
        with open(old_file, "w") as f:
            f.write("old")
        
        # Set mtime to 40 days ago
        past_time = time.time() - (40 * 24 * 60 * 60)
        os.utime(old_file, (past_time, past_time))
        
        # Run cleanup with default policy (30 days for uploads)
        stats = cleanup_expired_artifacts()
        
        self.assertFalse(os.path.exists(old_file))
        self.assertGreaterEqual(stats["files_removed"], 1)

    def test_cleanup_preserves_active_session_files(self):
        session_data = {
            "job_id": self.job_id,
            "files": [
                {
                    "original_filename": "active.csv",
                    "path": os.path.join(WORKSPACE_DIRS["uploads"], f"{self.job_id}_active.csv"),
                    "preprocessed_path": os.path.join(WORKSPACE_DIRS["filtered"], f"{self.job_id}_active_filtered.csv"),
                }
            ],
            "script_path": os.path.join(WORKSPACE_DIRS["scripts"], f"{self.job_id}.py"),
            "pdf_path": os.path.join(WORKSPACE_DIRS["reports"], f"{self.job_id}.pdf"),
        }
        create_session(self.job_id, session_data)

        active_paths = [
            session_data["files"][0]["path"],
            session_data["files"][0]["preprocessed_path"],
            session_data["script_path"],
            session_data["pdf_path"],
        ]
        for path in active_paths:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("active")
            past_time = time.time() - (40 * 24 * 60 * 60)
            os.utime(path, (past_time, past_time))

        stats = cleanup_expired_artifacts()

        for path in active_paths:
            self.assertTrue(os.path.exists(path), f"Active file {path} should not be removed")
        self.assertEqual(stats["files_removed"], 0)

    def test_workspace_usage_returns_dict(self):
        usage = get_workspace_usage()
        self.assertIn("total", usage)
        self.assertIn("sessions", usage)
        self.assertTrue(usage["total"]["human_size"].endswith("MB"))

if __name__ == "__main__":
    unittest.main()
