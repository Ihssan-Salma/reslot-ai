"""A session capability is supplied by trusted Python code, never by tool arguments."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.api.dependencies import authorize_tenant
from app.core.context import AuthContext, TenantContext
from app.core.security import digest_token
from app.db.session import assert_runtime_role, get_engine, set_tenant_context, set_user_context
from app.integrations.google.errors import GoogleError
from app.integrations.google.http import GoogleHTTP
from app.mcp.schemas import ToolResult
from app.models import User
from app.repositories.auth import AuthRepository


def denied() -> ToolResult:
    return ToolResult(category="ACCESS_DENIED", message="Authorized business session required")


@dataclass(frozen=True, repr=False)
class SessionExecutor:
    """Pinned business; every operation revalidates expiry, membership and manager role."""

    token_hash: SecretStr
    tenant_id: UUID
    engine: Engine = field(repr=False)
    http_factory: Callable[[], httpx.Client] = field(repr=False)

    @classmethod
    def from_session(
        cls,
        session_token: str,
        *,
        engine: Engine | None = None,
        http_factory: Callable[[], httpx.Client] | None = None,
    ) -> "SessionExecutor":
        runtime = engine or get_engine()
        assert_runtime_role(runtime)
        hashed = digest_token(session_token)
        with Session(runtime) as db, db.begin():
            login = AuthRepository(db).valid_session(hashed)
            if login is None or login.active_tenant_id is None:
                raise PermissionError("Authorized business session required")
            tenant_id = login.active_tenant_id
        executor = cls(
            SecretStr(hashed),
            tenant_id,
            runtime,
            http_factory or (lambda: httpx.Client(timeout=15, follow_redirects=False)),
        )
        result = executor.execute(lambda *_: ToolResult(category="OK", message="Authorized"))
        if result.category != "OK":
            raise PermissionError("Authorized business session required")
        return executor

    def execute(
        self,
        operation: Callable[[Session, TenantContext, GoogleHTTP], ToolResult],
    ) -> ToolResult:
        try:
            with Session(self.engine, expire_on_commit=False) as db, db.begin():
                db.execute(text("SELECT set_config('app.user_id', '', true)"))
                set_tenant_context(db, None)
                login = AuthRepository(db).valid_session(self.token_hash.get_secret_value())
                if login is None or login.active_tenant_id != self.tenant_id:
                    return denied()
                user = db.get(User, login.user_id)
                if user is None:
                    return denied()
                set_user_context(db, user.id)
                context = authorize_tenant(db, AuthContext(user, login), self.tenant_id)
                if context.membership.role not in ("owner", "admin"):
                    return denied()
                with self.http_factory() as client:
                    try:
                        return operation(db, context, GoogleHTTP(client))
                    except GoogleError as exc:
                        # Commit refresh revocation/calendar-permission changes even on failure.
                        return google_failure(exc)
        except HTTPException:
            return denied()
        except ValidationError:
            return ToolResult(category="REVIEW_REQUIRED", message="Source data needs review")
        except Exception:
            # Do not include exception text, rejected arguments, provider bodies or credentials.
            return ToolResult(category="GOOGLE_API_ERROR", message="Tool operation unavailable")


def google_failure(exc: GoogleError) -> ToolResult:
    category: Any = {
        404: "NOT_FOUND",
        403: "ACCESS_DENIED",
        401: "ACCESS_DENIED",
        422: "REVIEW_REQUIRED",
        409: "REVIEW_REQUIRED",
    }.get(exc.status, "GOOGLE_API_ERROR")
    return ToolResult(
        category=category,
        message={
            "NOT_FOUND": "Record not found in the authorized resource",
            "ACCESS_DENIED": "Google resource access denied",
            "REVIEW_REQUIRED": "Integration configuration or source data needs review",
            "GOOGLE_API_ERROR": "Google request could not be completed",
        }[category],
    )
