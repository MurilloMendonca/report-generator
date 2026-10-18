import os
import json
import uuid
import unittest
from unittest.mock import patch

from app.services.pipeline_service import (
    _advance_render_modes_after_failure,
    _apply_binding_warning_render_fallbacks,
    _failed_components_from_manifest,
    run_pipeline,
    run_script_generation,
)
from app.services.session_manager import delete_session, get_session
from app.utils.template_library import get_layout_template
from tests.auth_helpers import create_session


class PipelineContractTests(unittest.TestCase):
    def setUp(self):
        self.job_ids = []
        self.created_paths = []

    def tearDown(self):
        for job_id in self.job_ids:
            delete_session(job_id)
        for path in self.created_paths:
            if os.path.exists(path):
                os.remove(path)

    def new_job_id(self):
        job_id = f"test-contract-{uuid.uuid4().hex}"
        self.job_ids.append(job_id)
        return job_id

    def test_binding_warning_advances_library_to_hybrid(self):
        artifacts = [{
            "id": "g_cases_by_virus",
            "type": "graphic",
            "component_preset_id": "radial_donut",
            "render_mode": "library",
        }]
        issues = [{
            "id": "g_cases_by_virus",
            "code": "data_binding_role_mismatch",
            "role": "value",
        }]

        updated, components, changed = _apply_binding_warning_render_fallbacks(artifacts, issues, {})

        self.assertEqual(changed, ["g_cases_by_virus"])
        self.assertEqual(updated[0]["render_mode"], "hybrid")
        self.assertEqual(components, {})

    def test_render_failure_advances_hybrid_to_generated_code(self):
        session = {
            "artifacts": [{
                "id": "g_cases_by_virus",
                "type": "graphic",
                "component_preset_id": "radial_donut",
                "render_mode": "hybrid",
            }],
            "components": {
                "g_cases_by_virus": "def render_g_cases_by_virus(pdf, dataframes, x, y, w, h):\n    pass"
            },
        }

        payload, next_stage, changed = _advance_render_modes_after_failure(session)

        self.assertEqual(changed, ["g_cases_by_virus"])
        self.assertEqual(next_stage, "script")
        self.assertEqual(payload["artifacts"][0]["render_mode"], "generated_code")
        self.assertIn("g_cases_by_virus", payload["components"])

    def test_render_failure_fallback_only_touches_failed_manifest_components(self):
        session = {
            "artifacts": [
                {
                    "id": "g_failed",
                    "type": "graphic",
                    "component_preset_id": "bar_ranked",
                    "render_mode": "library",
                },
                {
                    "id": "g_unrelated",
                    "type": "graphic",
                    "component_preset_id": "bar_ranked",
                    "render_mode": "library",
                },
            ],
            "components": {},
        }

        payload, next_stage, changed = _advance_render_modes_after_failure(session, {"g_failed"})

        modes = {artifact["id"]: artifact["render_mode"] for artifact in payload["artifacts"]}
        self.assertEqual(changed, ["g_failed"])
        self.assertEqual(next_stage, "pipeline")
        self.assertEqual(modes["g_failed"], "hybrid")
        self.assertEqual(modes["g_unrelated"], "library")

    def test_failed_components_from_manifest_reads_only_failed_ids(self):
        path = f"workspace/reports/test_manifest_{uuid.uuid4().hex}.json"
        self.created_paths.append(path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "components": [
                    {"id": "g_ok", "status": "rendered"},
                    {"id": "g_failed", "status": "failed"},
                ]
            }, f)

        self.assertEqual(_failed_components_from_manifest(path), {"g_failed"})

    def test_document_text_only_template_is_registered(self):
        template = get_layout_template("document_text_only")

        self.assertIsNotNone(template)
        self.assertTrue(template["slots"])
        self.assertTrue(all(slot["type"] == "text" for slot in template["slots"]))

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

        with patch("app.services.pipeline_service.generate_data_filter", return_value={"needs_preprocessing": False, "description": "", "code": ""}) as preprocessing, \
             patch("app.services.pipeline_service.generate_artifacts", return_value=artifacts), \
             patch("app.services.pipeline_service.generate_report_title", return_value="Título Contrato"), \
             patch("app.services.pipeline_service.calculate_metric_value", return_value=123.45), \
             patch("app.services.pipeline_service.generate_template", return_value=template), \
             patch("app.services.pipeline_service.generate_all_texts", return_value=text_map), \
             patch("app.services.pipeline_service.condense_texts_to_fit", side_effect=lambda texts_map, *args, **kwargs: texts_map), \
             patch("app.services.pipeline_service.generate_component_code", side_effect=fake_generate_component_code), \
             patch("app.services.pipeline_service.validate_report_plan", side_effect=lambda plan, *args, **kwargs: (plan, [])), \
             patch("app.services.pipeline_service.fit_artifacts_to_template", side_effect=lambda items, *_args, **_kwargs: items):
            run_pipeline(job_id)
            run_pipeline(job_id)
            preprocessing.assert_called_once()

        session = get_session(job_id)
        self.assertEqual(session["current_step"], "completed")
        self.assertTrue(session["data_preprocessing"]["evaluated"])
        self.assertTrue(session["status"].startswith("Relatório concluído"))
        self.assertEqual(session["settings"]["title"], "Título Contrato")
        self.assertIn("t_summary", session["components"])
        self.assertIn("g_chart", session["components"])
        self.assertTrue(session["pdf_path"])
        self.assertTrue(os.path.exists(session["pdf_path"]))
        self.assertTrue(os.path.exists(f"{session['pdf_path']}.manifest.json"))

    def test_document_only_pipeline_uses_text_template_and_document_metrics(self):
        job_id = self.new_job_id()
        upload_path = f"workspace/uploads/{uuid.uuid4().hex}.pdf"
        with open(upload_path, "wb") as f:
            f.write(b"%PDF-1.3\n% test\n")
        self.created_paths.append(upload_path)

        create_session(job_id, {
            "job_id": job_id,
            "initial_prompt": "Resuma o documento e destaque métricas relevantes",
            "current_goal": "Resuma o documento e destaque métricas relevantes",
            "files": [{
                "original_filename": "brief.pdf",
                "path": upload_path,
                "raw_artifact": {
                    "filename": "brief.pdf",
                    "type": "pdf",
                    "pages": 1,
                    "text_excerpt": "O orçamento aprovado foi de 10 milhões de euros em 2024.",
                    "text_preview": ["O orçamento aprovado foi de 10 milhões de euros em 2024."],
                },
                "artifact": {
                    "filename": "brief.pdf",
                    "type": "pdf",
                    "pages": 1,
                    "text_excerpt": "O orçamento aprovado foi de 10 milhões de euros em 2024.",
                    "text_preview": ["O orçamento aprovado foi de 10 milhões de euros em 2024."],
                },
            }],
            "logs": [],
            "artifacts": [],
            "template": [],
            "components": {},
            "calculated_metrics": {},
            "document_insights": {},
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
            {"id": "kpi_resumo", "type": "text", "indicator": "Resumo executivo"},
            {"id": "document_key_findings", "type": "text", "indicator": "Principais achados"},
            {"id": "document_implications", "type": "text", "indicator": "Implicações e ressalvas"},
            {"id": "kpi_conclusao", "type": "text", "indicator": "Conclusão"},
        ]
        text_map = {
            "kpi_resumo": "Resumo do documento com orçamento aprovado.",
            "document_key_findings": "O documento informa orçamento de 10 milhões de euros em 2024.",
            "document_implications": "A evidência vem do texto extraído do PDF.",
            "kpi_conclusao": "Recomenda-se acompanhar a execução do orçamento.",
        }
        insights = {
            "summary": "Documento sobre orçamento.",
            "key_points": ["Orçamento aprovado em 2024."],
            "entities": [],
            "periods": ["2024"],
            "metrics": [{
                "id": "m_budget",
                "label": "Orçamento aprovado",
                "value": 10,
                "unit": "milhões de euros",
                "period": "2024",
                "source": "brief.pdf",
                "confidence": "high",
                "evidence": "orçamento aprovado foi de 10 milhões de euros em 2024",
            }],
            "warnings": [],
        }

        with patch("app.services.pipeline_service.generate_document_insights", return_value=insights), \
             patch("app.services.pipeline_service.generate_artifacts", return_value=artifacts), \
             patch("app.services.pipeline_service.generate_report_title", return_value="Relatório Documental"), \
             patch("app.services.pipeline_service.generate_all_texts", return_value=text_map), \
             patch("app.services.pipeline_service.condense_texts_to_fit", side_effect=lambda texts_map, *args, **kwargs: texts_map), \
             patch("app.services.pipeline_service.validate_report_plan", side_effect=lambda plan, *args, **kwargs: (plan, [])):
            run_pipeline(job_id)

        session = get_session(job_id)
        self.assertEqual(session["current_step"], "completed")
        self.assertEqual(session["settings"]["selected_template_id"], "document_text_only")
        self.assertEqual(session["calculated_metrics"]["m_budget"]["source_type"], "document")

    def test_document_only_pipeline_sends_unusable_pdf_to_document_model(self):
        job_id = self.new_job_id()
        upload_path = f"workspace/uploads/{uuid.uuid4().hex}.pdf"
        with open(upload_path, "wb") as f:
            f.write(b"%PDF-1.4\n% encoded test pdf\n")
        self.created_paths.append(upload_path)

        pdf_artifact = {
            "filename": "encoded.pdf",
            "type": "pdf",
            "pages": 1,
            "text_excerpt": "",
            "text_preview": [],
            "text_extraction": {"quality": "unusable"},
        }
        create_session(job_id, {
            "job_id": job_id,
            "initial_prompt": "Extraia métricas relevantes dos PDFs",
            "current_goal": "Extraia métricas relevantes dos PDFs",
            "files": [{
                "original_filename": "encoded.pdf",
                "path": upload_path,
                "raw_artifact": pdf_artifact,
                "artifact": dict(pdf_artifact),
            }],
            "logs": [],
            "artifacts": [],
            "template": [],
            "components": {},
            "calculated_metrics": {},
            "document_insights": {},
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
            {"id": "kpi_resumo", "type": "text", "indicator": "Resumo executivo"},
            {"id": "document_key_findings", "type": "text", "indicator": "Principais achados"},
            {"id": "document_implications", "type": "text", "indicator": "Implicações e ressalvas"},
            {"id": "kpi_conclusao", "type": "text", "indicator": "Conclusão"},
        ]
        text_map = {artifact["id"]: "Texto" for artifact in artifacts}
        insights = {
            "summary": "Resumo vindo do PDF completo.",
            "key_points": ["Métrica lida no anexo."],
            "entities": [],
            "periods": [],
            "metrics": [{
                "id": "m_pdf_total",
                "label": "Total no PDF",
                "value": 42,
                "source": "encoded.pdf",
                "confidence": "high",
                "evidence": "Total 42",
            }],
            "warnings": [],
            "extraction_source": "pdf_file",
        }

        with patch("app.services.pipeline_service.generate_document_insights") as text_extract, \
             patch("app.services.pipeline_service.generate_document_insights_from_pdf_file", return_value=insights) as pdf_extract, \
             patch("app.services.pipeline_service.generate_artifacts", return_value=artifacts), \
             patch("app.services.pipeline_service.generate_report_title", return_value="Relatório Documental"), \
             patch("app.services.pipeline_service.generate_all_texts", return_value=text_map), \
             patch("app.services.pipeline_service.condense_texts_to_fit", side_effect=lambda texts_map, *args, **kwargs: texts_map), \
             patch("app.services.pipeline_service.validate_report_plan", side_effect=lambda plan, *args, **kwargs: (plan, [])):
            run_pipeline(job_id)

        session = get_session(job_id)
        text_extract.assert_not_called()
        pdf_extract.assert_called_once()
        self.assertEqual(session["document_insights"]["encoded.pdf"]["extraction_source"], "pdf_file")
        self.assertEqual(session["calculated_metrics"]["m_pdf_total"]["source_type"], "document")
        self.assertIn("document_key_findings", session["components"])
        self.assertTrue(all(item["id"] in text_map for item in session["template"]))

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

    def test_script_generation_ignores_stale_component_for_library_mode(self):
        job_id = self.new_job_id()
        upload_path = f"workspace/uploads/{uuid.uuid4().hex}.csv"
        with open(upload_path, "w", encoding="utf-8") as f:
            f.write("country,case_id\n")
            f.write("Brazil,A\n")
            f.write("Brazil,B\n")
            f.write("Peru,C\n")
        self.created_paths.append(upload_path)

        create_session(job_id, {
            "job_id": job_id,
            "initial_prompt": "Casos por pais",
            "current_goal": "Casos por pais",
            "files": [{
                "original_filename": "cases.csv",
                "path": upload_path,
                "artifact": {
                    "filename": "cases.csv",
                    "columns": [
                        {"name": "country", "type": "object", "role": "dimension"},
                        {"name": "case_id", "type": "object", "role": "dimension"},
                    ],
                },
            }],
            "logs": [],
            "artifacts": [{
                "id": "g_cases_by_country",
                "type": "graphic",
                "indicator": "Casos por Pais",
                "component_preset_id": "bar_ranked",
                "render_mode": "library",
                "columns": ["cases.csv:country", "cases.csv:case_id"],
                "data_binding": {
                    "source": "cases.csv",
                    "roles": {"category": "country", "value": "case_id"},
                    "aggregation": "count",
                    "filters": [],
                },
                "visual_options": {"top_n": 5},
            }],
            "template": [{"id": "g_cases_by_country", "page": 1, "x": 10, "y": 20, "w": 120, "h": 80}],
            "components": {
                "g_cases_by_country": "def render_g_cases_by_country(pdf, dataframes, x, y, w, h):\n    raise RuntimeError('stale component')"
            },
            "calculated_metrics": {},
            "settings": {
                "title": "Cases",
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
        self.assertEqual(session["current_step"], "completed")
        self.assertNotIn("g_cases_by_country", session["components"])
        self.assertNotIn("stale component", session["script"])


if __name__ == "__main__":
    unittest.main()
