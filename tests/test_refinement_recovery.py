import asyncio
import json
import os
import unittest
import uuid
from copy import deepcopy
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi import BackgroundTasks

from app.routers import api
from app.services.job_runner import run_job
from app.services import web_helpers
from app.services.llm.errors import LLMContractError, LLMTimeoutError
from app.services.llm.refinement import parse_llm_refinement_decision, refine_session_modular
from app.services.session_manager import (
    delete_session, get_session,
    preserve_report_on_failure, update_session,
)
from tests.auth_helpers import create_session, request_as
from app.utils.component_library import get_theme
from app.utils.component_renderers import sandbox_runtime_source


PRINT_REQUEST = "Use uma paleta de cores com mais contraste nos gráficos, pois será impresso em preto e branco"


def route(intent="visual", **changes):
    decision = {"intent": intent, "scope": "report", "instruction": PRINT_REQUEST}
    if intent == "visual":
        decision["settings_changes"] = {"visual_theme_id": "print_mono", "chart_palette": []}
    decision.update(changes)
    return decision


class RefinementRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.job_id = f"test-refinement-{uuid.uuid4().hex}"
        self.original = {
            "job_id": self.job_id, "initial_prompt": "Analyze data", "current_goal": "Analyze data",
            "pdf_version": 4, "current_step": "completed", "pdf_path": "existing.pdf",
            "script": "last working script", "script_path": "existing.py",
            "files": [{"path": "original.csv", "original_filename": "data.csv", "preprocessed_path": "filtered.csv",
                       "artifact": {"columns": [{"name": "revenue"}], "sample": [{"sensitive": "DO_NOT_SEND_ROWS"}]}}],
            "artifacts": [
                {"id": "g_chart", "type": "graphic", "indicator": "Chart", "render_mode": "library", "component_preset_id": "bar_ranked"},
                {"id": "g_other", "type": "graphic", "indicator": "Other", "render_mode": "library", "component_preset_id": "line_timeseries"},
                {"id": "t_summary", "type": "text", "indicator": "Summary"},
            ],
            "template": [
                {"id": "g_chart", "page": 1, "x": 12, "y": 31, "w": 90, "h": 80},
                {"id": "g_other", "page": 1, "x": 110, "y": 31, "w": 90, "h": 80},
            ],
            "components": {"t_summary": "saved narrative"}, "calculated_metrics": {"total": 1350},
            "settings": {"title": "Pokémon report", "chart_palette": ["#ff0000"]},
        }
        create_session(self.job_id, self.original)

    def tearDown(self):
        delete_session(self.job_id)

    def assert_report_unchanged(self, before):
        after = get_session(self.job_id)
        for key in ("artifacts", "template", "components", "files", "calculated_metrics", "settings",
                    "script", "pdf_version", "current_goal", "refinement_history"):
            self.assertEqual(after[key], before[key], key)

    def run_refinement(self, prompt, decision):
        with patch("app.services.llm.client.call_llm", return_value=json.dumps(decision)) as llm, \
             patch.object(web_helpers, "run_pipeline") as pipeline, \
             patch.object(web_helpers, "run_script_generation") as render:
            web_helpers.process_chat_refinement(self.job_id, prompt)
        return llm, pipeline, render

    def test_api_and_helpers_share_exactly_one_refinement_handler(self):
        self.assertIs(api.process_chat_refinement, web_helpers.process_chat_refinement)

    def test_print_refinement_routes_once_and_preserves_analysis_without_replanning(self):
        before = get_session(self.job_id)
        llm, pipeline, render = self.run_refinement(PRINT_REQUEST, route())
        llm.assert_called_once()
        pipeline.assert_not_called()
        render.assert_called_once_with(self.job_id)
        after = get_session(self.job_id)
        for key in ("artifacts", "template", "components", "files", "calculated_metrics"):
            if key != "artifacts":
                self.assertEqual(after[key], before[key])
        self.assertEqual([item["id"] for item in after["artifacts"]], [item["id"] for item in before["artifacts"]])
        self.assertEqual(after["settings"]["visual_theme_id"], "print_mono")
        self.assertEqual(after["settings"]["chart_palette"], [])
        self.assertNotIn("DO_NOT_SEND_ROWS", llm.call_args.args[3])
        self.assertNotIn("last working script", llm.call_args.args[3])

    def test_single_chart_color_uses_structured_destination_not_prompt_substrings(self):
        decision = route(
            scope="components", component_ids=["g_chart"], settings_changes={},
            visual_options={"bar_color": "#123456"},
        )
        llm, pipeline, render = self.run_refinement("Use a layered red and blue style on the first chart", decision)
        after = get_session(self.job_id)
        self.assertEqual(after["artifacts"][0]["visual_options"]["bar_color"], "#123456")
        self.assertNotIn("visual_options", after["artifacts"][1])
        self.assertEqual(after["settings"]["chart_palette"], ["#ff0000"])
        llm.assert_called_once()
        pipeline.assert_not_called()
        render.assert_called_once()

    def test_global_theme_clears_stale_component_color_overrides(self):
        artifacts = deepcopy(self.original["artifacts"])
        artifacts[0]["visual_options"] = {"bar_color": "#ff0000", "show_values": True}
        update_session(self.job_id, {"artifacts": artifacts})
        self.run_refinement(PRINT_REQUEST, route())
        options = get_session(self.job_id)["artifacts"][0]["visual_options"]
        self.assertNotIn("bar_color", options)
        self.assertTrue(options["show_values"])

    def test_theme_change_resets_prior_palette_even_if_model_omits_reset(self):
        self.run_refinement(PRINT_REQUEST, route(settings_changes={"visual_theme_id": "print_mono"}))
        self.assertEqual(get_session(self.job_id)["settings"]["chart_palette"], [])

    def test_visual_labels_merge_without_changing_source_columns(self):
        artifacts = deepcopy(self.original["artifacts"])
        artifacts[0]["visual_options"] = {"labels": {"region": "Região"}}
        update_session(self.job_id, {"artifacts": artifacts})
        decision = route(scope="components", component_ids=["g_chart"], settings_changes={},
                         visual_options={"labels": {"revenue": "Receita"}})
        self.run_refinement("Normalize only the labels, not the data", decision)
        after = get_session(self.job_id)
        self.assertEqual(after["artifacts"][0]["visual_options"]["labels"], {"region": "Região", "revenue": "Receita"})
        self.assertEqual(after["files"][0]["artifact"]["columns"], [{"name": "revenue"}])

    def test_layout_patch_preserves_other_blocks_and_artifacts(self):
        before = get_session(self.job_id)
        llm, pipeline, render = self.run_refinement("Move the first chart", route(
            "layout", scope="components", component_ids=["g_chart"], layout_updates=[{"id": "g_chart", "x": 14, "y": 33}],
        ))
        after = get_session(self.job_id)
        self.assertEqual(after["template"][0]["x"], 14)
        self.assertEqual(after["template"][0]["y"], 33)
        self.assertEqual(after["template"][1], before["template"][1])
        self.assertEqual(after["artifacts"], before["artifacts"])
        llm.assert_called_once()
        pipeline.assert_not_called()
        render.assert_called_once()

    def test_settings_change_uses_router_parameters_without_a_second_model_call(self):
        llm, pipeline, render = self.run_refinement("Make the title readable", route(
            "settings", settings_changes={"title": "New title", "font_size": 12},
        ))
        after = get_session(self.job_id)
        self.assertEqual(after["settings"]["title"], "New title")
        self.assertEqual(after["settings"]["font_size"], 12)
        llm.assert_called_once()
        pipeline.assert_not_called()
        render.assert_called_once()

    def test_vague_request_pauses_without_mutating_report_or_goal(self):
        before = get_session(self.job_id)
        llm, pipeline, render = self.run_refinement("Improve this", route(
            "clarify", instruction="Improve this", clarification_question="A aparência, o layout ou o conteúdo?",
        ))
        self.assert_report_unchanged(before)
        after = get_session(self.job_id)
        self.assertEqual(after["current_step"], "awaiting_clarification")
        self.assertEqual(after["pending_refinement"]["prompt"], "Improve this")
        self.assertEqual(after["refinement_messages"][-1]["kind"], "clarification")
        pipeline.assert_not_called()
        render.assert_not_called()

    def test_clarification_answer_has_pending_context_and_commits_only_resolved_request(self):
        self.run_refinement("Improve this", route(
            "clarify", instruction="Improve this", clarification_question="A aparência ou os dados?",
        ))
        decision = route(instruction="Use grayscale for black-and-white printing.")
        llm, pipeline, render = self.run_refinement("The appearance, for printing", decision)
        context = json.loads(llm.call_args.args[3].split("\n\nDecision JSON schema:")[0])
        self.assertEqual(context["pending_clarification"]["prompt"], "Improve this")
        after = get_session(self.job_id)
        self.assertEqual(after["pending_refinement"], {})
        self.assertEqual(after["refinement_history"], ["Use grayscale for black-and-white printing."])
        self.assertNotIn("Improve this", after["current_goal"])
        pipeline.assert_not_called()
        render.assert_called_once()

    def test_data_route_is_explicit_and_supports_mixed_print_styling(self):
        decision = route("data", instruction="Analyze only legendary Pokémon and use grayscale.",
                         settings_changes={"visual_theme_id": "print_mono", "chart_palette": []})
        llm, pipeline, render = self.run_refinement("Só lendários, gráficos em preto e branco", decision)
        llm.assert_called_once()
        pipeline.assert_called_once_with(self.job_id)
        render.assert_not_called()
        after = get_session(self.job_id)
        self.assertEqual(after["artifacts"], [])
        self.assertEqual(after["template"], [])
        self.assertEqual(after["components"], {})
        self.assertIsNone(after["files"][0]["preprocessed_path"])
        self.assertEqual(after["settings"]["visual_theme_id"], "print_mono")
        self.assertIn("Analyze only legendary", after["current_goal"])

    def test_explicit_report_route_regenerates_without_clearing_active_preprocessing(self):
        llm, pipeline, render = self.run_refinement("Add an indicator for margins", route("report", instruction="Add a margin indicator."))
        pipeline.assert_called_once()
        render.assert_not_called()
        self.assertEqual(get_session(self.job_id)["files"][0]["preprocessed_path"], "filtered.csv")

    def test_invalid_or_legacy_routing_output_never_defaults_to_full_regeneration(self):
        for response in ("ALL", "g_chart", "not JSON", '{"intent":"surprise","scope":"report","instruction":"Change"}'):
            with self.subTest(response=response):
                create_session(self.job_id, self.original)
                before = get_session(self.job_id)
                with patch("app.services.llm.client.call_llm", return_value=response), \
                     patch.object(web_helpers, "run_pipeline") as pipeline, \
                     patch.object(web_helpers, "run_script_generation") as render:
                    web_helpers.process_chat_refinement(self.job_id, "Whatever")
                self.assert_report_unchanged(before)
                self.assertEqual(get_session(self.job_id)["current_step"], "failed")
                pipeline.assert_not_called()
                render.assert_not_called()

    def test_timeout_preserves_report_and_does_not_guess_a_route(self):
        before = get_session(self.job_id)
        with patch("app.services.llm.client.call_llm", side_effect=LLMTimeoutError("refinement_routing", 45)), \
             patch.object(web_helpers, "run_pipeline") as pipeline, \
             patch.object(web_helpers, "run_script_generation") as render:
            web_helpers.process_chat_refinement(self.job_id, "Use only black and white")
        self.assert_report_unchanged(before)
        self.assertEqual(get_session(self.job_id)["error"]["code"], "llm_timeout")
        pipeline.assert_not_called()
        render.assert_not_called()

    def test_failed_data_refinement_restores_report_goal_and_accepted_history(self):
        before = get_session(self.job_id)
        def fail_pipeline(job_id):
            update_session(job_id, {"current_step": "failed", "error": {"code": "llm_timeout", "stage": "artifact_plan"}})
        with patch("app.services.llm.client.call_llm", return_value=json.dumps(route("data", instruction="Only legendary Pokémon"))), \
             patch.object(web_helpers, "run_pipeline", side_effect=fail_pipeline):
            web_helpers.process_chat_refinement(self.job_id, "Only legendary Pokémon")
        self.assert_report_unchanged(before)
        after = get_session(self.job_id)
        self.assertEqual(after["current_step"], "failed")
        self.assertEqual(after["error"]["code"], "llm_timeout")
        self.assertTrue(any(message["kind"] == "error" for message in after["refinement_messages"]))

    def test_queue_does_not_accept_feedback_into_goal_before_classification(self):
        before = get_session(self.job_id)
        tasks = BackgroundTasks()
        asyncio.run(api.chat_refinement(request_as(), self.job_id, tasks, "Make this better"))
        after = get_session(self.job_id)
        self.assertEqual(after["current_goal"], before["current_goal"])
        self.assertEqual(after["refinement_history"], [])
        self.assertEqual(after["refinement_messages"][-2]["role"], "user")
        self.assertIs(tasks.tasks[0].func, run_job)
        self.assertIs(tasks.tasks[0].args[0], web_helpers.process_chat_refinement)

    def test_guard_preserves_report_if_refinement_raises(self):
        @preserve_report_on_failure
        def fail(job_id):
            update_session(job_id, {"artifacts": [], "template": []})
            raise RuntimeError("failed")
        with self.assertRaises(RuntimeError):
            fail(self.job_id)
        self.assertEqual(get_session(self.job_id)["artifacts"], self.original["artifacts"])

    def test_successful_new_pdf_is_not_rolled_back(self):
        @preserve_report_on_failure
        def succeed(job_id):
            update_session(job_id, {"pdf_version": 5, "artifacts": [], "current_step": "completed"})
        succeed(self.job_id)
        self.assertEqual(get_session(self.job_id)["pdf_version"], 5)
        self.assertEqual(get_session(self.job_id)["artifacts"], [])

    def test_contract_rejects_invalid_scope_settings_and_layout_before_any_mutation(self):
        session = get_session(self.job_id)
        invalid = [
            route(scope="components", component_ids=["unknown"], settings_changes={}, visual_options={"color": "#123456"}),
            route(scope="components", component_ids=["t_summary"], settings_changes={}, visual_options={"color": "#123456"}),
            route(settings_changes={"visual_theme_id": "nonexistent"}),
            route(settings_changes={"chart_style_id": "nonexistent"}),
            route(settings_changes={"chart_palette": ["red"]}),
            route(settings_changes={"llm_call_budget": 9999}),
            route("component", scope="components", component_ids=["g_chart"], settings_changes={"font_size": 12}),
            route("layout", layout_updates=[{"id": "unknown", "x": 10}]),
            route("layout", layout_updates=[{"id": "g_chart", "x": float("nan")}]),
            route("layout", layout_updates=[{"id": "g_chart", "x": -10}]),
            route("layout", layout_updates=[{"id": "g_chart", "x": 200}]),
            route("layout", layout_updates=[{"id": "g_chart", "y": 275}]),
            route("layout", scope="components", component_ids=["g_other"], layout_updates=[{"id": "g_chart", "x": 10}]),
            route("settings", settings_changes={"font_size": 1000}),
            route("settings", settings_changes={"font": "/etc/passwd"}),
            route("visual", settings_changes={}, visual_options={"filters": []}),
            route("clarify", clarification_question="How?", settings_changes={"title": "Unexpected"}),
        ]
        for decision in invalid:
            with self.subTest(decision=decision), self.assertRaises(LLMContractError):
                parse_llm_refinement_decision(json.dumps(decision), session)

    def test_modular_application_does_not_mutate_caller_or_reclassify(self):
        before = get_session(self.job_id)
        with patch("app.services.llm.client.call_llm") as llm:
            updated = refine_session_modular(before, PRINT_REQUEST, decision=route())
        llm.assert_not_called()
        self.assertEqual(before, get_session(self.job_id))
        self.assertEqual(updated["settings"]["visual_theme_id"], "print_mono")



class MonochromeRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = {}
        exec(sandbox_runtime_source(), cls.runtime)

    def setUp(self):
        theme = get_theme("print_mono")
        self.runtime["configure_visual_runtime"](theme, theme["palette"])
        self.figures = []
        self.runtime["_save_chart_to_pdf"] = lambda fig, *args: self.figures.append(fig)
        self.frames = {"data.csv": pd.DataFrame({
            "x": [1, 2, 3, 4], "y": [3, 5, 4, 8],
            "group": ["A", "A", "B", "B"], "date": [2020, 2021, 2020, 2021],
        })}

    def tearDown(self):
        for fig in self.figures:
            self.runtime["plt"].close(fig)

    def test_scatter_uses_distinct_marker_shapes_in_print_theme(self):
        self.runtime["render_scatter_plot"](
            object(), self.frames, 0, 0, 100, 80, x_value="x", y_value="y", color="group",
        )
        groups = self.figures[0].axes[0].collections
        self.assertEqual(len(groups), 2)
        self.assertFalse(np.array_equal(groups[0].get_paths()[0].vertices, groups[1].get_paths()[0].vertices))
        self.assertEqual(self.figures[0].axes[0].lines[0].get_linestyle(), "--")

    def test_grouped_bars_use_distinct_patterns(self):
        self.runtime["render_bar_chart"](
            object(), self.frames, 0, 0, 100, 80, category="date", value="y", series="group",
        )
        containers = self.figures[0].axes[0].containers
        self.assertNotEqual(containers[0][0].get_hatch(), containers[1][0].get_hatch())

    def test_lines_use_distinct_styles_and_markers(self):
        self.runtime["render_line_chart"](
            object(), self.frames, 0, 0, 100, 80, date="date", value="y", series="group",
        )
        lines = self.figures[0].axes[0].lines
        self.assertNotEqual(lines[0].get_linestyle(), lines[1].get_linestyle())
        self.assertNotEqual(lines[0].get_marker(), lines[1].get_marker())


if __name__ == "__main__":
    unittest.main()
