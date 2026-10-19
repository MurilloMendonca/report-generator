from contextlib import ExitStack
import json
import shutil
import unittest
from unittest.mock import patch
import uuid

import pandas as pd

from app.services.executor_service import execute_script, probe_bubblewrap
from app.services.llm.script import assemble_script
from app.services.pipeline_service import run_execution
from app.utils.report_paths import ReportPaths


BWRAP_AVAILABLE, BWRAP_ERROR = probe_bubblewrap()


@unittest.skipUnless(BWRAP_AVAILABLE, f'bubblewrap unavailable: {BWRAP_ERROR}')
class LibraryExecutionTests(unittest.TestCase):
    def setUp(self):
        self.job_id = f'test-library-{uuid.uuid4().hex}'
        self.paths = ReportPaths.for_new_report(self.job_id, 'library-tests').ensure()
        self.addCleanup(shutil.rmtree, self.paths.root)
        self.csv = self.paths.uploads / 'data.csv'
        pd.DataFrame({'region': ['A', 'A', 'B'], 'revenue': [1, 2, 4], 'cost': [2, 3, 6]}).to_csv(self.csv, index=False)
        self.pdf = (self.paths.output / 'report.pdf').resolve()
        self.script_path = self.paths.scripts / 'report.py'
        self.artifact = {
            'id': 'chart', 'indicator': 'Evidence', 'type': 'graphic',
            'component_preset_id': 'bar_ranked', 'render_mode': 'library',
            'data_binding': {'source': 'data.csv', 'roles': {'category': 'region', 'value': 'revenue'}},
        }

    def execute(self, components=None):
        script = assemble_script(components or {}, [{'id': 'chart', 'x': 10, 'y': 30, 'w': 150, 'h': 100}],
                                 {'data.csv': str(self.csv)}, str(self.pdf), artifacts=[self.artifact])
        result = execute_script(script, str(self.script_path), paths=self.paths)
        manifest = json.loads(self.pdf.with_suffix('.pdf.manifest.json').read_text())
        return result, manifest['components'][0]

    def test_real_pdf_and_manifest_record_library_usage(self):
        result, outcome = self.execute()
        self.assertTrue(result['success'], result['stderr'])
        self.assertTrue(self.pdf.exists())
        self.assertEqual(outcome['status'], 'rendered')
        self.assertEqual(outcome['render_mode'], 'library')
        self.assertEqual(outcome['filtered_rows'], 3)
        self.assertGreater(outcome['duration_ms'], 0)

    def test_empty_chart_cannot_produce_a_successful_pdf(self):
        self.artifact['data_binding']['filters'] = [{'column': 'region', 'value': 'absent'}]
        result, outcome = self.execute()
        self.assertFalse(result['success'])
        self.assertEqual(outcome['status'], 'empty')
        self.assertFalse(self.pdf.exists())

    def test_missing_column_is_an_invalid_binding_in_the_manifest(self):
        self.artifact['data_binding']['roles']['value'] = 'missing'
        result, outcome = self.execute()
        self.assertFalse(result['success'])
        self.assertEqual(outcome['status'], 'invalid_binding')

    def test_generated_adapter_internal_typeerror_uses_library_fallback(self):
        self.artifact['render_mode'] = 'hybrid'
        result, outcome = self.execute({'chart': 'def render_chart(pdf, dataframes, x, y, w, h, df=None):\n    raise TypeError("internal failure")'})
        self.assertTrue(result['success'], result['stderr'])
        self.assertEqual(outcome['status'], 'rendered')
        self.assertTrue(outcome['fallback_used'])
        self.assertEqual(outcome['render_mode'], 'library')

    def test_multicolumn_table_renders_inside_the_report_sandbox(self):
        self.artifact['component_preset_id'] = 'table_summary'
        self.artifact['data_binding']['roles'] = {'dimensions': ['region'], 'metrics': ['revenue', 'cost']}
        result, outcome = self.execute()
        self.assertTrue(result['success'], result['stderr'])
        self.assertEqual(outcome['status'], 'rendered')


class EmptyVisualRecoveryTests(unittest.TestCase):
    def test_empty_data_does_not_trigger_model_repair_or_generated_fallback(self):
        session = {'job_id': 'test-empty-render', 'script': 'pass', 'script_path': 'unused.py', 'pdf_path': 'unused.pdf'}
        with ExitStack() as stack:
            def mocked(name, **kwargs):
                return stack.enter_context(patch(f'app.services.pipeline_service.{name}', **kwargs))
            mocked('get_session', return_value=session)
            mocked('_ensure_current_run', return_value=True)
            mocked('add_log')
            mocked('append_stage_event')
            mocked('update_session')
            mocked('execute_script', return_value={'success': False, 'stderr': 'empty chart'})
            mocked('_read_render_manifest', return_value={'components': [{'id': 'chart', 'preset_id': 'bar_ranked', 'status': 'empty'}]})
            fallback = mocked('_advance_render_modes_after_failure')
            fix = mocked('fix_script')
            stage = mocked('set_session_stage')
            run_execution('test-empty-render', run_id=1)
        fallback.assert_not_called()
        fix.assert_not_called()
        self.assertEqual(stage.call_args.args[3]['code'], 'empty_visual')
        self.assertEqual(stage.call_args.args[3]['component_ids'], ['chart'])


if __name__ == '__main__':
    unittest.main()
