from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models
from database import get_db
from security import (get_current_admin, get_current_user, get_optional_user, hash_password, is_hashed,
                      new_token, verify_password)
from utils import get_or_404, is_admin, serialize

router = APIRouter()

USER_HIDDEN = ("password", "remember_token")


class UserCreate(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    password: str
    password_confirmation: Optional[str] = None
    type: str = "user"


class LoginRequest(BaseModel):
    email: str
    password: str


class PhoneLoginRequest(BaseModel):
    phone: str


def serialize_user(user: models.User) -> dict:
    return serialize(user, hide=USER_HIDDEN)


def clean_phone(phone) -> str:
    """Digits only, without the Bangladesh country code: "+880 1712-345678" -> "01712345678"."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if digits.startswith("880") and len(digits) == 13:
        digits = digits[2:]
    return digits


def validation_error(errors: dict) -> JSONResponse:
    """Laravel-style 422 body; the frontend reads `errors.<field>[0]`."""
    first = next(iter(errors.values()))[0]
    return JSONResponse(status_code=422, content={"message": first, "errors": errors})


def bonus_settings(db: Session) -> Optional[models.BonusCoin]:
    return db.query(models.BonusCoin).order_by(models.BonusCoin.id).first()


def get_or_create_customer(db: Session, phone: str, name: Optional[str] = None) -> models.User:
    """Customer account keyed by phone; a new one gets the first-registration bonus."""
    phone = clean_phone(phone)
    user = db.query(models.User).filter(models.User.phone == phone).first()
    if user:
        return user
    bonus = bonus_settings(db)
    user = models.User(name=name or phone, phone=phone, type="user",
                       points=int(bonus.first_reg_bonus or 0) if bonus else 0)
    db.add(user)
    db.flush()
    return user


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    # The login form's field is "Email or Phone".
    login_id = payload.email.strip()
    user = db.query(models.User).filter(models.User.email == login_id).first()
    if not user and clean_phone(login_id):
        user = db.query(models.User).filter(models.User.phone == clean_phone(login_id)).first()
    if not user or not verify_password(payload.password, user.password):
        raise HTTPException(status_code=400, detail="Invalid Credentials")
    if not is_hashed(user.password):
        user.password = hash_password(payload.password)
    # Reuse the current token so logging in on a second device keeps the first one signed in.
    user.remember_token = user.remember_token or new_token()
    db.commit()
    return {"remember_token": user.remember_token, "type": user.type}


@router.post("/userlogin")
def phone_login(payload: PhoneLoginRequest, db: Session = Depends(get_db)):
    """Storefront login by phone number only (customers have no password).

    Staff accounts are excluded, so a phone number can never open the admin dashboard."""
    user = db.query(models.User).filter(models.User.phone == clean_phone(payload.phone)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.type != "user":
        raise HTTPException(status_code=403, detail="Please log in with email and password")
    # Reuse the current token so logging in on a second device keeps the first one signed in.
    user.remember_token = user.remember_token or new_token()
    db.commit()
    return {"token": user.remember_token, "user": serialize_user(user)}


@router.post("/logout")
def logout(db: Session = Depends(get_db), user: Optional[models.User] = Depends(get_optional_user)):
    if user:
        user.remember_token = None
        db.commit()
    return {"message": "Logged out successfully"}


@router.post("/users", status_code=201)
def register_user(payload: UserCreate, db: Session = Depends(get_db),
                  current_user: Optional[models.User] = Depends(get_optional_user)):
    errors = {}
    if not payload.email and not payload.phone:
        errors["email"] = ["The email field is required."]
    if payload.password_confirmation is not None and payload.password != payload.password_confirmation:
        errors["password_confirmation"] = ["The password confirmation does not match."]
    if payload.type not in ("user", "admin", "moderator"):
        errors["type"] = ["The selected type is invalid."]
    if payload.email and db.query(models.User).filter(models.User.email == payload.email).first():
        errors["email"] = ["The email has already been taken."]
    phone = clean_phone(payload.phone) if payload.phone else None
    if phone and db.query(models.User).filter(models.User.phone == phone).first():
        errors["phone"] = ["The phone has already been taken."]
    if errors:
        return validation_error(errors)
    # Public sign-up can only create customer accounts; only an admin can create staff.
    if payload.type != "user" and not is_admin(current_user):
        raise HTTPException(status_code=403, detail="Only an admin can create staff accounts")

    new_user = models.User(name=payload.name, email=payload.email, phone=phone,
                           password=hash_password(payload.password), type=payload.type)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "User registered successfully", "user_id": new_user.id, "user": serialize_user(new_user)}


@router.get("/users", dependencies=[Depends(get_current_admin)])
def get_users(db: Session = Depends(get_db)):
    return {"users": [serialize_user(u) for u in db.query(models.User).order_by(models.User.id).all()]}


@router.get("/users/{phone}")
def get_user_by_phone(phone: str, db: Session = Depends(get_db),
                      current_user: models.User = Depends(get_current_user)):
    """Points lookup for the order page. Customers may only look up their own number."""
    phone = clean_phone(phone)
    if not is_admin(current_user) and clean_phone(current_user.phone) != phone:
        raise HTTPException(status_code=403, detail="You can only view your own account")
    user = db.query(models.User).filter(models.User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return serialize_user(user)


@router.post("/users/{phone}/add-points", dependencies=[Depends(get_current_admin)])
def add_points_by_phone(phone: str, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    try:
        points = int(float(payload.get("points") or 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="'points' must be a number")
    user = get_or_create_customer(db, phone, payload.get("name"))
    user.points = (user.points or 0) + points
    db.commit()
    return {"message": "Points added", "points_added": points, "total_points": user.points}


@router.put("/usersupdate/{id}", dependencies=[Depends(get_current_admin)])
def update_user(id: int, payload: dict, db: Session = Depends(get_db)):
    user = get_or_404(db, models.User, id, "User")
    if payload.get("email") and payload["email"] != user.email:
        if db.query(models.User).filter(models.User.email == payload["email"]).first():
            return validation_error({"email": ["The email has already been taken."]})
        user.email = payload["email"]
    if payload.get("phone") and clean_phone(payload["phone"]) != user.phone:
        phone = clean_phone(payload["phone"])
        if db.query(models.User).filter(models.User.phone == phone).first():
            return validation_error({"phone": ["The phone has already been taken."]})
        user.phone = phone
    if "type" in payload:
        if payload["type"] not in ("user", "admin", "moderator"):
            return validation_error({"type": ["The selected type is invalid."]})
        user.type = payload["type"]
    if "name" in payload:
        user.name = payload["name"]
    if payload.get("points") not in (None, ""):
        user.points = int(float(payload["points"]))
    if payload.get("password"):
        if "password_confirmation" in payload and payload["password"] != payload["password_confirmation"]:
            return validation_error({"password_confirmation": ["The password confirmation does not match."]})
        user.password = hash_password(payload["password"])
        user.remember_token = None
    db.commit()
    db.refresh(user)
    return {"success": True, "message": f"User {id} updated", "data": serialize_user(user)}


@router.delete("/userdelete/{id}")
def delete_user(id: int, db: Session = Depends(get_db), admin: models.User = Depends(get_current_admin)):
    user = get_or_404(db, models.User, id, "User")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    db.delete(user)
    db.commit()
    return {"success": True, "message": f"User {id} deleted"}


@router.get("/user")
def get_authenticated_user(current_user: models.User = Depends(get_current_user)):
    return serialize_user(current_user)
