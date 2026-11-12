"""Build the first act's baseline before applying the product bonus.

Sources: IPO-014/brief.md and decision C; «Разбор правил IPO» sections
«Старт», «Очки действий» and «Найм»; «Holst — тексты и таблицы» sections
«3 Активы», «Подсчет очков» and «1 Развитие продукта».
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class ActOneBaseline:
    balance: Decimal
    clients: int
    base_action_points: int
    starting_action_point_additions: int
    hired_development_team_grades: tuple[Literal["junior"], ...]
    hiring_level: int
    hiring_capacity: int
    experiments_per_development_team: int

    @property
    def available_action_points(self) -> int:
        return self.base_action_points + self.starting_action_point_additions


def build_act_one_baseline() -> ActOneBaseline:
    """Return the fixed starting state agreed in decision C."""
    return ActOneBaseline(
        balance=Decimal("675.00"),  # Holst: «3 Активы», dollars.
        clients=60,  # Holst: «3 Активы».
        base_action_points=130,  # Holst: «Подсчет очков».
        starting_action_point_additions=0,  # Decision C: team points start next act.
        hired_development_team_grades=("junior", "junior"),  # Fixed roster in C.
        hiring_level=0,  # Decision C: before any purchase.
        hiring_capacity=2,  # Holst: starting step of «Покупка команд».
        experiments_per_development_team=3,  # Holst: «Процессы и технологии».
    )
