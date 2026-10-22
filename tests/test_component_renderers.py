import json
import os
import unittest
import uuid

import pandas as pd

from app.services.executor_service import execute_script
from app.services.llm.script import assemble_script
from app.utils.component_library import list_presets
from app.utils.component_renderers import (
    library_component_code,
    rename_generated_component_code,
    runtime_api_prompt_reference,
    sandbox_runtime_source,
    supports_library_renderer,
)


class ComponentRendererTests(unittest.TestCase):
    def setUp(self):
        self.created_paths = []
        os.makedirs("workspace/uploads", exist_ok=True)
        os.makedirs("workspace/reports", exist_ok=True)
        os.makedirs("workspace/scripts", exist_ok=True)

    def tearDown(self):
        for path in self.created_paths:
            if os.path.exists(path):
                os.remove(path)

    def tracked_path(self, path):
        self.created_paths.append(path)
        return path

    def test_library_metadata_exposes_supported_render_modes(self):
        presets = {preset["id"]: preset for preset in list_presets()}

        self.assertTrue(supports_library_renderer("bar_ranked"))
        self.assertIn("library", presets["bar_ranked"]["supported_render_modes"])
        self.assertIn("hybrid", presets["table_summary"]["supported_render_modes"])

    def test_library_component_code_wraps_visual_renderer(self):
        code = library_component_code({
            "id": "g_sales",
            "component_preset_id": "bar_ranked",
            "render_mode": "library",
        })

        self.assertIn("def render_g_sales", code)
        self.assertIn("render_visual_component", code)
        self.assertIn("json.loads", code)
        self.assertIn('\\"render_mode\\": \\"library\\"', code)

    def test_renames_generated_code_for_hybrid_fallback(self):
        code = "def render_g_sales(pdf, dataframes, x, y, w, h):\n    pass"

        renamed = rename_generated_component_code("g_sales", code)

        self.assertIn("def render_g_sales_generated", renamed)
        self.assertNotIn("def render_g_sales(pdf", renamed)

    def test_hybrid_wrapper_prefers_generated_adapter(self):
        from app.utils.component_renderers import hybrid_component_code

        code = hybrid_component_code({
            "id": "g_stats",
            "indicator": "Stats",
            "component_preset_id": "table_summary",
            "render_mode": "hybrid",
        })
        namespace = {
            "json": json,
            "render_visual_component": lambda *_args, **_kwargs: "library",
            "render_g_stats_generated": lambda *_args, **_kwargs: "generated",
        }
        exec(code, namespace)

        self.assertEqual(namespace["render_g_stats"](object(), {}, 0, 0, 10, 10), "generated")

    def test_runtime_api_reference_supports_concise_customization(self):
        reference = runtime_api_prompt_reference("table_summary")

        self.assertIn("render_summary_table", reference)
        self.assertIn("_render_table_image(pdf, dataframe, x, y, w, h, title=None, theme=None, options=None)", reference)
        self.assertIn("render_runtime", reference)
        self.assertIn("_library_dataframe", reference)
        self.assertIn("Do not paste runtime helper bodies", reference)

    def test_table_image_accepts_options_table_theme(self):
        namespace = {}
        exec(sandbox_runtime_source(), namespace)
        namespace["_save_chart_to_pdf"] = lambda *_args, **_kwargs: None

        namespace["_render_table_image"](
            object(),
            pd.DataFrame({"Pais": ["Brazil"], "Casos": [207]}),
            0,
            0,
            90,
            60,
            title="Resumo",
            options={"table_theme": {"header_bg": "#123456"}},
        )

    def test_assemble_script_executes_library_bar_renderer(self):
        csv_path = self.tracked_path(f"workspace/uploads/library_renderer_{uuid.uuid4().hex}.csv")
        pd.DataFrame({
            "region": ["Norte", "Sul", "Norte", "Leste"],
            "revenue": [100, 60, 40, 30],
        }).to_csv(csv_path, index=False)
        output_pdf = os.path.abspath(self.tracked_path(f"workspace/reports/library_renderer_{uuid.uuid4().hex}.pdf"))
        self.created_paths.append(f"{output_pdf}.manifest.json")
        script_path = self.tracked_path(f"workspace/scripts/library_renderer_{uuid.uuid4().hex}.py")

        artifact = {
            "id": "g_sales",
            "indicator": "Receita por regiao",
            "type": "bar",
            "component_preset_id": "bar_ranked",
            "render_mode": "library",
            "visual_options": {
                "orientation": "horizontal",
                "top_n": 5,
                "show_values": True,
                "aggregate_others": True,
            },
            "data_binding": {
                "source": "sales.csv",
                "roles": {"category": "region", "value": "revenue"},
                "aggregation": "sum",
                "filters": [],
            },
        }
        script = assemble_script(
            {},
            [{"id": "g_sales", "page": 1, "x": 10, "y": 20, "w": 120, "h": 80}],
            {"sales.csv": csv_path},
            output_pdf,
            settings={"title": "Library Test", "visual_theme_id": "executive_clean", "chart_style_id": "modern"},
            include_title=False,
            artifacts=[artifact],
        )

        self.assertIn("import pdf_report_renderers.runtime as render_runtime", script)
        self.assertIn("from pdf_report_renderers.runtime import", script)
        self.assertNotIn("def render_bar_chart", script)
        self.assertNotIn("def render_donut_chart", script)
        self.assertNotIn("def render_gauge_chart", script)

        result = execute_script(script, script_path, timeout=30)

        self.assertTrue(result["success"], result.get("stderr") or result.get("stdout"))
        self.assertTrue(os.path.exists(output_pdf))
        with open(f"{output_pdf}.manifest.json", "r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
        self.assertEqual(manifest["components"][0]["id"], "g_sales")
        self.assertEqual(manifest["components"][0]["status"], "rendered")

    def test_assemble_script_prefers_custom_library_adapter_when_present(self):
        csv_path = self.tracked_path(f"workspace/uploads/library_adapter_{uuid.uuid4().hex}.csv")
        pd.DataFrame({
            "name": ["Alpha", "Beta", "Gamma"],
            "base_stat_total": [720, 680, 650],
        }).to_csv(csv_path, index=False)
        output_pdf = os.path.abspath(self.tracked_path(f"workspace/reports/library_adapter_{uuid.uuid4().hex}.pdf"))
        self.created_paths.append(f"{output_pdf}.manifest.json")
        script_path = self.tracked_path(f"workspace/scripts/library_adapter_{uuid.uuid4().hex}.py")

        adapter_code = """
def render_g_stats(pdf, dataframes, x, y, w, h, df=None):
    source = "pokemon.csv"
    raw = dataframes.get(source)
    if raw is None:
        raw = list(dataframes.values())[0] if dataframes else pd.DataFrame()
    work = raw.rename(columns={"name": "Pokemon", "base_stat_total": "Total de Stats Base"}).copy()
    artifact = {
        "id": "g_stats",
        "indicator": "Lendarios Mais Poderosos",
        "component_preset_id": "table_summary",
        "render_mode": "library",
        "visual_options": {"top_n": 3, "title": "Lendarios Mais Poderosos"},
        "data_binding": {
            "source": source,
            "roles": {"dimensions": "Pokemon", "metrics": "Total de Stats Base"},
            "aggregation": "sum",
            "filters": [],
        },
    }
    adjusted = dict(dataframes)
    adjusted[source] = work
    return render_visual_component(pdf, adjusted, artifact, x, y, w, h)
""".strip()
        artifact = {
            "id": "g_stats",
            "indicator": "Top stats",
            "type": "graphic",
            "component_preset_id": "table_summary",
            "render_mode": "library",
            "data_binding": {
                "source": "pokemon.csv",
                "roles": {"dimensions": "name", "metrics": "base_stat_total"},
                "aggregation": "sum",
                "filters": [],
            },
        }

        script = assemble_script(
            {"g_stats": adapter_code},
            [{"id": "g_stats", "page": 1, "x": 10, "y": 20, "w": 120, "h": 80}],
            {"pokemon.csv": csv_path},
            output_pdf,
            settings={"title": "Adapter Test", "visual_theme_id": "executive_clean", "chart_style_id": "modern"},
            include_title=False,
            artifacts=[artifact],
        )

        self.assertIn('"Total de Stats Base"', script)
        self.assertEqual(script.count("def render_g_stats("), 1)

        result = execute_script(script, script_path, timeout=30)

        self.assertTrue(result["success"], result.get("stderr") or result.get("stdout"))
        with open(f"{output_pdf}.manifest.json", "r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
        self.assertEqual(manifest["components"][0]["status"], "rendered")

    def test_library_count_aggregation_accepts_categorical_values(self):
        namespace = {}
        exec(sandbox_runtime_source(), namespace)
        aggregate = namespace["_library_aggregate"]

        result = aggregate(
            pd.DataFrame({
                "type_1": ["Water", "Water", "Grass", "Fire", "Fire", "Fire"],
                "name": ["A", "B", "C", "D", "E", "F"],
            }),
            ["type_1"],
            "name",
            "count",
        )

        counts = dict(zip(result["type_1"], result["name"]))
        self.assertEqual(counts, {"Fire": 3, "Grass": 1, "Water": 2})

    def test_library_line_count_does_not_treat_text_ids_as_empty(self):
        namespace = {}
        exec(sandbox_runtime_source(), namespace)

        def fail_empty_chart(*_args, **_kwargs):
            raise AssertionError("line chart should not render an empty placeholder for count aggregation")

        namespace["_library_empty_chart"] = fail_empty_chart
        namespace["_save_chart_to_pdf"] = lambda *_args, **_kwargs: None

        namespace["render_line_chart"](
            object(),
            {"cases.csv": pd.DataFrame({
                "report_date": ["2026-01-01", "2026-01-02", "2026-01-02"],
                "case_id": ["A", "B", "C"],
            })},
            0,
            0,
            120,
            80,
            date="report_date",
            value="case_id",
            source="cases.csv",
            title="Cases",
            options={"aggregation": "count"},
        )

    def test_library_line_monthly_title_groups_by_month(self):
        namespace = {}
        exec(sandbox_runtime_source(), namespace)
        captured = {}

        class PlotFrame(pd.DataFrame):
            @property
            def _constructor(self):
                return PlotFrame

            def plot(self, *args, **kwargs):
                captured["values"] = self.iloc[:, 0].tolist()

        original_pivot_table = pd.DataFrame.pivot_table

        def tracked_pivot_table(self, *args, **kwargs):
            return PlotFrame(original_pivot_table(self, *args, **kwargs))

        namespace["pd"].DataFrame.pivot_table = tracked_pivot_table
        namespace["_save_chart_to_pdf"] = lambda *_args, **_kwargs: None
        try:
            namespace["render_line_chart"](
                object(),
                {"cases.csv": pd.DataFrame({
                    "report_date": ["2026-01-01", "2026-01-15", "2026-02-01"],
                    "case_id": ["A", "B", "C"],
                    "series": ["Total", "Total", "Total"],
                })},
                0,
                0,
                120,
                80,
                date="report_date",
                value="case_id",
                series="series",
                source="cases.csv",
                title="Evolução Mensal de Casos",
                options={"aggregation": "count"},
            )
        finally:
            namespace["pd"].DataFrame.pivot_table = original_pivot_table

        self.assertEqual(captured["values"], [2, 1])

    def test_library_line_annual_axis_uses_label_and_integer_years(self):
        namespace = {}
        exec(sandbox_runtime_source(), namespace)
        namespace["_save_chart_to_pdf"] = lambda *_args, **_kwargs: None

        namespace["render_line_chart"](
            object(),
            {"cases.xlsx": pd.DataFrame({
                "Year": [2020, 2020, 2021, 2022],
                "Violence_Victims": [10, 5, 12, 14],
            })},
            0,
            0,
            90,
            75,
            date="Year",
            value="Violence_Victims",
            source="cases.xlsx",
            title="Tendência Anual do Total de Vítimas",
            options={"aggregation": "sum", "labels": {"Year": "Ano", "Violence_Victims": "Vítimas"}},
        )

        ax = namespace["plt"].gcf().axes[0]
        self.assertEqual(ax.get_xlabel(), "Ano")
        tick_labels = [label.get_text() for label in ax.get_xticklabels()]
        self.assertIn("2020", tick_labels)
        self.assertIn("2022", tick_labels)

    def test_library_line_chart_uses_requested_line_color(self):
        namespace = {}
        exec(sandbox_runtime_source(), namespace)
        namespace["_save_chart_to_pdf"] = lambda *_args, **_kwargs: None

        namespace["render_line_chart"](
            object(),
            {"cases.csv": pd.DataFrame({
                "report_date": ["2026-01-01", "2026-01-02"],
                "case_id": ["A", "B"],
            })},
            0,
            0,
            90,
            75,
            date="report_date",
            value="case_id",
            source="cases.csv",
            title="Tendência Temporal de Casos",
            options={"aggregation": "count", "line_color": "#dc2626"},
        )

        ax = namespace["plt"].gcf().axes[0]
        self.assertEqual(ax.lines[0].get_color(), "#dc2626")

    def test_library_summary_table_count_keeps_categorical_ids(self):
        namespace = {}
        exec(sandbox_runtime_source(), namespace)
        captured = {}

        def capture_table(_pdf, dataframe, *_args, **_kwargs):
            captured["dataframe"] = dataframe.copy()

        namespace["_render_table_image"] = capture_table

        namespace["render_summary_table"](
            object(),
            {"cases.csv": pd.DataFrame({
                "fatality": ["No", "No", "Yes"],
                "case_id": ["A", "B", "C"],
            })},
            0,
            0,
            120,
            80,
            dimensions=["fatality"],
            metrics=["case_id"],
            source="cases.csv",
            title="Summary",
            options={"aggregation": "count"},
        )

        result = captured["dataframe"]
        counts = dict(zip(result["Fatality"], result["Case ID"]))
        self.assertEqual(counts, {"No": 2, "Yes": 1})

    def test_library_runtime_uses_translated_display_labels(self):
        namespace = {}
        exec(sandbox_runtime_source(), namespace)

        self.assertEqual(namespace["_library_display_label"]("base_stat_total", {}), "Total de Stats Base")
        self.assertEqual(namespace["_library_display_label"]("is_legendary", {}), "Lendario")
        self.assertEqual(
            namespace["_library_measure_label"]("name", {"labels": {"count": "Quantidade"}}, "count"),
            "Quantidade",
        )

        display_df = namespace["_library_display_dataframe"](
            pd.DataFrame({"type_1": ["Fire"], "base_stat_total": [680]}),
            {"labels": {"type_1": "Tipo", "base_stat_total": "Total de Stats Base"}},
        )

        self.assertEqual(list(display_df.columns), ["Tipo", "Total de Stats Base"])

    def test_library_runtime_wraps_chart_titles_without_tight_bbox(self):
        source = sandbox_runtime_source()
        namespace = {}
        exec(source, namespace)

        wrapped = namespace["_wrapped_chart_title"](
            "Media do GPA Pos-Semestre por Categoria de Curso com um titulo longo",
            90,
        )

        self.assertIn("\n", wrapped)
        self.assertNotIn("bbox_inches='tight'", source)
        self.assertEqual(namespace["_table_bbox_for_title"](2), [0.0, 0.03, 1.0, 0.84])
        self.assertEqual(namespace["_table_top_margin"](2), 0.98)
        self.assertIn("bbox=table_bbox", source)
        self.assertIn("_draw_table_title(fig, title, w)", source)


class TableFittingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = {}
        exec(sandbox_runtime_source(), cls.runtime)

    def render_table(self, dataframe, width=90, height=55, title=None, options=None):
        captured = []
        self.runtime['_save_chart_to_pdf'] = lambda fig, *_args: captured.append(fig)
        self.runtime['begin_component_render']('library')
        self.runtime['_render_table_image'](object(), dataframe, 0, 0, width, height, title=title, options=options)
        figure = captured[0]
        self.addCleanup(self.runtime['plt'].close, figure)
        figure.canvas.draw()
        return figure, self.runtime['component_render_outcome']()

    def assert_text_fits(self, figure):
        renderer = figure.canvas.get_renderer()
        table = figure.axes[0].tables[0]
        for position, cell in table.get_celld().items():
            with self.subTest(cell=position):
                bounds = cell.get_window_extent(renderer)
                text_bounds = cell.get_text().get_window_extent(renderer)
                self.assertGreaterEqual(text_bounds.x0, bounds.x0 + 0.5)
                self.assertLessEqual(text_bounds.x1, bounds.x1 - 0.5)
                self.assertGreaterEqual(text_bounds.y0, bounds.y0 + 0.5)
                self.assertLessEqual(text_bounds.y1, bounds.y1 - 0.5)
        if figure.texts:
            title_bounds = figure.texts[0].get_window_extent(renderer)
            self.assertGreaterEqual(title_bounds.x0, 0)
            self.assertLessEqual(title_bounds.x1, figure.bbox.width)
            table_top = max(cell.get_window_extent(renderer).y1 for cell in table.get_celld().values())
            self.assertLess(table_top, title_bounds.y0)

    def test_long_headers_and_descriptions_wrap_inside_cells(self):
        dataframe = pd.DataFrame({
            'Departamento responsável pelo atendimento': ['Relacionamento com clientes corporativos', 'Operações e infraestrutura regional'],
            'Descrição das ações recomendadas': ['Acompanhar os chamados e revisar o prazo de resposta com a equipe.', 'Definir responsáveis e monitorar as entregas semanais.'],
            'Receita acumulada no período (R$)': [1234567.89, 98765.43],
        })
        original = dataframe.copy(deep=True)
        figure, _ = self.render_table(dataframe, title='Resumo das recomendações e resultados por departamento')
        self.assert_text_fits(figure)
        headers = [cell.get_text().get_text() for (row, _), cell in figure.axes[0].tables[0].get_celld().items() if row == 0]
        self.assertTrue(any('\n' in header for header in headers))
        self.assertEqual(' '.join(headers[0].split()), dataframe.columns[0])
        pd.testing.assert_frame_equal(dataframe, original)

    def test_dense_table_limits_text_and_keeps_total(self):
        dataframe = pd.DataFrame({
            'Identificador do contrato': ['CONTRATO_' + 'W' * 180] * 8 + ['Total'],
            'Observações': ['Descrição muito extensa ' * 70] * 8 + [''],
            'Valor': list(range(8)) + [28],
        })
        figure, outcome = self.render_table(dataframe, width=75, height=36, title='Resultados detalhados', options={'show_totals': True})
        self.assert_text_fits(figure)
        texts = [cell.get_text().get_text() for cell in figure.axes[0].tables[0].get_celld().values()]
        self.assertIn('Total', texts)
        self.assertIn('28', texts)
        self.assertTrue(any('…' in text for text in texts))
        self.assertTrue(outcome['warnings'])

    def test_headers_with_wide_letters_and_explicit_newlines_fit_at_export_dpi(self):
        dataframe = pd.DataFrame({
            'WWWWWWWWWWWWWWWWWWWWWWWWWW': ['ID_' + 'W' * 70],
            'Prazo\nprevisto para conclusão': ['Concluir até sexta-feira\nResponsável: equipe técnica'],
        })
        figure, _ = self.render_table(dataframe, width=70, height=42, title='W' * 80)
        for dpi in (100, 300):
            with self.subTest(dpi=dpi):
                figure.set_dpi(dpi)
                figure.canvas.draw()
                self.assert_text_fits(figure)

    def test_formatted_numbers_and_literal_currency_are_preserved(self):
        dataframe = pd.DataFrame({'Cliente / contrato': ['$250.00$', 'ACME'], 'Receita': [1234567.89, -234567.89]})
        figure, _ = self.render_table(dataframe, width=85, height=40, options={'format': ',.2f'})
        self.assert_text_fits(figure)
        values = [cell.get_text().get_text() for cell in figure.axes[0].tables[0].get_celld().values()]
        self.assertIn('1,234,567.89', values)
        self.assertIn('-234,567.89', values)
        self.assertIn('$250.00$', values)

    def test_missing_values_unicode_and_repeated_labels_fit(self):
        dataframe = pd.DataFrame([['São José / região Sul', None, ['A', 'B']], ['Operações técnicas', pd.NA, 'Revisão concluída']], columns=['Descrição', 'Descrição', 'Observações'])
        figure, _ = self.render_table(dataframe, width=100, height=50)
        self.assert_text_fits(figure)
        values = [cell.get_text().get_text() for cell in figure.axes[0].tables[0].get_celld().values()]
        self.assertIn('São José / região Sul', values)
        self.assertEqual(values.count('Descrição'), 2)

    def test_too_small_slot_reports_the_problem_instead_of_drawing_overflow(self):
        figure, outcome = self.render_table(pd.DataFrame({'Total': [1234567.89]}), width=30, height=8, title='Receita total')
        self.assertEqual(len(figure.axes[0].tables), 0)
        self.assertEqual(outcome['status'], 'empty')
        self.assertIn('too small', outcome['message'])


if __name__ == "__main__":
    unittest.main()
