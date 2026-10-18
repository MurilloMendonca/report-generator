import ast
import unittest
from pathlib import Path
from unittest.mock import patch

LLM_PACKAGE = Path(__file__).resolve().parents[1] / "app" / "services" / "llm"


class LlmPackageLayoutTests(unittest.TestCase):
    """Tests mock providers by patching app.services.llm.client.call_llm, which
    only works if every caller looks call_llm up on the client module."""

    def test_call_llm_is_only_reached_through_the_client_module(self):
        for path in sorted(LLM_PACKAGE.glob("*.py")):
            if path.name == "client.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "app.services.llm.client":
                    imported = {alias.name for alias in node.names}
                    self.assertNotIn("call_llm", imported, f"{path.name} imports call_llm by name")
                if isinstance(node, ast.Name) and node.id == "call_llm":
                    self.fail(f"{path.name}:{node.lineno} uses bare call_llm; use client.call_llm")

    def test_package_does_not_re_export_names(self):
        tree = ast.parse((LLM_PACKAGE / "__init__.py").read_text(encoding="utf-8"))
        self.assertFalse(
            [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))],
            "re-exports would let tests patch a copy that real callers never use",
        )

    def test_one_patch_reaches_every_generator(self):
        from app.services.llm.planning import fallback_report_title, generate_report_title

        with patch("app.services.llm.client.call_llm", return_value="Vendas por Região") as llm:
            self.assertEqual(generate_report_title("[]", "vendas", []), "Vendas por Região")
        llm.assert_called_once()
        self.assertTrue(fallback_report_title("vendas"))


if __name__ == "__main__":
    unittest.main()
