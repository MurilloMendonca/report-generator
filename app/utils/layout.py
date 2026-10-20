PAGE_WIDTH_MM = 210
PAGE_HEIGHT_MM = 297
CONTENT_TOP_MM = 30
CONTENT_BOTTOM_MM = 285
DEFAULT_MARGIN_MM = 10


def normalize_template(template: list) -> list:
    """Keep layout items inside A4 pages and add an explicit 1-based page field."""
    normalized = []
    for raw_item in template or []:
        if "id" not in raw_item:
            continue

        item = raw_item.copy()
        page = int(item.get("page") or 1)
        y = int(round(item.get("y", CONTENT_TOP_MM)))
        h = min(
            max(10, int(round(item.get("h", 40)))),
            CONTENT_BOTTOM_MM - CONTENT_TOP_MM
        )

        if y >= PAGE_HEIGHT_MM:
            page += y // PAGE_HEIGHT_MM
            y = y % PAGE_HEIGHT_MM

        if y < CONTENT_TOP_MM:
            y = CONTENT_TOP_MM

        while y + h > CONTENT_BOTTOM_MM:
            page += 1
            y = CONTENT_TOP_MM

        item["page"] = page
        width = max(10, min(PAGE_WIDTH_MM, int(round(item.get("w", PAGE_WIDTH_MM - 2 * DEFAULT_MARGIN_MM)))))
        item["x"] = max(0, min(PAGE_WIDTH_MM - width, int(round(item.get("x", DEFAULT_MARGIN_MM)))))
        item["y"] = y
        item["w"] = width
        item["h"] = h
        normalized.append(item)

    return normalized


def fallback_template(artifacts: list, items_per_page: int = 5) -> list:
    """Build a predictable multi-page vertical layout when the LLM returns no usable template."""
    if not artifacts:
        return []

    slot_gap = 5
    usable_height = CONTENT_BOTTOM_MM - CONTENT_TOP_MM
    slot_height = (usable_height - slot_gap * (items_per_page - 1)) // items_per_page

    template = []
    for index, artifact in enumerate(artifacts):
        position = index % items_per_page
        page = index // items_per_page + 1
        template.append({
            "id": artifact["id"],
            "page": page,
            "x": DEFAULT_MARGIN_MM,
            "y": CONTENT_TOP_MM + position * (slot_height + slot_gap),
            "w": PAGE_WIDTH_MM - 2 * DEFAULT_MARGIN_MM,
            "h": slot_height,
        })

    return template
