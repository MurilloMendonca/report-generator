import unittest

from app.utils.semantic_model import build_semantic_model


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


if __name__ == "__main__":
    unittest.main()
