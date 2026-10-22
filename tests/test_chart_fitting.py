import unittest

import pandas as pd

from app.utils.component_library import get_chart_style, get_theme
from app.utils.component_renderers import sandbox_runtime_source


class ChartFittingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = {}
        exec(sandbox_runtime_source(), cls.runtime)

    def setUp(self):
        self.figures = []
        self.runtime['configure_visual_runtime'](get_theme('executive_clean'), style=get_chart_style('modern'))
        self.runtime['begin_component_render']('library')
        self.runtime['_save_chart_to_pdf'] = lambda fig, *_: self.figures.append(fig)
        self.labels = ['Departamento de relacionamento com clientes corporativos - ' + region for region in ['Norte', 'Sul', 'Leste', 'Oeste', 'Centro']]
        self.data = pd.DataFrame({'department': self.labels, 'value': [29, 17, 11, 3, 1], 'cost': [8, 12, 20, 27, 35],
                                  'series': ['Clientes corporativos do segmento de ' + segment for segment in ['energia', 'varejo', 'serviços', 'indústria', 'tecnologia']],
                                  'date': pd.date_range('2025-01-01', periods=5, freq='MS')})

    def tearDown(self):
        for figure in self.figures:
            self.runtime['plt'].close(figure)

    def render(self, kind, *, width=70, height=45, title=None, **options):
        self.runtime['begin_component_render']('library')
        self.runtime[kind](None, {'data.csv': self.data}, 0, 0, width, height, source='data.csv',
                           title=title or 'Resultados por departamento e segmento durante o período de avaliação dos indicadores operacionais', **options)
        figure = self.figures[-1]
        figure.canvas.draw()
        self.assertEqual(self.runtime['component_render_outcome']()['status'], 'rendered')
        return figure

    def assert_text_inside(self, figure):
        figure.canvas.draw()
        renderer = figure.canvas.get_renderer()
        texts = list(figure.texts)
        for axis in figure.axes:
            texts.extend(axis.texts)
            legend = axis.get_legend()
            if legend:
                texts.extend([*legend.get_texts(), legend.get_title()])
                self.assertFalse(legend.get_window_extent(renderer).overlaps(axis.get_window_extent(renderer)), 'legend must leave the data area clear')
            if axis.axison:
                texts.extend([axis.title, axis.xaxis.label, axis.yaxis.label])
                for direction in (axis.xaxis, axis.yaxis):
                    lower, upper = sorted(direction.get_view_interval())
                    tick_texts = [tick.label1 for tick in direction.get_major_ticks() if lower <= tick.get_loc() <= upper and tick.label1.get_visible() and tick.label1.get_text()]
                    texts.extend(tick_texts)
                    tick_boxes = [text.get_window_extent(renderer) for text in tick_texts]
                    for index, bounds in enumerate(tick_boxes):
                        self.assertFalse(any(bounds.overlaps(other) for other in tick_boxes[index + 1:]), 'tick labels must not overlap')
        for text in texts:
            if not text.get_visible() or not text.get_text().strip():
                continue
            with self.subTest(text=text.get_text()):
                bounds = text.get_window_extent(renderer)
                self.assertGreaterEqual(bounds.x0, -1)
                self.assertGreaterEqual(bounds.y0, -1)
                self.assertLessEqual(bounds.x1, figure.bbox.width + 1)
                self.assertLessEqual(bounds.y1, figure.bbox.height + 1)

    def test_shared_prefixes_keep_every_bar_and_value_distinct(self):
        for orientation in ('horizontal', 'vertical'):
            with self.subTest(orientation=orientation):
                figure = self.render('render_bar_chart', category='department', value='value', options={'orientation': orientation, 'show_values': True})
                axis = figure.axes[0]
                positions = [bar.get_y() if orientation == 'horizontal' else bar.get_x() for bar in axis.patches]
                self.assertEqual(len(set(positions)), len(self.data))
                values = [bar.get_width() if orientation == 'horizontal' else bar.get_height() for bar in axis.patches]
                self.assertEqual(sorted(values), sorted(self.data.value))
                ticks = axis.get_yticklabels() if orientation == 'horizontal' else axis.get_xticklabels()
                self.assertEqual(len({tick.get_text() for tick in ticks}), len(self.data))
                self.assert_text_inside(figure)

    def test_grouped_bars_line_and_scatter_fit_long_legends(self):
        cases = [('render_bar_chart', {'category': 'department', 'value': 'value', 'series': 'series'}),
                 ('render_line_chart', {'date': 'date', 'value': 'value', 'series': 'series', 'options': {'show_values': True}}),
                 ('render_scatter_plot', {'x_value': 'cost', 'y_value': 'value', 'color': 'series', 'options': {'show_labels': True}})]
        for kind, parameters in cases:
            with self.subTest(kind=kind):
                figure = self.render(kind, **parameters)
                legend = figure.axes[0].get_legend()
                self.assertEqual(len(legend.get_texts()), len(self.data))
                self.assertEqual(len({text.get_text() for text in legend.get_texts()}), len(self.data))
                if kind == 'render_bar_chart':
                    renderer = figure.canvas.get_renderer()
                    for bar in figure.axes[0].patches:
                        if bar.get_width() > 0:
                            thickness = bar.get_window_extent(renderer).height
                            self.assertGreater(thickness, 2 * renderer.points_to_pixels(bar.get_linewidth()), 'bar borders must leave a visible fill')
                self.assert_text_inside(figure)

    def test_donut_keeps_small_slices_and_percentages_in_a_fitted_legend(self):
        figure = self.render('render_donut_chart', category='department', value='value')
        axis = figure.axes[0]
        self.assertEqual(len(axis.patches), len(self.data))
        self.assertEqual(len(axis.get_legend().get_texts()), len(self.data))
        self.assertTrue(all('%' in text.get_text() for text in axis.get_legend().get_texts()))
        self.assertEqual([text.get_text() for text in axis.texts if text.get_text()], ['61'])
        self.assert_text_inside(figure)

    def test_gauge_keeps_large_formatted_value_intact(self):
        self.data = pd.DataFrame({'value': [1234567890123.45]})
        figure = self.render('render_gauge_chart', width=40, height=30, title='Faturamento acumulado', value='value',
                             options={'target': 2000000000000, 'show_center_value': False, 'format': ',.2f'})
        self.assertEqual([text.get_text() for text in figure.axes[0].texts], ['1,234,567,890,123.45'])
        self.assert_text_inside(figure)

    def test_dense_point_annotations_do_not_collide_or_remove_data(self):
        self.data = pd.DataFrame({'cost': [1] * 25, 'value': [1] * 25})
        figure = self.render('render_scatter_plot', x_value='cost', y_value='value', options={'show_labels': True, 'show_trendline': False})
        axis = figure.axes[0]
        self.assertEqual(sum(len(collection.get_offsets()) for collection in axis.collections), 25)
        renderer = figure.canvas.get_renderer()
        boxes = [text.get_window_extent(renderer) for text in axis.texts if text.get_visible()]
        self.assertTrue(boxes)
        for index, bounds in enumerate(boxes):
            self.assertFalse(any(bounds.overlaps(other) for other in boxes[index + 1:]))
        self.assertTrue(any('omitted' in warning for warning in self.runtime['component_render_outcome']()['warnings']))
        self.assert_text_inside(figure)

    def test_long_chart_title_has_visible_ellipsis_and_does_not_overlap_plot(self):
        figure = self.render('render_bar_chart', category='department', value='value')
        heading = figure.texts[0]
        self.assertIn('…', heading.get_text())
        self.assert_text_inside(figure)
        self.assertGreater(heading.get_window_extent(figure.canvas.get_renderer()).y0, figure.axes[0].get_window_extent().y1)

    def test_dense_date_ticks_preserve_every_plotted_value(self):
        self.data = pd.DataFrame({'date': pd.date_range('2020-01-01', periods=36, freq='MS'), 'value': range(36)})
        figure = self.render('render_line_chart', date='date', value='value', options={'date_granularity': 'month'})
        self.assertEqual(len(figure.axes[0].lines[0].get_ydata()), 36)
        self.assert_text_inside(figure)

    def test_impossible_slot_reports_resizing_instead_of_clipping(self):
        self.runtime['begin_component_render']('library')
        self.runtime['render_bar_chart'](None, {'data.csv': self.data}, 0, 0, 20, 12,
                                         category='department', value='value', source='data.csv', title='Resultado detalhado')
        self.assertEqual(self.runtime['component_render_outcome']()['code'], 'layout_too_small')
        self.assert_text_inside(self.figures[-1])


if __name__ == '__main__':
    unittest.main()
