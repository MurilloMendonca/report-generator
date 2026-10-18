"""HTML pages, with versioned asset URLs.

Every `/static/...css|js` reference in a page gets `?v=<content hash>`, so a
browser or Cloudflare never pairs a new page with an old script. Versioned
assets can then be cached for a year; the pages themselves are never cached.
"""
import hashlib
import re
from pathlib import Path

from fastapi.responses import HTMLResponse

STATIC_DIR = Path("static")
_ASSET = re.compile(r'(?P<attr>(?:src|href)=")(?P<path>/static/[\w./-]+\.(?:js|css))"')
_hashes: dict[Path, tuple[float, str]] = {}


def asset_version(path: Path) -> str:
    mtime = path.stat().st_mtime
    cached = _hashes.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    version = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    _hashes[path] = (mtime, version)
    return version


def _versioned(match: re.Match) -> str:
    path = match["path"]
    file = STATIC_DIR / path.removeprefix("/static/")
    if not file.is_file():
        return match.group(0)
    return f'{match["attr"]}{path}?v={asset_version(file)}"'


def render_page(name: str) -> HTMLResponse:
    html = (STATIC_DIR / name).read_text(encoding="utf-8")
    return HTMLResponse(_ASSET.sub(_versioned, html), headers={"Cache-Control": "no-cache"})
