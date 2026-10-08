import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

import models
from database import get_db
from routers.auth import clean_phone
from security import get_current_admin
from utils import FILE, UPLOAD_DIR, form_body, get_or_404, json_body, paginate, public_url, read_payload, serialize

router = APIRouter()
admin_only = [Depends(get_current_admin)]

_PAGE_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")
APPLICATION_STATUSES = ("pending", "approved", "rejected")


# --- Editable content of static storefront pages ("home-lab", "wholesaler", ...) ---
def _check_key(page: str) -> str:
    if not _PAGE_KEY.match(page):
        raise HTTPException(status_code=422, detail="Invalid page key")
    return page


@router.get("/page-content/{page}")
def get_page_content(page: str, db: Session = Depends(get_db)):
    """The page's saved blocks, or an empty object (the frontend then shows its built-in defaults)."""
    row = db.query(models.PageContent).filter(models.PageContent.page == _check_key(page)).first()
    return {"page": page, "content": (row.content if row else None) or {}, "updated_at": row.updated_at if row else None}


@router.post("/page-content/{page}", dependencies=admin_only,
             openapi_extra=json_body({"content": {"banner_title": "...", "steps": [{"title": "...", "text": "..."}]}}))
@router.put("/page-content/{page}", dependencies=admin_only,
            openapi_extra=json_body({"content": {"banner_title": "...", "steps": [{"title": "...", "text": "..."}]}}))
async def save_page_content(page: str, request: Request, db: Session = Depends(get_db)):
    """Replace the page's content. Body: {"content": {...}} (or the content object itself)."""
    payload = await read_payload(request)
    content = payload.get("content") if isinstance(payload.get("content"), dict) else payload
    row = db.query(models.PageContent).filter(models.PageContent.page == _check_key(page)).first() \
        or models.PageContent(page=page)
    row.content = content
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "message": "Page content saved", "page": page, "content": row.content}


@router.post("/upload-image", dependencies=admin_only, status_code=201,
             openapi_extra=form_body({"image": FILE}, required=["image"]))
async def upload_image(request: Request):
    """Store an image for page content (e.g. a banner) and return its /storage path."""
    payload = await read_payload(request)
    path = payload.get("image")
    if not isinstance(path, str) or not path.startswith("images/"):
        raise HTTPException(status_code=422, detail="Choose an image file")
    if not path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        os.remove(os.path.join(UPLOAD_DIR, path))  # read_payload already saved it
        raise HTTPException(status_code=422, detail="Only PNG, JPG, WEBP or GIF images are allowed")
    return {"success": True, "path": path, "url": public_url(request, path)}


# --- Wholesale account / quote requests ---
APPLICATION_EXAMPLE = {"request_type": "account", "business_name": "City Pharmacy", "owner_name": "Rahim",
                       "phone": "017XXXXXXXX", "email": "", "address": "Dhaka", "trade_license": "TL-123",
                       "message": "Need monthly supply"}


@router.post("/wholesale-applications", status_code=201, openapi_extra=json_body(APPLICATION_EXAMPLE))
async def create_wholesale_application(request: Request, db: Session = Depends(get_db)):
    payload = await read_payload(request)
    errors = {}
    if not str(payload.get("owner_name") or "").strip():
        errors["owner_name"] = ["Name is required."]
    phone = clean_phone(payload.get("phone"))
    if len(phone) != 11:
        errors["phone"] = ["Enter a valid 11-digit mobile number."]
    if errors:
        raise HTTPException(status_code=422, detail=next(iter(errors.values()))[0])
    application = models.WholesaleApplication(
        request_type="quote" if payload.get("request_type") == "quote" else "account",
        business_name=str(payload.get("business_name") or "").strip() or None,
        owner_name=str(payload["owner_name"]).strip(),
        phone=phone,
        email=str(payload.get("email") or "").strip() or None,
        address=str(payload.get("address") or "").strip() or None,
        trade_license=str(payload.get("trade_license") or "").strip() or None,
        message=str(payload.get("message") or "").strip() or None,
        status="pending",
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return {"success": True, "message": "Request received", "id": application.id}


@router.get("/wholesale-applications", dependencies=admin_only)
def list_wholesale_applications(status: Optional[str] = None, page: int = 1, per_page: int = 20,
                                db: Session = Depends(get_db)):
    query = db.query(models.WholesaleApplication)
    if status and status != "all":
        query = query.filter(models.WholesaleApplication.status == status)
    return paginate(query.order_by(models.WholesaleApplication.id.desc()), page, per_page)


@router.post("/wholesale-applications/{id}", dependencies=admin_only,
             openapi_extra=json_body({"status": "approved"}))
@router.put("/wholesale-applications/{id}", dependencies=admin_only,
            openapi_extra=json_body({"status": "approved"}))
async def update_wholesale_application(id: int, request: Request, db: Session = Depends(get_db)):
    application = get_or_404(db, models.WholesaleApplication, id, "Request")
    payload = await read_payload(request)
    if "status" in payload:
        if payload["status"] not in APPLICATION_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(APPLICATION_STATUSES)}")
        application.status = payload["status"]
    db.commit()
    db.refresh(application)
    return {"success": True, "message": f"Request {id} updated", "data": serialize(application)}


@router.delete("/wholesale-applications/{id}", dependencies=admin_only)
def delete_wholesale_application(id: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.WholesaleApplication, id, "Request"))
    db.commit()
    return {"success": True, "message": f"Request {id} deleted"}
