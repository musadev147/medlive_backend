import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

import models
from database import get_db
from security import get_current_admin
from utils import (FILE, FILES, apply_payload, form_body, get_or_404, json_body, paginate, read_payload, serialize,
                   unique_slug)

router = APIRouter()
admin_only = [Depends(get_current_admin)]

_JSON_TEXT = "JSON text, e.g. %s. The frontend sends the bracket form instead (%s)."
PRODUCT_FIELDS = {
    "name": "string",
    "slug": {"type": "string", "description": "Optional; generated from the name when empty"},
    "price": "number",
    "discount_price": "number",
    "category_id": "integer",
    "clothing": {"type": "integer", "enum": [0, 1]},
    "images[]": FILES,
    "colors": {"type": "string", "description": _JSON_TEXT % (
        '[{"color": "Red", "sizes": [{"size": "XL"}]}]', "colors[0][color], colors[0][image], colors[0][sizes][0][size]")},
    "singleProductSizes": {"type": "string", "description": _JSON_TEXT % ('[{"size": "M"}]', "singleProductSizes[0][size]")},
    "bulk_discounts": {"type": "string", "description": _JSON_TEXT % (
        '[{"title": "Buy 3", "offer_quantity": 3, "discount_percentage": 10}]', "bulk_discounts[0][title], ...")},
    "bumps": {"type": "string", "description": _JSON_TEXT % (
        '[{"bump_price": 50, "title": "Gift", "description": "..."}]', "bumps[0][bump_price], bumps[0][image], ...")},
    "homepage": {"type": "string", "description": _JSON_TEXT % (
        '{"headline": "...", "paragraph": "...", "description": "..."}', "homepage[headline], ...")},
}
CATEGORY_FIELDS = {
    "name": "string",
    "image": FILE,
    "sort_order": "integer",
    "product_limit": "integer",
    "parent_id": {"type": "integer", "description": "Parent category id (from GET /category); leave empty or 0 "
                                                    "for a top-level category"},
    "show_on_homepage": {"type": "integer", "enum": [0, 1]},
}
COLOR_FIELDS = {"product_id": "integer", "color": "string", "image": FILE}

# Product keys handled by hand rather than copied onto columns.
_PRODUCT_SPECIAL = {"images", "existing_images", "colors", "singleProductSizes", "single_product_sizes",
                    "bulk_discounts", "bumps", "slug"}


# ---------- product helpers ----------

def _as_list(value) -> list:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [value]
    if isinstance(value, dict):
        return [value]
    return list(value)


def _with_ids(items: list, previous: Optional[list] = None) -> list:
    """Give each sub-item (sizes, discounts, bumps) a stable id the dashboard can send back."""
    used = [int(i["id"]) for i in (previous or []) + items if isinstance(i, dict) and str(i.get("id", "")).isdigit()]
    next_id = max(used, default=0) + 1
    result = []
    for item in items:
        item = dict(item)
        if str(item.get("id", "")).isdigit():
            item["id"] = int(item["id"])
        else:
            item["id"] = next_id
            next_id += 1
        result.append(item)
    return result


def _image_list(images) -> list:
    out = []
    for image in images or []:
        path = image.get("image") if isinstance(image, dict) else image
        if path:
            out.append({"image": path})
    return out


def serialize_products(products: list, db: Session) -> list:
    """Products with their colour rows (and each colour's sizes) attached, as the frontend expects."""
    ids = [p.id for p in products]
    colors = db.query(models.Color).filter(models.Color.product_id.in_(ids)).order_by(models.Color.id).all() \
        if ids else []
    color_ids = [c.id for c in colors]
    sizes = db.query(models.Size).filter(models.Size.color_id.in_(color_ids)).order_by(models.Size.id).all() \
        if color_ids else []
    sizes_by_color: dict = {}
    for size in sizes:
        sizes_by_color.setdefault(size.color_id, []).append(serialize(size))
    colors_by_product: dict = {}
    for color in colors:
        colors_by_product.setdefault(color.product_id, []).append(
            {**serialize(color), "sizes": sizes_by_color.get(color.id, [])})

    result = []
    for product in products:
        data = serialize(product)
        data["images"] = _image_list(product.images)
        data["single_product_sizes"] = product.single_product_sizes or []
        data["bulk_discounts"] = product.bulk_discounts or []
        data["bumps"] = product.bumps or []
        data["colors"] = colors_by_product.get(product.id, [])
        result.append(data)
    return result


def serialize_product(product: models.Product, db: Session) -> dict:
    return serialize_products([product], db)[0]


def _sync_colors(product: models.Product, colors: list, db: Session):
    """Replace the product's colour/size rows with the submitted list (rows sent back with an id are kept)."""
    existing = {c.id: c for c in db.query(models.Color).filter(models.Color.product_id == product.id)}
    kept = set()
    for item in colors:
        if not isinstance(item, dict) or not str(item.get("color") or "").strip():
            continue
        color_id = int(item["id"]) if str(item.get("id", "")).isdigit() else None
        row = existing.get(color_id) or models.Color(product_id=product.id)
        row.color = str(item["color"]).strip()
        new_image = item.get("image") or item.get("existing_image")
        if new_image:
            row.image = new_image
        db.add(row)
        db.flush()
        kept.add(row.id)

        old_sizes = {s.id: s for s in db.query(models.Size).filter(models.Size.color_id == row.id)}
        kept_sizes = set()
        for size_item in _as_list(item.get("sizes")):
            if not isinstance(size_item, dict) or not str(size_item.get("size") or "").strip():
                continue
            size_id = int(size_item["id"]) if str(size_item.get("id", "")).isdigit() else None
            size_row = old_sizes.get(size_id) or models.Size(product_id=product.id, color_id=row.id)
            size_row.size = str(size_item["size"]).strip()
            db.add(size_row)
            db.flush()
            kept_sizes.add(size_row.id)
        for size_id, size_row in old_sizes.items():
            if size_id not in kept_sizes:
                db.delete(size_row)

    for color_id, row in existing.items():
        if color_id not in kept:
            db.query(models.Size).filter(models.Size.color_id == color_id).delete()
            db.delete(row)


def _apply_product(product: models.Product, payload: dict, db: Session):
    apply_payload(product, {k: v for k, v in payload.items() if k not in _PRODUCT_SPECIAL})
    if product.category_id is not None and not db.get(models.Category, product.category_id):
        raise HTTPException(status_code=422, detail="category_id does not exist")

    if str(payload.get("slug") or "").strip() not in ("", "null", "undefined"):
        product.slug = unique_slug(db, models.Product, payload["slug"], exclude_id=product.id)
    elif not product.slug:
        product.slug = unique_slug(db, models.Product, product.name, exclude_id=product.id)

    if "images" in payload or "existing_images" in payload:
        product.images = _image_list(_as_list(payload.get("existing_images")) + _as_list(payload.get("images")))

    sizes_key = "singleProductSizes" if "singleProductSizes" in payload else "single_product_sizes"
    if sizes_key in payload:
        sizes = [s for s in _as_list(payload[sizes_key]) if isinstance(s, dict) and str(s.get("size") or "").strip()]
        product.single_product_sizes = _with_ids(sizes, product.single_product_sizes)

    if "bulk_discounts" in payload:
        discounts = [d for d in _as_list(payload["bulk_discounts"]) if isinstance(d, dict) and d.get("title")]
        product.bulk_discounts = _with_ids(discounts, product.bulk_discounts)

    if "bumps" in payload:
        bumps = []
        for bump in _as_list(payload["bumps"]):
            if not isinstance(bump, dict) or not bump.get("title"):
                continue
            bump = dict(bump)
            existing_image = bump.pop("existing_image", None)
            bump["image"] = bump.get("image") or existing_image
            bumps.append(bump)
        product.bumps = _with_ids(bumps, product.bumps)


def _category_and_children(db: Session, category_id: int) -> list[int]:
    ids, frontier = [category_id], [category_id]
    while frontier:
        children = [c.id for c in db.query(models.Category).filter(models.Category.parent_id.in_(frontier))]
        children = [c for c in children if c not in ids]
        ids += children
        frontier = children
    return ids


# --- Products ---
@router.get("/products")
def get_products(page: Optional[int] = None, per_page: int = 20, limit: Optional[int] = None, offset: int = 0,
                 search: Optional[str] = None, category_id: Optional[int] = None,
                 category_ids: Optional[str] = None, wholesale: Optional[bool] = None,
                 db: Session = Depends(get_db)):
    """No paging params -> plain array of every product (most storefront screens).
    `page`/`per_page` -> Laravel paginator. `limit`/`offset` -> {data, total}.
    `wholesale=1` -> only products listed on the Wholesaler page."""
    query = db.query(models.Product)
    if wholesale is not None:
        query = query.filter(func.coalesce(models.Product.is_wholesale, 0) == int(wholesale))
    if search and search.strip():
        like = f"%{search.strip()}%"
        query = query.filter(or_(models.Product.name.ilike(like), models.Product.slug.ilike(like)))
    if category_id:
        query = query.filter(models.Product.category_id == category_id)
    if category_ids:
        ids = [int(i) for i in category_ids.split(",") if i.strip().isdigit()]
        query = query.filter(models.Product.category_id.in_(ids or [-1]))
    query = query.order_by(models.Product.id.desc())

    if page is not None:
        result = paginate(query, page, per_page, item=lambda p: p)
        result["data"] = serialize_products(result["data"], db)
        result["products"] = result["data"]
        return result
    if limit is not None:
        total = query.order_by(None).count()
        rows = serialize_products(query.offset(max(0, offset)).limit(max(1, min(limit, 500))).all(), db)
        return {"data": rows, "products": rows, "total": total}
    return serialize_products(query.all(), db)


@router.get("/products/{key}")
def get_product(key: str, db: Session = Depends(get_db)):
    """Product by id or slug."""
    query = db.query(models.Product)
    product = query.filter(models.Product.id == int(key)).first() if key.isdigit() else None
    product = product or query.filter(models.Product.slug == key).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return serialize_product(product, db)


@router.post("/products", dependencies=admin_only, status_code=201,
             openapi_extra=form_body(PRODUCT_FIELDS, required=["name", "price", "category_id"]))
async def create_product(request: Request, db: Session = Depends(get_db)):
    payload = await read_payload(request)
    for field in ("name", "price", "category_id"):
        if payload.get(field) in (None, ""):
            raise HTTPException(status_code=422, detail=f"'{field}' is required")
    product = models.Product()
    _apply_product(product, payload, db)
    db.add(product)
    db.flush()
    if "colors" in payload:
        _sync_colors(product, _as_list(payload["colors"]), db)
    db.commit()
    db.refresh(product)
    return {"success": True, "message": "Product created successfully", "product_id": product.id,
            "data": serialize_product(product, db)}


@router.post("/productsupdate/{id}", dependencies=admin_only,
                  openapi_extra=form_body(PRODUCT_FIELDS))
@router.put("/productsupdate/{id}", dependencies=admin_only,
                  openapi_extra=form_body(PRODUCT_FIELDS))
async def update_product(id: int, request: Request, db: Session = Depends(get_db)):
    product = get_or_404(db, models.Product, id, "Product")
    payload = await read_payload(request)
    _apply_product(product, payload, db)
    if "colors" in payload:
        _sync_colors(product, _as_list(payload["colors"]), db)
    db.commit()
    db.refresh(product)
    return {"success": True, "message": f"Product {id} updated", "data": serialize_product(product, db)}


@router.post("/products/{id}/wholesale", dependencies=admin_only,
             openapi_extra=json_body({"is_wholesale": 1, "wholesale_price": 95, "wholesale_min_qty": 10}))
@router.put("/products/{id}/wholesale", dependencies=admin_only,
            openapi_extra=json_body({"is_wholesale": 1, "wholesale_price": 95, "wholesale_min_qty": 10}))
async def set_product_wholesale(id: int, request: Request, db: Session = Depends(get_db)):
    """List/unlist a product on the Wholesaler page and set its trade price and minimum quantity."""
    product = get_or_404(db, models.Product, id, "Product")
    payload = await read_payload(request)
    apply_payload(product, {k: v for k, v in payload.items()
                            if k in ("is_wholesale", "wholesale_price", "wholesale_min_qty")})
    if product.wholesale_price is not None and product.wholesale_price < 0:
        raise HTTPException(status_code=422, detail="wholesale_price cannot be negative")
    if product.wholesale_min_qty is not None and product.wholesale_min_qty < 1:
        raise HTTPException(status_code=422, detail="wholesale_min_qty must be at least 1")
    db.commit()
    db.refresh(product)
    return {"success": True, "message": f"Product {id} wholesale settings saved", "data": serialize_product(product, db)}


@router.delete("/productdelete/{id}", dependencies=admin_only)
def delete_product(id: int, db: Session = Depends(get_db)):
    product = get_or_404(db, models.Product, id, "Product")
    db.query(models.Size).filter(models.Size.product_id == id).delete()
    db.query(models.Color).filter(models.Color.product_id == id).delete()
    db.delete(product)
    db.commit()
    return {"success": True, "message": f"Product {id} deleted"}


# --- Categories ---
def _find_category(db: Session, key: str) -> Optional[models.Category]:
    query = db.query(models.Category)
    category = query.filter(models.Category.id == int(key)).first() if key.isdigit() else None
    return category or query.filter(models.Category.slug == key).first()


def _check_parent(db: Session, category: models.Category):
    if not category.parent_id:  # empty or 0 -> top-level category
        category.parent_id = None
        return
    if category.id is not None and category.parent_id == category.id:
        raise HTTPException(status_code=422, detail="A category cannot be its own parent")
    if not db.query(models.Category).filter(models.Category.id == category.parent_id).first():
        raise HTTPException(status_code=422, detail=f"Parent category {category.parent_id} does not exist")


@router.get("/category")
def get_categories(db: Session = Depends(get_db)):
    categories = db.query(models.Category).order_by(models.Category.sort_order, models.Category.id).all()
    return {"categories": [serialize(c) for c in categories]}


@router.get("/category/{key}")
def get_category_products(key: str, page: int = 1, per_page: int = 20, db: Session = Depends(get_db)):
    """A category (by id or slug) with a page of its products, including sub-categories' products."""
    category = _find_category(db, key)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    query = db.query(models.Product).filter(
        models.Product.category_id.in_(_category_and_children(db, category.id))).order_by(models.Product.id.desc())
    products = paginate(query, page, per_page, item=lambda p: p)
    products["data"] = serialize_products(products["data"], db)
    return {"category": serialize(category), "products": products}


@router.post("/category", dependencies=admin_only, status_code=201,
             openapi_extra=form_body(CATEGORY_FIELDS, required=["name"]))
async def create_category(request: Request, db: Session = Depends(get_db)):
    payload = await read_payload(request)
    if not payload.get("name"):
        raise HTTPException(status_code=422, detail="'name' is required")
    category = apply_payload(models.Category(), payload, protected={"slug"})
    _check_parent(db, category)
    category.slug = unique_slug(db, models.Category, payload.get("slug") or category.name)
    db.add(category)
    db.commit()
    db.refresh(category)
    return {"success": True, "message": "Category created successfully", "category_id": category.id,
            "data": serialize(category)}


@router.post("/categoryupdate/{id}", dependencies=admin_only,
                  openapi_extra=form_body(CATEGORY_FIELDS))
@router.put("/categoryupdate/{id}", dependencies=admin_only,
                  openapi_extra=form_body(CATEGORY_FIELDS))
async def update_category(id: int, request: Request, db: Session = Depends(get_db)):
    category = get_or_404(db, models.Category, id, "Category")
    payload = await read_payload(request)
    apply_payload(category, payload, protected={"slug"})
    _check_parent(db, category)
    if payload.get("slug") or not category.slug:
        category.slug = unique_slug(db, models.Category, payload.get("slug") or category.name, exclude_id=id)
    db.commit()
    db.refresh(category)
    return {"success": True, "message": f"Category {id} updated", "data": serialize(category)}


@router.delete("/categorydelete/{id}", dependencies=admin_only)
def delete_category(id: int, db: Session = Depends(get_db)):
    category = get_or_404(db, models.Category, id, "Category")
    if db.query(models.Product).filter(models.Product.category_id == id).first():
        raise HTTPException(status_code=400, detail="Category has products; move or delete them first")
    db.query(models.Category).filter(models.Category.parent_id == id).update({"parent_id": None})
    db.delete(category)
    db.commit()
    return {"success": True, "message": f"Category {id} deleted"}


# --- Colors & Sizes (rows that belong to a product) ---
@router.get("/colors")
def get_colors(db: Session = Depends(get_db)):
    return [serialize(c) for c in db.query(models.Color).order_by(models.Color.id).all()]


@router.post("/colors", dependencies=admin_only, status_code=201,
             openapi_extra=form_body(COLOR_FIELDS, required=["color"]))
async def create_color(request: Request, db: Session = Depends(get_db)):
    payload = await read_payload(request)
    if not str(payload.get("color") or "").strip():
        raise HTTPException(status_code=422, detail="'color' is required")
    color = apply_payload(models.Color(), payload)
    if color.product_id and not db.get(models.Product, color.product_id):
        raise HTTPException(status_code=422, detail="product_id does not exist")
    db.add(color)
    db.commit()
    db.refresh(color)
    return {"success": True, "message": "Color created", "id": color.id, "data": serialize(color)}


async def _update_color(id: int, request: Request, db: Session):
    color = get_or_404(db, models.Color, id, "Color")
    payload = await read_payload(request)
    if payload.get("image") in ("null", "undefined", ""):
        payload.pop("image")  # the dashboard appends a missing file as the text "null"
    apply_payload(color, payload)
    db.query(models.Size).filter(models.Size.color_id == id).update({"product_id": color.product_id})
    db.commit()
    db.refresh(color)
    return {"success": True, "message": f"Color {id} updated", "data": serialize(color)}


@router.put("/colorsupdate/{id}", dependencies=admin_only,
                  openapi_extra=form_body(COLOR_FIELDS))
@router.post("/colorsupdate/{id}", dependencies=admin_only,
                  openapi_extra=form_body(COLOR_FIELDS))
async def update_color(id: int, request: Request, db: Session = Depends(get_db)):
    return await _update_color(id, request, db)


@router.put("/livesupdate/{id}", dependencies=admin_only,
                  openapi_extra=form_body(COLOR_FIELDS))
@router.post("/livesupdate/{id}", dependencies=admin_only,
                  openapi_extra=form_body(COLOR_FIELDS))
async def update_color_live(id: int, request: Request, db: Session = Depends(get_db)):
    """Same as /colorsupdate; the dashboard's colour screen posts here."""
    return await _update_color(id, request, db)


@router.delete("/colordelete/{id}", dependencies=admin_only)
def delete_color(id: int, db: Session = Depends(get_db)):
    color = get_or_404(db, models.Color, id, "Color")
    db.query(models.Size).filter(models.Size.color_id == id).delete()
    db.delete(color)
    db.commit()
    return {"success": True, "message": f"Color {id} deleted"}


@router.get("/sizes")
def get_sizes(db: Session = Depends(get_db)):
    return [serialize(s) for s in db.query(models.Size).order_by(models.Size.id).all()]


def _apply_size(size: models.Size, payload: dict, db: Session):
    apply_payload(size, payload)
    if not str(size.size or "").strip():
        raise HTTPException(status_code=422, detail="'size' is required")
    if size.color_id:
        color = db.get(models.Color, size.color_id)
        if not color:
            raise HTTPException(status_code=422, detail="color_id does not exist")
        size.product_id = color.product_id


@router.post("/sizes", dependencies=admin_only, status_code=201,
             openapi_extra=json_body({"color_id": 1, "size": "XL"}))
async def create_size(request: Request, db: Session = Depends(get_db)):
    size = models.Size()
    _apply_size(size, await read_payload(request), db)
    db.add(size)
    db.commit()
    db.refresh(size)
    return {"success": True, "message": "Size created", "id": size.id, "data": serialize(size)}


@router.put("/sizesupdate/{id}", dependencies=admin_only,
                  openapi_extra=json_body({"color_id": 1, "size": "XL"}))
@router.post("/sizesupdate/{id}", dependencies=admin_only,
                  openapi_extra=json_body({"color_id": 1, "size": "XL"}))
async def update_size(id: int, request: Request, db: Session = Depends(get_db)):
    size = get_or_404(db, models.Size, id, "Size")
    _apply_size(size, await read_payload(request), db)
    db.commit()
    db.refresh(size)
    return {"success": True, "message": f"Size {id} updated", "data": serialize(size)}


@router.delete("/sizedelete/{id}", dependencies=admin_only)
def delete_size(id: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.Size, id, "Size"))
    db.commit()
    return {"success": True, "message": f"Size {id} deleted"}
