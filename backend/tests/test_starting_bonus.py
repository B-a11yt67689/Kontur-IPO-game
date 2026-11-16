from dataclasses import FrozenInstanceError, fields, is_dataclass
from decimal import Decimal

import pytest

from app.act_one_baseline import build_act_one_baseline
from app.starting_bonus import StartingBonus, resolve_starting_roll


@pytest.mark.parametrize(
    "roll, code, description, action_points, clients, deferred",
    [
        (1, "action_points", "+10 очков действий в начале первого акта", 10, 0, False),
        (2, "virality", "+1 шаг виральности один раз", 0, 0, True),
        (3, "retention", "+1 шаг удержания один раз", 0, 0, True),
        (4, "orders_per_client", "+1 шаг количества покупок один раз", 0, 0, True),
        (5, "average_price", "+1 шаг среднего чека один раз", 0, 0, True),
        (6, "payment_conversion", "+1 шаг конверсии в оплату один раз", 0, 0, True),
        (7, "experiment", "+1 эксперимент один раз", 0, 0, True),
        (8, "infrastructure", "+2 балла инфраструктуры один раз", 0, 0, True),
        (9, "clients", "+10 клиентов на старте", 0, 10, False),
        (
            10,
            "interest_free_loan",
            "Выплата кредита без процентов один раз",
            0,
            0,
            True,
        ),
    ],
)
def test_starting_roll_matches_agreed_bonus(
    roll, code, description, action_points, clients, deferred
):
    bonus = resolve_starting_roll(roll)

    assert isinstance(bonus, StartingBonus)
    assert bonus.roll == roll
    assert bonus.code == code
    assert bonus.description == description
    assert bonus.action_point_bonus == action_points
    assert bonus.client_bonus == clients
    assert bonus.deferred is deferred


@pytest.mark.parametrize("roll", [0, -1, 11, 10**100, -(10**100)])
def test_starting_roll_rejects_out_of_range_integers(roll):
    with pytest.raises(ValueError, match="between 1 and 10"):
        resolve_starting_roll(roll)


@pytest.mark.parametrize(
    "roll", [True, False, 1.0, 10.0, "1", None, Decimal("1"), [], {}, b"1", 1j]
)
def test_starting_roll_rejects_non_integers_and_booleans(roll):
    with pytest.raises(TypeError, match="must be an int"):
        resolve_starting_roll(roll)


@pytest.mark.parametrize("field", fields(StartingBonus))
def test_starting_bonus_is_an_immutable_dataclass(field):
    bonus = resolve_starting_roll(1)

    assert is_dataclass(bonus)
    with pytest.raises(FrozenInstanceError):
        setattr(bonus, field.name, getattr(bonus, field.name))


@pytest.mark.parametrize("roll", range(1, 11))
def test_resolving_starting_roll_is_repeatable_and_preserves_baseline(roll):
    baseline = build_act_one_baseline()

    first = resolve_starting_roll(roll)
    second = resolve_starting_roll(roll)

    assert first == second
    assert baseline == build_act_one_baseline()
    assert baseline.available_action_points == 130
    assert baseline.clients == 60
    assert baseline.balance == Decimal("675.00")
