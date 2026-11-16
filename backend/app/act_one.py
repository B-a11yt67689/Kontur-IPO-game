"""Transactional first-act operations and construction of public snapshots."""

import hashlib
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import DecimalException
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Select, and_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.act_one_baseline import build_act_one_baseline
from app.act_one_schemas import (
    ActOneSnapshot,
    PnlPreviewInputs,
    PnlPreviewResult,
    StartingBonusSnapshot,
)
from app.auth import unauthorized
from app.database import create_database_engine, transaction
from app.models import ActOneState, GameSession, Participant, RoleAssignment
from app.pnl import PnlInputs, calculate_act_pnl
from app.schemas import RoleCode
from app.starting_bonus import resolve_starting_roll

FIRST_HIRING_UPGRADE_COST = 30


@contextmanager
def act_one_transaction() -> Iterator[Session]:
    """Own the engine and translate database failures, including failed commit."""
    try:
        engine = create_database_engine()
        try:
            with transaction(engine) as session:
                yield session
        finally:
            engine.dispose()
    except (SQLAlchemyError, ValueError):
        raise HTTPException(status_code=503, detail="Act one unavailable") from None


def locked_game(session: Session, session_id: UUID) -> GameSession:
    """Serialize changes to a session with preparation and other act actions."""
    game = session.scalar(
        select(GameSession).where(GameSession.id == session_id).with_for_update()
    )
    if game is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return game


def participant_digest(credential: str) -> str:
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


def require_session_role(
    session: Session, session_id: UUID, credential: str, role: RoleCode
) -> UUID:
    """Check membership and role without loading phase or mutable act state."""
    actor = session.execute(
        _participant_role_query(credential).where(Participant.session_id == session_id)
    ).one_or_none()
    if actor is None:
        raise unauthorized()
    _require_role(actor.role_code, role)
    return actor.id


def _require_role(actual_role: str | None, required_role: RoleCode) -> None:
    if actual_role != required_role:
        raise HTTPException(status_code=403, detail="Required role is not assigned")


def _participant_role_query(credential: str) -> Select:
    return (
        select(Participant.id, RoleAssignment.role_code)
        .select_from(Participant)
        .outerjoin(
            RoleAssignment,
            and_(
                RoleAssignment.session_id == Participant.session_id,
                RoleAssignment.participant_id == Participant.id,
            ),
        )
        .where(
            Participant.credential_digest == participant_digest(credential),
        )
    )


def build_act_one_snapshot(state: ActOneState) -> ActOneSnapshot:
    bonus = resolve_starting_roll(state.starting_roll)
    return ActOneSnapshot(
        session_id=state.session_id,
        started_at=state.started_at,
        balance=state.balance,
        clients=state.clients,
        action_points=state.action_points,
        hiring_level=state.hiring_level,
        hiring_capacity=state.hiring_capacity,
        hired_development_team_grades=list(state.hired_development_team_grades),
        experiments_per_development_team=state.experiments_per_development_team,
        starting_bonus=StartingBonusSnapshot(
            roll=bonus.roll,
            code=bonus.code,
            description=bonus.description,
            available=state.starting_bonus_available,
        ),
    )


def start_act_one(session_id: UUID) -> ActOneSnapshot:
    """Create the act once or return its current committed state on a retry."""
    with act_one_transaction() as session:
        game = locked_game(session, session_id)
        state = session.get(ActOneState, session_id)
        if game.status == "act_one":
            if state is None:
                raise HTTPException(status_code=409, detail="Act one state missing")
        elif game.status == "preparation":
            roles = set(
                session.scalars(
                    select(RoleAssignment.role_code).where(
                        RoleAssignment.session_id == session_id
                    )
                )
            )
            if (
                game.captain_participant_id is None
                or roles != {"cto", "cmo", "people", "cfo"}
                or state is not None
            ):
                raise HTTPException(status_code=409, detail="Session is not ready")
            baseline = build_act_one_baseline()
            bonus = resolve_starting_roll(secrets.randbelow(10) + 1)
            state = ActOneState(
                session_id=session_id,
                balance=baseline.balance,
                clients=baseline.clients + bonus.client_bonus,
                action_points=baseline.available_action_points
                + bonus.action_point_bonus,
                hiring_level=baseline.hiring_level,
                hiring_capacity=baseline.hiring_capacity,
                hired_development_team_grades=list(
                    baseline.hired_development_team_grades
                ),
                experiments_per_development_team=baseline.experiments_per_development_team,
                starting_roll=bonus.roll,
                starting_bonus_available=bonus.deferred,
            )
            session.add(state)
            game.status = "act_one"
            session.flush()
        else:
            raise HTTPException(status_code=409, detail="Session cannot start act one")
        response = build_act_one_snapshot(state)
    return response


def read_act_one(
    session_id: UUID, credential: str, *, required_role: RoleCode | None = None
) -> ActOneSnapshot:
    """Authorize membership and read the act from one PostgreSQL snapshot."""
    actor = (
        _participant_role_query(credential)
        .where(Participant.session_id == GameSession.id)
        .correlate(GameSession)
    )
    statement = (
        select(
            GameSession.status,
            ActOneState,
            actor.with_only_columns(RoleAssignment.role_code).scalar_subquery(),
        )
        .outerjoin(ActOneState, ActOneState.session_id == GameSession.id)
        .where(GameSession.id == session_id, actor.exists())
    )
    with act_one_transaction() as session:
        row = session.execute(statement).one_or_none()
        if row is None:
            raise unauthorized()
        status, state, role_code = row
        if required_role is not None:
            _require_role(role_code, required_role)
        if status != "act_one" or state is None:
            raise HTTPException(status_code=409, detail="Act one is not available")
        response = build_act_one_snapshot(state)
    return response


def preview_act_one_pnl(
    session_id: UUID, credential: str, inputs: PnlPreviewInputs
) -> PnlPreviewResult:
    """Calculate P&L without writing results or ending the current act."""
    snapshot = read_act_one(session_id, credential, required_role="cfo")
    try:
        result = calculate_act_pnl(
            PnlInputs(
                act_number=1,
                balance_at_start=snapshot.balance,
                **inputs.model_dump(),
            )
        )
    except DecimalException:
        raise HTTPException(
            status_code=422, detail="P&L inputs cannot be represented"
        ) from None
    return PnlPreviewResult.model_validate(result)


def upgrade_hiring(session_id: UUID, credential: str) -> ActOneSnapshot:
    """Buy capacity 2 -> 3 once; actual teams and other resources stay intact."""
    with act_one_transaction() as session:
        require_session_role(session, session_id, credential, "people")
        game = locked_game(session, session_id)
        if game.status != "act_one":
            raise HTTPException(status_code=409, detail="Act one is not available")
        # Load state only after waiting for the shared game lock, so a queued
        # request observes committed resources rather than a stale ORM object.
        state = session.get(ActOneState, session_id)
        if state is None:
            raise HTTPException(status_code=409, detail="Act one state missing")
        if state.hiring_level == 0:
            if state.action_points < FIRST_HIRING_UPGRADE_COST:
                raise HTTPException(status_code=409, detail="Not enough action points")
            state.action_points -= FIRST_HIRING_UPGRADE_COST
            state.hiring_level = 1
            state.hiring_capacity = 3
            session.flush()
        response = build_act_one_snapshot(state)
    return response
