"""Import the pre-Postgres workspace into the database (plan section 9).

Each `workspace/sessions/{job_id}.json` becomes a `reports` row owned by the
given account, under the same job_id. Its files are copied into
`workspace/tenants/{owner_id}/reports/{job_id}/` and every path in the session
that pointed at the old location is rewritten. The old files and JSON stay
where they are until the operator removes them.
"""
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import db
from app.models.db import LayoutTemplate, Report, User
from app.models.session import normalize_session
from app.services.auth_service import get_user_by_email, normalize_email
from app.services.session_manager import report_columns, is_valid_job_id
from app.utils.report_paths import ReportPaths
from app.utils.template_library import _default_templates

logger = logging.getLogger(__name__)

# Old shared folder -> folder inside the report's own directory.
LEGACY_FOLDERS = {"uploads": "uploads", "filtered": "filtered", "scripts": "scripts", "reports": "output"}
SKIPPED_SUFFIXES = (".lock", ".tmp")


@dataclass
class ImportSummary:
    dry_run: bool
    imported: list[str] = field(default_factory=list)
    already_imported: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    files_copied: int = 0
    missing_files: dict[str, list[str]] = field(default_factory=dict)
    templates_imported: int = 0

    def as_text(self) -> str:
        lines = [
            ("Dry run: nothing was changed." if self.dry_run else "Import finished."),
            f"Reports imported: {len(self.imported)}",
            f"Already imported (skipped): {len(self.already_imported)}",
            f"Unreadable session files: {len(self.unreadable)}" + (f" ({', '.join(self.unreadable)})" if self.unreadable else ""),
            f"Files copied: {self.files_copied}",
            f"Layout templates imported: {self.templates_imported}",
        ]
        for job_id, paths in self.missing_files.items():
            lines.append(f"Missing files for {job_id}: {', '.join(paths)}")
        return "\n".join(lines)


def _timestamp(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def ensure_demo_user(email: str) -> tuple[User, bool]:
    """Return the demo account, creating it (verified, no password yet) if needed."""
    email = normalize_email(email)
    with db.transaction() as tx:
        user = tx.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is not None:
            return user, False
        user = User(email=email, display_name="Demo", is_demo=True, email_verified_at=datetime.now(timezone.utc))
        tx.add(user)
        tx.flush()
        return user, True


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _rewrite(value, replacements: list[tuple[str, str]]):
    if isinstance(value, str):
        for old, new in replacements:
            if old in value:
                value = value.replace(old, new)
        return value
    if isinstance(value, dict):
        return {key: _rewrite(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite(item, replacements) for item in value]
    return value


def _path_forms(path: Path) -> set[str]:
    """The ways a path may have been written into a session: as given, absolute, relative."""
    resolved = path.resolve()
    forms = {str(path), str(resolved)}
    try:
        forms.add(os.path.relpath(resolved))
    except ValueError:
        pass
    return {form for form in forms if form not in {"", "."}}


def _legacy_folder(candidate: str, folders: dict[str, Path]) -> tuple[Path, str] | None:
    """If `candidate` names a file directly inside a legacy folder, return (path, target subfolder)."""
    if not candidate or len(candidate) > 4096 or "\n" in candidate:
        return None
    try:
        path = Path(candidate).resolve()
    except (OSError, ValueError):
        return None
    for name, folder in folders.items():
        if path.parent == folder:
            return path, LEGACY_FOLDERS[name]
    return None


def _job_prefixes(job_id: str) -> tuple[str, ...]:
    return (f"{job_id}.", f"{job_id}_", f"{job_id}-", f"preprocess_{job_id}", f"explore_{job_id}")


def _copy(source: Path, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
        return sum(1 for item in target.rglob("*") if item.is_file())
    shutil.copy2(source, target)
    return 1


def _folder_size(root: Path) -> int:
    return sum(item.stat().st_size for item in root.rglob("*") if item.is_file() and not item.is_symlink())


def _plan_files(job_id: str, state: dict, destination: ReportPaths, folders: dict[str, Path]):
    """Work out which files to copy and how to rewrite paths. Returns
    (copies, replacements, missing) where copies is [(source, target)]."""
    copies: dict[Path, Path] = {}
    missing: list[str] = []

    storage_dir = state.get("storage_dir")
    if storage_dir:
        # Already in the per-report layout (created after Phase 0): copy the whole folder.
        old_root = Path(storage_dir)
        if old_root.is_dir():
            copies[old_root.resolve()] = destination.root
        else:
            missing.append(str(old_root))
        replacements = [(form, str(destination.root)) for form in _path_forms(old_root)]
        return copies, sorted(replacements, key=lambda pair: -len(pair[0])), missing

    for candidate in set(_strings(state)):
        found = _legacy_folder(candidate, folders)
        if found is None:
            continue
        source, subfolder = found
        if source.exists():
            copies[source] = getattr(destination, subfolder) / source.name
        elif candidate in {state.get("script_path"), state.get("pdf_path")} or any(
            candidate in {info.get("path"), info.get("preprocessed_path"), info.get("filtered_path")}
            for info in state.get("files", []) if isinstance(info, dict)
        ):
            missing.append(candidate)

    for name, folder in folders.items():
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            if entry.name.startswith(_job_prefixes(job_id)) and not entry.name.endswith(SKIPPED_SUFFIXES):
                copies.setdefault(entry.resolve(), getattr(destination, LEGACY_FOLDERS[name]) / entry.name)

    replacements = []
    for source, target in copies.items():
        replacements.extend((form, str(target)) for form in _path_forms(source))
    return copies, sorted(set(replacements), key=lambda pair: -len(pair[0])), missing


def _import_templates(owner: User, workspace: Path, dry_run: bool) -> int:
    path = workspace / "layout_templates.json"
    if not path.is_file():
        return 0
    try:
        templates = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read %s", path)
        return 0
    if not isinstance(templates, list):
        return 0
    defaults = {template["id"]: template for template in _default_templates()}
    imported = 0
    with db.transaction() as tx:
        existing = dict(tx.execute(select(LayoutTemplate.id, LayoutTemplate.data)).all())
        position = tx.execute(select(func.coalesce(func.max(LayoutTemplate.position), -1))).scalar()
        for template in templates:
            if not isinstance(template, dict) or not template.get("id"):
                continue
            template_id = str(template["id"])
            if template_id in defaults:
                # An edited built-in: keep the edit, still shared by everyone.
                current = existing.get(template_id, defaults[template_id])
                if template == current:
                    continue
                imported += 1
                if not dry_run:
                    position += 1
                    statement = pg_insert(LayoutTemplate).values(id=template_id, position=position, data=template)
                    tx.execute(statement.on_conflict_do_update(index_elements=[LayoutTemplate.id], set_={"data": template}))
                continue
            if template_id in existing:
                continue
            imported += 1
            if not dry_run:
                position += 1
                tx.add(LayoutTemplate(id=template_id, owner_id=owner.id, position=position, data=template))
    return imported


def import_legacy(owner_email: str, *, workspace: Path = Path("workspace"), dry_run: bool = False) -> ImportSummary:
    owner = get_user_by_email(owner_email)
    if owner is None:
        raise LookupError(f"No account for {owner_email.strip().lower()!r}. Run `python -m app.cli create-demo-user` first.")

    summary = ImportSummary(dry_run=dry_run)
    folders = {name: (workspace / name).resolve() for name in LEGACY_FOLDERS}
    session_dir = workspace / "sessions"
    session_files = sorted(session_dir.glob("*.json")) if session_dir.is_dir() else []

    for session_file in session_files:
        job_id = session_file.stem
        try:
            if not is_valid_job_id(job_id):
                raise ValueError("invalid job id")
            state = normalize_session(json.loads(session_file.read_text(encoding="utf-8")), job_id)
            if not state:
                raise ValueError("empty session")
        except (OSError, ValueError, ValidationError):
            summary.unreadable.append(session_file.name)
            continue

        with db.transaction() as tx:
            if tx.get(Report, job_id) is not None:
                summary.already_imported.append(job_id)
                continue

        destination = ReportPaths.for_new_report(job_id, str(owner.id))
        copies, replacements, missing = _plan_files(job_id, state, destination, folders)
        if missing:
            summary.missing_files[job_id] = sorted(missing)
        summary.imported.append(job_id)
        if dry_run:
            summary.files_copied += len(copies)
            continue

        destination.ensure()
        for source, target in copies.items():
            summary.files_copied += _copy(source, target)
        state = _rewrite(state, replacements)
        state["storage_dir"] = str(destination.root)
        state = normalize_session(state, job_id)
        # Keep the original times so the recent-reports list keeps its order.
        times = {"created_at": _timestamp(state.get("created_at")), "updated_at": _timestamp(state.get("updated_at"))}
        with db.transaction() as tx:
            tx.add(Report(
                id=job_id, owner_id=owner.id, state=state,
                storage_bytes=_folder_size(destination.root), **report_columns(state),
                **{key: value for key, value in times.items() if value is not None},
            ))

    summary.templates_imported = _import_templates(owner, workspace, dry_run)
    return summary

