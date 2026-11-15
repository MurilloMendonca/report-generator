"""Word export of a published report.

The DOCX follows the published PDF and its render manifest. Narrative blocks
become editable paragraphs; charts, tables and custom components are cropped
from the rendered PDF page, so they look exactly as they do in the PDF.
"""
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Mm, Pt
from PIL import Image
from pypdf import PdfReader

from app.services.report_files import ReportFileUnavailable, read_report_file
from app.utils.layout import normalize_template

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_PDF_BYTES = 200 * 1024 * 1024
MAX_PAGES = 50
RASTER_DPI = 200
PAGE_WIDTH_MM, PAGE_HEIGHT_MM, MARGIN_MM = 210, 297, 20
PDF_CONTENT_WIDTH_MM = 190  # Report slots span x = 10..200 on an A4 page.
ALIGNMENTS = {"J": WD_ALIGN_PARAGRAPH.JUSTIFY, "L": WD_ALIGN_PARAGRAPH.LEFT,
              "C": WD_ALIGN_PARAGRAPH.CENTER, "R": WD_ALIGN_PARAGRAPH.RIGHT}


class DocxExportError(Exception):
    def __init__(self, message: str, code: str = "docx_export_failed"):
        super().__init__(message)
        self.code = code

    def as_error_dict(self) -> dict:
        return {"code": self.code, "message": str(self)}


def build_report_docx(session: dict) -> bytes:
    pdf_path = session.get("pdf_path")
    if not pdf_path:
        raise DocxExportError("O relatório ainda não tem PDF publicado.", "pdf_missing")
    try:
        pdf_bytes = read_report_file(session, pdf_path, max_bytes=MAX_PDF_BYTES)
    except ReportFileUnavailable:
        raise DocxExportError("O PDF publicado não está disponível.", "pdf_missing") from None
    manifest = _read_manifest(session, pdf_path)
    outcomes = {item.get("id"): item for item in manifest.get("components", []) if isinstance(item, dict)}
    artifacts = {item["id"]: item for item in session.get("artifacts", []) if item.get("id")}
    template = normalize_template(session.get("template", []))
    settings = session.get("settings") or {}

    with tempfile.TemporaryDirectory(prefix="report-docx-") as directory:
        pdf_file = Path(directory) / "report.pdf"
        pdf_file.write_bytes(pdf_bytes)
        try:
            reader = PdfReader(pdf_file)
            page_count = len(reader.pages)
        except Exception:
            raise DocxExportError("O PDF publicado não pôde ser lido.") from None
        if not 1 <= page_count <= MAX_PAGES:
            raise DocxExportError(f"A exportação aceita PDFs com 1 a {MAX_PAGES} páginas.")

        document = _new_document(settings)
        scale = (PAGE_WIDTH_MM - 2 * MARGIN_MM) / PDF_CONTENT_WIDTH_MM
        # Word reflows content, so PDF pages become one continuous document.
        for number in range(1, page_count + 1):
            box = reader.pages[number - 1].mediabox
            page_size = (float(box.width) * 25.4 / 72, float(box.height) * 25.4 / 72)
            page = _LazyPage(pdf_file, number, Path(directory))
            slots = [slot for slot in template if int(slot.get("page", 1)) == number]
            for row in reading_rows(slots):
                if len(row) == 1:
                    _add_component(document, row[0], artifacts, outcomes, page, page_size, scale, settings)
                    continue
                table = document.add_table(rows=1, cols=len(row))
                table.alignment = WD_TABLE_ALIGNMENT.CENTER
                for cell, slot in zip(table.rows[0].cells, row):
                    cell.width = Mm(float(slot["w"]) * scale)
                    _add_component(cell, slot, artifacts, outcomes, page, page_size, scale, settings, padding_mm=4)
                    first = cell.paragraphs[0]
                    if not first.text and not first.runs and len(cell.paragraphs) > 1:
                        first._element.getparent().remove(first._element)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def reading_rows(slots: list) -> list:
    """Group slots that share vertical space into rows, read top to bottom, left to right."""
    rows, bottom = [], None
    for slot in sorted(slots, key=lambda item: (float(item["y"]), float(item["x"]))):
        top, height = float(slot["y"]), float(slot["h"])
        if rows and top < bottom - 1:
            rows[-1].append(slot)
            bottom = max(bottom, top + height)
        else:
            rows.append([slot])
            bottom = top + height
    return [sorted(row, key=lambda item: float(item["x"])) for row in rows]


def _new_document(settings: dict):
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Mm(PAGE_WIDTH_MM), Mm(PAGE_HEIGHT_MM)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Mm(MARGIN_MM))
    normal = document.styles["Normal"]
    normal.font.size = Pt(float(settings.get("font_size") or 10) + 1)
    title = str(settings.get("title") or "Relatório Analítico")
    document.core_properties.title = title
    document.add_heading(title, level=0)
    return document


def _add_component(container, slot, artifacts, outcomes, page, page_size, scale, settings, padding_mm=0):
    component_id = slot["id"]
    outcome = outcomes.get(component_id)
    if outcome is not None and outcome.get("status") != "rendered":
        return
    blocks = (outcome or {}).get("text_blocks") or []
    if (artifacts.get(component_id) or {}).get("type") == "text" and blocks:
        alignment = ALIGNMENTS.get(str(settings.get("alignment") or "J"), WD_ALIGN_PARAGRAPH.JUSTIFY)
        for block in blocks:
            if block.get("title"):
                container.add_paragraph(str(block["title"]), style="Heading 2")
            for line in str(block.get("text") or "").splitlines():
                if line.strip():
                    container.add_paragraph(line.strip()).alignment = alignment
        return
    image = page.crop(slot, page_size)
    width = max(20.0, float(slot["w"]) * scale - padding_mm)
    paragraph = container.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.add_run().add_picture(image, width=Mm(width))


class _LazyPage:
    """Rasterize a PDF page once, and only when a component needs an image."""

    def __init__(self, pdf_file: Path, number: int, directory: Path):
        self.pdf_file, self.number, self.directory = pdf_file, number, directory
        self.image = None

    def crop(self, slot: dict, page_size: tuple) -> io.BytesIO:
        if self.image is None:
            self.image = self._rasterize()
        width, height = self.image.size
        x, y, w, h = (float(slot[key]) for key in ("x", "y", "w", "h"))
        box = (max(0, round(x / page_size[0] * width)), max(0, round(y / page_size[1] * height)),
               min(width, round((x + w) / page_size[0] * width)), min(height, round((y + h) / page_size[1] * height)))
        if box[2] <= box[0] or box[3] <= box[1]:
            raise DocxExportError(f"O componente {slot['id']} está fora da página.")
        buffer = io.BytesIO()
        self.image.crop(box).save(buffer, format="PNG", optimize=True)
        buffer.seek(0)
        return buffer

    def _rasterize(self):
        if not shutil.which("pdftoppm"):
            raise DocxExportError("Instale poppler-utils para exportar gráficos para DOCX.", "rasterizer_unavailable")
        prefix = self.directory / f"page-{self.number}"
        try:
            subprocess.run(["pdftoppm", "-f", str(self.number), "-l", str(self.number), "-singlefile",
                            "-r", str(RASTER_DPI), "-png", str(self.pdf_file), str(prefix)],
                           check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with Image.open(f"{prefix}.png") as image:
                return image.convert("RGB")
        except (OSError, subprocess.SubprocessError):
            raise DocxExportError("Não foi possível converter a página do PDF em imagem.") from None


def _read_manifest(session: dict, pdf_path: str) -> dict:
    try:
        data = json.loads(read_report_file(session, f"{pdf_path}.manifest.json", max_bytes=5 * 1024 * 1024))
    except (ReportFileUnavailable, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
