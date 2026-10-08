import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import false, func, or_
from sqlalchemy.orm import Session

import models
from database import get_db
from routers.auth import bonus_settings, clean_phone, get_or_create_customer
from security import get_current_admin, get_current_user, get_optional_user
from utils import (apply_payload, get_or_404, is_admin, json_body, paginate, read_payload, register_crud,
                   serialize)

router = APIRouter()
admin_only = [Depends(get_current_admin)]

# Fields a customer must never set on their own order.
ORDER_PROTECTED = {"user_id", "points", "consignment_id", "tracking_code", "delivery_status"}

FAST_ORDER_EXAMPLE = {
    "order_id": "A2C123456", "product_name": "Product Name", "color": "Red", "size": "XL", "quantity": 1,
    "delivery_charge": 120, "cod_advance": 0, "product_price": 500, "total": 620, "transaction_id": "",
    "customer_name": "John Doe", "customer_address": "Village/Road: ..., district: ..., division: ...",
    "phone_number": "017XXXXXXXX", "coins_used": 0, "payment_method": "cash", "payment_number": "",
    "coin_discount": 0, "delivery_note": "Handle with care", "bulk_discounts": [{"id": 1}],
    "bumps": [{"id": 1, "bump_price": "100"}],
    "items": [{"product_id": 1, "product_name": "Product Name", "quantity": 1, "price": 500, "color": "Red",
               "size": "XL"}],
}
CART_ORDER_EXAMPLE = {
    "order_id": "A2C123457", "order_type": "product", "customer_name": "John Doe", "phone_number": "017XXXXXXXX",
    "customer_address": "Village/Road: ..., District: ..., Division: ...", "product_name": "Item 1, Item 2",
    "quantity": 2, "lab_test_name": None, "lab_name": None, "patient_count": None, "report_time": None,
    "lab_test_image": None, "delivery_charge": 120, "product_price": 1000, "subtotal": 1000, "lab_tests_total": 0,
    "regular_products_total": 1000, "total": 1120, "payment_method": "cod", "delivery_note": None,
    "delivery_status": "New Order", "has_lab_tests": False, "has_regular_products": True,
    "items": [{"id": "1-colorId-size", "product_id": 1, "product_name": "Item 1", "product_image": "url",
               "quantity": 1, "price": 500, "color": "Red", "size": "XL"}],
}
LEAD_EXAMPLE = {
    "order_id": "A2C123456", "customer_name": "John Doe", "phone_number": "017XXXXXXXX", "product_price": 500,
    "customer_address": "...", "status": "lead", "quantity": 1, "product_name": "Product Name",
    "page_url": "https://...",
}


def _new_order_id(db: Session) -> str:
    while True:
        order_id = "A2C" + "".join(secrets.choice("0123456789") for _ in range(6))
        if not db.query(models.Order).filter(models.Order.order_id == order_id).first():
            return order_id


def _create_order(payload: dict, db: Session, user: Optional[models.User], default_type: str) -> models.Order:
    for field in ("customer_name", "phone_number"):
        if not str(payload.get(field) or "").strip():
            raise HTTPException(status_code=422, detail=f"'{field}' is required")
    if "items" in payload and payload["items"] is not None and not isinstance(payload["items"], list):
        raise HTTPException(status_code=422, detail="'items' must be a list")

    order_id = str(payload.get("order_id") or "").strip() or _new_order_id(db)
    if db.query(models.Order).filter(models.Order.order_id == order_id).first():
        raise HTTPException(status_code=409, detail=f"Order {order_id} already exists")

    order = apply_payload(models.Order(order_type=default_type), payload, protected=ORDER_PROTECTED)
    order.order_id = order_id
    order.delivery_status = "New Order"
    order.total = order.total or 0

    coins_used = order.coins_used or 0
    if coins_used < 0:
        raise HTTPException(status_code=422, detail="'coins_used' cannot be negative")
    if coins_used:
        if not user:
            raise HTTPException(status_code=401, detail="Login required to use coins")
        if (user.points or 0) < coins_used:
            raise HTTPException(status_code=400, detail="Insufficient coins")
        user.points = (user.points or 0) - coins_used
    if user:
        order.user_id = user.id

    db.add(order)
    if str(order.transaction_id or "").strip():
        # Advance payments show up on the dashboard's Transactions screen.
        db.add(models.Transaction(order_id=order_id, tnxId=order.transaction_id, tnxNumber=order.payment_number,
                                  amount=order.cod_advance or order.total, payment_method=order.payment_method,
                                  status="pending"))
    # The customer reached checkout, so the abandoned-cart lead is no longer open.
    db.query(models.Lead).filter(models.Lead.order_id == order_id, models.Lead.status == "lead") \
        .update({"status": "converted"})
    db.commit()
    db.refresh(order)
    return order


@router.post("/customers", status_code=201, openapi_extra=json_body(FAST_ORDER_EXAMPLE))
async def fast_order_checkout(request: Request, db: Session = Depends(get_db),
                              user: Optional[models.User] = Depends(get_optional_user)):
    order = _create_order(await read_payload(request), db, user, "product")
    return {"success": True, "message": "Order placed successfully", "order_id": order.order_id,
            "customer": serialize(order), "data": serialize(order)}


@router.post("/cartstore", status_code=201, openapi_extra=json_body(CART_ORDER_EXAMPLE))
async def cart_order_checkout(request: Request, db: Session = Depends(get_db),
                              user: Optional[models.User] = Depends(get_optional_user)):
    order = _create_order(await read_payload(request), db, user, "product")
    return {"success": True, "message": "Order placed successfully", "order_id": order.order_id,
            "customer": serialize(order), "data": serialize(order)}


def _status_filter(query, status: Optional[str]):
    """`status` is a dashboard tab key ("all", "new_order", "ready_to_ship", ...) or a status text."""
    key = (status or "").strip().lower()
    if not key or key == "all":
        return query
    column = func.lower(func.coalesce(models.Order.delivery_status, ""))
    if key in ("new_order", "new order"):
        return query.filter(column.in_(["", "new order"]))
    return query.filter(column == key.replace("_", " "))


def _date_filter(query, start_date: Optional[str], end_date: Optional[str]):
    try:
        if start_date:
            query = query.filter(models.Order.created_at >= datetime.strptime(start_date, "%Y-%m-%d"))
        if end_date:
            query = query.filter(models.Order.created_at < datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1))
    except ValueError:
        raise HTTPException(status_code=422, detail="Dates must be YYYY-MM-DD")
    return query


@router.get("/customers", dependencies=admin_only)
def get_orders(page: Optional[int] = None, per_page: int = 20, status: Optional[str] = None,
               start_date: Optional[str] = None, end_date: Optional[str] = None, export_csv: bool = False,
               lightweight: bool = False, db: Session = Depends(get_db)):
    """No `page` -> plain array. `page` -> {customers: paginator}. `export_csv` -> {customers: [...]}
    (the dashboard builds the CSV file itself)."""
    query = _date_filter(_status_filter(db.query(models.Order), status), start_date, end_date)
    query = query.order_by(models.Order.id.desc())
    if export_csv:
        return {"customers": [serialize(o) for o in query.all()]}
    if page is not None:
        return {"customers": paginate(query, page, per_page)}
    return [serialize(o) for o in query.all()]


def _orders_for_phone(db: Session, phone: str):
    """Orders whose phone matches, ignoring a +88 prefix or formatting; newest first."""
    phone = clean_phone(phone)
    if len(phone) < 10:
        return db.query(models.Order).filter(false())
    return db.query(models.Order).filter(models.Order.phone_number.like(f"%{phone[-10:]}")) \
        .order_by(models.Order.id.desc())


@router.get("/customers/search/customerdata")
def search_customers(search: Optional[str] = None, query: Optional[str] = None, q: Optional[str] = None,
                     page: int = 1, per_page: int = 20, db: Session = Depends(get_db),
                     user: Optional[models.User] = Depends(get_optional_user)):
    """Admins search name/phone/order id. Everyone else (the thank-you page) may only count the orders
    placed with an exact phone number, and gets no personal details back."""
    term = next((t for t in (search, query, q) if t), "").strip()
    if not term:
        return {"customers": paginate(db.query(models.Order).filter(false()), page, per_page)}
    if is_admin(user):
        like = f"%{term}%"
        rows = db.query(models.Order).filter(or_(
            models.Order.customer_name.ilike(like),
            models.Order.phone_number.ilike(like),
            models.Order.order_id.ilike(like),
        )).order_by(models.Order.id.desc())
        return {"customers": paginate(rows, page, per_page)}
    rows = _orders_for_phone(db, term)
    return {"customers": paginate(rows, page, per_page,
                                  item=lambda o: {"id": o.id, "order_id": o.order_id, "phone_number": o.phone_number})}


@router.get("/customers/phone/{phone}")
def customer_status_by_phone(phone: str, db: Session = Depends(get_db)):
    """Latest order status for a phone (the order page hides some payment options for blocked customers)."""
    order = _orders_for_phone(db, phone).first()
    if not order:
        raise HTTPException(status_code=404, detail="Customer not found")
    return {"customer": {"phone_number": order.phone_number, "delivery_status": order.delivery_status}}


@router.get("/customers/{key}")
def get_order(key: str, db: Session = Depends(get_db), user: Optional[models.User] = Depends(get_optional_user)):
    """Order by its order id (public: the thank-you page). Admins may also use the numeric row id."""
    order = db.query(models.Order).filter(models.Order.order_id == key).first()
    if not order and key.isdigit() and is_admin(user):
        order = db.get(models.Order, int(key))
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"customer": serialize(order)}


@router.put("/customersupdate/{id}", dependencies=admin_only,
                  openapi_extra=json_body({"customer_name": "John Doe", "customer_address": "...", "total": 620}))
@router.post("/customersupdate/{id}", dependencies=admin_only,
                  openapi_extra=json_body({"customer_name": "John Doe", "customer_address": "...", "total": 620}))
async def update_order(id: int, request: Request, db: Session = Depends(get_db)):
    order = get_or_404(db, models.Order, id, "Order")
    payload = await read_payload(request)
    new_id = payload.get("order_id")
    if new_id and new_id != order.order_id and \
            db.query(models.Order).filter(models.Order.order_id == new_id).first():
        raise HTTPException(status_code=409, detail=f"Order {new_id} already exists")
    apply_payload(order, payload, protected={"user_id"})
    db.commit()
    db.refresh(order)
    return {"success": True, "message": f"Order {id} updated", "data": serialize(order)}


@router.delete("/customerdelete/{id}", dependencies=admin_only)
def delete_order(id: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.Order, id, "Order"))
    db.commit()
    return {"success": True, "message": f"Order {id} deleted"}


@router.put("/customersstatusupdate/{id}", dependencies=admin_only,
                  openapi_extra=json_body({"delivery_status": "Shipped"}))
@router.post("/customersstatusupdate/{id}", dependencies=admin_only,
                  openapi_extra=json_body({"delivery_status": "Shipped"}))
def update_order_status(id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    status = str(payload.get("delivery_status") or "").strip()
    if not status:
        raise HTTPException(status_code=422, detail="'delivery_status' is required")
    order = get_or_404(db, models.Order, id, "Order")
    order.delivery_status = status
    db.commit()
    return {"success": True, "message": f"Status updated for order {id}", "delivery_status": status}


@router.put("/customersconsignmentupdate/{invoice}", dependencies=admin_only,
                  openapi_extra=json_body({"consignment_id": "123456", "tracking_code": "ABC123",
                                           "delivery_status": "Ready to Ship"}))
@router.post("/customersconsignmentupdate/{invoice}", dependencies=admin_only,
                  openapi_extra=json_body({"consignment_id": "123456", "tracking_code": "ABC123",
                                           "delivery_status": "Ready to Ship"}))
async def update_consignment(invoice: str, request: Request, db: Session = Depends(get_db)):
    order = db.query(models.Order).filter(models.Order.order_id == invoice).first()
    if not order:
        raise HTTPException(status_code=404, detail=f"Order with invoice {invoice} not found")
    apply_payload(order, await read_payload(request), protected={"user_id", "order_id"})
    db.commit()
    db.refresh(order)
    return {"success": True, "message": f"Consignment updated for invoice {invoice}", "data": serialize(order)}


@router.post("/customers/{id}/add-points", dependencies=admin_only,
             openapi_extra=json_body({"points": 50}, required=False))
async def add_points(id: int, request: Request, db: Session = Depends(get_db)):
    """Reward the customer of an order. Without a body the points are the bonus percentage
    (Coin Bonus settings) of the order total; they go to the account with the order's phone number."""
    order = get_or_404(db, models.Order, id, "Order")
    if order.points:
        raise HTTPException(status_code=400, detail="Points were already added for this order")
    payload = await read_payload(request)
    if payload.get("points") not in (None, ""):
        points = int(float(payload["points"]))
    else:
        bonus = bonus_settings(db)
        points = round((order.total or 0) * float(bonus.bonus_percentage or 0) / 100) if bonus else 0
    if points <= 0:
        raise HTTPException(status_code=400, detail="No points to add; set a bonus percentage under Coin Bonus")
    user = get_or_create_customer(db, order.phone_number, order.customer_name)
    user.points = (user.points or 0) + points
    order.points = points
    order.user_id = order.user_id or user.id
    db.commit()
    return {"success": True, "message": "Points added", "points_added": points, "total_points": user.points}


# --- Leads ---
@router.get("/leads", dependencies=admin_only)
def get_leads(page: int = 1, per_page: int = 20, db: Session = Depends(get_db)):
    result = paginate(db.query(models.Lead).order_by(models.Lead.id.desc()), page, per_page)
    result["leads"] = result["data"]
    return result


@router.post("/leads", openapi_extra=json_body(LEAD_EXAMPLE))
async def create_lead(request: Request, db: Session = Depends(get_db)):
    payload = await read_payload(request)
    if not str(payload.get("phone_number") or "").strip() and not str(payload.get("customer_name") or "").strip():
        raise HTTPException(status_code=422, detail="'phone_number' or 'customer_name' is required")
    order_id = str(payload.get("order_id") or "").strip()
    # The checkout page auto-saves repeatedly; keep one lead per order_id.
    lead = db.query(models.Lead).filter(models.Lead.order_id == order_id).first() if order_id else None
    if lead and lead.status != "lead":
        return {"success": True, "message": "Lead already processed", "id": lead.id}
    lead = apply_payload(lead or models.Lead(), payload, protected={"status"})
    lead.status = "lead"
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return {"success": True, "message": "Lead saved", "id": lead.id}


@router.get("/leads/{id}", dependencies=admin_only)
def get_lead(id: int, db: Session = Depends(get_db)):
    return serialize(get_or_404(db, models.Lead, id, "Lead"))


@router.put("/leads/{id}", dependencies=admin_only, openapi_extra=json_body(LEAD_EXAMPLE))
@router.put("/leadsupdate/{id}", dependencies=admin_only,
                  openapi_extra=json_body(LEAD_EXAMPLE))
@router.post("/leadsupdate/{id}", dependencies=admin_only,
                  openapi_extra=json_body(LEAD_EXAMPLE))
async def update_lead(id: int, request: Request, db: Session = Depends(get_db)):
    lead = get_or_404(db, models.Lead, id, "Lead")
    apply_payload(lead, await read_payload(request))
    db.commit()
    db.refresh(lead)
    return {"success": True, "message": f"Lead {id} updated", "data": serialize(lead)}


@router.delete("/leads/{id}", dependencies=admin_only)
@router.delete("/leaddelete/{id}", dependencies=admin_only)
def delete_lead(id: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.Lead, id, "Lead"))
    db.commit()
    return {"success": True, "message": f"Lead {id} deleted"}


@router.post("/leads/{id}/convert", dependencies=admin_only,
             openapi_extra=json_body({"delivery_charge": 120, "payment_method": "cod"}, required=False))
async def convert_lead(id: int, request: Request, db: Session = Depends(get_db)):
    """Mark a lead converted. The dashboard usually creates the order itself first (POST /customers);
    if no order with the lead's order id exists yet, one is created from the lead."""
    lead = get_or_404(db, models.Lead, id, "Lead")
    order = db.query(models.Order).filter(models.Order.order_id == lead.order_id).first() if lead.order_id else None
    if not order:
        overrides = await read_payload(request)
        payload = {
            "order_id": lead.order_id,
            "customer_name": lead.customer_name,
            "phone_number": lead.phone_number,
            "customer_address": lead.customer_address,
            "product_name": lead.product_name,
            "quantity": lead.quantity,
            "product_price": lead.product_price or 0,
            **overrides,
        }
        payload.setdefault("total", float(payload.get("product_price") or 0) + float(payload.get("delivery_charge") or 0))
        payload.setdefault("payment_method", "cod")
        order = _create_order(payload, db, None, "product")
    lead.status = "converted"
    db.commit()
    return {"success": True, "message": "Lead converted to order", "order_id": order.order_id,
            "data": serialize(order)}


# --- Transactions ---
register_crud(router, models.Transaction, label="Transaction", list_key="transactions", read_dep=get_current_admin,
              list_path="/transactions", create_path="/transactions",
              update_paths=["/transactionsupdate/{id}"], delete_paths=["/transactiondelete/{id}"],
              order_by=[models.Transaction.id.desc()],
              create_body=json_body({"tnxId": "TX123", "tnxNumber": "017XXXXXXXX", "amount": 100}))


# --- Coins ---
def _only_active(query, model):
    return query.filter(func.coalesce(model.is_active, 0) == 1)


register_crud(router, models.BonusCoin, label="Bonus", list_path="/bonuscoins", create_path="/bonuscoins",
              update_paths=["/bonuscoins/{id}"], delete_paths=["/bonuscoins/{id}"],
              create_body=json_body({"first_reg_bonus": 50, "bonus_percentage": 5, "coin_name": "Coin"}))
register_crud(router, models.CoinPackage, label="Coin package", list_path="/coinpackages",
              create_path="/coinpackages", update_paths=["/coinpackages/{id}"], delete_paths=["/coinpackages/{id}"],
              order_by=[models.CoinPackage.price], public_filter=lambda q: _only_active(q, models.CoinPackage),
              create_body=json_body({"name": "Small", "coins": 500, "price": 100, "is_active": 1}))
register_crud(router, models.CodAdvance, label="COD advance", list_path="/codadvances", create_path="/codadvances",
              update_paths=["/codadvancesupdate/{id}"], delete_paths=["/codadvancedelete/{id}"],
              public_filter=lambda q: _only_active(q, models.CodAdvance),
              create_body=json_body({"title": "...", "sub_title": "...", "headline": "...", "pay_amount": 100,
                                     "is_active": 1}))

COIN_TNX_EXAMPLE = {"coin_package_id": 1, "name": "John Doe", "phone": "017XXXXXXXX", "wallet_number": "017XXXXXXXX"}


@router.get("/cointransactions")
def get_coin_transactions(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.CoinTransaction)
    if not is_admin(user):
        query = query.filter(or_(models.CoinTransaction.user_id == user.id,
                                 models.CoinTransaction.phone == (user.phone or "-")))
    return [serialize(t) for t in query.order_by(models.CoinTransaction.id.desc()).all()]


@router.post("/cointransactions", status_code=201, openapi_extra=json_body(COIN_TNX_EXAMPLE))
async def create_coin_transaction(request: Request, db: Session = Depends(get_db),
                                  user: Optional[models.User] = Depends(get_optional_user)):
    """A purchase request; coins are only credited once an admin approves it."""
    payload = await read_payload(request)
    transaction = apply_payload(models.CoinTransaction(), payload, protected={"user_id", "status"})
    if not transaction.coin_package_id or not db.get(models.CoinPackage, transaction.coin_package_id):
        raise HTTPException(status_code=422, detail="coin_package_id does not exist")
    if not str(transaction.phone or "").strip():
        raise HTTPException(status_code=422, detail="'phone' is required")
    transaction.phone = clean_phone(transaction.phone)
    transaction.user_id = user.id if user else None
    transaction.status = 0
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return {"success": True, "message": "Coin transaction saved", "id": transaction.id,
            "data": serialize(transaction)}


@router.put("/cointransactions/{id}", dependencies=admin_only,
                  openapi_extra=json_body({**COIN_TNX_EXAMPLE, "status": 0}))
@router.post("/cointransactions/{id}", dependencies=admin_only,
                  openapi_extra=json_body({**COIN_TNX_EXAMPLE, "status": 0}))
async def update_coin_transaction(id: int, request: Request, db: Session = Depends(get_db)):
    transaction = get_or_404(db, models.CoinTransaction, id, "Coin transaction")
    apply_payload(transaction, await read_payload(request), protected={"user_id"})
    db.commit()
    db.refresh(transaction)
    return {"success": True, "message": f"Coin transaction {id} updated", "data": serialize(transaction)}


@router.put("/cointransactions/{id}/update-status", dependencies=admin_only)
@router.post("/cointransactions/{id}/update-status", dependencies=admin_only)
def approve_coin_transaction(id: int, db: Session = Depends(get_db)):
    """Mark approved. The dashboard credits the coins separately via POST /users/{phone}/add-points."""
    transaction = get_or_404(db, models.CoinTransaction, id, "Coin transaction")
    transaction.status = 1
    db.commit()
    return {"success": True, "message": f"Coin transaction {id} approved", "data": serialize(transaction)}


@router.delete("/cointransactions/{id}", dependencies=admin_only)
def delete_coin_transaction(id: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, models.CoinTransaction, id, "Coin transaction"))
    db.commit()
    return {"success": True, "message": f"Coin transaction {id} deleted"}
