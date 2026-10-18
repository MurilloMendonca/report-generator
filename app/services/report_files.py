"""Serve an already opened file beneath the report root, without following links.

Generated code can create symlinks while another request downloads an artifact.
Walk with directory descriptors and O_NOFOLLOW, then stream that descriptor so
renaming a path after validation cannot redirect the response to another owner.
"""
import os
import stat
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.utils.report_paths import ReportPaths


def report_file_response(session, path, *, media_type, filename=None, disposition="inline", headers=None):
    directory_fd = file_fd = None
    try:
        paths = ReportPaths.from_session(session)
        root = paths.root
        if paths.is_legacy:
            root = paths.output.parent  # production refuses shared folders
        root = root.absolute()
        relative = Path(os.path.abspath(path)).relative_to(root)
        if not relative.parts:
            raise ValueError("Missing file")
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for segment in relative.parts[:-1]:
            child = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child
        file_fd = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Not a regular file")
        source = os.fdopen(file_fd, "rb")
        file_fd = None
    except (OSError, ValueError, TypeError):
        if file_fd is not None:
            os.close(file_fd)
        raise HTTPException(404, detail="Arquivo indisponível.") from None
    finally:
        if directory_fd is not None:
            os.close(directory_fd)

    def chunks():
        try:
            remaining = info.st_size
            while remaining:
                data = source.read(min(65536, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data
        finally:
            source.close()

    response_headers = {"Content-Length": str(info.st_size), "Cache-Control": "private, no-store", **(headers or {})}
    if filename:
        response_headers["Content-Disposition"] = f"{disposition}; filename*=utf-8''{quote(str(filename), safe='')}"
    return StreamingResponse(chunks(), media_type=media_type, headers=response_headers, background=BackgroundTask(source.close))
