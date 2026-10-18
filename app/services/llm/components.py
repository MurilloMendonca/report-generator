"""Generated render code for visual components."""

import json
import re
import time

from app.utils.component_library import resolved_visual_context
from app.utils.component_renderers import runtime_api_prompt_reference
from app.utils.layout import normalize_template
from app.utils.model_config import DEFAULT_MODEL_NAME, DEFAULT_PROVIDER
from app.services.llm import client
from app.services.llm.client import llm_operation_context
from app.services.llm.parsing import strip_code_fence
from app.services.llm.prompts import load_prompt


VISUAL_COMPONENT_TYPES = {'graphic', 'bar', 'line', 'pie', 'scatter', 'table', 'chart', 'radial', 'donut', 'gauge'}


def chart_code_has_layout_risk(code: str) -> bool:
    lowered = str(code or "").lower()
    has_x_label = bool(re.search(r"\.set_xlabel\(\s*['\"][^'\"]+['\"]", lowered))
    has_bottom_legend = bool(re.search(r"bbox_to_anchor\s*=\s*\([^)]*-\d*\.?\d+", lowered))
    if has_x_label and has_bottom_legend:
        return True

    has_data_annotation = any(token in lowered for token in [
        "ax.annotate",
        "plt.annotate",
        "ax.text(",
        "plt.text(",
    ])
    if not has_data_annotation:
        return False

    has_inside_upper_legend = any(token in lowered for token in [
        "legend(loc='upper right'",
        'legend(loc="upper right"',
        "legend(loc='upper left'",
        'legend(loc="upper left"',
        "legend(loc='best'",
        'legend(loc="best"',
    ])
    if not has_inside_upper_legend:
        return False

    return "bbox_to_anchor" not in lowered


def slot_by_artifact_id(template: list) -> dict:
    return {item.get("id"): item for item in normalize_template(template or []) if item.get("id")}


def visual_slot_guidance(block: dict | None) -> dict | None:
    if not block:
        return None
    width = block.get("w") or 0
    height = block.get("h") or 0
    compact = width < 110 or height < 75
    return {
        "page": block.get("page", 1),
        "x_mm": block.get("x"),
        "y_mm": block.get("y"),
        "w_mm": width,
        "h_mm": height,
        "compact": compact,
        "guidance": (
            "Compact chart block: prioritize a simple chart, small title, no non-essential data labels, "
            "and avoid bottom legend plus x-axis label."
            if compact else
            "Larger chart block: reserve explicit margins for title, tick labels, and legend."
        ),
    }


def generate_component_code(all_metadata: str, artifact: dict, provider: str = DEFAULT_PROVIDER, model_name: str = DEFAULT_MODEL_NAME, calculated_metrics: dict = None, preceding_text: str = None, settings: dict = None, layout_slot: dict = None, job_id: str | None = None) -> str:
    with llm_operation_context(job_id=job_id, operation="component_code", settings=settings, provider=provider, model_name=model_name):
        system_instruction = load_prompt("GENERATE_COMPONENT_CODE.md")
        lang = settings.get('language', 'Portuguese') if settings else 'Portuguese'
        context = [
            f"Target Language: {lang}",
            f"Target Component JSON:\n{json.dumps(artifact, indent=2)}",
            f"Data Artifacts:\n{all_metadata}"
        ]

        if calculated_metrics:
            context.append(f"Calculated Metrics (Available for reference):\n{json.dumps(calculated_metrics, indent=2)}")

        if preceding_text:
            context.append(f"Preceding Report Text (for context/conclusion):\n{preceding_text}")

        slot_guidance = visual_slot_guidance(layout_slot)
        if slot_guidance:
            context.append(f"Layout Slot Constraints:\n{json.dumps(slot_guidance, indent=2, ensure_ascii=False)}")

        if artifact.get("type") in VISUAL_COMPONENT_TYPES:
            visual_context = resolved_visual_context(artifact, settings or {})
            context.append(
                "Component Preset Contract:\n"
                f"{json.dumps(visual_context['preset'], indent=2, ensure_ascii=False)}"
            )
            context.append(
                "Resolved Visual Theme:\n"
                f"{json.dumps(visual_context['theme'], indent=2, ensure_ascii=False)}"
            )
            context.append(
                "Resolved Chart Style:\n"
                f"{json.dumps(visual_context['style'], indent=2, ensure_ascii=False)}"
            )
            context.append(
                "Resolved Visual Options:\n"
                f"{json.dumps(visual_context['options'], indent=2, ensure_ascii=False)}"
            )
            if artifact.get("data_binding"):
                context.append(
                    "Data Binding Contract:\n"
                    f"{json.dumps(artifact.get('data_binding'), indent=2, ensure_ascii=False)}"
                )
            if artifact.get("render_mode"):
                context.append(f"Render Mode: {artifact.get('render_mode')}")
            context.append(
                "Runtime Renderer API Reference:\n"
                f"{runtime_api_prompt_reference(artifact.get('component_preset_id'))}"
            )

        if artifact.get("type") in VISUAL_COMPONENT_TYPES and settings:
            visual_instructions = settings.get("visual_instructions")
            chart_palette = settings.get("chart_palette")
            if visual_instructions:
                context.append(f"Visual Style Instructions:\n{visual_instructions}")
            if chart_palette:
                context.append(
                    "Preferred Chart Palette:\n"
                    f"{json.dumps(chart_palette, indent=2, ensure_ascii=False)}\n"
                    "Use these colors for chart marks instead of default blue palettes."
                )

        user_prompt = "\n\n".join(context)

        last_code = ""
        for attempt in range(3):
            attempt_prompt = user_prompt
            if attempt > 0:
                attempt_prompt += (
                    "\n\nPrevious attempt violated chart layout safety: it combined data labels "
                    "or annotations with an inside upper-corner legend. Regenerate the function "
                    "with the legend outside the axes using bbox_to_anchor, or remove non-essential "
                    "data labels/annotations."
                )

            text = client.call_llm(provider, model_name, system_instruction, attempt_prompt)
            code = strip_code_fence(text, "python")
            last_code = code
            if artifact.get("type") not in VISUAL_COMPONENT_TYPES or not chart_code_has_layout_risk(code):
                return code
            time.sleep(1)

        return last_code
