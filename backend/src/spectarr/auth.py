from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_session
from .models import (
    Agent,
    ApiToken,
    Artifact,
    AuditLog,
    Experiment,
    Job,
    ProjectMembership,
    Run,
    Sample,
    TokenKind,
    UploadSession,
    User,
    UserRole,
)


ROLE_SCOPES: dict[UserRole, set[str]] = {
    UserRole.ADMIN: {"*"},
    UserRole.OPERATOR: {"library:read", "library:write", "jobs:read", "jobs:write"},
    UserRole.VIEWER: {"library:read", "jobs:read"},
    UserRole.SERVICE: set(),
}

LOCAL_PASSWORD_SENTINEL = "!spectarr-local-mode-has-no-password!"


@dataclass(frozen=True)
class Principal:
    actor_type: str
    actor_id: str | None
    role: UserRole
    scopes: frozenset[str]
    user_id: str | None = None
    agent_id: str | None = None

    def allows(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        password.encode(), salt=salt, n=32768, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024
    )
    return "scrypt$32768$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(derived).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, cost, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(digest_text)
        actual = hashlib.scrypt(
            password.encode(), salt=salt, n=int(cost), r=8, p=1, dklen=len(expected), maxmem=64 * 1024 * 1024
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def issue_secret(prefix: str = "spx") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_secret(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def ensure_local_user(session: Session, settings: Settings | None = None) -> User:
    settings = settings or get_settings()
    user = session.scalar(select(User).where(User.username == settings.local_user))
    if user is not None:
        if user.role != UserRole.ADMIN or not user.active:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                f"Local user '{settings.local_user}' must be an active administrator",
            )
        return user
    user = User(
        username=settings.local_user,
        display_name="Local Administrator",
        password_hash=LOCAL_PASSWORD_SENTINEL,
        role=UserRole.ADMIN,
        active=True,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        user = session.scalar(select(User).where(User.username == settings.local_user))
        if user is None or user.role != UserRole.ADMIN or not user.active:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                f"Local user '{settings.local_user}' could not be initialized",
            ) from None
    session.refresh(user)
    return user


def authenticate_token(session: Session, raw_token: str) -> Principal | None:
    digest = hash_secret(raw_token)
    now = datetime.now(timezone.utc)
    if raw_token.startswith("agt_"):
        agent = session.scalar(select(Agent).where(Agent.token_hash == digest, Agent.enabled.is_(True)))
        if agent:
            agent.last_seen_at = now
            session.commit()
            return Principal(
                actor_type="agent",
                actor_id=agent.id,
                agent_id=agent.id,
                role=UserRole.SERVICE,
                scopes=frozenset({"agents:write", "library:read", "library:write"}),
            )
        return None
    token = session.scalar(select(ApiToken).where(ApiToken.token_hash == digest, ApiToken.revoked_at.is_(None)))
    if token is None:
        return None
    if token.expires_at:
        comparison_now = datetime.now(token.expires_at.tzinfo) if token.expires_at.tzinfo else datetime.now()
        if token.expires_at <= comparison_now:
            return None
    if token.user is None or not token.user.active:
        return None
    token.last_used_at = now
    session.commit()
    effective_scopes = ROLE_SCOPES[token.user.role] if token.kind == TokenKind.SESSION else set(token.scopes)
    return Principal(
        actor_type="user",
        actor_id=token.user.id,
        user_id=token.user.id,
        role=token.user.role,
        scopes=frozenset(effective_scopes),
    )


def scope_for_request(request: Request) -> str:
    path = request.url.path
    is_read = request.method in {"GET", "HEAD", "OPTIONS"}
    if "/auth/" in path:
        return "library:read"
    if any(segment in path for segment in ("/jobs", "/webhook-deliveries", "/extraction-results")) or path.endswith(
        "/extract"
    ):
        return "jobs:read" if is_read else "jobs:write"
    if "/agents" in path or "/upload-sessions" in path:
        return "agents:write"
    if any(segment in path for segment in ("/users", "/audit-log")):
        return "admin"
    return "library:read" if is_read else "library:write"


async def require_request_access(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
    x_spectarr_worker_token: Annotated[str | None, Header()] = None,
) -> Principal:
    settings = get_settings()
    if x_spectarr_worker_token and settings.worker_token and hmac.compare_digest(
        x_spectarr_worker_token, settings.worker_token
    ):
        principal = Principal(
            "service",
            None,
            UserRole.SERVICE,
            frozenset({"jobs:write", "jobs:read", "library:read", "library:write"}),
        )
    elif authorization and authorization.lower().startswith("bearer "):
        principal = authenticate_token(session, authorization.split(" ", 1)[1].strip())
        if principal is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired bearer token")
    elif settings.effective_auth_mode == "local":
        user = ensure_local_user(session, settings)
        principal = Principal(
            "local",
            user.id,
            UserRole.ADMIN,
            frozenset({"*"}),
            user_id=user.id,
        )
    else:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer authentication required")
    required_scope = scope_for_request(request)
    if not principal.allows(required_scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing required scope: {required_scope}")
    project_id = project_id_for_request(session, request)
    if project_id:
        require_project_access(
            session,
            principal,
            project_id,
            write=request.method not in {"GET", "HEAD", "OPTIONS"},
        )
    request.state.principal = principal
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        session.add(
            AuditLog(
                actor_type=principal.actor_type,
                actor_id=principal.actor_id,
                action=f"{request.method} {request.url.path}",
                resource_type="api_route",
                request_id=request.headers.get("X-Request-Id"),
            )
        )
        session.commit()
    return principal


def require_admin(request: Request) -> Principal:
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal is None or not principal.allows("admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required")
    return principal


def require_agent(request: Request) -> Principal:
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal is None or principal.agent_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Agent authentication required")
    return principal


def require_project_access(session: Session, principal: Principal, project_id: str, write: bool = False) -> None:
    if principal.agent_id or principal.actor_type == "service" or principal.role in {UserRole.ADMIN, UserRole.OPERATOR}:
        return
    if principal.user_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Project access denied")
    membership = session.scalar(
        select(ProjectMembership).where(
            ProjectMembership.user_id == principal.user_id,
            ProjectMembership.project_id == project_id,
        )
    )
    if membership is None or write and membership.role == UserRole.VIEWER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Project access denied")


def project_id_for_request(session: Session, request: Request) -> str | None:
    parameters = request.path_params
    if project_id := parameters.get("project_id"):
        return project_id
    if experiment_id := parameters.get("experiment_id"):
        experiment = session.get(Experiment, experiment_id)
        return experiment.project_id if experiment else None
    if sample_id := parameters.get("sample_id"):
        sample = session.get(Sample, sample_id)
        return sample.experiment.project_id if sample else None
    if run_id := parameters.get("run_id"):
        run = session.get(Run, run_id)
        return run.experiment.project_id if run else None
    if artifact_id := parameters.get("artifact_id"):
        artifact = session.get(Artifact, artifact_id)
        return artifact.run.experiment.project_id if artifact else None
    if job_id := parameters.get("job_id"):
        job = session.get(Job, job_id)
        return job.input_artifact.run.experiment.project_id if job and job.input_artifact else None
    if upload_id := parameters.get("upload_id"):
        upload = session.get(UploadSession, upload_id)
        return upload.run.experiment.project_id if upload else None
    return None
