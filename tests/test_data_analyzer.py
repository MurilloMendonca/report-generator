import os
import unittest
import uuid

import pandas as pd

from app.utils.data_analyzer import extract_metadata


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


if __name__ == "__main__":
    unittest.main()
