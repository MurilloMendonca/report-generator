"""API documentation for signed-in users: the OpenAPI schema and a self-hosted Swagger UI page."""
from fastapi import APIRouter, Request
from fastapi.openapi.utils import get_openapi

from app.pages import render_page
from app.security import current_user

router = APIRouter()

API_DESCRIPTION = """
API do Report Generator. Autentique com o cookie de sessão do navegador (requisições que
alteram dados também enviam `X-CSRF-Token` igual ao cookie `csrf_token`) ou com um token
pessoal criado na página Conta, no cabeçalho `Authorization: Bearer rg_...`.

Ações demoradas (criar, regenerar, refinar, explorar) respondem logo com um `task_id`.
Consulte `GET /api/sessions/{job_id}/tasks/{task_id}` até o estado ser `completed`,
`failed` ou `superseded`.
"""
TAGS = (("/api/sessions", "Relatórios"), ("/api/shared-reports", "Compartilhamento"),
        ("/api/data-sources", "Fontes de dados"), ("/api/layout-templates", "Templates"),
        ("/api/component-library", "Biblioteca de componentes"), ("/api/usage", "Consumo"),
        ("/api/models", "Modelos"), ("/api/model-preferences", "Modelos"),
        ("/api/credentials", "Conta"), ("/api/auth", "Conta"), ("/api/tokens", "Conta"))
_schema: dict | None = None


def api_schema(app) -> dict:
    """OpenAPI for the JSON API only: no HTML pages and no admin routes."""
    global _schema
    if _schema is None:
        schema = get_openapi(title=app.title, version=app.version, description=API_DESCRIPTION, routes=app.routes)
        schema["paths"] = {path: item for path, item in schema["paths"].items()
                           if path.startswith("/api/") and not path.startswith("/api/admin") and path != "/api/openapi.json"}
        for path, item in schema["paths"].items():
            tag = next((name for prefix, name in TAGS if path.startswith(prefix)), "Outros")
            for operation in item.values():
                operation["tags"] = [tag]
        schema.setdefault("components", {})["securitySchemes"] = {
            "apiToken": {"type": "http", "scheme": "bearer", "description": "Token pessoal criado na página Conta (rg_...)."},
            "browserSession": {"type": "apiKey", "in": "cookie", "name": "pdf_report_session"},
        }
        schema["security"] = [{"apiToken": []}, {"browserSession": []}]
        _schema = schema
    return _schema


@router.get("/api/openapi.json", include_in_schema=False)
def openapi_schema(request: Request):
    current_user(request)
    return api_schema(request.app)


@router.get("/docs", include_in_schema=False)
def api_docs_page():
    return render_page("docs.html")
