"""
Authentication Helper Module (JWT & Password Hashing)
"""

import jwt
import hashlib
import os
from datetime import datetime, timedelta
from typing import Optional

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "change-this-key-before-production")
PASSWORD_SALT = os.environ.get("PASSWORD_SALT", "change-this-salt-before-production")
ALGORITHM = "HS256"

def hash_password(password: str) -> str:
    return hashlib.sha256((password + PASSWORD_SALT).encode('utf-8')).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return hash_password(plain_password) == hashed_password

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=7))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None
