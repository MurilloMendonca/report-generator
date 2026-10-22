from pathlib import Path
import tempfile
import unittest

import pandas as pd

from app.utils.data_analyzer import extract_metadata
from app.utils.semantic_model import build_semantic_model
from app.utils.tabular import load_tabular_dataframes


class SemanticModelTests(unittest.TestCase):
    def test_builds_roles_and_relationship_candidates(self):
        model = build_semantic_model([
            {
                "original_filename": "sales.csv",
                "artifact": {
                    "filename": "sales.csv",
                    "shape": {"rows": 2, "columns": 3},
                    "columns": [
                        {"name": "region", "role": "dimension", "unique_values": 2},
                        {"name": "date", "role": "date", "unique_values": 2},
                        {"name": "revenue", "role": "measure", "unique_values": 2},
                    ],
                },
            },
            {
                "original_filename": "targets.csv",
                "artifact": {
                    "filename": "targets.csv",
                    "shape": {"rows": 2, "columns": 2},
                    "columns": [
                        {"name": "region", "role": "dimension", "unique_values": 2},
                        {"name": "target", "role": "measure", "unique_values": 2},
                    ],
                },
            },
        ])

        self.assertEqual(model["summary"]["dataset_count"], 2)
        self.assertEqual(model["datasets"][0]["roles"]["measures"], ["revenue"])
        self.assertEqual(model["datasets"][0]["roles"]["dates"], ["date"])
        self.assertEqual(model["relationships"][0]["column_name"], "region")
        self.assertEqual(model["relationships"][0]["confidence"], "medium")

    def test_excel_sheets_use_runtime_keys_and_expose_relationships_within_one_workbook(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.xlsx"
            with pd.ExcelWriter(path) as writer:
                pd.DataFrame({"region": ["A", "A", "B"], "sales": [10, 20, 30]}).to_excel(writer, sheet_name="sales", index=False)
                pd.DataFrame({"region": ["A", "B"], "target": [35, 40]}).to_excel(writer, sheet_name="targets", index=False)
            metadata = extract_metadata(str(path), "book.xlsx")
            model = build_semantic_model([{"original_filename": "book.xlsx", "artifact": metadata}])
            runtime_keys = set(load_tabular_dataframes("book.xlsx", str(path)))

        self.assertEqual({dataset["filename"] for dataset in model["datasets"]}, runtime_keys)
        self.assertEqual(model["summary"]["dataset_count"], 2)
        self.assertEqual(model["datasets"][0]["roles"]["measures"], ["sales"])
        self.assertEqual(model["datasets"][1]["roles"]["measures"], ["target"])
        relation = model["relationships"][0]
        self.assertEqual(relation["column_name"], "region")
        self.assertEqual({ref["file"] for ref in relation["references"]}, runtime_keys)
        self.assertEqual([ref["rows"] for ref in relation["references"]], [3, 2])
        self.assertEqual([ref["unique_values"] for ref in relation["references"]], [2, 2])
        self.assertTrue(all(ref["type"] for ref in relation["references"]))

    def test_legacy_sheets_without_dataframe_keys_use_loader_aliases(self):
        model = build_semantic_model([{"original_filename": "book.xlsx", "artifact": {"sheets": [
            {"sheet_name": "sales", "columns": [{"name": "region", "role": "dimension"}]},
            {"sheet_name": "targets", "columns": [{"name": "region", "role": "dimension"}]},
        ]}}])
        self.assertEqual([dataset["filename"] for dataset in model["datasets"]], ["book.xlsx", "book.xlsx::targets"])


if __name__ == "__main__":
    unittest.main()
