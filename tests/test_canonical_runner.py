import unittest

from tests.canonical.run_canonical_sessions import (
    title_in_pdf_text,
    validate_chart_layout_safety,
    validate_expected_filter,
    validate_expected_preprocessing,
)


class CanonicalRunnerTests(unittest.TestCase):
    def test_title_in_pdf_text_matches_meaningful_title_words(self):
        self.assertTrue(title_in_pdf_text(
            "Resumo Executivo de Vendas Regionais",
            "Resumo Executivo\nVendas por região e produto",
        ))
        self.assertFalse(title_in_pdf_text(
            "Resumo Executivo de Vendas Regionais",
            "Relatório operacional sem o título esperado",
        ))

    def test_validate_expected_filter_requires_applied_filtered_file(self):
        session = {
            "data_filter": {"needs_filter": True, "description": "Excluir canal Partner", "code": "def filter_dataframes(dataframes): pass"},
            "script": "dataframes['sales.csv'] = pd.read_csv(r'workspace/filtered/out.csv')",
            "files": [{
                "original_filename": "sales.csv",
                "filtered_path": "workspace/filtered/out.csv",
                "raw_artifact": {"shape": {"rows": 8}},
                "artifact": {"shape": {"rows": 4}},
            }],
        }

        checks = validate_expected_filter(session)

        self.assertTrue(all(item["passed"] for item in checks))

    def test_validate_expected_filter_fails_when_filter_was_only_component_level(self):
        session = {
            "data_filter": {"needs_filter": False, "description": "", "code": ""},
            "script": "df = df[df['channel'] != 'Partner']",
            "files": [{
                "original_filename": "sales.csv",
                "raw_artifact": {"shape": {"rows": 8}},
                "artifact": {"shape": {"rows": 8}},
            }],
        }

        checks = validate_expected_filter(session)

        self.assertFalse(all(item["passed"] for item in checks))

    def test_validate_expected_preprocessing_requires_computed_columns(self):
        session = {
            "data_preprocessing": {
                "needs_preprocessing": True,
                "description": "Criar colunas calculadas",
                "code": "def preprocess_dataframes(dataframes): pass",
            },
            "script": "dataframes['sales.csv'] = pd.read_csv(r'workspace/filtered/out.csv')",
            "files": [{
                "original_filename": "sales.csv",
                "preprocessed_path": "workspace/filtered/out.csv",
                "raw_artifact": {
                    "shape": {"rows": 8, "columns": 3},
                    "columns": [{"name": "revenue"}, {"name": "discount_pct"}, {"name": "units"}],
                },
                "artifact": {
                    "shape": {"rows": 8, "columns": 5},
                    "columns": [
                        {"name": "revenue"},
                        {"name": "discount_pct"},
                        {"name": "units"},
                        {"name": "net_revenue_after_discount"},
                        {"name": "revenue_per_unit"},
                    ],
                },
            }],
        }

        checks = validate_expected_preprocessing(session, {
            "preprocessing": True,
            "computed_columns": ["net_revenue_after_discount", "revenue_per_unit"],
        })

        self.assertTrue(all(item["passed"] for item in checks))

    def test_chart_layout_safety_flags_annotations_with_inside_upper_legend(self):
        checks = validate_chart_layout_safety({
            "g_bad": (
                "def render_g_bad(pdf, dataframes, x, y, w, h):\n"
                "    ax.annotate('104%', xy=(0, 10))\n"
                "    ax.legend(loc='upper right')\n"
            )
        })

        self.assertFalse(checks[0]["passed"])
        self.assertEqual(checks[0]["detail"][0]["id"], "g_bad")

    def test_chart_layout_safety_flags_text_labels_with_inside_upper_legend(self):
        checks = validate_chart_layout_safety({
            "g_bad": (
                "def render_g_bad(pdf, dataframes, x, y, w, h):\n"
                "    ax.text(0, 10, 'Meta: 100k')\n"
                "    ax.legend(loc='upper left')\n"
            )
        })

        self.assertFalse(checks[0]["passed"])
        self.assertEqual(checks[0]["detail"][0]["reason"], "data_label_with_inside_upper_legend")

    def test_chart_layout_safety_allows_outside_legend_with_headroom(self):
        checks = validate_chart_layout_safety({
            "g_good": (
                "def render_g_good(pdf, dataframes, x, y, w, h):\n"
                "    ax.set_ylim(0, max_value * 1.25)\n"
                "    ax.annotate('104%', xy=(0, 10))\n"
                "    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.18), ncol=2)\n"
            )
        })

        self.assertTrue(checks[0]["passed"])

    def test_chart_layout_safety_flags_x_label_with_bottom_legend(self):
        checks = validate_chart_layout_safety({
            "g_bad": (
                "def render_g_bad(pdf, dataframes, x, y, w, h):\n"
                "    ax.set_xlabel('Semana')\n"
                "    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.18), ncol=3)\n"
            )
        })

        self.assertFalse(checks[0]["passed"])
        self.assertEqual(checks[0]["detail"][0]["reason"], "x_label_with_bottom_legend")

    def test_chart_layout_safety_allows_bottom_legend_without_x_label(self):
        checks = validate_chart_layout_safety({
            "g_good": (
                "def render_g_good(pdf, dataframes, x, y, w, h):\n"
                "    ax.set_xlabel('')\n"
                "    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.18), ncol=3)\n"
            )
        })

        self.assertTrue(checks[0]["passed"])


if __name__ == "__main__":
    unittest.main()
