"""Resolve the agreed d10 starting bonus without changing game state.

Sources: IPO-015/brief.md and ACT-ONE-DECISIONS.md. Deferred bonuses describe
one-use rights; their activation belongs to later game actions.
"""

from dataclasses import dataclass
from typing import Literal

BonusCode = Literal[
    "action_points",
    "virality",
    "retention",
    "orders_per_client",
    "average_price",
    "payment_conversion",
    "experiment",
    "infrastructure",
    "clients",
    "interest_free_loan",
]


@dataclass(frozen=True)
class StartingBonus:
    roll: int
    code: BonusCode
    description: str
    action_point_bonus: int
    client_bonus: int
    deferred: bool


_BONUS_DETAILS: tuple[tuple[BonusCode, str], ...] = (
    ("action_points", "+10 очков действий в начале первого акта"),
    ("virality", "+1 шаг виральности один раз"),
    ("retention", "+1 шаг удержания один раз"),
    ("orders_per_client", "+1 шаг количества покупок один раз"),
    ("average_price", "+1 шаг среднего чека один раз"),
    ("payment_conversion", "+1 шаг конверсии в оплату один раз"),
    ("experiment", "+1 эксперимент один раз"),
    ("infrastructure", "+2 балла инфраструктуры один раз"),
    ("clients", "+10 клиентов на старте"),
    ("interest_free_loan", "Выплата кредита без процентов один раз"),
)


def resolve_starting_roll(roll: int) -> StartingBonus:
    """Return the deterministic bonus for an integer d10 roll from 1 to 10."""
    if not isinstance(roll, int) or isinstance(roll, bool):
        raise TypeError("roll must be an int")
    if not 1 <= roll <= 10:
        raise ValueError("roll must be between 1 and 10")
    code, description = _BONUS_DETAILS[roll - 1]
    return StartingBonus(
        roll=roll,
        code=code,
        description=description,
        action_point_bonus=10 if roll == 1 else 0,
        client_bonus=10 if roll == 9 else 0,
        deferred=roll not in (1, 9),
    )
