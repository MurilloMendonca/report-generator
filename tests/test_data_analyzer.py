import os
import unittest
import uuid

import pandas as pd
from fpdf import FPDF

from app.utils.data_analyzer import extract_metadata
from app.services.pipeline_service import metadata_json, raw_metadata_json


class DataAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.created_paths = []

    def tearDown(self):
        for path in self.created_paths:
            if os.path.exists(path):
                os.remove(path)

    def temp_path(self, suffix):
        path = f"workspace/uploads/test_analyzer_{uuid.uuid4().hex}{suffix}"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.created_paths.append(path)
        return path

    def test_csv_detects_separator_and_column_profiles(self):
        path = self.temp_path(".csv")
        with open(path, "w", encoding="latin-1") as f:
            f.write("data;regiao;receita\n")
            f.write("2026-01-01;Sul;10.5\n")
            f.write("2026-01-02;Norte;20.0\n")

        metadata = extract_metadata(path, "vendas.csv")

        self.assertEqual(metadata["parser"]["separator"], ";")
        self.assertEqual(metadata["shape"], {"rows": 2, "columns": 3})
        self.assertIn("receita", metadata["profile"]["column_roles"]["measures"])
        self.assertIn("regiao", metadata["profile"]["column_roles"]["dimensions"])
        self.assertEqual(metadata["columns"][2]["stats"]["median"], 15.25)

    def test_excel_multi_sheet_keeps_sheet_metadata(self):
        path = self.temp_path(".xlsx")
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame({"region": ["A"], "sales": [10]}).to_excel(writer, sheet_name="sales", index=False)
            pd.DataFrame({"region": ["A"], "target": [12]}).to_excel(writer, sheet_name="targets", index=False)

        metadata = extract_metadata(path, "book.xlsx")

        self.assertEqual(metadata["type"], "excel_workbook")
        self.assertEqual(metadata["sheet_count"], 2)
        self.assertEqual({sheet["sheet_name"] for sheet in metadata["sheets"]}, {"sales", "targets"})
        self.assertIn("sales:sales", [column["name"] for column in metadata["columns"]])

    def test_boolean_columns_do_not_trigger_numeric_stats(self):
        path = self.temp_path(".xlsx")
        pd.DataFrame({
            "name": ["A", "B"],
            "is_legendary": [True, False],
            "score": [10, 20],
        }).to_excel(path, index=False)

        metadata = extract_metadata(path, "pokemon.xlsx")

        self.assertNotIn("error", metadata)
        bool_column = next(column for column in metadata["columns"] if column["name"] == "is_legendary")
        self.assertEqual(bool_column["role"], "boolean")
        self.assertNotIn("stats", bool_column)

    def test_numeric_year_column_is_treated_as_date_role(self):
        path = self.temp_path(".xlsx")
        pd.DataFrame({
            "Year": [2020, 2021, 2022],
            "value": [10, 20, 30],
        }).to_excel(path, index=False)

        metadata = extract_metadata(path, "yearly.xlsx")

        year_column = next(column for column in metadata["columns"] if column["name"] == "Year")
        self.assertEqual(year_column["role"], "date")
        self.assertIn("Year", metadata["profile"]["column_roles"]["dates"])

    def test_redacts_sensitive_sample_values(self):
        path = self.temp_path(".csv")
        pd.DataFrame({
            "customer_email": ["ana@example.com", "bob@example.com"],
            "api_token": ["secret-1", "secret-2"],
            "region": ["Sul", "Norte"],
        }).to_csv(path, index=False)

        metadata = extract_metadata(path, "customers.csv")

        self.assertEqual(metadata["sample"][0]["customer_email"], "[REDACTED]")
        self.assertEqual(metadata["sample"][0]["api_token"], "[REDACTED]")
        self.assertEqual(metadata["sample"][0]["region"], "Sul")
        email_column = next(column for column in metadata["columns"] if column["name"] == "customer_email")
        self.assertTrue(email_column["sensitive"])
        self.assertEqual(email_column["top_values"], [])

    def test_prompt_metadata_omits_samples_by_default(self):
        path = self.temp_path(".csv")
        pd.DataFrame({
            "customer_email": ["ana@example.com", "bob@example.com"],
            "region": ["Sul", "Norte"],
        }).to_csv(path, index=False)

        artifact = extract_metadata(path, "customers.csv")
        session = {"files": [{"artifact": artifact, "raw_artifact": artifact}]}

        prompt_payload = metadata_json(session)
        raw_prompt_payload = raw_metadata_json(session)

        self.assertNotIn('"sample"', prompt_payload)
        self.assertNotIn('"sample"', raw_prompt_payload)
        self.assertIn('"sample_policy"', prompt_payload)
        self.assertIn('"included": false', prompt_payload)

    def test_pdf_metadata_includes_text_excerpt_for_document_extraction(self):
        path = self.temp_path(".pdf")
        pdf = FPDF()
        pdf.set_compression(False)
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.multi_cell(0, 8, "Orçamento total aprovado: 10 milhões de euros em 2024.")
        pdf.output(path)

        metadata = extract_metadata(path, "brief.pdf")

        self.assertEqual(metadata["type"], "pdf")
        self.assertIn("text_excerpt", metadata)
        self.assertIn("Orçamento", metadata["text_excerpt"])
        self.assertGreater(metadata["text_extraction"]["characters_extracted"], 0)

    def test_pdf_metadata_discards_binary_like_fallback_text(self):
        path = self.temp_path(".pdf")
        with open(path, "wb") as f:
            f.write(
                b"%PDF-1.4\n"
                b"1 0 obj << /Type /Page /Filter /FlateDecode /Length 42 >>\n"
                b"stream x\x9c(\x02\x8a\xff\x00\x91\x8d\x03)\n"
                b"(N\xf7\xc5q|K\xc2\xe8\x81\x03)\n"
                b"endstream\n"
                b"endobj\n"
                b"xref\n"
            )

        metadata = extract_metadata(path, "encoded.pdf")

        self.assertEqual(metadata["type"], "pdf")
        self.assertEqual(metadata["text_excerpt"], "")
        self.assertEqual(metadata["text_preview"], [])
        self.assertEqual(metadata["text_extraction"]["quality"], "unusable")
        self.assertGreater(metadata["text_extraction"]["discarded_candidates"], 0)


if __name__ == "__main__":
    unittest.main()
