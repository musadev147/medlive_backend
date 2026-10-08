import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

import models
from database import get_db
from security import get_current_admin, get_optional_user
from utils import FILE, apply_payload, form_body, get_or_404, is_admin, read_payload, register_crud, serialize

router = APIRouter()
admin_only = [Depends(get_current_admin)]

SECTIONS = ("trending", "packages", "most_booked")

LAB_TEST_FIELDS = {
    "name": "string", "description": "string", "icon": "string", "original_price": "number",
    "discounted_price": "number", "discount": "string", "report_time": "string", "booking_count": "string",
    "rating": "number", "image": FILE, "sort_order": "integer", "is_active": {"type": "integer", "enum": [0, 1]},
    "sections": {"type": "string", "description": f"Home Lab rows, e.g. [\"trending\"]. One of {', '.join(SECTIONS)}"},
    "category_ids": {"type": "string", "description": "Lab category ids, e.g. [1, 3]"},
    "labs": {"type": "string", "description": 'JSON text, e.g. [{"name": "Popular", "original_price": 500, '
                                              '"discounted_price": 400, "rating": 4.5, "is_recommended": 1}]. '
                                              "The dashboard sends labs[0][name], labs[0][logo], ... instead."},
}
LAB_CATEGORY_FIELDS = {"name": "string", "icon": "string", "image": FILE,
                       "type": {"type": "string", "enum": ["organ", "concern"]}, "sort_order": "integer",
                       "is_active": {"type": "integer", "enum": [0, 1]}}
LAB_PARTNER_FIELDS = {"name": "string", "icon": "string", "logo": FILE, "sort_order": "integer",
                      "is_active": {"type": "integer", "enum": [0, 1]}}
_NUMBER_FIELDS = ("original_price", "discounted_price", "rating")


def _as_list(value) -> list:
    """Accept a list, a JSON list, a comma-separated string or a single value."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [v for v in value.split(",")]
    if isinstance(value, dict):
        value = list(value.values())
    return value if isinstance(value, list) else [value]


def _clean_labs(labs) -> list:
    cleaned = []
    for index, lab in enumerate(_as_list(labs), start=1):
        if not isinstance(lab, dict) or not str(lab.get("name") or "").strip():
            continue
        lab = dict(lab)
        existing_logo = lab.pop("existing_logo", None)
        if not lab.get("logo") or lab.get("logo") in ("null", "undefined"):
            lab["logo"] = existing_logo or None  # keep the old logo when no new file was chosen
        for field in _NUMBER_FIELDS:
            try:
                lab[field] = float(lab.get(field) or 0)
            except (TypeError, ValueError):
                lab[field] = 0.0
        lab["is_recommended"] = 1 if str(lab.get("is_recommended", "0")).lower() in ("1", "true") else 0
        lab["id"] = index
        cleaned.append(lab)
    return cleaned


async def _save(test: models.LabTest, request: Request, db: Session):
    payload = await read_payload(request)
    labs = payload.pop("labs", None)
    sections = payload.pop("sections", None)
    category_ids = payload.pop("category_ids", None)
    if not (test.name or payload.get("name")):
        raise HTTPException(status_code=422, detail="'name' is required")
    if payload.get("image") in ("null", "undefined"):
        payload.pop("image")
    apply_payload(test, payload)
    if labs is not None:
        test.labs = _clean_labs(labs)
    if sections is not None:
        test.sections = [s for s in (str(v).strip() for v in _as_list(sections)) if s in SECTIONS]
    if category_ids is not None:
        test.category_ids = sorted({int(v) for v in _as_list(category_ids) if str(v).strip().isdigit()})
    db.add(test)
    db.commit()
    db.refresh(test)


def _out(test: models.LabTest) -> dict:
    return {**serialize(test), "labs": test.labs or [], "sections": test.sections or [],
            "category_ids": test.category_ids or []}


@router.get("/lab-tests")
def get_lab_tests(section: Optional[str] = None, category_id: Optional[int] = None, db: Session = Depends(get_db),
                  user: Optional[models.User] = Depends(get_optional_user)):
    """Public list shows active tests only. Filter by Home Lab `section` or `category_id`."""
    query = db.query(models.LabTest)
    if not is_admin(user):
        query = query.filter(func.coalesce(models.LabTest.is_active, 1) == 1)
    tests = [_out(t) for t in query.order_by(models.LabTest.sort_order, models.LabTest.id).all()]
    if section:
        tests = [t for t in tests if section in t["sections"]]
    if category_id:
        tests = [t for t in tests if category_id in t["category_ids"]]
    return tests


@router.get("/lab-tests/{id}")
def get_lab_test(id: int, db: Session = Depends(get_db)):
    return _out(get_or_404(db, models.LabTest, id, "Lab test"))


@router.post("/lab-tests", dependencies=admin_only, status_code=201,
             openapi_extra=form_body(LAB_TEST_FIELDS, required=["name"]))
async def create_lab_test(request: Request, db: Session = Depends(get_db)):
    test = models.LabTest()
    await _save(test, request, db)
    return {"success": True, "message": "Lab test created successfully", "lab_test_id": test.id, "data": _out(test)}


@router.post("/lab-tests/{id}", dependencies=admin_only, openapi_extra=form_body(LAB_TEST_FIELDS))
@router.put("/lab-tests/{id}", dependencies=admin_only, openapi_extra=form_body(LAB_TEST_FIELDS))
async def update_lab_test(id: int, request: Request, db: Session = Depends(get_db)):
    test = get_or_404(db, models.LabTest, id, "Lab test")
    await _save(test, request, db)
    return {"success": True, "message": f"Lab test {id} updated", "data": _out(test)}


@router.delete("/lab-tests/{id}", dependencies=admin_only)
def delete_lab_test(id: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.LabTest, id, "Lab test"))
    db.commit()
    return {"success": True, "message": f"Lab test {id} deleted"}


# --- Home Lab page: categories (vital organs / health concerns) and partner labs ---
def _only_active(model):
    return lambda q: q.filter(func.coalesce(model.is_active, 1) == 1)


def _check_category_type(obj, payload, request, db):
    if obj.type not in ("organ", "concern"):
        raise HTTPException(status_code=422, detail="type must be 'organ' or 'concern'")
    if not str(obj.name or "").strip():
        raise HTTPException(status_code=422, detail="'name' is required")


def _check_name(obj, payload, request, db):
    if not str(obj.name or "").strip():
        raise HTTPException(status_code=422, detail="'name' is required")


register_crud(router, models.LabCategory, label="Lab category", list_path="/lab-categories",
              create_path="/lab-categories", update_paths=["/lab-categories/{id}"],
              delete_paths=["/lab-categories/{id}"], public_filter=_only_active(models.LabCategory),
              order_by=[models.LabCategory.sort_order, models.LabCategory.id], before_save=_check_category_type,
              create_body=form_body(LAB_CATEGORY_FIELDS, required=["name", "type"]),
              update_body=form_body(LAB_CATEGORY_FIELDS))
register_crud(router, models.LabPartner, label="Lab partner", list_path="/lab-partners",
              create_path="/lab-partners", update_paths=["/lab-partners/{id}"],
              delete_paths=["/lab-partners/{id}"], public_filter=_only_active(models.LabPartner),
              order_by=[models.LabPartner.sort_order, models.LabPartner.id], before_save=_check_name,
              create_body=form_body(LAB_PARTNER_FIELDS, required=["name"]),
              update_body=form_body(LAB_PARTNER_FIELDS))
