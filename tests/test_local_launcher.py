"""Exercise local bootstrap without starting Docker or touching repository secrets."""
import base64
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent.parent


class LocalLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory(prefix="repgen-local-launcher-"))
        self.root = Path(self.temp)
        (self.root / "scripts").mkdir()
        for name in ("scripts/local.sh", ".env.local.example", "compose.local.yml"):
            shutil.copyfile(ROOT / name, self.root / name)

    def run_launcher(self, *args, env=None):
        return subprocess.run(["sh", str(self.root / "scripts/local.sh"), *args],
                              cwd=self.root, capture_output=True, text=True, timeout=20, env=env)

    def test_init_generates_private_persistent_secrets_without_touching_production_env(self):
        (self.root / ".env").write_text("production-sentinel\n")
        result = self.run_launcher("init")
        self.assertEqual(result.returncode, 0, result.stderr)
        local_file = self.root / ".env.local"
        contents = local_file.read_text()
        values = dotenv_values(local_file)
        self.assertEqual(len(values["POSTGRES_PASSWORD"]), 48)
        self.assertEqual(len(base64.b64decode(values["APP_ENCRYPTION_KEYS"].split(":", 1)[1])), 32)
        self.assertEqual(local_file.stat().st_mode & 0o777, 0o600)
        endpoint = json.loads(values["PDF_REPORT_LLM_ENDPOINTS"])[0]
        self.assertFalse(endpoint["requires_api_key"])
        self.assertEqual(endpoint["base_url"], "http://host.docker.internal:11434/v1")
        self.assertNotIn(values["POSTGRES_PASSWORD"], result.stdout)
        self.assertNotIn(values["APP_ENCRYPTION_KEYS"], result.stdout)
        self.assertEqual(self.run_launcher("init").returncode, 0)
        self.assertEqual(local_file.read_text(), contents)
        self.assertEqual((self.root / ".env").read_text(), "production-sentinel\n")

    def test_up_and_down_use_only_local_project_and_do_not_remove_volumes(self):
        binary = self.root / "bin"
        binary.mkdir()
        docker = binary / "docker"
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$LOCAL_LAUNCHER_TRACE"\n')
        docker.chmod(0o700)
        trace = self.root / "trace"
        env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"], "LOCAL_LAUNCHER_TRACE": str(trace)}
        result = self.run_launcher("up", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_launcher("down", env=env).returncode, 0)
        calls = trace.read_text().splitlines()
        self.assertEqual(len(calls), 3)
        for call in calls:
            self.assertTrue(call.startswith("compose --project-name repgen-local --env-file .env.local --file compose.local.yml "))
            self.assertNotIn("--volumes", call)
        self.assertTrue(calls[0].endswith("up --build --detach --wait"))
        self.assertTrue(calls[1].endswith("exec -T app python -m app.cli create-user --email local@example.com"))
        self.assertTrue(calls[2].endswith("down"))

    def test_unknown_action_does_not_create_config(self):
        self.assertEqual(self.run_launcher("invalid-action").returncode, 2)
        self.assertFalse((self.root / ".env.local").exists())


if __name__ == "__main__":
    unittest.main()
