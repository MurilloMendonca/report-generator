import json
import re
from pathlib import Path
from textwrap import dedent

from PIL import Image


REPORTS_DIR = Path("workspace/reports")
MAX_CHARTS = 3
MAX_CHART_BYTES = 5 * 1024 * 1024
MAX_CHART_SIDE = 2400
MAX_MANIFEST_BYTES = 16000
MAX_ANSWER_CHARS = 20000
SESSION_ID_PATTERN = r"[A-Za-z0-9_-]{1,128}"
TURN_ID_PATTERN = r"[0-9a-f]{32}"
CHART_ID_PATTERN = r"chart_[1-3]"


def exploration_directory(job_id: str, turn_id: str) -> Path:
    if not re.fullmatch(SESSION_ID_PATTERN, job_id) or not re.fullmatch(TURN_ID_PATTERN, turn_id):
        raise ValueError("Invalid exploration identifier")
    directory = REPORTS_DIR.absolute() / f"explore_{job_id}_{turn_id}"
    if directory.is_symlink() or directory.resolve().parent != REPORTS_DIR.resolve():
        raise ValueError("Invalid exploration directory")
    return directory


def chart_path(job_id: str, turn_id: str, chart_id: str) -> Path:
    if not re.fullmatch(CHART_ID_PATTERN, chart_id):
        raise ValueError("Invalid chart identifier")
    directory = exploration_directory(job_id, turn_id)
    path = directory / f"{chart_id}.png"
    if path.is_symlink() or path.resolve().parent != directory.absolute():
        raise ValueError("Invalid chart path")
    return path


def validate_chart_image(path: Path) -> tuple[int, int]:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_CHART_BYTES:
        raise ValueError("Chart is missing or exceeds the image size limit")
    try:
        with Image.open(path) as picture:
            width, height = picture.size
            if picture.format != "PNG" or not (0 < width <= MAX_CHART_SIDE and 0 < height <= MAX_CHART_SIDE):
                raise ValueError("Chart must be a bounded PNG image")
            picture.load()
    except Image.DecompressionBombError as exc:
        raise ValueError("Chart exceeds the pixel limit") from exc
    return width, height


def collect_exploration_charts(job_id: str, turn_id: str) -> list[dict]:
    directory = exploration_directory(job_id, turn_id)
    manifest = directory / "charts.json"
    if not manifest.exists():
        if any(directory.iterdir()):
            raise ValueError("Chart output is missing its manifest")
        return []  # Backward-compatible text-only scripts.
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Invalid chart manifest")
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or len(entries) > MAX_CHARTS:
        raise ValueError("Too many charts or invalid chart manifest")
    charts = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"id", "title", "caption"}:
            raise ValueError("Invalid chart manifest entry")
        chart_id, title, caption = (entry[key] for key in ("id", "title", "caption"))
        if not all(isinstance(value, str) for value in (chart_id, title, caption)):
            raise ValueError("Chart descriptions must be text")
        if chart_id in seen or not title.strip() or len(title) > 200 or len(caption) > 2000:
            raise ValueError("Invalid chart description or duplicate chart ID")
        seen.add(chart_id)
        width, height = validate_chart_image(chart_path(job_id, turn_id, chart_id))
        charts.append({
            "id": chart_id, "title": title, "caption": caption, "width": width, "height": height,
            "url": f"/api/sessions/{job_id}/explore/{turn_id}/charts/{chart_id}",
        })
    expected = {"charts.json", *(f"{chart['id']}.png" for chart in charts)}
    if {path.name for path in directory.iterdir()} != expected:
        raise ValueError("Unexpected exploration output files")
    return charts


def exploration_runtime_source(output_directory: str, theme: dict, palette: list[str]) -> str:
    setup = (
        f"_exploration_output = {str(Path(output_directory).absolute())!r}\n"
        f"_exploration_theme = {theme!r}\n"
        f"_exploration_palette = {palette!r}\n"
    )
    return setup + dedent(r'''
        import json
        import os
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import seaborn as sns
        import pdf_report_renderers.runtime as render_runtime

        render_runtime.CHART_TITLE_FONTSIZE = 13
        render_runtime.CHART_LABEL_FONTSIZE = 11
        render_runtime.CHART_TICK_FONTSIZE = 10
        render_runtime.CHART_LEGEND_FONTSIZE = 10
        render_runtime.configure_visual_runtime(_exploration_theme, _exploration_palette)
        plt.rcParams.update({'figure.figsize': (10, 5.5), 'figure.dpi': 144, 'font.size': 11})
        _exploration_charts = []

        def show_chart(fig=None, title=None, caption=''):
            """Attach a Matplotlib figure to this explorer response; no paths needed."""
            if len(_exploration_charts) >= 3:
                raise ValueError('An explorer response supports at most three charts')
            fig = fig if fig is not None else plt.gcf()
            title = str(title or (fig.axes[0].get_title() if fig.axes else '') or 'Gráfico')
            caption = str(caption or '')
            if len(title) > 200 or len(caption) > 2000:
                raise ValueError('Chart title or caption is too long')
            width, height = fig.get_size_inches()
            scale = min(1.0, 2200 / (max(width, height) * 144))
            fig.set_size_inches(width * scale, height * scale)
            if not any(ax.get_title() for ax in fig.axes) and fig.axes:
                fig.axes[0].set_title(title)
            chart_id = 'chart_' + str(len(_exploration_charts) + 1)
            path = os.path.join(_exploration_output, chart_id + '.png')
            try:
                fig.savefig(path, format='png', dpi=144, bbox_inches='tight', facecolor='white')
                if os.path.getsize(path) > 5 * 1024 * 1024:
                    raise ValueError('Chart exceeds the image size limit')
                _exploration_charts.append({'id': chart_id, 'title': title, 'caption': caption})
            finally:
                plt.close(fig)

        def _capture_open_charts(*args, **kwargs):
            for number in list(plt.get_fignums()):
                show_chart(plt.figure(number))

        plt.show = _capture_open_charts

        def _finish_exploration():
            _capture_open_charts()
            with open(os.path.join(_exploration_output, 'charts.json'), 'w', encoding='utf-8') as manifest:
                json.dump(_exploration_charts, manifest, ensure_ascii=False)
    ''').strip()
