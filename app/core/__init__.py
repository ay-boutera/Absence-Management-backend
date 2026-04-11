"""Compatibility package for legacy imports.

The codebase now keeps shared helpers under app.helpers and app.services,
but some tests and older modules still import from app.core.
"""

from app.helpers.email import validate_esi_email, extract_name_hint_from_email
from app.helpers.security import (
    ACCESS_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    clear_auth_cookies,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_token_from_cookie,
    hash_password,
    set_auth_cookies,
    validate_csrf_token,
    verify_password,
)

__all__ = [
    "validate_esi_email",
    "extract_name_hint_from_email",
    "ACCESS_COOKIE_NAME",
    "CSRF_COOKIE_NAME",
    "REFRESH_COOKIE_NAME",
    "clear_auth_cookies",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "get_token_from_cookie",
    "hash_password",
    "set_auth_cookies",
    "validate_csrf_token",
    "verify_password",
]
