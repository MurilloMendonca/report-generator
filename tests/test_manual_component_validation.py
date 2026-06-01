import unittest

from fastapi import HTTPException

from app.main import validate_component_code


class ManualComponentValidationTests(unittest.TestCase):
    def test_accepts_expected_render_function_signature(self):
        validate_component_code(
            "chart",
            "def render_chart(pdf, dataframes, x, y, w, h):\n    return None",
        )

    def test_rejects_syntax_errors(self):
        with self.assertRaises(HTTPException) as error:
            validate_component_code("chart", "def render_chart(:\n    pass")

        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("Invalid Python syntax", error.exception.detail)

    def test_rejects_missing_render_function(self):
        with self.assertRaises(HTTPException) as error:
            validate_component_code("chart", "def other(pdf, dataframes, x, y, w, h):\n    pass")

        self.assertIn("render_chart", error.exception.detail)

    def test_rejects_wrong_signature(self):
        with self.assertRaises(HTTPException) as error:
            validate_component_code("chart", "def render_chart(pdf, x, y):\n    pass")

        self.assertIn("must start with arguments", error.exception.detail)


if __name__ == "__main__":
    unittest.main()
