from fastapi import APIRouter, Depends, Request, Response

from app.api.dependencies import CurrentUser, Db
from app.core.security import (
    csrf_cookie_name,
    issue_csrf,
    require_csrf,
    session_cookie_name,
    set_private_cookie,
)
from app.schemas.auth import Credentials, LoginCredentials, UserProfile
from app.services.auth import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


@router.get("/csrf")
def csrf(request: Request, response: Response) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": issue_csrf(request, response)}


@router.post(
    "/register", response_model=UserProfile, status_code=201, dependencies=[Depends(require_csrf)]
)
def register(data: Credentials, db: Db) -> UserProfile:
    return UserProfile.model_validate(AuthService(db).register(data))


@router.post("/login", response_model=UserProfile, dependencies=[Depends(require_csrf)])
def login(data: LoginCredentials, request: Request, response: Response, db: Db) -> UserProfile:
    user, token = AuthService(db).login(data, request.cookies.get(session_cookie_name()))
    set_private_cookie(response, session_cookie_name(), token)
    response.headers["Cache-Control"] = "no-store"
    return UserProfile.model_validate(user)


@router.get("/me", response_model=UserProfile)
def profile(auth: CurrentUser, response: Response) -> UserProfile:
    response.headers["Cache-Control"] = "no-store"
    result = UserProfile.model_validate(auth.user)
    result.active_tenant_id = auth.login.active_tenant_id
    return result


@router.post("/logout", status_code=204, dependencies=[Depends(require_csrf)])
def logout(auth: CurrentUser, db: Db, response: Response) -> None:
    db.delete(auth.login)
    for name in (session_cookie_name(), csrf_cookie_name()):
        response.delete_cookie(
            name,
            path="/",
            secure=True if name.startswith("__Host-") else False,
            httponly=True,
            samesite="lax",
        )
