#!/usr/bin/env python3
"""Restore a downloaded backup into an ephemeral, network-isolated Postgres.

Never connects to the app database or changes the live workspace. Requires Docker
and the postgres image locally. No provider keys are needed to validate artifacts.
"""
import argparse
import json
import subprocess
import tarfile
import tempfile
import time
import uuid
from pathlib import Path


def checked_archive(archive, destination):
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "workspace" or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ValueError("Backup contains an unsafe archive member")
        tar.extractall(destination, filter="data")


def drill(dump, archive, image):
    name = f"repgen-restore-{uuid.uuid4().hex[:12]}"
    def docker(*args, **kwargs):
        return subprocess.run(["docker", *args], check=True, capture_output=True, **kwargs)
    with tempfile.TemporaryDirectory(prefix="repgen-restore-") as directory:
        root = Path(directory)
        checked_archive(archive, root)
        started = False
        try:
            docker("run", "--detach", "--rm", "--name", name, "--network", "none",
                   "--tmpfs", "/var/lib/postgresql/data", "-e", "POSTGRES_HOST_AUTH_METHOD=trust", image)
            started = True
            for _ in range(60):
                try:
                    docker("exec", name, "pg_isready", "-U", "postgres")
                    break
                except subprocess.CalledProcessError:
                    time.sleep(0.5)
            else:
                raise RuntimeError("Drill database did not start")
            with dump.open("rb") as source:
                docker("exec", "-i", name, "pg_restore", "-U", "postgres", "-d", "postgres", "--no-owner", "--exit-on-error", stdin=source)
            def query(sql):
                return docker("exec", name, "psql", "-U", "postgres", "-d", "postgres", "-At", "-c", sql).stdout.decode()
            counts = json.loads(query("SELECT json_build_object('users', (SELECT count(*) FROM users), 'reports', (SELECT count(*) FROM reports), 'credentials', (SELECT count(*) FROM provider_credentials), 'usage_events', (SELECT count(*) FROM llm_usage_events))"))
            checked = missing = 0
            # Never print restored state, emails, hashes or encrypted credentials.
            for line in query("SELECT state FROM reports").splitlines():
                state = json.loads(line)
                paths = [state.get("pdf_path"), state.get("script_path")]
                for file in state.get("files", []):
                    paths.extend(file.get(field) for field in ("path", "preprocessed_path", "filtered_path"))
                for value in set(path for path in paths if isinstance(path, str) and path):
                    path = Path(value)
                    # Production paths can be absolute under /app/workspace.
                    if path.parts[:3] == ("/", "app", "workspace"):
                        path = Path(*path.parts[2:])
                    if path.is_absolute() or ".." in path.parts or path.parts[0] != "workspace":
                        missing += 1
                        continue
                    checked += 1
                    target = root / path
                    if not target.is_file():
                        missing += 1
                    elif path.suffix.lower() == ".pdf":
                        with target.open("rb") as pdf:
                            if pdf.read(5) != b"%PDF-":
                                raise ValueError("Restored PDF is invalid")
            return {"restored": counts, "artifacts_checked": checked, "missing_artifacts": missing, "ok": missing == 0}
        finally:
            if started:
                subprocess.run(["docker", "rm", "--force", name], capture_output=True, check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path)
    parser.add_argument("workspace_archive", type=Path)
    parser.add_argument("--image", default="postgres:17.6-alpine")
    args = parser.parse_args()
    try:
        result = drill(args.dump, args.workspace_archive, args.image)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, tarfile.TarError):
        print("Restore drill failed. Verify the backup pair and local Docker/image availability.")
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
