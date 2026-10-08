"""Prescriptions and doctor appointments."""
import hashlib
import hmac
import os
import secrets
import time
import uuid
from datetime import date as Date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

import models
from database import get_db
from routers.auth import clean_phone
from security import get_current_admin, get_optional_user
from utils import (FILE, UPLOAD_DIR, form_body, get_or_404, json_body, paginate, read_payload,
                   register_crud, serialize)

router = APIRouter()
admin_only = [Depends(get_current_admin)]


# ---------- private file storage ----------
# Prescriptions are medical records, so they are NOT stored under the public /storage folder.
PRIVATE_DIR = os.getenv("PRIVATE_UPLOAD_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                                "private_uploads")
os.makedirs(os.path.join(PRIVATE_DIR, "prescriptions"), exist_ok=True)

MAX_PRESCRIPTION_BYTES = 5 * 1024 * 1024
FILE_LINK_SECONDS = 2 * 60 * 60  # signed file links stay valid for 2 hours
_ALLOWED = {
    ".pdf": ("pdf", (b"%PDF",)),
    ".png": ("image", (b"\x89PNG",)),
    ".jpg": ("image", (b"\xff\xd8\xff",)),
    ".jpeg": ("image", (b"\xff\xd8\xff",)),
    ".webp": ("image", (b"RIFF",)),
}
_MEDIA_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp"}


def _url_secret() -> bytes:
    """Secret for signing file links: env PRESCRIPTION_URL_SECRET, else one generated and kept on disk."""
    env = os.getenv("PRESCRIPTION_URL_SECRET")
    if env:
        return env.encode()
    path = os.path.join(PRIVATE_DIR, ".url_secret")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip().encode()


def _sign(prescription_id: int, expires: int) -> str:
    return hmac.new(_url_secret(), f"{prescription_id}:{expires}".encode(), hashlib.sha256).hexdigest()


def _file_url(request: Request, row: models.Prescription) -> Optional[str]:
    if not row.file:
        return None
    expires = int(time.time()) + FILE_LINK_SECONDS
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/prescriptions/{row.id}/file?expires={expires}&signature={_sign(row.id, expires)}"


def _file_path(row: models.Prescription) -> Optional[str]:
    if not row.file:
        return None
    for root in (PRIVATE_DIR, UPLOAD_DIR):  # older uploads were stored publicly
        path = os.path.realpath(os.path.join(root, row.file))
        if path.startswith(os.path.realpath(root)) and os.path.isfile(path):
            return path
    return None


async def _read_upload(file: UploadFile) -> tuple[str, str, bytes]:
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED:
        raise HTTPException(status_code=422, detail="Only JPG, PNG, WEBP or PDF files are allowed")
    data = await file.read(MAX_PRESCRIPTION_BYTES + 1)
    if len(data) > MAX_PRESCRIPTION_BYTES:
        raise HTTPException(status_code=422, detail="File is too large (max 5 MB)")
    kind, signatures = _ALLOWED[ext]
    if not data.startswith(signatures) or (ext == ".webp" and data[8:12] != b"WEBP"):
        raise HTTPException(status_code=422, detail="The file is not a valid image or PDF")
    return ext, kind, data


# ---------- prescriptions ----------
PRESCRIPTION_STATUSES = ("pending", "contacted", "completed", "cancelled")
PRESCRIPTION_FIELDS = {"name": "string", "phone": "string", "notes": "string", "file": FILE}


def _prescription_out(row: models.Prescription, request: Request) -> dict:
    data = serialize(row)
    ext = os.path.splitext(row.file or "")[1].lower()
    data["file_type"] = row.file_type or _ALLOWED.get(ext, ("file",))[0]
    data["file_url"] = _file_url(request, row)
    data["file"] = None  # the storage path is internal
    return data


@router.post("/prescriptions", status_code=201, openapi_extra=form_body(PRESCRIPTION_FIELDS, ["name", "phone", "file"]))
async def upload_prescription(request: Request, db: Session = Depends(get_db),
                              user: Optional[models.User] = Depends(get_optional_user)):
    form = await request.form()
    name = str(form.get("name") or "").strip()
    phone = clean_phone(form.get("phone"))
    upload = form.get("file")
    errors = {}
    if not name:
        errors["name"] = ["Please enter your name."]
    if len(phone) != 11 or not phone.startswith("01"):
        errors["phone"] = ["Enter a valid 11-digit mobile number."]
    if not isinstance(upload, UploadFile) or not upload.filename:
        errors["file"] = ["Please choose a prescription file."]
    if errors:
        raise HTTPException(status_code=422, detail=next(iter(errors.values()))[0])

    ext, kind, data = await _read_upload(upload)
    relative = f"prescriptions/{uuid.uuid4().hex}{ext}"
    with open(os.path.join(PRIVATE_DIR, relative), "wb") as out:
        out.write(data)
    row = models.Prescription(name=name, phone=phone, notes=str(form.get("notes") or "").strip() or None,
                              file=relative, file_type=kind, original_name=(upload.filename or "")[:255],
                              status="pending")
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "message": "Prescription uploaded. Our pharmacist will contact you soon.", "id": row.id}


@router.get("/prescriptions", dependencies=admin_only)
def list_prescriptions(request: Request, status: Optional[str] = None, search: Optional[str] = None,
                       page: Optional[int] = None, per_page: int = 20, db: Session = Depends(get_db)):
    """Newest first. Without `page` returns a plain array; with `page` a paginator."""
    query = db.query(models.Prescription)
    if status and status != "all":
        query = query.filter(models.Prescription.status == status)
    if search and search.strip():
        like = f"%{search.strip()}%"
        query = query.filter(or_(models.Prescription.name.ilike(like), models.Prescription.phone.ilike(like)))
    query = query.order_by(models.Prescription.id.desc())
    if page is not None:
        return paginate(query, page, per_page, item=lambda r: _prescription_out(r, request))
    return [_prescription_out(r, request) for r in query.all()]


@router.get("/prescriptions/{id}/file")
def prescription_file(id: int, expires: int, signature: str, download: bool = False, db: Session = Depends(get_db)):
    """Serves the private file for links produced by the admin list (valid for a limited time)."""
    if expires < time.time() or not hmac.compare_digest(signature, _sign(id, expires)):
        raise HTTPException(status_code=403, detail="This link has expired. Reload the page.")
    row = get_or_404(db, models.Prescription, id, "Prescription")
    path = _file_path(row)
    if not path:
        raise HTTPException(status_code=404, detail="File not found")
    ext = os.path.splitext(path)[1].lower()
    filename = f"prescription-{row.id}{ext}"
    return FileResponse(path, media_type=_MEDIA_TYPES.get(ext, "application/octet-stream"), filename=filename,
                        content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/prescriptions/{id}", dependencies=admin_only,
             openapi_extra=json_body({"status": "contacted", "admin_note": "Called, will order Napa"}))
@router.put("/prescriptions/{id}", dependencies=admin_only,
            openapi_extra=json_body({"status": "contacted", "admin_note": "Called, will order Napa"}))
async def update_prescription(id: int, request: Request, db: Session = Depends(get_db)):
    row = get_or_404(db, models.Prescription, id, "Prescription")
    payload = await read_payload(request)
    if "status" in payload:
        if payload["status"] not in PRESCRIPTION_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(PRESCRIPTION_STATUSES)}")
        row.status = payload["status"]
    if "admin_note" in payload:
        row.admin_note = str(payload["admin_note"] or "").strip() or None
    db.commit()
    db.refresh(row)
    return {"success": True, "message": f"Prescription {id} updated", "data": _prescription_out(row, request)}


@router.delete("/prescriptions/{id}", dependencies=admin_only)
def delete_prescription(id: int, db: Session = Depends(get_db)):
    row = get_or_404(db, models.Prescription, id, "Prescription")
    path = _file_path(row)
    db.delete(row)
    db.commit()
    if path:
        os.remove(path)
    return {"success": True, "message": f"Prescription {id} deleted"}


# ---------- appointments ----------
APPOINTMENT_STATUSES = ("pending", "confirmed", "completed", "cancelled")
DEFAULT_TIME_SLOTS = ["10:00 AM", "10:30 AM", "11:00 AM", "11:30 AM", "12:00 PM", "04:00 PM", "04:30 PM",
                      "05:00 PM", "05:30 PM", "06:00 PM", "06:30 PM", "07:00 PM"]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def appointment_settings(db: Session) -> dict:
    """Slots, capacity and closed days, edited in the admin panel (stored as page content "appointment")."""
    row = db.query(models.PageContent).filter(models.PageContent.page == "appointment").first()
    content = (row.content if row else None) or {}
    slots = [str(s.get("time") if isinstance(s, dict) else s).strip() for s in content.get("time_slots") or []]
    slots = [s for s in slots if s] or DEFAULT_TIME_SLOTS
    try:
        capacity = max(1, int(content.get("max_per_slot") or 3))
    except (TypeError, ValueError):
        capacity = 3
    closed = {str(d.get("day") if isinstance(d, dict) else d).strip().lower() for d in content.get("closed_days") or []}
    try:
        advance_days = max(1, int(content.get("booking_days_ahead") or 30))
    except (TypeError, ValueError):
        advance_days = 30
    return {"time_slots": slots, "max_per_slot": capacity, "closed_days": sorted(closed & set(WEEKDAYS)),
            "booking_days_ahead": advance_days}


def _parse_date(value: str) -> Date:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Date must be YYYY-MM-DD")


def _slot_counts(db: Session, day: str, exclude_id: Optional[int] = None) -> dict:
    query = db.query(models.Appointment.time, func.count(models.Appointment.id)).filter(
        models.Appointment.date == day, models.Appointment.status != "cancelled")
    if exclude_id:
        query = query.filter(models.Appointment.id != exclude_id)
    return dict(query.group_by(models.Appointment.time).all())


def _check_slot(db: Session, day: str, slot: str, exclude_id: Optional[int] = None, allow_past: bool = False):
    settings = appointment_settings(db)
    booking_date = _parse_date(day)
    today = Date.today()
    if not allow_past and booking_date < today:
        raise HTTPException(status_code=422, detail="Please choose today or a later date")
    if not allow_past and (booking_date - today).days > settings["booking_days_ahead"]:
        raise HTTPException(status_code=422, detail=f"Bookings open {settings['booking_days_ahead']} days ahead")
    if WEEKDAYS[booking_date.weekday()] in settings["closed_days"]:
        raise HTTPException(status_code=422, detail="We are closed on that day. Please pick another date.")
    if slot not in settings["time_slots"]:
        raise HTTPException(status_code=422, detail="Please choose one of the available time slots")
    if _slot_counts(db, day, exclude_id).get(slot, 0) >= settings["max_per_slot"]:
        raise HTTPException(status_code=409, detail="That time slot is full. Please pick another time.")


def _new_appointment_no(db: Session) -> str:
    while True:
        number = "APT" + "".join(secrets.choice("0123456789") for _ in range(6))
        if not db.query(models.Appointment).filter(models.Appointment.appointment_no == number).first():
            return number


def _check_type(obj, payload, request, db):
    if not str(obj.name or "").strip():
        raise HTTPException(status_code=422, detail="'name' is required")
    if (obj.fee or 0) < 0:
        raise HTTPException(status_code=422, detail="Fee cannot be negative")


register_crud(router, models.AppointmentType, label="Appointment type", list_path="/appointment-types",
              create_path="/appointment-types", update_paths=["/appointment-types/{id}"],
              delete_paths=["/appointment-types/{id}"],
              public_filter=lambda q: q.filter(func.coalesce(models.AppointmentType.is_active, 1) == 1),
              order_by=[models.AppointmentType.sort_order, models.AppointmentType.id], before_save=_check_type,
              create_body=json_body({"name": "General Consultation", "icon": "🩺", "fee": 500, "duration": "15 mins",
                                     "description": "", "sort_order": 1, "is_active": 1}))


@router.get("/appointment-slots")
def appointment_slots(date: str, db: Session = Depends(get_db)):
    """Time slots for a day with how many places are left."""
    settings = appointment_settings(db)
    booking_date = _parse_date(date)
    closed = WEEKDAYS[booking_date.weekday()] in settings["closed_days"]
    counts = _slot_counts(db, date)
    now = datetime.now()
    slots = []
    for slot in settings["time_slots"]:
        remaining = max(0, settings["max_per_slot"] - counts.get(slot, 0))
        try:  # a slot that already started today can't be booked
            started = booking_date == now.date() and datetime.strptime(slot, "%I:%M %p").time() <= now.time()
        except ValueError:
            started = False
        slots.append({"time": slot, "remaining": remaining, "available": not closed and not started and remaining > 0})
    return {"date": date, "closed": closed, "slots": slots, "settings": settings}


APPOINTMENT_EXAMPLE = {"appointment_type_id": 1, "date": "2026-10-20", "time": "10:30 AM", "patient_name": "Rahim",
                       "mobile": "01712345678", "age": 35, "gender": "male", "issue": "Fever for 3 days", "notes": ""}


@router.post("/appointments", status_code=201, openapi_extra=json_body(APPOINTMENT_EXAMPLE))
async def book_appointment(request: Request, db: Session = Depends(get_db),
                           user: Optional[models.User] = Depends(get_optional_user)):
    payload = await read_payload(request)
    name = str(payload.get("patient_name") or "").strip()
    mobile = clean_phone(payload.get("mobile"))
    if not name:
        raise HTTPException(status_code=422, detail="Patient name is required")
    if len(mobile) != 11 or not mobile.startswith("01"):
        raise HTTPException(status_code=422, detail="Enter a valid 11-digit mobile number")
    try:
        age = int(payload.get("age"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Age is required")
    if not 0 < age < 130:
        raise HTTPException(status_code=422, detail="Enter a valid age")
    gender = str(payload.get("gender") or "").lower()
    if gender not in ("male", "female", "other"):
        raise HTTPException(status_code=422, detail="Please select a gender")

    type_id = payload.get("appointment_type_id") or payload.get("type")
    appointment_type = db.get(models.AppointmentType, int(type_id)) if str(type_id or "").isdigit() else None
    if not appointment_type or not appointment_type.is_active:
        raise HTTPException(status_code=422, detail="Please select an appointment type")

    day, slot = str(payload.get("date") or ""), str(payload.get("time") or "").strip()
    _check_slot(db, day, slot)

    row = models.Appointment(
        appointment_no=_new_appointment_no(db), user_id=user.id if user else None,
        appointment_type_id=appointment_type.id, type_name=appointment_type.name, fee=appointment_type.fee or 0,
        date=day, time=slot, patient_name=name, mobile=mobile, age=age, gender=gender,
        issue=str(payload.get("issue") or "").strip() or None, notes=str(payload.get("notes") or "").strip() or None,
        status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "message": "Appointment booked", "appointment_no": row.appointment_no,
            "data": serialize(row)}


@router.get("/appointments", dependencies=admin_only)
def list_appointments(status: Optional[str] = None, date: Optional[str] = None, search: Optional[str] = None,
                      page: int = 1, per_page: int = 20, db: Session = Depends(get_db)):
    query = db.query(models.Appointment)
    if status and status != "all":
        query = query.filter(models.Appointment.status == status)
    if date:
        query = query.filter(models.Appointment.date == date)
    if search and search.strip():
        like = f"%{search.strip()}%"
        query = query.filter(or_(models.Appointment.patient_name.ilike(like), models.Appointment.mobile.ilike(like),
                                 models.Appointment.appointment_no.ilike(like)))
    result = paginate(query.order_by(models.Appointment.date.desc(), models.Appointment.id.desc()), page, per_page)
    counts = dict(db.query(models.Appointment.status, func.count(models.Appointment.id))
                  .group_by(models.Appointment.status).all())
    result["counts"] = {s: counts.get(s, 0) for s in APPOINTMENT_STATUSES}
    return result


@router.post("/appointments/{id}", dependencies=admin_only,
             openapi_extra=json_body({"status": "confirmed", "admin_note": "Dr. Karim", "date": "2026-10-20",
                                      "time": "11:00 AM"}))
@router.put("/appointments/{id}", dependencies=admin_only,
            openapi_extra=json_body({"status": "confirmed", "admin_note": "Dr. Karim", "date": "2026-10-20",
                                     "time": "11:00 AM"}))
async def update_appointment(id: int, request: Request, db: Session = Depends(get_db)):
    row = get_or_404(db, models.Appointment, id, "Appointment")
    payload = await read_payload(request)
    if "status" in payload:
        if payload["status"] not in APPOINTMENT_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(APPOINTMENT_STATUSES)}")
        row.status = payload["status"]
    new_date, new_time = payload.get("date") or row.date, payload.get("time") or row.time
    if (new_date, new_time) != (row.date, row.time):
        _check_slot(db, new_date, new_time, exclude_id=row.id)  # rescheduling
        row.date, row.time = new_date, new_time
    if "admin_note" in payload:
        row.admin_note = str(payload["admin_note"] or "").strip() or None
    db.commit()
    db.refresh(row)
    return {"success": True, "message": f"Appointment {id} updated", "data": serialize(row)}


@router.delete("/appointments/{id}", dependencies=admin_only)
def delete_appointment(id: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.Appointment, id, "Appointment"))
    db.commit()
    return {"success": True, "message": f"Appointment {id} deleted"}
