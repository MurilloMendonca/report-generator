"""Owner link management and public GET-only report access."""
from fastapi import APIRouter, HTTPException, Request

from app.models.sharing import CreateShareLink
from app.pages import render_page
from app.security import current_user
from app.services import sharing_service
from app.services.report_files import report_file_response
from app.services.web_helpers import file_response, get_session_file, require_session
from app.utils.exploration_charts import chart_path
from app.utils.report_paths import ReportPaths

router = APIRouter()


@router.get("/api/sessions/{job_id}/share-links")
def list_report_share_links(request: Request, job_id: str):
    require_session(job_id, request)
    return {"links": sharing_service.list_share_links(job_id, current_user(request).id)}


@router.post("/api/sessions/{job_id}/share-links", status_code=201)
def create_report_share_link(request: Request, job_id: str, payload: CreateShareLink):
    require_session(job_id, request)
    return sharing_service.create_share_link(job_id, current_user(request).id, payload.label, payload.expires_in_hours)


@router.delete("/api/sessions/{job_id}/share-links/{link_id}")
def revoke_report_share_link(request: Request, job_id: str, link_id: str):
    require_session(job_id, request)
    return sharing_service.revoke_share_link(job_id, current_user(request).id, link_id)


@router.get("/share/{token}")
def shared_report_page(token: str):
    status = 200
    try:
        sharing_service.require_shared_report(token)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        status = 404
    response = render_page("shared.html")
    response.status_code = status
    response.headers.update(sharing_service.SHARED_HEADERS)
    return response


@router.get("/api/shared-reports/{token}")
def get_shared_report(token: str):
    session, sharing = sharing_service.require_shared_report(token)
    from fastapi.responses import JSONResponse
    return JSONResponse(sharing_service.shared_report_payload(session, sharing), headers=sharing_service.SHARED_HEADERS)


@router.get("/api/shared-reports/{token}/pdf")
def shared_report_pdf(token: str):
    session, _sharing = sharing_service.require_shared_report(token)
    return report_file_response(session, session.get("pdf_path"), media_type="application/pdf", filename="relatorio.pdf", headers=sharing_service.SHARED_HEADERS)


@router.get("/api/shared-reports/{token}/files/{file_index}/{mode}")
def shared_report_file(token: str, file_index: int, mode: str, variant: str = "original"):
    from fastapi import HTTPException
    if mode not in {"preview", "download"} or variant not in {"original", "preprocessed"}:
        raise HTTPException(404)
    session, _sharing = sharing_service.require_shared_report(token)
    response = file_response(get_session_file(session, file_index), inline=mode == "preview", variant=variant, session=session)
    response.headers.update(sharing_service.SHARED_HEADERS)
    return response


@router.get("/api/shared-reports/{token}/explore/{turn_id}/charts/{chart_id}")
def shared_explorer_chart(token: str, turn_id: str, chart_id: str):
    session, _sharing = sharing_service.require_shared_report(token)
    try:
        path = chart_path(session["job_id"], turn_id, chart_id, ReportPaths.from_session(session).output)
    except ValueError:
        raise HTTPException(404, detail="Gráfico indisponível.") from None
    return report_file_response(session, path, media_type="image/png", headers=sharing_service.SHARED_HEADERS)


@router.get("/api/shared-reports/{token}/source-snapshots/{snapshot_id}/download")
def shared_source_snapshot(token: str, snapshot_id: str):
    session, _sharing = sharing_service.require_shared_report(token)
    snapshot = next((file for file in session.get("source_snapshot_history", []) if file.get("source", {}).get("snapshot_id") == snapshot_id), None)
    if snapshot is None:
        raise HTTPException(404, detail="Versão indisponível.")
    return report_file_response(session, snapshot.get("path"), media_type=snapshot.get("mime_type") or "application/json",
                                filename=snapshot.get("original_filename") or "snapshot.json", disposition="attachment", headers=sharing_service.SHARED_HEADERS)
