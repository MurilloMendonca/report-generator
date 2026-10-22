from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

from fpdf import FPDF
import matplotlib
matplotlib.use('Agg')
import numpy as np
from PIL import Image
from pypdf import PdfReader

from app.services.report_quality import review_report
from app.utils.component_renderers import sandbox_runtime_source
from app.utils.narrative_evidence import ground_narrative, metric_catalog, review_narrative
from app.utils.pdf_text import MissingGlyphError, ReportPDF, render_pdf_text_block


class NarrativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.metrics = {'revenue': {'value': 1234.567, 'currency': 'EUR', 'period': '2026-Q1'}, 'missing': None, 'ratio': .125}
        self.artifact = {'id': 'summary', 'type': 'text'}

    def test_rounding_nested_values_units_and_periods_are_grounded(self):
        text = ground_narrative(self.artifact, 'Revenue: €{{metric:/revenue/value|number:2}} in {{metric:/revenue/period}}.', self.metrics)
        self.assertEqual(text, 'Revenue: €1234.57 in 2026-Q1.')
        issues, references = review_narrative(self.artifact, text, self.metrics)
        self.assertEqual(issues, [])
        self.assertEqual(references[0]['currency'], 'EUR')
        self.assertEqual(references[1]['display'], '2026-Q1')

    def test_changed_or_missing_metric_blocks_publication(self):
        text = ground_narrative(self.artifact, '{{metric:/revenue/value|number:2}}', self.metrics)
        for metrics in ({'revenue': {'value': 999}}, {'revenue': {'value': None}}, {}):
            issues, _ = review_narrative(self.artifact, text, metrics)
            self.assertIn('metric_reference_changed', [issue[1] for issue in issues])

    def test_changed_unit_or_period_invalidates_old_evidence_even_if_value_matches(self):
        text = ground_narrative(self.artifact, '{{metric:/revenue/value}}', self.metrics)
        for field, value in (('currency', 'GBP'), ('period', '2026-Q2')):
            metrics = {'revenue': {**self.metrics['revenue'], field: value}}
            issues, _ = review_narrative(self.artifact, text, metrics)
            self.assertIn('metric_reference_changed', [issue[1] for issue in issues])

    def test_fraction_and_percentage_points_cannot_be_labeled_as_percentages(self):
        for unit in ('ratio', 'percentage_points'):
            metrics = {'rate': {'value': .125, 'unit': unit}}
            text = ground_narrative(self.artifact, '{{metric:/rate/value}}%', metrics)
            issues, _ = review_narrative(self.artifact, text, metrics)
            self.assertIn('metric_unit_mismatch', [issue[1] for issue in issues])

    def test_manual_edit_does_not_keep_trusted_provenance(self):
        text = ground_narrative(self.artifact, '{{metric:/revenue/value}}', self.metrics)
        issues, references = review_narrative(self.artifact, text + ' up 99% in 2030', self.metrics)
        self.assertEqual(references, [])
        self.assertEqual({issue[1] for issue in issues}, {'evidence_stale', 'unreferenced_numbers'})

    def test_currency_and_absolute_percentage_mismatches_are_errors(self):
        for template in ('£{{metric:/revenue/value}}', '{{metric:/revenue/value}}%'):
            with self.subTest(template=template):
                text = ground_narrative(self.artifact, template, self.metrics)
                issues, _ = review_narrative(self.artifact, text, self.metrics)
                self.assertIn(('error', 'metric_unit_mismatch'), [issue[:2] for issue in issues])

    def test_unavailable_or_invalid_references_never_become_zero(self):
        for token in ('{{metric:/missing}}', '{{metric:/absent}}', '{{metric:/revenue|oops}}'):
            text = ground_narrative(self.artifact, token, self.metrics)
            issues, _ = review_narrative(self.artifact, text, self.metrics)
            self.assertIn('invalid_metric_reference', [issue[1] for issue in issues])

    def test_catalog_excludes_unavailable_values_and_escapes_json_pointers(self):
        catalog = metric_catalog({'a/b': 12, 'unavailable': 'N/A', 'nan': float('nan'), 'evidence': 'private', 'missing': None})
        self.assertEqual(catalog, [{'pointer': '/a~1b', 'value': 12}])
        self.assertEqual(ground_narrative(self.artifact, '{{metric:/a~1b}}', {'a/b': 12}), '12')


class ExportedPDFQualityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='test-pdf-quality-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'report.pdf'
        self.slot = {'id': 'summary', 'page': 1, 'x': 10, 'y': 30, 'w': 180, 'h': 50}
        self.text = 'Receita €1234,56 e despesa £987,65.'

    def pdf(self, text=None, color=0, family='helvetica'):
        pdf = FPDF(); pdf.add_page(); pdf.set_auto_page_break(False); pdf.set_text_color(color)
        layout = render_pdf_text_block(pdf, text if text is not None else self.text, 10, 30, 180, 50, font_family=family)
        pdf.output(self.path)
        return {'components': [{'id': 'summary', 'status': 'rendered', 'glyph_check': True,
                                'text_blocks': [{'text': layout['text'], 'title': layout['title']}]}]}

    def session(self):
        return {'template': [self.slot], 'artifacts': [{'id': 'summary', 'type': 'text'}],
                'components': {'summary': f'def render_summary(pdf, dataframes, x, y, w, h):\n    render_text_block(pdf, {self.text!r}, x,y,w,h)'}}

    def test_euro_and_pound_survive_export_with_embedded_fonts_and_visible_pixels(self):
        for family in ('helvetica', 'times', 'courier'):
            with self.subTest(family=family):
                manifest = self.pdf(family=family)
                reader = PdfReader(self.path)
                self.assertIn(self.text, reader.pages[0].extract_text())
                fonts = reader.pages[0]['/Resources']['/Font'].get_object().values()
                self.assertTrue(any('/ToUnicode' in font.get_object() for font in fonts))
                review = review_report(self.session(), self.path, manifest)
                self.assertNotEqual(review['status'], 'failed', review)
                self.assertIn('unreferenced_numbers', [item['code'] for item in review['issues']])

    @unittest.skipUnless(shutil.which('pdftoppm'), 'poppler-utils required')
    def test_currency_symbols_have_distinct_visible_exported_glyphs(self):
        images = []
        for text in ('€', '£', '?', ' '):
            self.pdf(text)
            prefix = str(Path(self.directory.name) / 'glyph')
            subprocess.run(['pdftoppm', '-singlefile', '-r', '150', '-gray', '-png', str(self.path), prefix], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with Image.open(prefix + '.png') as image:
                images.append(np.asarray(image.convert('L')).copy())
        for index in (0, 1):
            self.assertGreater(np.count_nonzero(images[index] < 200), 30)
            self.assertFalse(np.array_equal(images[index], images[2]))
            self.assertFalse(np.array_equal(images[index], images[3]))
        self.assertFalse(np.array_equal(images[0], images[1]))

    def test_invisible_text_is_rejected_even_when_extraction_and_manifest_succeed(self):
        manifest = self.pdf(color=255)
        self.assertIn(self.text, PdfReader(self.path).pages[0].extract_text())
        review = review_report(self.session(), self.path, manifest)
        self.assertEqual(review['status'], 'failed')
        self.assertIn('blank_component', [item['code'] for item in review['issues']])

    def test_missing_text_missing_component_and_missing_page_are_rejected(self):
        manifest = self.pdf()
        manifest['components'][0]['text_blocks'][0]['text'] = 'Content silently lost €£'
        review = review_report(self.session(), self.path, manifest)
        self.assertIn('pdf_text_missing', [item['code'] for item in review['issues']])
        review = review_report(self.session(), self.path, {})
        self.assertIn('missing_component', [item['code'] for item in review['issues']])
        session = self.session(); session['template'][0]['page'] = 2
        review = review_report(session, self.path, self.pdf())
        self.assertIn('missing_page', [item['code'] for item in review['issues']])

    def test_unavailable_rasterizer_blocks_publication_with_actionable_issue(self):
        manifest = self.pdf()
        with patch('app.services.report_quality.shutil.which', return_value=None):
            review = review_report(self.session(), self.path, manifest)
        self.assertEqual(review['status'], 'failed')
        self.assertIn('pixel_check_unavailable', [item['code'] for item in review['issues']])

    def test_unsupported_glyph_fails_explicitly_instead_of_replacing_it(self):
        with self.assertRaises(MissingGlyphError):
            self.pdf('Unsupported character: \U0001f9ec')

    def test_existing_core_font_does_not_break_embedding_and_custom_cells_preserve_currencies(self):
        pdf = FPDF(); pdf.add_page(); pdf.set_font('helvetica', size=10); pdf.cell(100, 10, 'Earlier core font')
        render_pdf_text_block(pdf, self.text, 10, 30, 180, 50)
        self.assertIn(self.text, PdfReader(BytesIO(pdf.output())).pages[0].extract_text())
        pdf = ReportPDF(); pdf.add_page(); pdf.set_font('Arial', size=10); pdf.cell(180, 10, self.text)
        with self.assertRaises(MissingGlyphError):
            pdf.cell(180, 10, 'Unsupported \U0001f9ec')
        self.assertIn(self.text, PdfReader(BytesIO(pdf.output())).pages[0].extract_text())


class ChartPixelQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = {}
        exec(sandbox_runtime_source(), cls.runtime)

    def tearDown(self):
        self.runtime['plt'].close('all')

    def test_table_text_with_broken_clip_is_detected_despite_visible_cell_background(self):
        from matplotlib.transforms import Bbox
        fig, ax = self.runtime['plt'].subplots()
        ax.axis('off')
        table = ax.table(cellText=[['€125', '£99']], colLabels=['Revenue', 'Cost'], loc='center')
        for cell in table.get_celld().values():
            cell.get_text().set_clip_box(Bbox.from_bounds(0, 0, 1, 1))
            cell.get_text().set_clip_on(True)
        self.runtime['begin_component_render']()
        with self.assertRaises(self.runtime['InvisibleTextError']):
            self.runtime['check_chart_output'](fig)

    def test_actual_table_pixels_and_currency_glyphs_are_checked(self):
        fig, ax = self.runtime['plt'].subplots(); ax.axis('off')
        ax.table(cellText=[['€125', '£99']], colLabels=['Revenue €', 'Cost £'], loc='center')
        self.runtime['begin_component_render']()
        self.runtime['check_chart_output'](fig)
        outcome = self.runtime['component_render_outcome']()
        self.assertEqual(outcome['pixel_text_count'], 4)
        self.assertTrue(outcome['glyph_check'])


class QualityPublicationTests(unittest.TestCase):
    def test_failed_review_preserves_previous_pdf_and_stale_runs_cannot_publish(self):
        from app.services.cleanup_service import delete_entire_session
        from app.services.session_manager import finish_report_review, get_session, reserve_session_run
        from tests.auth_helpers import create_session
        from app.utils.report_paths import ReportPaths
        job_id = 'test-review-publish-' + uuid.uuid4().hex
        paths = ReportPaths.for_new_report(job_id).ensure()
        self.addCleanup(delete_entire_session, job_id)
        old = paths.output / 'previous.pdf'; old.write_bytes(b'last good PDF')
        candidate = paths.output / 'candidate.pdf'; candidate.write_bytes(b'broken candidate')
        create_session(job_id, {'job_id': job_id, **paths.session_fields(), 'pdf_path': str(old), 'pdf_version': 3})
        run_id = reserve_session_run(job_id)['run_id']
        review = {'status': 'failed', 'issues': [{'code': 'blank_component'}]}
        finish_report_review(job_id, run_id, review, str(candidate))
        session = get_session(job_id)
        self.assertEqual(session['pdf_path'], str(old)); self.assertEqual(session['pdf_version'], 3)
        self.assertEqual(old.read_bytes(), b'last good PDF')
        self.assertEqual(session['error']['code'], 'report_quality_failed')
        reserve_session_run(job_id)
        finish_report_review(job_id, run_id, {'status': 'passed'}, str(candidate))
        self.assertEqual(get_session(job_id)['pdf_version'], 3)
        latest = get_session(job_id)['active_run_id']
        finish_report_review(job_id, latest, {'status': 'warning'}, str(candidate))
        self.assertEqual(get_session(job_id)['pdf_version'], 4)
        self.assertEqual(get_session(job_id)['pdf_path'], str(candidate))


if __name__ == '__main__':
    unittest.main()
