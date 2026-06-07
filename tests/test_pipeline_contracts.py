import os
import uuid
import unittest
from unittest.mock import patch

from app.services.pipeline_service import is_visual_label_refinement, run_pipeline, run_script_generation, should_consider_data_filter
from app.services.session_manager import create_session, get_session, get_session_path, get_session_lock_path


class PipelineContractTests(unittest.TestCase):
    def setUp(self):
        self.job_ids = []
        self.created_paths = []

    def tearDown(self):
        for job_id in self.job_ids:
            for path in [get_session_path(job_id), get_session_lock_path(job_id)]:
                if os.path.exists(path):
                    os.remove(path)
        for path in self.created_paths:
            if os.path.exists(path):
                os.remove(path)

    def new_job_id(self):
        job_id = f"test-contract-{uuid.uuid4().hex}"
        self.job_ids.append(job_id)
        return job_id

    def test_friendly_label_feedback_is_visual_not_data_preprocessing(self):
        prompt = (
            'Troque o nome das legendas dos gráficos por nomes mais amigáveis, '
            'por exemplo base_stat_total por Total de Stats Base'
        )

        self.assertTrue(is_visual_label_refinement(prompt))
        self.assertFalse(should_consider_data_filter(prompt))

    def test_full_pipeline_completes_with_mocked_llm_contracts(self):
        job_id = self.new_job_id()
        upload_path = f"workspace/uploads/{uuid.uuid4().hex}.csv"
        with open(upload_path, "w", encoding="utf-8") as f:
            f.write("month,revenue,region,product\n")
            f.write("2026-01,100,Norte,Alpha\n")
            f.write("2026-02,150,Sul,Beta\n")
        self.created_paths.append(upload_path)

        create_session(job_id, {
            "job_id": job_id,
            "initial_prompt": "Relatório de contrato determinístico",
            "current_goal": "Relatório de contrato determinístico",
            "files": [{
                "original_filename": "sales.csv",
                "path": upload_path,
            }],
            "logs": [],
            "artifacts": [],
            "template": [],
            "components": {},
            "calculated_metrics": {},
            "settings": {
                "title": "",
                "font": "Arial",
                "font_size": 10,
                "alignment": "J",
                "line_height": 5,
                "language": "Portuguese",
                "include_data_samples": False,
            },
            "provider": "deepseek",
            "model_name": "deepseek-v4-flash",
        })

        artifacts = [
            {"id": "t_summary", "type": "text", "indicator": "Resumo"},
            {"id": "g_chart", "type": "graphic", "indicator": "Receita"},
            {"id": "m_total", "type": "metric", "indicator": "Total"},
        ]
        template = [
            {"id": "t_summary", "page": 1, "x": 10, "y": 20, "w": 180, "h": 40},
            {"id": "g_chart", "page": 1, "x": 10, "y": 70, "w": 180, "h": 100},
        ]
        text_map = {
            "t_summary": "Resumo objetivo do relatório.",
        }

        def fake_generate_component_code(*args, **kwargs):
            artifact = args[1]
            component_id = artifact["id"]
            return (
                f"def render_{component_id}(pdf, dataframes, x, y, w, h):\n"
                f"    pdf.set_xy(x, y)\n"
                f"    pdf.set_font('helvetica', '', 10)\n"
                f"    pdf.cell(w, 8, {component_id!r})"
            )

        with patch("app.services.pipeline_service.generate_artifacts", return_value=artifacts), \
             patch("app.services.pipeline_service.generate_report_title", return_value="Título Contrato"), \
             patch("app.services.pipeline_service.calculate_metric_value", return_value=123.45), \
             patch("app.services.pipeline_service.generate_template", return_value=template), \
             patch("app.services.pipeline_service.generate_all_texts", return_value=text_map), \
             patch("app.services.pipeline_service.condense_texts_to_fit", side_effect=lambda texts_map, *args, **kwargs: texts_map), \
             patch("app.services.pipeline_service.generate_component_code", side_effect=fake_generate_component_code), \
             patch("app.services.pipeline_service.validate_report_plan", side_effect=lambda plan, *args, **kwargs: (plan, [])), \
             patch("app.services.pipeline_service.fit_artifacts_to_template", side_effect=lambda items, *_args, **_kwargs: items):
            run_pipeline(job_id)

        session = get_session(job_id)
        self.assertEqual(session["current_step"], "completed")
        self.assertTrue(session["status"].startswith("Relatório concluído"))
        self.assertEqual(session["settings"]["title"], "Título Contrato")
        self.assertIn("t_summary", session["components"])
        self.assertIn("g_chart", session["components"])
        self.assertTrue(session["pdf_path"])
        self.assertTrue(os.path.exists(session["pdf_path"]))
        self.assertTrue(os.path.exists(f"{session['pdf_path']}.manifest.json"))

    def test_script_generation_revalidates_stale_library_bindings(self):
        job_id = self.new_job_id()
        upload_path = f"workspace/uploads/{uuid.uuid4().hex}.csv"
        with open(upload_path, "w", encoding="utf-8") as f:
            f.write("name,type_1,base_stat_total\n")
            f.write("Mewtwo,Psychic,680\n")
            f.write("Pikachu,Electric,320\n")
            f.write("Bulbasaur,Grass,318\n")
        self.created_paths.append(upload_path)

        create_session(job_id, {
            "job_id": job_id,
            "initial_prompt": "Tabela Pokemon",
            "current_goal": "Tabela Pokemon",
            "files": [{
                "original_filename": "pokemon.csv",
                "path": upload_path,
                "artifact": {
                    "filename": "pokemon.csv",
                    "columns": [
                        {"name": "name", "type": "str", "role": "dimension"},
                        {"name": "type_1", "type": "str", "role": "dimension"},
                        {"name": "base_stat_total", "type": "int64", "role": "measure"},
                    ],
                },
            }],
            "logs": [],
            "artifacts": [{
                "id": "g_top_pokemon_table",
                "type": "graphic",
                "indicator": "Top Pokemon",
                "component_preset_id": "table_summary",
                "render_mode": "library",
                "columns": [
                    "pokemon.csv:name",
                    "pokemon.csv:type_1",
                    "pokemon.csv:base_stat_total",
                ],
                "data_binding": {
                    "source": "pokemon.csv",
                    "roles": {},
                    "aggregation": "sum",
                    "filters": [],
                    "limit": 3,
                },
                "visual_options": {"top_n": 3},
                "visual_warnings": [
                    "data_binding.roles.dimensions ausente",
                    "data_binding.roles.metrics ausente",
                ],
            }],
            "template": [{"id": "g_top_pokemon_table", "page": 1, "x": 10, "y": 20, "w": 120, "h": 70}],
            "components": {},
            "calculated_metrics": {},
            "settings": {
                "title": "Pokemon",
                "font": "Arial",
                "font_size": 10,
                "alignment": "J",
                "line_height": 5,
                "language": "Portuguese",
                "include_data_samples": False,
            },
            "provider": "deepseek",
            "model_name": "deepseek-v4-flash",
        })

        run_script_generation(job_id)

        session = get_session(job_id)
        artifact = next(item for item in session["artifacts"] if item["id"] == "g_top_pokemon_table")
        self.assertEqual(artifact["data_binding"]["roles"]["dimensions"], "name")
        self.assertEqual(artifact["data_binding"]["roles"]["metrics"], "base_stat_total")
        self.assertEqual(artifact["visual_warnings"], [])
        self.assertIn("from pdf_report_renderers.runtime import", session["script"])
        self.assertNotIn("def render_summary_table", session["script"])
        self.assertEqual(session["current_step"], "completed")


if __name__ == "__main__":
    unittest.main()
