"""Create a PDF of the bundled layout structures without models or report data."""

import argparse
import json
from pathlib import Path

from fpdf import FPDF


CATALOG = Path(__file__).resolve().parents[1] / "templates" / "layout_templates.json"


def draw_slot(pdf, slot):
    x, y, width, height = (slot[key] for key in ("x", "y", "w", "h"))
    visual = slot["type"] == "graphic"
    pdf.set_fill_color(*(231, 242, 247) if visual else (248, 244, 236))
    pdf.set_draw_color(*(139, 181, 199) if visual else (209, 195, 166))
    pdf.rect(x, y, width, height, style="DF")
    pdf.set_text_color(*(33, 87, 108) if visual else (110, 88, 54))
    pdf.set_font("Helvetica", "B", 6)
    pdf.set_xy(x + 3, y + 3)
    pdf.cell(width - 6, 3, "VISUAL" if visual else "TEXTO")
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_xy(x + 3, y + 9)
    pdf.multi_cell(width - 6, 4, slot["name"])
    body_y = max(y + 22, pdf.get_y() + 5)
    if visual and height >= 55:
        chart_height = max(10, y + height - body_y - 7)
        bar_width = (width - 20) / 8
        pdf.set_fill_color(108, 163, 184)
        fractions = (0.42, 0.7, 0.55, 0.85, 0.65, 0.94)
        if "tabela" in slot["name"].lower():
            pdf.rect(x + 5, body_y, width - 10, 5, style="F")
            pdf.set_draw_color(167, 196, 207)
            for row in range(1, 7):
                position = body_y + chart_height * row / 7
                pdf.line(x + 5, position, x + width - 5, position)
            for column in (0, 0.6, 1):
                position = x + 5 + (width - 10) * column
                pdf.line(position, body_y, position, body_y + chart_height)
        elif slot["slot_id"] in {"trend_main", "trend_segments"}:
            pdf.set_draw_color(108, 163, 184)
            pdf.set_line_width(0.8)
            points = [(x + 8 + index * (width - 16) / 5, body_y + chart_height * (1 - fraction))
                      for index, fraction in enumerate(fractions)]
            for start, end in zip(points, points[1:]):
                pdf.line(*start, *end)
            pdf.set_line_width(0.2)
        else:
            for index, fraction in enumerate(fractions):
                bar_height = chart_height * fraction
                pdf.rect(x + 8 + index * (width - 16) / 6, body_y + chart_height - bar_height,
                         bar_width, bar_height, style="F")
    elif not visual:
        pdf.set_fill_color(222, 214, 196)
        for index in range(max(0, int((y + height - body_y - 5) / 5))):
            pdf.rect(x + 3, body_y + index * 5, (width - 6) * (0.68 if index % 4 == 3 else 1), 0.6, style="F")


def create_preview(output):
    templates = [template for template in json.loads(CATALOG.read_text(encoding="utf-8"))
                 if template.get("builtin") is True]
    pdf = FPDF(format="A4", unit="mm")
    pdf.core_fonts_encoding = "cp1252"
    pdf.set_auto_page_break(False)
    pdf.set_title("Novos templates de relatório — prévias de estrutura")
    pdf.set_author("Report Generator")
    for template in templates:
        for page in range(1, template["page_count"] + 1):
            pdf.add_page()
            pdf.set_fill_color(28, 51, 69)
            pdf.rect(10, 10, 2, 12, style="F")
            pdf.set_text_color(28, 51, 69)
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_xy(16, 10)
            pdf.cell(184, 6, template["name"])
            pdf.set_text_color(100, 116, 127)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_xy(16, 18)
            pdf.cell(184, 4, f"PRÉVIA DE ESTRUTURA · Página {page} de {template['page_count']} · A4")
            for slot in template["slots"]:
                if slot["page"] == page:
                    draw_slot(pdf, slot)
            pdf.set_font("Helvetica", "", 6)
            pdf.set_text_color(100, 116, 127)
            pdf.set_xy(10, 289)
            pdf.cell(190, 4, "Esquema de posições. Barras e linhas são ilustrativas; nenhum dado de relatório foi utilizado.")
    output.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(output)
    return len(templates), len(pdf.pages)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("workspace/layout-templates-preview.pdf"))
    args = parser.parse_args()
    designs, pages = create_preview(args.output)
    print(f"Created {args.output}: {designs} layouts, {pages} pages")
