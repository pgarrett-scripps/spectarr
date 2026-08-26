from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from .api import StorageDep, commit_or_conflict, create_artifact_record, fetch_or_404
from .auth import (
    Principal,
    hash_password,
    hash_secret,
    issue_secret,
    require_admin,
    require_agent,
    verify_password,
)
from .config import get_settings
from .database import get_session
from .library import LibraryMaterializer
from .models import (
    Agent,
    ApiToken,
    Artifact,
    ArtifactState,
    AuditLog,
    AutomationRule,
    EventOutbox,
    Experiment,
    ExtractionResult,
    Instrument,
    Job,
    JobKind,
    LoginThrottle,
    Project,
    ProjectMembership,
    Run,
    RunSample,
    Sample,
    UploadPart,
    UploadSession,
    UploadState,
    User,
    UserRole,
    TokenKind,
    WebhookDelivery,
    WebhookDestination,
)
from .schemas import (
    AgentHeartbeat,
    AgentRead,
    AgentRegister,
    AgentUpdate,
    ArtifactRead,
    AuthConfiguration,
    AuditLogRead,
    AutomationRuleCreate,
    AutomationRuleRead,
    AutomationRuleUpdate,
    BootstrapRequest,
    ExtractionResultCreate,
    ExtractionResultRead,
    ExtractRequest,
    JobRead,
    LoginRequest,
    LoginResponse,
    MembershipCreate,
    MembershipRead,
    PasswordChange,
    TokenCreate,
    TokenRead,
    UploadSessionCreate,
    UserCreate,
    UserRead,
    UserUpdate,
    WebhookCreate,
    WebhookDeliveryRead,
    WebhookRead,
    WebhookUpdate,
)
from .storage import hash_file
from .pipeline import enqueue_webhook_deliveries, schedule_source_pipeline
from .processing import reconcile_processing_rule


auth_router = APIRouter(tags=["auth"])
platform_router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


def issue_user_session(session: Session, user: User, name: str) -> tuple[ApiToken, str]:
    raw = issue_secret()
    token = ApiToken(
        user_id=user.id,
        name=name,
        token_prefix=raw[:12],
        token_hash=hash_secret(raw),
        kind=TokenKind.SESSION,
        scopes=[],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=get_settings().session_hours),
    )
    session.add(token)
    session.commit()
    session.refresh(token)
    return token, raw


@auth_router.post("/auth/bootstrap", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def bootstrap_admin(payload: BootstrapRequest, session: SessionDep) -> dict:
    if get_settings().effective_auth_mode == "local":
        raise HTTPException(status.HTTP_409_CONFLICT, "Password bootstrap is unavailable in local mode")
    if session.scalar(select(func.count(User.id))):
        raise HTTPException(status.HTTP_409_CONFLICT, "Bootstrap has already been completed")
    user = User(
        username=payload.username.strip().lower(),
        password_hash=hash_password(payload.password),
        role=UserRole.ADMIN,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    _, raw = issue_user_session(session, user, "bootstrap")
    return {"access_token": raw, "token_type": "bearer", "user": user}


@auth_router.get("/auth/bootstrap/status")
async def bootstrap_status(session: SessionDep) -> dict:
    if get_settings().effective_auth_mode == "local":
        return {"required": False}
    return {"required": not bool(session.scalar(select(func.count(User.id))))}


@auth_router.get("/auth/config", response_model=AuthConfiguration)
async def auth_configuration() -> AuthConfiguration:
    settings = get_settings()
    return AuthConfiguration(
        mode=settings.effective_auth_mode,
        local_user=settings.local_user if settings.effective_auth_mode == "local" else None,
        allow_remote_no_auth=settings.allow_remote_no_auth,
    )


@auth_router.post("/auth/login", response_model=LoginResponse)
async def login(payload: LoginRequest, session: SessionDep) -> dict:
    settings = get_settings()
    if settings.effective_auth_mode == "local":
        raise HTTPException(status.HTTP_409_CONFLICT, "Password login is unavailable in local mode")
    username = payload.username.strip().lower()
    now = datetime.now(timezone.utc)
    throttle = session.get(LoginThrottle, username)
    if throttle and throttle.locked_until:
        locked_until = aware_datetime(throttle.locked_until)
        if locked_until > now:
            retry_after = max(1, int((locked_until - now).total_seconds()))
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many failed login attempts. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )
    user = session.scalar(select(User).where(User.username == username))
    if user is None or not user.active or not verify_password(payload.password, user.password_hash):
        record_failed_login(session, username, now)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    if throttle:
        session.delete(throttle)
    user.last_login_at = now
    session.commit()
    _, raw = issue_user_session(session, user, "login")
    return {"access_token": raw, "token_type": "bearer", "user": user}


@platform_router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
async def logout(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    if authorization and authorization.lower().startswith("bearer "):
        digest = hash_secret(authorization.split(" ", 1)[1].strip())
        token = session.scalar(
            select(ApiToken).where(
                ApiToken.token_hash == digest,
                ApiToken.kind == TokenKind.SESSION,
                ApiToken.revoked_at.is_(None),
            )
        )
        if token is not None:
            token.revoked_at = datetime.now(timezone.utc)
            session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@platform_router.get("/auth/me", response_model=UserRead, tags=["auth"])
async def me(request: Request, session: SessionDep) -> User:
    principal: Principal = request.state.principal
    if principal.user_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This credential is not a user credential")
    return fetch_or_404(session, User, principal.user_id)


@platform_router.post("/auth/password", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
async def change_password(
    payload: PasswordChange,
    request: Request,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    settings = get_settings()
    if settings.effective_auth_mode == "local":
        raise HTTPException(status.HTTP_409_CONFLICT, "Passwords are unavailable in local mode")
    principal: Principal = request.state.principal
    if principal.user_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A user session is required")
    user = fetch_or_404(session, User, principal.user_id)
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect")
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "New password must be different")
    user.password_hash = hash_password(payload.new_password)
    current_digest = None
    if authorization and authorization.lower().startswith("bearer "):
        current_digest = hash_secret(authorization.split(" ", 1)[1].strip())
    now = datetime.now(timezone.utc)
    sessions = session.scalars(
        select(ApiToken).where(
            ApiToken.user_id == user.id,
            ApiToken.kind == TokenKind.SESSION,
            ApiToken.revoked_at.is_(None),
        )
    )
    for token in sessions:
        if token.token_hash != current_digest:
            token.revoked_at = now
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def record_failed_login(session: Session, username: str, now: datetime) -> None:
    settings = get_settings()
    throttle = session.get(LoginThrottle, username)
    if throttle is None:
        throttle = LoginThrottle(
            username=username,
            failed_attempts=0,
            first_failed_at=now,
            locked_until=None,
        )
        session.add(throttle)
    first_failed_at = aware_datetime(throttle.first_failed_at)
    if (now - first_failed_at).total_seconds() > settings.login_window_seconds:
        throttle.failed_attempts = 0
        throttle.first_failed_at = now
        throttle.locked_until = None
    throttle.failed_attempts += 1
    if throttle.failed_attempts >= settings.login_max_attempts:
        throttle.locked_until = now + timedelta(seconds=settings.login_lock_seconds)
    session.commit()


def aware_datetime(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@platform_router.get("/users", response_model=list[UserRead], tags=["auth"])
async def list_users(request: Request, session: SessionDep) -> list[User]:
    require_admin(request)
    return list(session.scalars(select(User).order_by(User.username)))


@platform_router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED, tags=["auth"])
async def create_user(payload: UserCreate, request: Request, session: SessionDep) -> User:
    require_admin(request)
    return commit_or_conflict(
        session,
        User(
            username=payload.username.strip().lower(),
            display_name=payload.display_name,
            password_hash=hash_password(payload.password),
            role=payload.role,
            active=payload.active,
        ),
    )


@platform_router.patch("/users/{user_id}", response_model=UserRead, tags=["auth"])
async def update_user(user_id: str, payload: UserUpdate, request: Request, session: SessionDep) -> User:
    require_admin(request)
    user = fetch_or_404(session, User, user_id)
    values = payload.model_dump(exclude_unset=True)
    settings = get_settings()
    if settings.effective_auth_mode == "local" and user.username == settings.local_user:
        if values.get("role") not in {None, UserRole.ADMIN} or values.get("active") is False:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "The active local administrator cannot be demoted or disabled",
            )
    password = values.pop("password", None)
    for field, value in values.items():
        setattr(user, field, value)
    if password:
        user.password_hash = hash_password(password)
    session.commit()
    session.refresh(user)
    return user


@platform_router.get("/tokens", response_model=list[TokenRead], tags=["auth"])
async def list_tokens(request: Request, session: SessionDep) -> list[ApiToken]:
    principal: Principal = request.state.principal
    query = (
        select(ApiToken)
        .where(ApiToken.kind == TokenKind.API, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    )
    if not principal.allows("admin"):
        query = query.where(ApiToken.user_id == principal.user_id)
    return list(session.scalars(query))


@platform_router.post("/tokens", status_code=status.HTTP_201_CREATED, tags=["auth"])
async def create_token(payload: TokenCreate, request: Request, session: SessionDep) -> dict:
    principal: Principal = request.state.principal
    user_id = payload.user_id or principal.user_id
    if user_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "user_id is required")
    if user_id != principal.user_id:
        require_admin(request)
    user = fetch_or_404(session, User, user_id)
    allowed = {"library:read", "library:write", "jobs:read", "jobs:write", "agents:write", "admin", "*"}
    if not set(payload.scopes).issubset(allowed):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Token contains an unknown scope")
    if not principal.allows("admin") and not set(payload.scopes).issubset(principal.scopes):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot grant scopes not held by the caller")
    raw = issue_secret()
    token = commit_or_conflict(
        session,
        ApiToken(
            user_id=user.id,
            name=payload.name,
            token_prefix=raw[:12],
            token_hash=hash_secret(raw),
            kind=TokenKind.API,
            scopes=payload.scopes,
            expires_at=payload.expires_at,
        ),
    )
    return {**TokenRead.model_validate(token).model_dump(), "token": raw}


@platform_router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
async def revoke_token(token_id: str, request: Request, session: SessionDep) -> Response:
    principal: Principal = request.state.principal
    token = session.scalar(select(ApiToken).where(ApiToken.id == token_id, ApiToken.kind == TokenKind.API))
    if token is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API token not found")
    if token.user_id != principal.user_id:
        require_admin(request)
    token.revoked_at = datetime.now(timezone.utc)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@platform_router.post(
    "/projects/{project_id}/memberships",
    response_model=MembershipRead,
    status_code=status.HTTP_201_CREATED,
    tags=["auth"],
)
async def create_membership(
    project_id: str, payload: MembershipCreate, request: Request, session: SessionDep
) -> ProjectMembership:
    require_admin(request)
    fetch_or_404(session, Project, project_id)
    fetch_or_404(session, User, payload.user_id)
    return commit_or_conflict(
        session,
        ProjectMembership(project_id=project_id, user_id=payload.user_id, role=payload.role),
    )


@platform_router.get(
    "/projects/{project_id}/memberships", response_model=list[MembershipRead], tags=["auth"]
)
async def list_memberships(project_id: str, request: Request, session: SessionDep) -> list[ProjectMembership]:
    require_admin(request)
    fetch_or_404(session, Project, project_id)
    return list(
        session.scalars(
            select(ProjectMembership)
            .where(ProjectMembership.project_id == project_id)
            .order_by(ProjectMembership.created_at)
        )
    )


@platform_router.delete(
    "/projects/{project_id}/memberships/{membership_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"]
)
async def delete_membership(
    project_id: str, membership_id: str, request: Request, session: SessionDep
) -> Response:
    require_admin(request)
    membership = fetch_or_404(session, ProjectMembership, membership_id)
    if membership.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ProjectMembership not found")
    session.delete(membership)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@platform_router.get("/audit-log", response_model=list[AuditLogRead], tags=["auth"])
async def list_audit_log(
    request: Request,
    session: SessionDep,
    offset: int = 0,
    limit: int = 100,
) -> list[AuditLog]:
    require_admin(request)
    return list(session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)))


@platform_router.post(
    "/artifacts/{artifact_id}/extraction-results",
    response_model=ExtractionResultRead,
    status_code=status.HTTP_201_CREATED,
    tags=["extraction"],
)
async def create_extraction_result(
    artifact_id: str, payload: ExtractionResultCreate, session: SessionDep, storage: StorageDep
) -> ExtractionResult:
    artifact = fetch_or_404(session, Artifact, artifact_id)
    existing = session.scalar(
        select(ExtractionResult).where(
            ExtractionResult.artifact_id == artifact_id,
            ExtractionResult.schema_version == payload.schema_version,
            ExtractionResult.extractor == payload.extractor,
            ExtractionResult.extractor_version == payload.extractor_version,
            ExtractionResult.result_type == payload.result_type,
        )
    )
    if existing:
        return existing
    result = ExtractionResult(artifact_id=artifact_id, **payload.model_dump())
    session.add(result)
    artifact.metadata_json = {**artifact.metadata_json, "latest_extraction": payload.payload}
    artifact.run.metadata_json = {**artifact.run.metadata_json, **normalized_run_metadata(payload.payload)}
    event = EventOutbox(
            topic="artifact.metadata_extracted",
            aggregate_type="artifact",
            aggregate_id=artifact.id,
            dedupe_key=f"metadata:{artifact.id}:{payload.extractor}:{payload.extractor_version}:{payload.schema_version}",
            payload={"artifact_id": artifact.id, "run_id": artifact.run_id, "result_type": payload.result_type},
        )
    session.add(event)
    session.flush()
    enqueue_webhook_deliveries(session, event)
    LibraryMaterializer(storage).write_run_manifest(artifact.run)
    session.commit()
    session.refresh(result)
    return result


@platform_router.get(
    "/artifacts/{artifact_id}/extraction-results",
    response_model=list[ExtractionResultRead],
    tags=["extraction"],
)
async def list_extraction_results(artifact_id: str, session: SessionDep) -> list[ExtractionResult]:
    fetch_or_404(session, Artifact, artifact_id)
    return list(
        session.scalars(
            select(ExtractionResult)
            .where(ExtractionResult.artifact_id == artifact_id)
            .order_by(ExtractionResult.created_at.desc())
        )
    )


@platform_router.get(
    "/artifacts/{artifact_id}/extraction-results/latest",
    response_model=ExtractionResultRead,
    tags=["extraction"],
)
async def latest_extraction_result(
    artifact_id: str, session: SessionDep, result_type: str | None = None
) -> ExtractionResult:
    query = select(ExtractionResult).where(ExtractionResult.artifact_id == artifact_id)
    if result_type:
        query = query.where(ExtractionResult.result_type == result_type)
    result = session.scalar(query.order_by(ExtractionResult.created_at.desc()))
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No extraction result exists")
    return result


@platform_router.get("/runs/{run_id}/qc", tags=["extraction"])
async def latest_run_qc(run_id: str, session: SessionDep) -> dict:
    fetch_or_404(session, Run, run_id)
    result = session.scalar(
        select(ExtractionResult)
        .join(Artifact, ExtractionResult.artifact_id == Artifact.id)
        .where(Artifact.run_id == run_id)
        .order_by(ExtractionResult.created_at.desc())
    )
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No QC extraction exists")
    return {
        "run_id": run_id,
        "extraction_result_id": result.id,
        "schema_version": result.schema_version,
        "qc_summary": result.payload.get("qc_summary", {}),
        "warnings": result.warnings,
        "created_at": result.created_at,
    }


@platform_router.post(
    "/artifacts/{artifact_id}/extract",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["extraction"],
)
async def request_extraction(artifact_id: str, payload: ExtractRequest, session: SessionDep) -> Job:
    artifact = fetch_or_404(session, Artifact, artifact_id)
    key_material = f"extract:{artifact.id}:{artifact.sha256}:{payload.extractor}:{payload.schema_version}"
    if payload.force:
        key_material += f":{secrets.token_hex(8)}"
    key = hashlib.sha256(key_material.encode()).hexdigest()
    existing = session.scalar(select(Job).where(Job.idempotency_key == key))
    if existing:
        return existing
    return commit_or_conflict(
        session,
        Job(
            kind=JobKind.EXTRACT_METADATA,
            input_artifact_id=artifact.id,
            parameters=payload.model_dump(),
            idempotency_key=key,
        ),
    )


@platform_router.get("/automation-rules", response_model=list[AutomationRuleRead], tags=["automation"])
async def list_automation_rules(session: SessionDep) -> list[AutomationRule]:
    return list(session.scalars(select(AutomationRule).order_by(AutomationRule.priority, AutomationRule.name)))


@platform_router.post(
    "/automation-rules",
    response_model=AutomationRuleRead,
    status_code=status.HTTP_201_CREATED,
    tags=["automation"],
)
async def create_automation_rule(
    payload: AutomationRuleCreate, request: Request, session: SessionDep
) -> AutomationRule:
    require_admin(request)
    if payload.project_id:
        fetch_or_404(session, Project, payload.project_id)
    if payload.instrument_id:
        fetch_or_404(session, Instrument, payload.instrument_id)
    for action in payload.actions:
        if action.get("kind") == JobKind.CONVERT.value:
            parameters = action.get("parameters", {})
            if not action.get("recipe_id") and not (parameters.get("format") or action.get("format")):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "Convert automation requires recipe_id or parameters.format",
                )
    rule = commit_or_conflict(session, AutomationRule(**payload.model_dump()))
    reconcile_processing_rule(session, rule)
    return rule


@platform_router.patch(
    "/automation-rules/{rule_id}", response_model=AutomationRuleRead, tags=["automation"]
)
async def update_automation_rule(
    rule_id: str, payload: AutomationRuleUpdate, request: Request, session: SessionDep
) -> AutomationRule:
    require_admin(request)
    rule = fetch_or_404(session, AutomationRule, rule_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    session.commit()
    session.refresh(rule)
    reconcile_processing_rule(session, rule)
    return rule


@platform_router.post("/agents/register", status_code=status.HTTP_201_CREATED, tags=["agents"])
async def register_agent(
    payload: AgentRegister,
    request: Request,
    session: SessionDep,
    storage: StorageDep,
) -> dict:
    require_admin(request)
    direct_destination = None
    if payload.destination_experiment_id:
        direct_destination = fetch_or_404(session, Experiment, payload.destination_experiment_id)
        if direct_destination.project.system_key:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Direct destination cannot be a system inbox")
    registration_key = request.headers.get("Idempotency-Key") or payload.metadata_json.get("local_agent_id")
    raw = issue_secret("agt")
    agent = session.scalar(select(Agent).where(Agent.registration_key == registration_key)) if registration_key else None
    if agent:
        agent.token_prefix = raw[:12]
        agent.token_hash = hash_secret(raw)
        agent.version = payload.version
        agent.capabilities = payload.capabilities
        agent.metadata_json = {**agent.metadata_json, **payload.metadata_json}
        session.commit()
        session.refresh(agent)
    else:
        agent = commit_or_conflict(
            session,
            Agent(
            name=payload.name,
            registration_key=registration_key,
            version=payload.version,
            capabilities=payload.capabilities,
            metadata_json=payload.metadata_json,
            token_prefix=raw[:12],
            token_hash=hash_secret(raw),
            ),
        )
    if direct_destination:
        agent.destination_mode = "direct"
        agent.destination_experiment_id = direct_destination.id
        session.commit()
        session.refresh(agent)
    elif not agent.destination_experiment_id:
        ensure_agent_inbox(session, storage, agent)
    return {**AgentRead.model_validate(agent).model_dump(), "token": raw}


@platform_router.get("/agents", response_model=list[AgentRead], tags=["agents"])
async def list_agents(request: Request, session: SessionDep) -> list[Agent]:
    require_admin(request)
    return list(session.scalars(select(Agent).order_by(Agent.name)))


@platform_router.patch("/agents/{agent_id}", response_model=AgentRead, tags=["agents"])
async def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    request: Request,
    session: SessionDep,
    storage: StorageDep,
) -> Agent:
    require_admin(request)
    agent = fetch_or_404(session, Agent, agent_id)
    destination = None
    if payload.destination_mode == "direct":
        destination = fetch_or_404(session, Experiment, payload.destination_experiment_id)
        if destination.project.system_key:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Direct destination cannot be a system inbox")
    if payload.enabled is not None:
        agent.enabled = payload.enabled
        agent.status = "offline" if payload.enabled else "disabled"
    if payload.destination_mode == "inbox":
        ensure_agent_inbox(session, storage, agent)
    elif payload.destination_mode == "direct":
        agent.destination_mode = "direct"
        agent.destination_experiment_id = destination.id
        session.commit()
        session.refresh(agent)
    else:
        session.commit()
        session.refresh(agent)
    return agent


@platform_router.post("/agents/{agent_id}/rotate-token", tags=["agents"])
async def rotate_agent_token(
    agent_id: str,
    request: Request,
    session: SessionDep,
) -> dict:
    require_admin(request)
    agent = fetch_or_404(session, Agent, agent_id)
    raw = issue_secret("agt")
    agent.token_prefix = raw[:12]
    agent.token_hash = hash_secret(raw)
    session.commit()
    session.refresh(agent)
    return {**AgentRead.model_validate(agent).model_dump(), "token": raw}


@platform_router.get("/instruments/{instrument_id}/agent-status", tags=["agents"])
async def instrument_agent_status(instrument_id: str, session: SessionDep) -> dict:
    instrument = fetch_or_404(session, Instrument, instrument_id)
    agents = [
        agent
        for agent in session.scalars(select(Agent).where(Agent.enabled.is_(True)))
        if agent.metadata_json.get("instrument_id") == instrument_id
    ]
    return {
        "instrument_id": instrument.id,
        "status": "online" if any(agent.status == "online" for agent in agents) else "offline",
        "agents": [AgentRead.model_validate(agent).model_dump() for agent in agents],
    }


@platform_router.post("/agents/{agent_id}/heartbeat", response_model=AgentRead, tags=["agents"])
async def heartbeat_agent(
    agent_id: str, payload: AgentHeartbeat, request: Request, session: SessionDep
) -> Agent:
    principal = require_agent(request)
    if principal.agent_id != agent_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Agent credential does not match path")
    agent = fetch_or_404(session, Agent, agent_id)
    agent.status = payload.status
    agent.capacity = payload.capacity
    agent.metadata_json = {**agent.metadata_json, **payload.metadata_json}
    agent.last_seen_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(agent)
    return agent


@platform_router.post("/upload-sessions", status_code=status.HTTP_201_CREATED, tags=["agents"])
async def create_upload_session(
    payload: UploadSessionCreate,
    request: Request,
    session: SessionDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    storage: StorageDep,
) -> dict:
    principal = require_agent(request)
    existing_session = session.scalar(
        select(UploadSession).where(
            UploadSession.agent_id == principal.agent_id,
            UploadSession.idempotency_key == idempotency_key,
        )
    )
    if existing_session:
        return upload_session_view(existing_session)
    agent = fetch_or_404(session, Agent, principal.agent_id)
    run = resolve_upload_run(session, payload, agent, storage)
    bundle_digest = bundle_manifest_digest(payload.bundle_manifest.model_dump()) if payload.bundle_manifest else None
    supplied_digest = payload.sha256.lower() if payload.sha256 else bundle_digest
    if supplied_digest:
        existing_artifact = session.scalar(
            select(Artifact).where(
                Artifact.sha256 == supplied_digest,
                Artifact.state == ArtifactState.READY,
            )
        )
        if existing_artifact:
            target_artifact = existing_artifact
            if existing_artifact.run_id != run.id:
                target_artifact = Artifact(
                    run_id=run.id,
                    parent_artifact_id=None,
                    recipe_id=None,
                    role=payload.role,
                    state=ArtifactState.READY,
                    format=payload.format,
                    original_filename=payload.filename,
                    storage_key=existing_artifact.storage_key,
                    byte_size=existing_artifact.byte_size,
                    sha256=existing_artifact.sha256,
                    bundle_manifest=existing_artifact.bundle_manifest,
                    metadata_json=payload.metadata_json,
                    immutable=True,
                )
                session.add(target_artifact)
                session.flush()
                materializer = LibraryMaterializer(storage)
                materializer.materialize_artifact(target_artifact)
                materializer.write_catalog(session)
            elif not target_artifact.library_path:
                materializer = LibraryMaterializer(storage)
                materializer.materialize_artifact(target_artifact)
                materializer.write_catalog(session)
            completed = UploadSession(
                agent_id=principal.agent_id,
                run_id=run.id,
                filename=payload.filename,
                format=payload.format,
                role=payload.role,
                total_size=payload.total_size or bundle_total_size(payload.bundle_manifest.model_dump()),
                expected_sha256=supplied_digest,
                offset=payload.total_size or bundle_total_size(payload.bundle_manifest.model_dump()),
                state=UploadState.COMPLETED,
                artifact_id=target_artifact.id,
                idempotency_key=idempotency_key,
                metadata_json=payload.metadata_json,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=get_settings().upload_session_hours),
            )
            session.add(completed)
            session.commit()
            session.refresh(completed)
            if target_artifact.id != existing_artifact.id:
                schedule_source_pipeline(session, target_artifact)
            return upload_session_view(completed)
    upload = UploadSession(
        agent_id=principal.agent_id,
        run_id=run.id,
        filename=payload.filename,
        format=payload.format,
        role=payload.role,
        total_size=payload.total_size or (
            bundle_total_size(payload.bundle_manifest.model_dump()) if payload.bundle_manifest else None
        ),
        expected_sha256=supplied_digest,
        bundle_manifest=payload.bundle_manifest.model_dump() if payload.bundle_manifest else None,
        idempotency_key=idempotency_key,
        metadata_json=payload.metadata_json,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=get_settings().upload_session_hours),
    )
    session.add(upload)
    session.flush()
    upload_root = storage.staging / "uploads" / upload.id
    upload_root.mkdir(parents=True, exist_ok=True)
    if payload.bundle_manifest:
        root = upload_root / "bundle" / payload.bundle_manifest.root_name
        root.mkdir(parents=True, exist_ok=True)
        upload.temporary_path = str(root)
        for file_info in payload.bundle_manifest.files:
            relative = safe_relative_path(file_info.path)
            temporary = root / relative
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.touch()
            session.add(
                UploadPart(
                    upload_session_id=upload.id,
                    relative_path=relative.as_posix(),
                    total_size=file_info.size,
                    expected_sha256=file_info.sha256.lower(),
                    temporary_path=str(temporary),
                )
            )
    else:
        temporary = upload_root / "payload"
        temporary.touch()
        upload.temporary_path = str(temporary)
    session.commit()
    session.refresh(upload)
    return upload_session_view(upload)


@platform_router.get("/upload-sessions/{upload_id}", tags=["agents"])
async def get_upload_session(upload_id: str, request: Request, session: SessionDep) -> dict:
    principal = require_agent(request)
    upload = owned_upload(session, upload_id, principal)
    return upload_session_view(upload)


@platform_router.patch("/upload-sessions/{upload_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["agents"])
async def upload_file_chunk(
    upload_id: str,
    request: Request,
    session: SessionDep,
    upload_offset: Annotated[int, Header(alias="Upload-Offset")],
) -> Response:
    principal = require_agent(request)
    upload = owned_upload(session, upload_id, principal)
    if upload.bundle_manifest:
        raise HTTPException(status.HTTP_409_CONFLICT, "Bundle uploads require the per-file endpoint")
    await append_request_body(request, Path(upload.temporary_path or ""), upload, upload_offset, upload.total_size or 0)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Upload-Offset": str(upload.offset)})


@platform_router.patch(
    "/upload-sessions/{upload_id}/files/{relative_path:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["agents"],
)
async def upload_bundle_chunk(
    upload_id: str,
    relative_path: str,
    request: Request,
    session: SessionDep,
    upload_offset: Annotated[int, Header(alias="Upload-Offset")],
) -> Response:
    principal = require_agent(request)
    upload = owned_upload(session, upload_id, principal)
    normalized = safe_relative_path(relative_path).as_posix()
    part = session.scalar(
        select(UploadPart).where(
            UploadPart.upload_session_id == upload.id,
            UploadPart.relative_path == normalized,
        )
    )
    if part is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload part not found")
    await append_request_body(request, Path(part.temporary_path), part, upload_offset, part.total_size)
    upload.offset = sum(item.offset for item in upload.parts)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Upload-Offset": str(part.offset)})


@platform_router.post("/upload-sessions/{upload_id}/complete", tags=["agents"])
async def complete_upload_session(
    upload_id: str,
    request: Request,
    session: SessionDep,
    storage: StorageDep,
) -> dict:
    principal = require_agent(request)
    upload = owned_upload(session, upload_id, principal)
    if upload.state == UploadState.COMPLETED:
        artifact = fetch_or_404(session, Artifact, upload.artifact_id)
        return {
            "upload": upload_session_view(upload),
            "artifact": ArtifactRead.model_validate(artifact).model_dump(),
        }
    upload.state = UploadState.VERIFYING
    session.commit()
    try:
        if upload.bundle_manifest:
            for part in upload.parts:
                digest, size = hash_file(Path(part.temporary_path))
                if size != part.total_size or not secrets.compare_digest(digest, part.expected_sha256):
                    raise ValueError(f"Bundle file checksum or size mismatch: {part.relative_path}")
            stored = storage.ingest_path(Path(upload.temporary_path or ""))
        else:
            if upload.offset != upload.total_size:
                raise ValueError("Upload is incomplete")
            digest, size = hash_file(Path(upload.temporary_path or ""))
            if size != upload.total_size or not secrets.compare_digest(digest, upload.expected_sha256 or ""):
                raise ValueError("Uploaded file checksum or size does not match")
            with Path(upload.temporary_path or "").open("rb") as handle:
                stored = storage.ingest_stream(handle)
        artifact = create_artifact_record(
            session,
            storage=storage,
            run_id=upload.run_id,
            stored=stored,
            filename=upload.filename,
            role=upload.role,
            artifact_format=upload.format,
            parent_artifact_id=None,
            recipe_id=None,
            metadata_json=upload.metadata_json,
        )
        upload.artifact_id = artifact.id
        upload.state = UploadState.COMPLETED
        upload.offset = stored.byte_size
        session.commit()
        return {
            "upload": upload_session_view(upload),
            "artifact": ArtifactRead.model_validate(artifact).model_dump(),
        }
    except ValueError as error:
        upload.state = UploadState.FAILED
        upload.error = str(error)
        session.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error


@platform_router.get("/events/outbox", tags=["events"])
async def list_outbox_events(session: SessionDep, after: datetime | None = None, limit: int = 100) -> list[dict]:
    query = select(EventOutbox).order_by(EventOutbox.created_at, EventOutbox.id)
    if after:
        query = query.where(EventOutbox.created_at > after)
    return [
        {
            "id": event.id,
            "topic": event.topic,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "payload": event.payload,
            "dedupe_key": event.dedupe_key,
            "created_at": event.created_at,
            "published_at": event.published_at,
        }
        for event in session.scalars(query.limit(min(limit, 500)))
    ]


@platform_router.get("/events/outbox/status", tags=["events"])
async def outbox_status(session: SessionDep) -> dict:
    pending = session.scalar(select(func.count(EventOutbox.id)).where(EventOutbox.published_at.is_(None))) or 0
    return {"pending": pending, "status": "pending" if pending else "idle"}


@platform_router.get("/audit-log/status", tags=["auth"])
async def audit_status(request: Request, session: SessionDep) -> dict:
    require_admin(request)
    latest = session.scalar(select(AuditLog).order_by(AuditLog.created_at.desc()))
    return {
        "entries": session.scalar(select(func.count(AuditLog.id))) or 0,
        "latest_at": latest.created_at if latest else None,
        "status": "healthy",
    }


@platform_router.get("/webhooks", response_model=list[WebhookRead], tags=["webhooks"])
async def list_webhooks(request: Request, session: SessionDep) -> list[WebhookDestination]:
    require_admin(request)
    return list(session.scalars(select(WebhookDestination).order_by(WebhookDestination.name)))


@platform_router.post("/webhooks", status_code=status.HTTP_201_CREATED, tags=["webhooks"])
async def create_webhook(payload: WebhookCreate, request: Request, session: SessionDep) -> dict:
    require_admin(request)
    destination = WebhookDestination(
        **payload.model_dump(),
        signing_secret_hash="pending",
        signing_secret_salt=secrets.token_hex(16),
    )
    session.add(destination)
    session.flush()
    secret = derive_webhook_secret(destination)
    destination.signing_secret_hash = hash_secret(secret)
    session.commit()
    session.refresh(destination)
    return {**WebhookRead.model_validate(destination).model_dump(), "signing_secret": secret}


@platform_router.patch("/webhooks/{webhook_id}", tags=["webhooks"])
async def update_webhook(
    webhook_id: str, payload: WebhookUpdate, request: Request, session: SessionDep
) -> dict:
    require_admin(request)
    destination = fetch_or_404(session, WebhookDestination, webhook_id)
    values = payload.model_dump(exclude_unset=True)
    rotate = values.pop("rotate_secret", False)
    for field, value in values.items():
        setattr(destination, field, value)
    response = WebhookRead.model_validate(destination).model_dump()
    if rotate:
        destination.signing_secret_salt = secrets.token_hex(16)
        destination.signing_secret_version += 1
        secret = derive_webhook_secret(destination)
        destination.signing_secret_hash = hash_secret(secret)
        response["signing_secret"] = secret
    session.commit()
    session.refresh(destination)
    return response


@platform_router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["webhooks"])
async def delete_webhook(webhook_id: str, request: Request, session: SessionDep) -> Response:
    require_admin(request)
    destination = fetch_or_404(session, WebhookDestination, webhook_id)
    session.delete(destination)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@platform_router.get(
    "/webhook-deliveries", response_model=list[WebhookDeliveryRead], tags=["webhooks"]
)
async def list_webhook_deliveries(
    request: Request, session: SessionDep, status_filter: str | None = None, limit: int = 100
) -> list[WebhookDelivery]:
    principal: Principal = request.state.principal
    if not principal.allows("jobs:read") and not principal.allows("admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Webhook delivery worker access required")
    query = select(WebhookDelivery).order_by(WebhookDelivery.created_at, WebhookDelivery.id)
    if status_filter:
        query = query.where(WebhookDelivery.status == status_filter)
    if status_filter in {"pending", "retry"}:
        now = datetime.now(timezone.utc)
        query = query.where(
            or_(WebhookDelivery.next_attempt_at.is_(None), WebhookDelivery.next_attempt_at <= now)
        )
    return list(session.scalars(query.limit(min(limit, 500))))


@platform_router.post("/webhook-deliveries/{delivery_id}/claim", tags=["webhooks"])
async def claim_webhook_delivery(delivery_id: str, request: Request, session: SessionDep) -> dict:
    principal: Principal = request.state.principal
    if not principal.allows("jobs:write") and not principal.allows("admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Webhook delivery worker access required")
    now = datetime.now(timezone.utc)
    worker_id = request.headers.get("X-Spectarr-Worker-Id") or "anonymous-webhook-worker"
    lease_expires_at = now + timedelta(seconds=max(30, get_settings().job_lease_seconds))
    result = session.execute(
        update(WebhookDelivery)
        .where(
            WebhookDelivery.id == delivery_id,
            or_(
                WebhookDelivery.status == "pending",
                and_(
                    WebhookDelivery.status == "retry",
                    or_(
                        WebhookDelivery.next_attempt_at.is_(None),
                        WebhookDelivery.next_attempt_at <= now,
                    ),
                ),
                and_(
                    WebhookDelivery.status == "delivering",
                    WebhookDelivery.lease_expires_at < now,
                ),
            ),
        )
        .values(
            status="delivering",
            attempts=WebhookDelivery.attempts + 1,
            worker_id=worker_id,
            lease_expires_at=lease_expires_at,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        fetch_or_404(session, WebhookDelivery, delivery_id)
        raise HTTPException(status.HTTP_409_CONFLICT, "Webhook delivery is not claimable")
    session.commit()
    delivery = fetch_or_404(session, WebhookDelivery, delivery_id)
    event_body = {
        "id": delivery.event.id,
        "topic": delivery.event.topic,
        "aggregate_type": delivery.event.aggregate_type,
        "aggregate_id": delivery.event.aggregate_id,
        "payload": delivery.event.payload,
        "created_at": delivery.event.created_at.isoformat(),
    }
    canonical_body = json.dumps(event_body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {
        "id": delivery.id,
        "url": delivery.destination.url,
        "event": event_body,
        "body": canonical_body,
        "signing_secret": derive_webhook_secret(delivery.destination),
        "attempt": delivery.attempts,
        "lease_expires_at": delivery.lease_expires_at,
        "signature": {
            "timestamp_header": "X-Spectarr-Timestamp",
            "signature_header": "X-Spectarr-Signature",
            "format": "t=<unix>,v1=<hmac_sha256>",
            "signed_payload": "<unix_timestamp>.<exact_body_bytes>",
        },
    }


@platform_router.patch("/webhook-deliveries/{delivery_id}", response_model=WebhookDeliveryRead, tags=["webhooks"])
async def update_webhook_delivery(
    delivery_id: str, payload: dict, request: Request, session: SessionDep
) -> WebhookDelivery:
    principal: Principal = request.state.principal
    if not principal.allows("jobs:write") and not principal.allows("admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Webhook delivery worker access required")
    delivery = fetch_or_404(session, WebhookDelivery, delivery_id)
    worker_id = request.headers.get("X-Spectarr-Worker-Id") or "anonymous-webhook-worker"
    if delivery.status != "delivering" or delivery.worker_id != worker_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Webhook delivery is not leased to this worker")
    next_status = payload.get("status")
    if next_status not in {"delivered", "retry", "failed"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid delivery status")
    delivery.status = next_status
    delivery.response_status = payload.get("response_status")
    delivery.last_error = payload.get("error")
    delivery.lease_expires_at = None
    if next_status == "delivered":
        delivery.delivered_at = datetime.now(timezone.utc)
    elif next_status == "retry":
        delay_seconds = min(3600, 2 ** min(delivery.attempts, 10) * 5)
        delivery.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
    session.commit()
    session.refresh(delivery)
    remaining = session.scalar(
        select(func.count(WebhookDelivery.id)).where(
            WebhookDelivery.event_id == delivery.event_id,
            WebhookDelivery.status != "delivered",
        )
    )
    if not remaining:
        delivery.event.published_at = datetime.now(timezone.utc)
        session.commit()
    return delivery


def normalized_run_metadata(payload: dict) -> dict:
    qc_summary = payload.get("qc_summary") if isinstance(payload.get("qc_summary"), dict) else {}
    merged = {**qc_summary, **payload}
    aliases = {
        "spectra_count": ("spectra_count", "spectrum_count"),
        "ms2_count": ("ms2_count",),
        "duration_minutes": ("duration_minutes",),
    }
    normalized = {
        target: merged[source]
        for target, sources in aliases.items()
        for source in sources
        if source in merged
    }
    levels = qc_summary.get("spectra_by_ms_level", {})
    if "ms2_count" not in normalized and isinstance(levels, dict):
        normalized["ms2_count"] = levels.get("2", levels.get(2))
    if "duration_minutes" not in normalized and qc_summary.get("acquisition_duration_seconds") is not None:
        normalized["duration_minutes"] = qc_summary["acquisition_duration_seconds"] / 60
    return {key: value for key, value in normalized.items() if value is not None}


def bundle_total_size(manifest: dict | None) -> int:
    return sum(item["size"] for item in (manifest or {}).get("files", []))


def bundle_manifest_digest(manifest: dict) -> str:
    canonical_manifest = {
        "version": 1,
        "root_name": manifest["root_name"],
        "files": sorted(
            [
                {"path": item["path"], "size": item["size"], "sha256": item["sha256"].lower()}
                for item in manifest["files"]
            ],
            key=lambda item: item["path"],
        ),
        "byte_size": bundle_total_size(manifest),
    }
    encoded = json.dumps(canonical_manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def derive_webhook_secret(destination: WebhookDestination) -> str:
    material = f"{destination.id}:{destination.signing_secret_salt}:{destination.signing_secret_version}".encode()
    digest = hmac.new(get_settings().secret_key.encode(), material, hashlib.sha256).hexdigest()
    return f"whsec_{digest}"


def resolve_upload_run(
    session: Session,
    payload: UploadSessionCreate,
    agent: Agent,
    storage: StorageDep,
) -> Run:
    if payload.run_id:
        return fetch_or_404(session, Run, payload.run_id)
    assert payload.run is not None
    values = payload.run.model_dump()
    requested_experiment_id = values.pop("experiment_id")
    if requested_experiment_id:
        destination = fetch_or_404(session, Experiment, requested_experiment_id)
        assignment_status = "assigned"
    else:
        if not agent.destination_experiment_id:
            ensure_agent_inbox(session, storage, agent)
        destination = fetch_or_404(session, Experiment, agent.destination_experiment_id)
        assignment_status = "needs_assignment" if agent.destination_mode == "inbox" else "assigned"
    if values.get("sample_id"):
        sample = fetch_or_404(session, Sample, values["sample_id"])
        if sample.experiment_id != destination.id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Sample belongs to a different experiment")
    if not values.get("instrument_id") and agent.metadata_json.get("instrument_id"):
        values["instrument_id"] = agent.metadata_json["instrument_id"]
    values["metadata_json"] = {
        **values.get("metadata_json", {}),
        "intake_agent_id": agent.id,
    }
    run = Run(
        experiment_id=destination.id,
        assignment_status=assignment_status,
        **values,
    )
    session.add(run)
    session.flush()
    if run.sample_id:
        session.add(RunSample(run=run, sample_id=run.sample_id, position=0))
        session.flush()
    return run


def ensure_agent_inbox(session: Session, storage: StorageDep, agent: Agent) -> Experiment:
    experiment = session.scalar(select(Experiment).where(Experiment.intake_agent_id == agent.id))
    if experiment is None:
        project = session.scalar(select(Project).where(Project.system_key == "instrument_inbox"))
        if project is None:
            name = "Instrument Inbox"
            if session.scalar(select(Project).where(Project.name == name)):
                name = "Instrument Inbox (System)"
            project = Project(
                name=name,
                description="Automatic intake for instrument agent uploads awaiting assignment.",
                system_key="instrument_inbox",
            )
            session.add(project)
            session.flush()
        name = f"{agent.name} Intake"
        if session.scalar(select(Experiment).where(Experiment.project_id == project.id, Experiment.name == name)):
            name = f"{name} {agent.id[:8]}"
        experiment = Experiment(
            project_id=project.id,
            name=name,
            description=f"Automatic uploads received from {agent.name}.",
            intake_agent_id=agent.id,
        )
        session.add(experiment)
        session.flush()
    agent.destination_mode = "inbox"
    agent.destination_experiment_id = experiment.id
    session.commit()
    session.refresh(agent)
    materializer = LibraryMaterializer(storage)
    materializer.write_project_manifest(experiment.project)
    materializer.write_catalog(session)
    return experiment


def safe_relative_path(raw: str) -> PurePosixPath:
    if "\\" in raw:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Bundle paths must use forward slashes")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid bundle relative path")
    return path


def owned_upload(session: Session, upload_id: str, principal: Principal) -> UploadSession:
    upload = fetch_or_404(session, UploadSession, upload_id)
    if upload.agent_id != principal.agent_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Upload session belongs to a different agent")
    comparison_now = datetime.now(upload.expires_at.tzinfo) if upload.expires_at.tzinfo else datetime.now()
    if upload.expires_at <= comparison_now and upload.state == UploadState.OPEN:
        upload.state = UploadState.EXPIRED
        session.commit()
        raise HTTPException(status.HTTP_410_GONE, "Upload session has expired")
    if upload.state not in {UploadState.OPEN, UploadState.COMPLETED}:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Upload session is {upload.state}")
    return upload


async def append_request_body(
    request: Request,
    path: Path,
    record: UploadSession | UploadPart,
    supplied_offset: int,
    total_size: int,
) -> None:
    if supplied_offset != record.offset:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Upload offset does not match",
            headers={"Upload-Offset": str(record.offset)},
        )
    written = 0
    with path.open("ab") as handle:
        async for chunk in request.stream():
            if record.offset + written + len(chunk) > total_size:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Chunk exceeds declared upload size")
            handle.write(chunk)
            written += len(chunk)
    record.offset += written


def upload_session_view(upload: UploadSession) -> dict:
    return {
        "id": upload.id,
        "agent_id": upload.agent_id,
        "run_id": upload.run_id,
        "filename": upload.filename,
        "format": upload.format,
        "role": upload.role,
        "total_size": upload.total_size,
        "expected_sha256": upload.expected_sha256,
        "offset": upload.offset,
        "state": upload.state,
        "artifact_id": upload.artifact_id,
        "expires_at": upload.expires_at,
        "files": [
            {
                "path": part.relative_path,
                "size": part.total_size,
                "offset": part.offset,
                "state": "complete" if part.offset == part.total_size else "open",
            }
            for part in sorted(upload.parts, key=lambda item: item.relative_path)
        ],
    }
