"""Panelist — sign in, sign out, and who am I."""

from fastapi import APIRouter, Depends, HTTPException, Response

from api.auth import (
    COOKIE_NAME,
    SESSION_HOURS,
    current_user,
    issue_token,
    verify_password,
)
from api.deps import store
from api.schemas import LoginRequest

router = APIRouter(tags=["auth"])


@router.post("/auth/login")
def login(req: LoginRequest, response: Response):
    user = store.get_user(req.username.strip().lower())
    # One message for both "no such user" and "wrong password", so the response
    # cannot be used to enumerate valid usernames.
    if not user or not verify_password(
        req.password, user["salt"], user["password_hash"]
    ):
        raise HTTPException(401, "Incorrect username or password.")

    store.touch_login(user["username"])
    response.set_cookie(
        COOKIE_NAME,
        issue_token(user["username"], user["role"]),
        httponly=True,          # page JavaScript can never read the session
        samesite="lax",
        max_age=SESSION_HOURS * 3600,
        path="/",
    )
    return {
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
    }


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"signed_out": True}


@router.get("/auth/me")
def me(user=Depends(current_user)):
    stored = store.get_user(user["username"])
    return {
        "username": user["username"],
        "role": user["role"],
        "display_name": stored["display_name"] if stored else user["username"],
    }
