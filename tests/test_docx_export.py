"""Word export: editable narrative paragraphs plus visuals cropped from the published PDF."""
import io
import json
import os
import shutil
import unittest
import uuid

from docx import Document
from fastapi.testclient import TestClient
from fpdf import FPDF
from PIL import Image

from app.main import app
from app.services.cleanup_service import delete_entire_session
from app.services.docx_export import DOCX_MEDIA_TYPE, DocxExportError, build_report_docx, reading_rows
from app.utils.report_paths import ReportPaths
from tests.auth_helpers import create_session, default_user, signed_in_headers

HAS_PDFTOPPM = shutil.which("pdftoppm") is not None
SUMMARY = "As vendas cresceram 12% no trimestre.\nA região Norte liderou."
TEMPLATE = [
    {"id": "t_resumo", "x": 10, "y": 30, "w": 190, "h": 40, "page": 1},
    {"id": "g_blue", "x": 10, "y": 80, "w": 90, "h": 60, "page": 1},
    {"id": "g_red", "x": 110, "y": 80, "w": 90, "h": 60, "page": 1},
    {"id": "g_green", "x": 10, "y": 30, "w": 190, "h": 80, "page": 2},
]
ARTIFACTS = [{"id": "t_resumo", "type": "text", "indicator": "Resumo"},
             {"id": "g_blue", "type": "graphic"}, {"id": "g_red", "type": "graphic"},
             {"id": "g_green", "type": "graphic"}, {"id": "m_total", "type": "metric"}]
COLORS = {"g_blue": (37, 99, 235), "g_red": (220, 38, 38), "g_green": (22, 163, 74)}


def write_report(paths: ReportPaths, *, manifest=True, statuses=None) -> str:
    pdf = FPDF()
    pdf.set_font("helvetica", size=10)
    for page in (1, 2):
        pdf.add_page()
        for slot in (s for s in TEMPLATE if s["page"] == page):
            if slot["id"] in COLORS:
                pdf.set_fill_color(*COLORS[slot["id"]])
                pdf.rect(slot["x"], slot["y"], slot["w"], slot["h"], style="F")
            else:
                pdf.set_xy(slot["x"], slot["y"])
                pdf.multi_cell(slot["w"], 5, "Resumo\n" + SUMMARY)
    pdf_path = os.path.join(paths.output, "report.r-1.pdf")
    pdf.output(pdf_path)
    if manifest:
        statuses = statuses or {}
        components = [{"id": s["id"], "page": s["page"], "status": statuses.get(s["id"], "rendered"),
                       "text_blocks": [{"title": "Resumo", "text": SUMMARY}] if s["id"] == "t_resumo" else []}
                      for s in TEMPLATE]
        with open(f"{pdf_path}.manifest.json", "w", encoding="utf-8") as handle:
            json.dump({"page_count": 2, "components": components}, handle)
    return pdf_path


def image_colors(document) -> list:
    colors = []
    for shape in document.inline_shapes:
        blob = document.part.related_parts[shape._inline.graphic.graphicData.pic.blipFill.blip.embed].blob
        with Image.open(io.BytesIO(blob)) as image:
            colors.append(image.convert("RGB").getpixel((image.width // 2, image.height // 2)))
    return colors


def near(color, expected):
    return all(abs(a - b) <= 12 for a, b in zip(color, expected))


class ReadingOrderTests(unittest.TestCase):
    def test_slots_sharing_vertical_space_form_one_row_read_left_to_right(self):
        slots = [{"id": "right", "x": 110, "y": 80, "w": 90, "h": 60}, {"id": "below", "x": 10, "y": 150, "w": 190, "h": 30},
                 {"id": "left", "x": 10, "y": 82, "w": 90, "h": 60}, {"id": "top", "x": 10, "y": 30, "w": 190, "h": 40}]
        self.assertEqual([[s["id"] for s in row] for row in reading_rows(slots)], [["top"], ["left", "right"], ["below"]])


@unittest.skipUnless(HAS_PDFTOPPM, "pdftoppm unavailable")
class DocxExportTests(unittest.TestCase):
    def setUp(self):
        self.job_id = f"test-docx-{uuid.uuid4().hex}"
        self.paths = ReportPaths.for_new_report(self.job_id, str(default_user().id)).ensure()
        self.addCleanup(shutil.rmtree, self.paths.root, True)

    def session(self, **pdf_options):
        return {"pdf_path": write_report(self.paths, **pdf_options), "template": TEMPLATE, "artifacts": ARTIFACTS,
                "settings": {"title": "Vendas do Trimestre", "language": "Portuguese"}, "job_id": self.job_id,
                **self.paths.session_fields()}

    def test_texts_are_editable_and_visuals_are_cropped_from_the_pdf(self):
        document = Document(io.BytesIO(build_report_docx(self.session())))
        paragraphs = [(p.style.name, p.text) for p in document.paragraphs if p.text]
        self.assertEqual(paragraphs, [("Title", "Vendas do Trimestre"), ("Heading 2", "Resumo"),
                                      ("Normal", "As vendas cresceram 12% no trimestre."), ("Normal", "A região Norte liderou.")])
        self.assertEqual(document.core_properties.title, "Vendas do Trimestre")
        colors = image_colors(document)
        self.assertEqual(len(colors), 3)
        for color, component_id in zip(colors, ["g_blue", "g_red", "g_green"]):
            self.assertTrue(near(color, COLORS[component_id]), (component_id, color))
        self.assertEqual(len(document.tables), 1)
        self.assertEqual([len(cell.paragraphs[0].runs) for cell in document.tables[0].rows[0].cells], [1, 1])
        widths = sorted(round(shape.width.mm) for shape in document.inline_shapes)
        self.assertEqual(widths, [77, 77, 170])  # 190 mm of PDF content map to the 170 mm Word text width.

    def test_without_a_manifest_every_component_becomes_an_image(self):
        document = Document(io.BytesIO(build_report_docx(self.session(manifest=False))))
        self.assertEqual(len(document.inline_shapes), 4)
        self.assertNotIn("Resumo", [p.text for p in document.paragraphs])

    def test_components_that_did_not_render_are_left_out(self):
        document = Document(io.BytesIO(build_report_docx(self.session(statuses={"g_red": "failed"}))))
        self.assertEqual(len(document.inline_shapes), 2)

    def test_links_and_missing_pdfs_are_refused(self):
        session = self.session()
        target = session["pdf_path"]
        link = os.path.join(self.paths.output, "linked.pdf")
        os.symlink(target, link)
        for path in (link, os.path.join(self.paths.output, "missing.pdf"), None):
            with self.subTest(path=path), self.assertRaises(DocxExportError) as raised:
                build_report_docx({**session, "pdf_path": path})
            self.assertEqual(raised.exception.code, "pdf_missing")


@unittest.skipUnless(HAS_PDFTOPPM, "pdftoppm unavailable")
class DocxRouteTests(unittest.TestCase):
    def test_owner_downloads_the_docx_and_reports_without_pdf_get_404(self):
        job_id = f"test-docx-{uuid.uuid4().hex}"
        paths = ReportPaths.for_new_report(job_id, str(default_user().id)).ensure()
        create_session(job_id, {"pdf_path": write_report(paths), "template": TEMPLATE, "artifacts": ARTIFACTS,
                                "settings": {"title": "Vendas"}, **paths.session_fields()})
        self.addCleanup(delete_entire_session, job_id)
        empty_id = f"test-docx-{uuid.uuid4().hex}"
        create_session(empty_id, ReportPaths.for_new_report(empty_id, str(default_user().id)).ensure().session_fields())
        self.addCleanup(delete_entire_session, empty_id)
        with TestClient(app) as client:
            response = client.get(f"/api/sessions/{job_id}/docx", headers=signed_in_headers())
            missing = client.get(f"/api/sessions/{empty_id}/docx", headers=signed_in_headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], DOCX_MEDIA_TYPE)
        self.assertIn(f"relatorio-{job_id}.docx", response.headers["content-disposition"])
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(len(Document(io.BytesIO(response.content)).inline_shapes), 3)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["detail"]["code"], "pdf_missing")


if __name__ == "__main__":
    unittest.main()
