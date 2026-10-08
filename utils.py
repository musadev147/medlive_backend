"""Helpers shared by the routers: request parsing, file uploads, model <-> dict, generic CRUD."""
import json
import math
import os
import re
import uuid
from typing import Any, Callable, Iterable, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import JSON, Boolean, DateTime, Float, Integer
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

import models
from database import get_db
from security import get_current_admin, get_optional_user

# Uploaded files are stored here and served at /storage/<path> (the frontend's `imageUrl`),
# so the database keeps Laravel-style relative paths such as "images/<name>.png".
UPLOAD_DIR = os.getenv("UPLOAD_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALWAYS_PROTECTED = {"id", "extra", "created_at", "updated_at", "_method"}
_TRUE = {"1", "true", "yes", "on"}


# ---------- request parsing ----------

def _listify(node):
    """Turn dicts whose keys are all indexes ("0", "1", ...) into lists."""
    if isinstance(node, dict):
        node = {k: _listify(v) for k, v in node.items()}
        if node and all(k.isdigit() for k in node):
            return [node[k] for k in sorted(node, key=int)]
    return node


def parse_nested_form(items: Iterable[tuple[str, Any]]) -> dict:
    """Parse PHP-style form keys (`colors[0][sizes][1][size]`, `images[]`) into nested data."""
    root: dict = {}
    for key, value in items:
        match = re.match(r"^([^\[]+)((?:\[[^\]]*\])*)$", key)
        if not match:
            root[key] = value
            continue
        path = [match.group(1)] + re.findall(r"\[([^\]]*)\]", match.group(2))
        node = root
        for i, part in enumerate(path):
            if part == "":
                part = str(len(node))
            if i == len(path) - 1:
                if part in node and i == 0:
                    # Same plain key repeated (e.g. several `images` parts).
                    prev = node[part]
                    node[part] = (prev if isinstance(prev, list) else [prev]) + [value]
                else:
                    node[part] = value
            else:
                child = node.get(part)
                if not isinstance(child, dict):
                    child = {}
                    node[part] = child
                node = child
    return _listify(root)


def save_upload(file: UploadFile, folder: str = "images") -> str:
    """Save an uploaded file and return its path relative to /storage."""
    ext = os.path.splitext(file.filename or "")[1].lower()[:10]
    os.makedirs(os.path.join(UPLOAD_DIR, folder), exist_ok=True)
    relative = f"{folder}/{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, relative), "wb") as out:
        out.write(file.file.read())
    return relative


def public_url(request: Request, relative: Optional[str]) -> Optional[str]:
    """Absolute URL for a stored file (for the few screens that use the value as-is)."""
    if not relative or str(relative).startswith(("http://", "https://")):
        return relative
    return f"{str(request.base_url).rstrip('/')}/storage/{str(relative).lstrip('/')}"


def _store_files(node):
    if isinstance(node, UploadFile):
        return save_upload(node) if node.filename else None
    if isinstance(node, dict):
        stored = {k: _store_files(v) for k, v in node.items()}
        return {k: v for k, v in stored.items() if v is not None}
    if isinstance(node, list):
        return [v for v in (_store_files(v) for v in node) if v is not None]
    return node


async def read_payload(request: Request) -> dict:
    """Read a JSON or multipart/urlencoded body. Uploaded files are saved and replaced by their
    relative path; empty file inputs are dropped so updates keep the existing file."""
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        data = _store_files(parse_nested_form(form.multi_items()))
    else:
        body = await request.body()
        if not body:
            return {}
        try:
            data = json.loads(body)
        except ValueError:
            raise HTTPException(status_code=400, detail="Body must be valid JSON")
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")
    data.pop("_method", None)  # Laravel method spoofing (POST + _method=PUT)
    return data


# ---------- OpenAPI bodies ----------
# Routes that read the raw Request have no body schema, so Swagger would show no
# input fields. These build the `openapi_extra` that documents the body instead.

FILE = {"type": "string", "format": "binary"}
FILES = {"type": "array", "items": FILE}


def form_body(fields: dict, required: Iterable[str] = ()) -> dict:
    """multipart/form-data body; values are OpenAPI schemas or a plain type name."""
    properties = {k: ({"type": v} if isinstance(v, str) else v) for k, v in fields.items()}
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = list(required)
    return {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": schema}}}}


def json_body(example: Optional[dict] = None, required: bool = True) -> dict:
    """Free-form JSON object body, with an example Swagger pre-fills."""
    media = {"schema": {"type": "object", "additionalProperties": True}}
    if example is not None:
        media["example"] = example
    return {"requestBody": {"required": required, "content": {"application/json": media}}}


# ---------- model <-> dict ----------

def _coerce(column, value):
    col_type = column.type
    if isinstance(col_type, JSON):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return value
        return value
    if value is None:
        return None
    if isinstance(col_type, Boolean):
        return value if isinstance(value, bool) else str(value).strip().lower() in _TRUE
    if isinstance(col_type, (Integer, Float)):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if text in ("", "null", "undefined"):
                return None
            if text in ("true", "false"):
                return int(text == "true")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"'{column.name}' must be a number")
        return int(number) if isinstance(col_type, Integer) else number
    if isinstance(col_type, DateTime):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def apply_payload(obj, payload: dict, protected: Iterable[str] = ()):
    """Copy payload fields onto a model; unknown fields are kept in `extra`."""
    columns = obj.__table__.columns
    blocked = ALWAYS_PROTECTED | set(protected)
    extra = dict(obj.extra or {})
    for key, value in payload.items():
        if key in blocked:
            continue
        if key in columns:
            setattr(obj, key, _coerce(columns[key], value))
        else:
            extra[key] = value
    obj.extra = extra or None
    return obj


def serialize(obj, hide: Iterable[str] = ()) -> Optional[dict]:
    """Model -> dict. Booleans become 1/0, matching what the Laravel API returned."""
    if obj is None:
        return None
    hidden = set(hide) | {"extra"}
    data = dict(obj.extra or {})
    for column in obj.__table__.columns:
        if column.name not in hidden:
            value = getattr(obj, column.name)
            data[column.name] = int(value) if isinstance(value, bool) else value
    return data


def get_or_404(db: Session, model, id: int, label: Optional[str] = None):
    obj = db.query(model).filter(model.id == id).first()
    if not obj:
        raise HTTPException(status_code=404, detail=f"{label or model.__name__} not found")
    return obj


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w]+", "-", str(text or "").strip().lower(), flags=re.UNICODE).strip("-_")
    return slug or "item"


def unique_slug(db: Session, model, text: str, exclude_id: Optional[int] = None) -> str:
    base = slugify(text)
    slug, n = base, 2
    while True:
        query = db.query(model).filter(model.slug == slug)
        if exclude_id is not None:
            query = query.filter(model.id != exclude_id)
        if not query.first():
            return slug
        slug, n = f"{base}-{n}", n + 1


def paginate(query, page: int, per_page: int, item: Callable = serialize) -> dict:
    """Laravel-style paginator: {data, current_page, last_page, per_page, total, ...}."""
    page = max(1, page)
    per_page = max(1, min(per_page, 500))
    total = query.order_by(None).count()
    items = [item(o) for o in query.offset((page - 1) * per_page).limit(per_page).all()]
    return {
        "data": items,
        "current_page": page,
        "last_page": max(1, math.ceil(total / per_page)),
        "per_page": per_page,
        "total": total,
        "from": (page - 1) * per_page + 1 if items else None,
        "to": (page - 1) * per_page + len(items) if items else None,
    }


def is_admin(user: Optional[models.User]) -> bool:
    return bool(user and user.type == "admin")


# ---------- generic CRUD ----------

def register_crud(router, model, *, label: str, list_path: str, create_path: str,
                  update_paths: Iterable[str], delete_paths: Iterable[str], list_key: Optional[str] = None,
                  read_dep=None, create_dep=get_current_admin, write_dep=get_current_admin,
                  create_body: Optional[dict] = None, update_body: Optional[dict] = None,
                  order_by=None, public_filter: Optional[Callable] = None,
                  before_save: Optional[Callable] = None, item: Optional[Callable] = None):
    """Register list/create/update/delete routes.

    - A dep of None means the route is public.
    - Update paths accept both PUT and POST (the frontend uses either, sometimes POST + `_method=PUT`).
    - `list_key` wraps the list as {list_key: [...]}, for screens that read e.g. `data.pixels`.
    - `public_filter(query)` narrows the list for non-admin callers (e.g. only active rows).
    - `before_save(obj, payload, request, db)` runs after the payload is applied.
    - `item(obj, request)` customises how each row is serialized.
    """

    def deps(dep):
        return [Depends(dep)] if dep else []

    def out(obj, request):
        return item(obj, request) if item else serialize(obj)

    @router.get(list_path, dependencies=deps(read_dep), name=f"list_{model.__tablename__}")
    def list_items(request: Request, db: Session = Depends(get_db),
                   user: Optional[models.User] = Depends(get_optional_user)):
        query = db.query(model)
        if public_filter and not is_admin(user):
            query = public_filter(query)
        rows = [out(o, request) for o in query.order_by(*(order_by or [model.id])).all()]
        return {list_key: rows} if list_key else rows

    @router.post(create_path, dependencies=deps(create_dep), name=f"create_{model.__tablename__}",
                 openapi_extra=create_body or json_body(), status_code=201)
    async def create_item(request: Request, db: Session = Depends(get_db)):
        payload = await read_payload(request)
        obj = apply_payload(model(), payload)
        if before_save:
            before_save(obj, payload, request, db)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return {"success": True, "message": f"{label} created", "id": obj.id, "data": out(obj, request)}

    for update_path in update_paths:
        update_kwargs = dict(dependencies=deps(write_dep), name=f"update_{model.__tablename__}",
                             openapi_extra=update_body or create_body or json_body())

        # One route per method so each gets its own OpenAPI operation id.
        @router.put(update_path, **update_kwargs)
        @router.post(update_path, **update_kwargs)
        async def update_item(id: int, request: Request, db: Session = Depends(get_db)):
            obj = get_or_404(db, model, id, label)
            payload = await read_payload(request)
            apply_payload(obj, payload)
            if before_save:
                before_save(obj, payload, request, db)
            db.commit()
            db.refresh(obj)
            return {"success": True, "message": f"{label} {id} updated", "data": out(obj, request)}

    for delete_path in delete_paths:
        @router.delete(delete_path, dependencies=deps(write_dep),
                       name=f"delete_{model.__tablename__}")
        def delete_item(id: int, db: Session = Depends(get_db)):
            db.delete(get_or_404(db, model, id, label))
            db.commit()
            return {"success": True, "message": f"{label} {id} deleted"}
