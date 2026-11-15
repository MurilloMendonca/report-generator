"""Choose template pages and compact automatic reports without dropping content."""

from app.utils.pagination import MAX_COMPONENTS_PER_PAGE, MAX_REPORT_PAGES


def page_designs(templates: list) -> list:
    """Split accessible report templates into reusable single-page designs."""
    designs = []
    for template in templates:
        slots = template.get("slots", [])
        for page in sorted({slot["page"] for slot in slots}):
            page_slots = [slot for slot in slots if slot["page"] == page]
            designs.append({
                "id": f"{template['id']}:{page}", "template_id": template["id"],
                "name": template.get("name", template["id"]), "template_page": page,
                "text_slots": sum(slot["type"] == "text" for slot in page_slots),
                "graphic_slots": sum(slot["type"] == "graphic" for slot in page_slots),
                "slots": page_slots,
            })
    return designs


def select_page_design(items: list, designs: list, preferred_template_id=None) -> dict | None:
    texts = sum(item.get("type") == "text" for item in items)
    graphics = len(items) - texts
    candidates = [design for design in designs
                  if texts <= design["text_slots"] and graphics <= design["graphic_slots"]]
    if not candidates:
        return None

    def score(design):
        votes = sum(item.get("page_layout_id") == design["id"] for item in items)
        unused = design["text_slots"] + design["graphic_slots"] - len(items)
        return (-votes, unused, design["template_id"] != preferred_template_id)

    return min(candidates, key=score)


def compact_report_pages(items: list, designs: list, preferred_template_id=None) -> list:
    """Pack consecutive content into the fewest template pages, then balance density.

    The AI's page boundaries guide ordering. No content is deleted or duplicated;
    user-specified page counts bypass this step. Unsupported template types use a
    bounded grid instead of making otherwise packable pages fall back to grids.
    """
    ordered = sorted(enumerate(items), key=lambda pair: (
        0 if pair[1].get("id") == "kpi_resumo" else 2 if pair[1].get("id") == "kpi_conclusao" else 1,
        pair[1].get("page", 1), pair[0],
    ))
    content = [dict(item) for _, item in ordered]
    count = len(content)
    best = {count: ((0, 0, 0), [])}
    for start in range(count - 1, -1, -1):
        options = []
        for end in range(start + 1, min(count, start + MAX_COMPONENTS_PER_PAGE) + 1):
            group = content[start:end]
            design = select_page_design(group, designs, preferred_template_id)
            if design is None and designs:
                kinds = {"text" if item.get("type") == "text" else "graphic" for item in group}
                if all(any(design[f"{kind}_slots"] for design in designs) for kind in kinds):
                    continue
            if end not in best:
                continue
            tail_cost, tail_groups = best[end]
            unused = design["text_slots"] + design["graphic_slots"] - len(group) if design else 0
            # First minimize pages, then avoid sparse trailing pages and wasted slots.
            cost = (tail_cost[0] + 1, tail_cost[1] + (MAX_COMPONENTS_PER_PAGE - len(group)) ** 2,
                    tail_cost[2] + unused)
            options.append((cost, [(group, design)] + tail_groups))
        best[start] = min(options, key=lambda option: option[0])

    if best[0][0][0] > MAX_REPORT_PAGES and designs:
        return compact_report_pages(items, [])

    result = []
    for page, (group, design) in enumerate(best[0][1], 1):
        for item in group:
            item["page"] = page
            if design:
                item["page_layout_id"] = design["id"]
            else:
                item.pop("page_layout_id", None)
            result.append(item)
    return result
