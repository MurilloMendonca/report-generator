"""Page assignments shared by the planner and deterministic A4 layout builder."""

MAX_REPORT_PAGES = 50
MAX_COMPONENTS_PER_PAGE = 6


def paginate_artifacts(artifacts: list, page_count: int | None = None) -> list:
    """Validate planned pages, retaining support for older unpaginated plans."""
    visible = [dict(item) for item in artifacts if item.get("type") != "metric"]
    if not visible:
        raise ValueError("The report must contain visible components.")
    assigned = [item.get("page") is not None for item in visible]
    if not any(assigned):
        if page_count is None:
            return visible
        if len(visible) < page_count:
            raise ValueError(f"Plan at least one visible component for each of the {page_count} pages.")
        for index, item in enumerate(visible):
            item["page"] = min(page_count, index * page_count // len(visible) + 1)
    elif not all(assigned):
        raise ValueError("Assign a page to every visible component.")

    pages = {}
    for item in visible:
        page = item["page"]
        if type(page) is not int or not 1 <= page <= (page_count or MAX_REPORT_PAGES):
            raise ValueError(f"Component {item.get('id')} has an invalid page; use integers from 1 to {page_count or MAX_REPORT_PAGES}.")
        pages[page] = pages.get(page, 0) + 1
    expected = page_count or max(pages)
    if set(pages) != set(range(1, expected + 1)):
        raise ValueError(f"Use every page from 1 to {expected}, with no empty pages.")
    if max(pages.values()) > MAX_COMPONENTS_PER_PAGE:
        raise ValueError(f"Use at most {MAX_COMPONENTS_PER_PAGE} visible components per page; combine related findings or use more pages when length is automatic.")
    for item in visible:
        if item.get("id") == "kpi_resumo" and item["page"] != 1:
            raise ValueError("Place kpi_resumo on page 1.")
        if item.get("id") == "kpi_conclusao" and item["page"] != expected:
            raise ValueError(f"Place kpi_conclusao on the last page ({expected}).")
    return visible


def validate_layout_pages(template: list, page_count: int | None = None) -> None:
    pages = {item.get("page", 1) for item in template}
    if not pages or any(type(page) is not int or not 1 <= page <= MAX_REPORT_PAGES for page in pages):
        raise ValueError("Use page numbers from 1 to 50.")
    if page_count is not None and pages != set(range(1, page_count + 1)):
        raise ValueError(f"O layout deve conter conteúdo em todas as {page_count} páginas solicitadas.")
