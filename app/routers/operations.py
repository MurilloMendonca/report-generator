"""Usage and admin pages; account-scoped usage and role/Access-protected operations."""
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.pages import render_page
from app.security import client_key, current_user, require_admin
from app.services import admin_service, auth_service, usage_service

router = APIRouter()


class UserChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "disabled"] | None = None
    monthly_token_limit: int | None = Field(default=None, ge=0, le=10**12, strict=True)
    report_call_limit: int | None = Field(default=None, ge=0, le=10**6, strict=True)


@router.get("/usage", include_in_schema=False)
def usage_page(request: Request):
    current_user(request)
    return render_page("usage.html")


@router.get("/admin", include_in_schema=False)
def admin_page(request: Request):
    require_admin(request)
    return render_page("admin.html")


@router.get("/api/usage/reports")
def report_usage(request: Request):
    return {"reports": usage_service.recent_reports(current_user(request).id)}


@router.get("/api/admin/users")
def users(request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    require_admin(request)
    return admin_service.list_users(offset, limit)


@router.patch("/api/admin/users/{user_id}")
def update_user(user_id: uuid.UUID, body: UserChanges, request: Request):
    actor = require_admin(request)
    changes = body.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] is None:
        raise HTTPException(422, detail="Escolha um estado válido.")
    try:
        return admin_service.update_user(actor, user_id, changes, client_key(request))
    except auth_service.AuthError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message}) from None


@router.get("/api/admin/usage")
def total_usage(request: Request):
    require_admin(request)
    return usage_service.account_summary()


@router.get("/api/admin/audit")
def audit(request: Request, before: int | None = Query(None, ge=1), limit: int = Query(50, ge=1, le=100)):
    require_admin(request)
    return admin_service.audit_events(before, limit)
