"""CFO P&L preview, safe input validation and persistence isolation."""

import json
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.database import create_database_engine
from app.main import app
from app.models import ActOneState, GameSession
from tests.test_act_one_hiring import hiring_path
from tests.test_act_one_snapshot import snapshot_path
from tests.test_act_one_start import client as client
from tests.test_act_one_start import engine as engine
from tests.test_act_one_start import prepared_team as prepared_team
from tests.test_act_one_start import start_path, stored_state
from tests.test_captain import create_session, join_session
from tests.test_roles import participant_headers

FINANCIAL_FIELDS = (
    "average_price",
    "orders_per_client",
    "advertising_budget",
    "marketing_payroll",
    "development_payroll",
    "support_payroll",
)


def preview_path(session_id):
    return f"/sessions/{session_id}/acts/1/pnl/preview"


@pytest.fixture
def payload():
    return {
        "average_price": "1.9",
        "orders_per_client": "1.3",
        "clients": 42,
        "advertising_budget": "0",
        "marketing_payroll": "10",
        "development_payroll": "90",
        "support_payroll": "60",
    }


@pytest.fixture
def validation_client(monkeypatch):
    database = Mock(side_effect=AssertionError("Invalid preview accessed DB"))
    monkeypatch.setattr("app.act_one.create_database_engine", database)
    with TestClient(app, headers={"Authorization": "Bearer validation-only"}) as http:
        yield http
    database.assert_not_called()


def test_preview_matches_excel_and_is_repeatable_without_writing_state(
    client, engine, prepared_team, monkeypatch, payload
):
    session_id, members = prepared_team
    started = client.post(start_path(session_id))
    assert started.status_code == 200
    before = stored_state(engine, session_id)
    random = Mock(side_effect=AssertionError("Preview rolled RNG"))
    monkeypatch.setattr("app.act_one.secrets.randbelow", random)
    headers = participant_headers(members[3])
    expected = {
        "arpc": "2.47",
        "gross_revenue": "103.74",
        "unit_cogs": "1",
        "total_cogs": "54.6",
        "business_costs": "30",
        "operating_costs": "190",
        "result": "-140.86",
        "balance_after_result": "534.14",
    }
    first = client.post(preview_path(session_id), headers=headers, json=payload)
    assert first.status_code == 200
    assert first.json() == expected
    assert all(isinstance(value, str) for value in first.json().values())
    assert "credential" not in first.text
    assert "digest" not in first.text
    repeat = client.post(preview_path(session_id), headers=headers, json=payload)
    assert repeat.status_code == 200
    assert repeat.json() == expected
    assert stored_state(engine, session_id) == before
    snapshot = client.get(snapshot_path(session_id), headers=headers)
    assert snapshot.status_code == 200
    assert snapshot.json() == started.json()
    random.assert_not_called()


def test_preview_accepts_json_numbers_and_uses_saved_balance(
    client, engine, prepared_team, payload
):
    session_id, members = prepared_team
    assert client.post(start_path(session_id)).status_code == 200
    with engine.begin() as connection:
        connection.execute(
            ActOneState.__table__.update()
            .where(ActOneState.session_id == UUID(session_id))
            .values(balance="500.123")
        )
    response = client.post(
        preview_path(session_id),
        headers=participant_headers(members[3]),
        json={
            **payload,
            "average_price": 1.9,
            "orders_per_client": 1.3,
            "advertising_budget": 0,
            "marketing_payroll": 10,
            "development_payroll": 90,
            "support_payroll": 60,
        },
    )
    assert response.status_code == 200
    assert response.json()["balance_after_result"] == "359.263"
    assert response.json()["unit_cogs"] == "1"
    assert response.json()["business_costs"] == "30"
    assert str(stored_state(engine, session_id)["balance"]) == "500.123"


def test_preview_authenticates_cfo_before_phase(client, engine, prepared_team, payload):
    session_id, members = prepared_team
    spectator = join_session(client, session_id, "Без роли")
    other_session = create_session(client)
    outsider = join_session(client, other_session, "Другая команда")
    path = preview_path(session_id)
    with TestClient(app) as anonymous:
        denied = [
            anonymous.post(path, json=payload),
            client.post(path, json=payload),
            anonymous.post(
                path, headers={"Authorization": "Bearer invalid"}, json=payload
            ),
            anonymous.post(path, headers=participant_headers(outsider), json=payload),
            anonymous.post(
                preview_path(str(uuid4())),
                headers=participant_headers(members[3]),
                json=payload,
            ),
        ]
    for response in denied:
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid credentials"}
        assert response.headers["WWW-Authenticate"] == "Bearer"
    for member in [*members[:3], spectator]:
        forbidden = client.post(path, headers=participant_headers(member), json=payload)
        assert forbidden.status_code == 403
        assert forbidden.json() == {"detail": "Required role is not assigned"}
    assert (
        client.post(
            path, headers=participant_headers(members[3]), json=payload
        ).status_code
        == 409
    )
    assert stored_state(engine, session_id) is None
    assert client.post(start_path(session_id)).status_code == 200
    assert (
        client.post(
            path, headers=participant_headers(members[3]), json=payload
        ).status_code
        == 200
    )
    assert (
        client.post(
            path, headers=participant_headers(members[2]), json=payload
        ).status_code
        == 403
    )


@pytest.mark.parametrize("phase", ["finished", "missing_state"])
def test_preview_rejects_wrong_phase_and_missing_state(
    client, engine, prepared_team, payload, phase
):
    session_id, members = prepared_team
    assert client.post(start_path(session_id)).status_code == 200
    with engine.begin() as connection:
        if phase == "finished":
            connection.execute(
                GameSession.__table__.update()
                .where(GameSession.id == UUID(session_id))
                .values(status=phase)
            )
        else:
            connection.execute(
                ActOneState.__table__.delete().where(
                    ActOneState.session_id == UUID(session_id)
                )
            )
    before = stored_state(engine, session_id)
    response = client.post(
        preview_path(session_id), headers=participant_headers(members[3]), json=payload
    )
    assert response.status_code == 409
    assert stored_state(engine, session_id) == before


def test_preview_uses_one_authorized_select_without_writes(
    client, migrated_database, prepared_team, payload, monkeypatch
):
    session_id, members = prepared_team
    assert client.post(start_path(session_id)).status_code == 200
    engine = create_database_engine(migrated_database)
    try:
        with engine.connect():
            pass
        statements = []
        event.listen(
            engine,
            "before_cursor_execute",
            lambda _connection, _cursor, statement, *_: statements.append(statement),
        )
        monkeypatch.setattr("app.act_one.create_database_engine", lambda: engine)
        response = client.post(
            preview_path(session_id),
            headers=participant_headers(members[3]),
            json=payload,
        )
        assert response.status_code == 200
        assert len(statements) == 1
        assert statements[0].lstrip().upper().startswith("SELECT ")
        assert "FOR UPDATE" not in statements[0].upper()
    finally:
        engine.dispose()


@pytest.mark.parametrize("field", FINANCIAL_FIELDS)
@pytest.mark.parametrize(
    "value", ["-0.01", "NaN", "Infinity", "-Infinity", True, None, "bad", [], {}]
)
def test_preview_validation_rejects_invalid_financial_values(
    validation_client, payload, field, value
):
    response = validation_client.post(
        preview_path(str(uuid4())), json={**payload, field: value}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid act one request"}


@pytest.mark.parametrize("clients", [True, False, 42.0, 42.5, "42", -1, None])
def test_preview_validation_requires_strict_nonnegative_clients(
    validation_client, payload, clients
):
    response = validation_client.post(
        preview_path(str(uuid4())), json={**payload, "clients": clients}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid act one request"}


@pytest.mark.parametrize("field", [*FINANCIAL_FIELDS, "clients"])
def test_preview_validation_requires_every_field(validation_client, payload, field):
    del payload[field]
    response = validation_client.post(preview_path(str(uuid4())), json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize("field", ["act_number", "balance_at_start", "balance", "roll"])
def test_preview_validation_forbids_server_fields(validation_client, payload, field):
    response = validation_client.post(
        preview_path(str(uuid4())), json={**payload, field: 1}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e10000"])
def test_preview_validation_handles_raw_nonfinite_json(
    validation_client, payload, number
):
    raw = json.dumps({**payload, "average_price": "RAW_NUMBER"}).replace(
        '"RAW_NUMBER"', number
    )
    response = validation_client.post(
        preview_path(str(uuid4())),
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid act one request"}


@pytest.mark.parametrize(
    "body", ["{", '{"average_price":', "private malformed sentinel", "null", "[]", "1"]
)
def test_preview_validation_handles_malformed_or_nonobject_json(
    validation_client, body
):
    response = validation_client.post(
        preview_path(str(uuid4())),
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid act one request"}
    assert "sentinel" not in response.text


def test_preview_decimal_overflow_is_safe_422(client, engine, prepared_team, payload):
    session_id, members = prepared_team
    assert client.post(start_path(session_id)).status_code == 200
    before = stored_state(engine, session_id)
    response = client.post(
        preview_path(session_id),
        headers=participant_headers(members[3]),
        json={**payload, "average_price": "1e999999", "orders_per_client": "1e999999"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "P&L inputs cannot be represented"}
    assert stored_state(engine, session_id) == before


@pytest.mark.parametrize("failure", ["connection", "query"])
def test_preview_database_failure_is_sanitized(
    client, engine, prepared_team, payload, monkeypatch, failure
):
    session_id, members = prepared_team
    assert client.post(start_path(session_id)).status_code == 200
    if failure == "connection":
        monkeypatch.setattr(
            "app.act_one.create_database_engine",
            Mock(
                side_effect=OperationalError(
                    "private SQL sentinel", {}, Exception("private DSN")
                )
            ),
        )
    else:

        def fail_query(*_):
            raise OperationalError("private SQL sentinel", {}, Exception("private DSN"))

        event.listen(engine, "before_cursor_execute", fail_query)
        monkeypatch.setattr("app.act_one.create_database_engine", lambda: engine)
    response = client.post(
        preview_path(session_id), headers=participant_headers(members[3]), json=payload
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Act one unavailable"}
    assert "sentinel" not in response.text


@pytest.mark.parametrize(
    "command, body", [("start", '{"roll": NaN}'), ("hiring", '{"cost": Infinity}')]
)
def test_empty_act_commands_sanitize_raw_nonfinite_extra_fields(
    client, engine, prepared_team, command, body
):
    session_id, members = prepared_team
    assert client.post(start_path(session_id)).status_code == 200
    before = stored_state(engine, session_id)
    path = start_path(session_id) if command == "start" else hiring_path(session_id)
    headers = {} if command == "start" else participant_headers(members[2])
    response = client.post(
        path, headers={**headers, "Content-Type": "application/json"}, content=body
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid act one request"}
    assert stored_state(engine, session_id) == before
