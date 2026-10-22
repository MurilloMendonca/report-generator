import unittest

from fpdf import FPDF

from app.utils.pdf_text import (
    TextBoxTooSmallError, narrative_measurement, pdf_text_lines,
    render_pdf_text_block, write_pdf_report_title,
)


class PDFTextFittingTests(unittest.TestCase):
    def pdf(self):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(False)
        return pdf

    def test_wide_letters_and_long_heading_stay_in_their_box(self):
        pdf = self.pdf()
        warnings = []
        layout = render_pdf_text_block(pdf, 'WWWWWWWWWWWWWWWWWWWWWW ' * 10, 15, 40, 40, 25,
                                       title='Responsabilidades operacionais e encaminhamentos prioritários', warn=warnings.append)
        self.assertLessEqual(pdf.get_y(), 65.00001)
        self.assertLessEqual(len(pdf_text_lines(pdf, layout['text'], 40, layout['line_height'])), layout['max_lines'])
        self.assertGreater(layout['title_height'], 0)
        self.assertTrue(layout['shortened'])
        self.assertTrue(layout['text'].endswith('...'))
        self.assertTrue(warnings)

    def test_font_metrics_and_heading_height_are_measured_for_each_font(self):
        text = 'Revisar responsabilidades e acompanhar entregas semanais. ' * 4
        for family in ('helvetica', 'courier', 'times'):
            with self.subTest(font=family):
                pdf = self.pdf()
                layout = render_pdf_text_block(pdf, text, 12, 30, 55, 38, title='Plano de ação por departamento', font_family=family)
                self.assertLessEqual(pdf.get_y(), 68.00001)
                self.assertLessEqual(len(pdf_text_lines(pdf, layout['text'], 55, layout['line_height'])), layout['max_lines'])
                self.assertEqual(pdf.page_no(), 1)

    def test_short_text_and_numeric_claims_are_not_changed(self):
        pdf = self.pdf()
        text = 'A receita foi de 1.234,56 e a margem foi de 12,5%.'
        layout = render_pdf_text_block(pdf, text, 10, 30, 90, 50, title='Resultados')
        self.assertEqual(layout['text'], text)
        self.assertFalse(layout['shortened'])
        self.assertEqual(layout['font_size'], 10)

    def test_tiny_slots_and_low_line_spacing_do_not_overflow(self):
        pdf = self.pdf()
        with self.assertRaises(TextBoxTooSmallError):
            render_pdf_text_block(pdf, 'Resultado', 10, 30, 1, 5, title='Título')
        with self.assertRaises(TextBoxTooSmallError):
            render_pdf_text_block(pdf, 'Resultado', 10, 30, 30, 3, title='Título')
        layout = render_pdf_text_block(pdf, 'Resultado importante ' * 10, 10, 30, 45, 25, line_height=1)
        self.assertGreaterEqual(layout['line_height'], layout['font_size'] * 25.4 / 72)
        self.assertLessEqual(pdf.get_y(), 55.00001)

    def test_report_heading_is_limited_to_two_measured_lines(self):
        pdf = self.pdf()
        warnings = []
        write_pdf_report_title(pdf, 'RELATÓRIO ' + 'W' * 250, warn=warnings.append)
        self.assertLessEqual(pdf.get_y(), 26.00001)
        self.assertTrue(warnings)

    def test_generation_guidance_counts_actual_lines_and_reserves_heading(self):
        text = 'W' * 22
        measured = narrative_measurement(text, {'w': 40, 'h': 25}, title='Resumo')
        self.assertEqual(measured['body_lines'], 2)
        long_heading = narrative_measurement(text, {'w': 40, 'h': 25}, title='Responsabilidades operacionais e encaminhamentos')
        self.assertLess(long_heading['max_lines'], measured['max_lines'])


if __name__ == '__main__':
    unittest.main()
