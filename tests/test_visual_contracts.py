import json
from pathlib import Path
import tempfile
from types import ModuleType
import sys
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from matplotlib.colors import to_hex

from app.utils.artifact_validation import validate_report_plan
from app.utils.component_library import (
    COMPONENT_PRESETS, component_preview_image_path, get_chart_style, get_theme,
    presets_prompt_summary,
)
from app.utils.component_renderers import hybrid_component_code, sandbox_runtime_source
from app.utils.template_library import _default_templates, build_layout_from_template, fit_artifacts_to_template
from app.services.pipeline_service import summarize_render_manifest
from app.models.session import normalize_session
from tests.canonical.run_canonical_sessions import validate_component_outcomes


class VisualContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = ModuleType('test_visual_contract_runtime')
        sys.modules[cls.runtime.__name__] = cls.runtime
        exec(sandbox_runtime_source(), cls.runtime.__dict__)

    def setUp(self):
        self.figures = []
        self.runtime.configure_visual_runtime(get_theme('executive_clean'), [], get_chart_style('modern'))
        self.runtime.begin_component_render('library')
        def capture(figure, *_args):
            self.runtime._apply_chart_theme(figure)
            self.figures.append(figure)
        self.runtime._save_chart_to_pdf = capture
        self.metadata = [{'filename': 'data.csv', 'columns': [
            {'name': 'region', 'type': 'object'}, {'name': 'segment', 'type': 'object'},
            {'name': 'revenue', 'type': 'float64'}, {'name': 'cost', 'type': 'float64'},
            {'name': 'date', 'type': 'datetime64[ns]'},
        ]}]
        self.data = {'data.csv': pd.DataFrame({'region': list('ABCDE'), 'revenue': [5, 4, 3, 2, 1]})}

    def tearDown(self):
        for figure in self.figures:
            self.runtime.plt.close(figure)

    def artifact(self, preset='bar_ranked', roles=None, **changes):
        return {
            'id': 'visual', 'type': 'graphic', 'indicator': 'Evidence', 'component_preset_id': preset,
            'data_binding': {'source': 'data.csv', 'roles': roles or {'category': 'region', 'value': 'revenue'}},
            **changes,
        }

    def validated(self, artifact):
        artifacts, issues = validate_report_plan([artifact], self.metadata)
        self.assertFalse([issue for issue in issues if issue['code'].startswith('data_binding')], issues)
        return artifacts[0]

    def render(self, artifact, data=None):
        self.runtime.render_visual_component(None, data or self.data, artifact, 0, 0, 120, 80)
        return self.figures[-1].axes[0]

    def test_multicolumn_table_bindings_survive_validation_and_render_correct_totals(self):
        artifact = self.validated(self.artifact('table_summary', {'dimensions': ['region', 'segment'], 'metrics': ['revenue', 'cost']}))
        self.assertEqual(artifact['data_binding']['roles']['metrics'], ['revenue', 'cost'])
        captured = Mock()
        with patch.object(self.runtime, '_render_table_image', captured):
            self.runtime.render_visual_component(None, {'data.csv': pd.DataFrame({
                'region': ['A', 'A', 'A'], 'segment': ['X', 'X', 'Y'],
                'revenue': [1, 2, 4], 'cost': [2, 3, 6],
            })}, artifact, 0, 0, 120, 80)
        table = captured.call_args.args[1]
        self.assertEqual(table.iloc[0].tolist(), ['A', 'Y', 4, 6])
        self.assertEqual(table.iloc[1].tolist(), ['A', 'X', 3, 5])

    def test_binding_limit_and_ascending_sort_override_preset_defaults(self):
        artifact = self.artifact()
        artifact['data_binding'].update(limit=2, sort='asc')
        axis = self.render(self.validated(artifact))
        self.assertEqual([bar.get_width() for bar in axis.patches], [1, 2])

    def test_explicit_monthly_grain_and_hidden_label_survive_normalization(self):
        artifact = self.validated(self.artifact('line_timeseries', {'date': 'date', 'value': 'revenue'},
                                               visual_options={'date_granularity': 'month', 'hide_x_label': True}))
        axis = self.render(artifact, {'data.csv': pd.DataFrame({
            'date': ['2026-01-01', '2026-01-02', '2026-02-01'], 'revenue': [1, 3, 8],
        })})
        self.assertEqual(list(axis.lines[0].get_ydata()), [4, 8])
        self.assertEqual(axis.get_xlabel(), '')

    def test_rolling_average_uses_chronological_history_before_latest_period_limit(self):
        artifact = self.artifact('line_timeseries', {'date': 'date', 'value': 'revenue'},
                                 visual_options={'rolling_average': True, 'rolling_window': 2, 'date_granularity': 'month'})
        artifact['data_binding'].update(sort='desc', limit=1)
        axis = self.render(self.validated(artifact), {'data.csv': pd.DataFrame({
            'date': ['2026-01-01', '2026-02-01', '2026-03-01'], 'revenue': [1, 3, 9],
        })})
        self.assertEqual(list(axis.lines[0].get_ydata()), [6])

    def test_malformed_filters_are_not_replaced_by_an_unfiltered_population(self):
        artifact = self.artifact()
        artifact['data_binding']['filters'] = {'column': 'region', 'value': 'A'}
        normalized, issues = validate_report_plan([artifact], self.metadata)
        self.assertIn('invalid_data_binding', {issue['code'] for issue in issues})
        with self.assertRaises(self.runtime.VisualBindingError):
            self.render(normalized[0])

    def test_totals_and_unstriped_rows_are_visible_in_a_compact_table(self):
        artifact = self.artifact('table_summary', {'dimensions': 'region', 'metrics': 'revenue'},
                                 visual_options={'show_totals': True, 'striped_rows': False, 'format': '.2f'})
        data = {'data.csv': pd.DataFrame({'region': list('ABCDEFGHIJ'), 'revenue': range(1, 11)})}
        axis = self.render(artifact, data)
        table = axis.tables[0]
        cells = table.get_celld()
        self.assertIn('Total', [cell.get_text().get_text() for cell in cells.values()])
        self.assertIn('55.00', [cell.get_text().get_text() for cell in cells.values()])
        self.assertEqual(cells[1, 0].get_facecolor(), cells[2, 0].get_facecolor())

    def test_detail_table_sort_and_limit_choose_the_requested_records(self):
        artifact = self.artifact('table_detail', {'columns': ['region', 'revenue']}, visual_options={'sort_by': 'revenue'})
        artifact['data_binding'].update(sort='asc', limit=2)
        axis = self.render(artifact)
        cells = axis.tables[0].get_celld()
        self.assertEqual(cells[1, 0].get_text().get_text(), 'E')
        self.assertEqual(cells[2, 0].get_text().get_text(), 'D')

    def test_visual_sort_direction_is_used_when_binding_does_not_override_it(self):
        artifact = self.validated(self.artifact('table_detail', {'columns': ['region', 'revenue']},
                                                visual_options={'sort_by': 'revenue', 'sort_desc': False}))
        self.assertEqual(artifact['data_binding']['sort'], 'asc')
        axis = self.render(artifact)
        self.assertEqual(axis.tables[0].get_celld()[1, 0].get_text().get_text(), 'E')

    def test_grouped_bars_honor_color_and_preserve_others_per_series(self):
        data = {'data.csv': pd.DataFrame({'region': ['A', 'A', 'B', 'B', 'C', 'C'],
                                         'segment': ['X', 'Y'] * 3, 'revenue': [5, 5, 4, 3, 2, 1]})}
        artifact = self.artifact('bar_grouped', {'category': 'region', 'series': 'segment', 'value': 'revenue'},
                                 visual_options={'top_n': 1, 'bar_color': '#123456', 'orientation': 'horizontal'})
        axis = self.render(artifact, data)
        self.assertEqual(sum(bar.get_width() for bar in axis.patches), 20)
        self.assertTrue(all(to_hex(bar.get_facecolor()) == '#123456' for bar in axis.patches))
        self.assertIn('Outros', [label.get_text() for label in axis.get_yticklabels()])

    def test_series_lines_honor_explicit_color(self):
        data = {'data.csv': pd.DataFrame({'date': ['2026-01-01', '2026-01-01', '2026-02-01', '2026-02-01'],
                                         'segment': ['X', 'Y'] * 2, 'revenue': [1, 2, 3, 4]})}
        axis = self.render(self.artifact('line_timeseries', {'date': 'date', 'value': 'revenue', 'series': 'segment'},
                                         visual_options={'line_color': '#123456'}), data)
        self.assertTrue(all(to_hex(line.get_color()) == '#123456' for line in axis.lines))

    def test_donut_limit_keeps_others_and_the_full_denominator(self):
        artifact = self.artifact('radial_donut')
        artifact['data_binding'].update(limit=2)
        axis = self.render(artifact)
        self.assertEqual(len(axis.patches), 2)
        self.assertIn('15', [text.get_text() for text in axis.texts])

    def test_invalid_options_and_binding_are_reported_instead_of_coerced(self):
        artifact = self.artifact(visual_options={'top_n': '2', 'date_granularity': 'month'})
        artifact['data_binding']['aggregation'] = 'not_an_aggregation'
        normalized, issues = validate_report_plan([artifact], self.metadata)
        self.assertIn('invalid_visual_options', {issue['code'] for issue in issues})
        self.assertIn('invalid_data_binding', {issue['code'] for issue in issues})
        self.assertEqual(normalized[0]['render_mode'], 'generated_code')
        with self.assertRaises(self.runtime.VisualBindingError):
            self.render(normalized[0])

    def test_missing_column_has_invalid_binding_status(self):
        artifact = self.artifact(roles={'category': 'region', 'value': 'missing'})
        with self.assertRaises(self.runtime.VisualBindingError) as error:
            self.render(artifact)
        self.assertEqual(error.exception.render_status, 'invalid_binding')

    def test_empty_filter_population_is_distinct_from_success(self):
        artifact = self.artifact()
        artifact['data_binding']['filters'] = [{'column': 'region', 'operator': '==', 'value': 'absent'}]
        self.render(artifact)
        outcome = self.runtime.component_render_outcome()
        self.assertEqual(outcome['status'], 'empty')
        self.assertEqual(outcome['input_rows'], 5)
        self.assertEqual(outcome['filtered_rows'], 0)
        self.assertEqual(self.runtime.component_render_outcome({'status': 'rendered'})['status'], 'empty')

    def test_scatter_preserves_ninth_category_and_null_category_points(self):
        data = {'data.csv': pd.DataFrame({'region': list('ABCDEFGHI') + [None], 'revenue': range(10), 'cost': range(10)})}
        axis = self.render(self.artifact('scatter_relationship', {'x': 'revenue', 'y': 'cost', 'color': 'region'}), data)
        self.assertEqual(sum(len(collection.get_offsets()) for collection in axis.collections), 10)
        self.assertEqual(len(axis.lines[0].get_xdata()), 2)
        self.assertTrue(self.runtime.component_render_outcome()['warnings'])

    def test_scatter_sampling_is_disclosed(self):
        data = {'data.csv': pd.DataFrame({'revenue': range(20), 'cost': range(20)})}
        self.render(self.artifact('scatter_relationship', {'x': 'revenue', 'y': 'cost'}, visual_options={'max_points': 10}), data)
        outcome = self.runtime.component_render_outcome()
        self.assertEqual(outcome['valid_rows'], 20)
        self.assertEqual(outcome['plotted_rows'], 10)
        self.assertTrue(outcome['warnings'])

    def test_component_theme_and_style_apply_without_leaking_to_next_chart(self):
        axis = self.render(self.artifact(visual_theme_id='finance_dark', chart_style_id='minimal'))
        self.assertEqual(to_hex(axis.patches[0].get_facecolor()), get_theme('finance_dark')['palette'][0])
        self.assertFalse(any(line.get_visible() for line in axis.get_xgridlines()))
        axis = self.render(self.artifact())
        self.assertEqual(to_hex(axis.patches[0].get_facecolor()), get_theme('executive_clean')['palette'][0])
        self.assertTrue(any(line.get_visible() for line in axis.get_xgridlines()))

    def test_styles_change_actual_chart_appearance(self):
        modern = self.render(self.artifact())
        editorial = self.render(self.artifact(chart_style_id='editorial'))
        dense = self.render(self.artifact(chart_style_id='dense_dashboard'))
        self.assertGreater(editorial.title.get_fontsize(), modern.title.get_fontsize())
        self.assertLess(dense.get_xticklabels()[0].get_fontsize(), modern.get_xticklabels()[0].get_fontsize())

    def test_hybrid_internal_typeerror_falls_back_once_for_both_signatures(self):
        for accepts_df in (False, True):
            namespace = {'json': json, 'render_visual_component': Mock(return_value='library'), 'render_runtime': self.runtime}
            calls = []
            if accepts_df:
                def generated(pdf, dataframes, x, y, w, h, df=None):
                    calls.append(1)
                    raise TypeError('internal error')
            else:
                def generated(pdf, dataframes, x, y, w, h):
                    calls.append(1)
                    raise TypeError('internal error')
            namespace['render_visual_generated'] = generated
            exec(hybrid_component_code(self.artifact()), namespace)
            with self.assertLogs(level='ERROR'):
                self.assertEqual(namespace['render_visual'](None, {}, 0, 0, 100, 80), 'library')
            self.assertEqual(len(calls), 1)
            namespace['render_visual_component'].assert_called_once()
            self.assertTrue(self.runtime.component_render_outcome()['fallback_used'])

    def test_planner_receives_renderer_capabilities_roles_and_options(self):
        for preset in presets_prompt_summary():
            self.assertIn('library', preset['supported_render_modes'])
            self.assertTrue(preset['data_roles'])
            self.assertTrue(preset['options_schema']['properties'])

    def test_explicit_custom_code_mode_is_preserved(self):
        self.assertEqual(self.validated(self.artifact(render_mode='generated_code'))['render_mode'], 'generated_code')

    def test_legacy_custom_render_function_is_not_replaced_by_a_library_default(self):
        session = normalize_session({'artifacts': [self.artifact()], 'components': {'visual': 'def render_visual(*args): pass'}})
        self.assertEqual(session['artifacts'][0]['render_mode'], 'generated_code')

    def test_overflow_keeps_conclusion_and_every_visual(self):
        template = next(item for item in _default_templates() if item['id'] == 'executive_standard')
        artifacts = [{'id': key, 'type': 'text'} for key in ('kpi_resumo', 'analysis', 'kpi_conclusao')]
        artifacts += [{'id': f'visual_{index}', 'type': 'graphic'} for index in range(5)]
        fitted = fit_artifacts_to_template(artifacts, template)
        with patch('app.utils.template_library.load_layout_templates', return_value=[template]):
            layout = build_layout_from_template(fitted, template['id'])
        self.assertEqual({item['id'] for item in layout}, {item['id'] for item in artifacts})
        self.assertGreater(max(item['page'] for item in layout), 1)

    def test_canonical_checks_reject_missing_or_empty_components(self):
        session = {'template': [{'id': 'chart'}]}
        for manifest in (None, {'components': []}, {'components': [{'id': 'chart', 'status': 'empty'}]}):
            self.assertFalse(all(check['passed'] for check in validate_component_outcomes(session, manifest)))
        self.assertTrue(all(check['passed'] for check in validate_component_outcomes(session, {'components': [{'id': 'chart', 'status': 'rendered'}]})))

    def test_render_summary_contains_counts_and_timing_without_report_content(self):
        summary = summarize_render_manifest({'components': [
            {'id': 'chart', 'preset_id': 'bar_ranked', 'render_mode': 'library', 'status': 'empty',
             'fallback_used': True, 'duration_ms': 12, 'error': 'private source detail'},
            {'id': 'text', 'status': 'rendered', 'duration_ms': 100},
        ]})
        self.assertEqual(summary['visual_count'], 1)
        self.assertEqual(summary['empty_count'], 1)
        self.assertEqual(summary['fallback_count'], 1)
        self.assertEqual(summary['duration_ms'], 12)
        self.assertNotIn('private', json.dumps(summary))

    def test_every_catalog_preview_uses_the_production_renderer(self):
        with tempfile.TemporaryDirectory(prefix='test-visual-previews-') as directory, patch(
            'app.utils.component_library.COMPONENT_PREVIEW_DIR', Path(directory)
        ):
            for preset in COMPONENT_PRESETS:
                path = component_preview_image_path(preset, 'executive_clean', 'modern')
                self.assertGreater(Path(path).stat().st_size, 1000)
            normal = Path(component_preview_image_path('bar_ranked', 'executive_clean', 'modern')).read_bytes()
            minimal = Path(component_preview_image_path('bar_ranked', 'executive_clean', 'minimal')).read_bytes()
            self.assertNotEqual(normal, minimal)


if __name__ == '__main__':
    unittest.main()
