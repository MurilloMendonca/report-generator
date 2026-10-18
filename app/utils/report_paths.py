"""Where one report's files live, and what its sandbox may see.

New reports get a private folder, recorded in the session as `storage_dir`:

    workspace/tenants/{tenant}/reports/{job_id}/
        uploads/    original files       (sandbox: read-only)
        filtered/   preprocessed tables  (sandbox: writable)
        scripts/    generated scripts    (sandbox: writable)
        output/     PDFs, previews, manifests, explorer charts (sandbox: writable)

Sessions created before this layout have no `storage_dir` and keep using the
shared workspace folders ("legacy"), where the sandbox sees every report's
files. The Phase 1 import moves them into their own folders.
"""
import re
from dataclasses import dataclass
from pathlib import Path

WORKSPACE_DIR = Path("workspace")
TENANTS_DIR = WORKSPACE_DIR / "tenants"
DEFAULT_TENANT = "default"  # becomes the owner's user ID in Phase 3
LEGACY_DIRS = {
    "uploads": WORKSPACE_DIR / "uploads",
    "filtered": WORKSPACE_DIR / "filtered",
    "scripts": WORKSPACE_DIR / "scripts",
    "output": WORKSPACE_DIR / "reports",
}
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


@dataclass(frozen=True)
class ReportPaths:
    job_id: str
    root: Path | None  # None means the legacy shared folders

    @classmethod
    def for_new_report(cls, job_id: str, tenant: str = DEFAULT_TENANT) -> "ReportPaths":
        if not _SEGMENT.fullmatch(job_id) or not _SEGMENT.fullmatch(tenant):
            raise ValueError("Invalid report or tenant identifier")
        return cls(job_id, TENANTS_DIR / tenant / "reports" / job_id)

    @classmethod
    def from_session(cls, session: dict) -> "ReportPaths":
        job_id = str((session or {}).get("job_id") or "")
        storage_dir = (session or {}).get("storage_dir")
        if not storage_dir:
            return cls(job_id, None)
        root = Path(storage_dir)
        # Only accept folders inside workspace/tenants; anything else is treated as tampering.
        if not root.resolve().is_relative_to(TENANTS_DIR.resolve()) or root.name != job_id:
            raise ValueError("Report storage folder is outside the workspace")
        return cls(job_id, root)

    @property
    def is_legacy(self) -> bool:
        return self.root is None

    def _dir(self, name: str) -> Path:
        return LEGACY_DIRS[name] if self.root is None else self.root / name

    @property
    def uploads(self) -> Path:
        return self._dir("uploads")

    @property
    def filtered(self) -> Path:
        return self._dir("filtered")

    @property
    def scripts(self) -> Path:
        return self._dir("scripts")

    @property
    def output(self) -> Path:
        return self._dir("output")

    @property
    def sandbox_read_only(self) -> list[Path]:
        return [self.uploads]

    @property
    def sandbox_writable(self) -> list[Path]:
        return [self.filtered, self.scripts, self.output]

    def ensure(self) -> "ReportPaths":
        for directory in (self.uploads, self.filtered, self.scripts, self.output):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def session_fields(self) -> dict:
        return {} if self.root is None else {"storage_dir": str(self.root)}
