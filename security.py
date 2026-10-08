import hashlib
import hmac
import secrets
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

import models
from database import get_db

_HASH_PREFIX = "pbkdf2_sha256"
_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()
    return f"{_HASH_PREFIX}${_ITERATIONS}${salt}${digest}"


def is_hashed(stored: Optional[str]) -> bool:
    return bool(stored) and stored.startswith(_HASH_PREFIX + "$")


def verify_password(password: str, stored: Optional[str]) -> bool:
    if not stored:
        return False
    if not is_hashed(stored):
        # Legacy rows saved before hashing was added.
        return hmac.compare_digest(password, stored)
    _, iterations, salt, digest = stored.split("$")
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(candidate, digest)


def new_token() -> str:
    return secrets.token_hex(32)


# Registered as the "HTTPBearer" security scheme, so Swagger's Authorize button applies the token to every route.
bearer_scheme = HTTPBearer(auto_error=False)


def _user_from_token(credentials: Optional[HTTPAuthorizationCredentials], db: Session) -> Optional[models.User]:
    if not credentials or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        return None
    return db.query(models.User).filter(models.User.remember_token == credentials.credentials).first()


def get_optional_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
                      db: Session = Depends(get_db)):
    return _user_from_token(credentials, db)


def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
                     db: Session = Depends(get_db)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    user = _user_from_token(credentials, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


def get_current_admin(current_user: models.User = Depends(get_current_user)):
    if current_user.type != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
