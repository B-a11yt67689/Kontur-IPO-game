"""Commands and snapshots for the persisted first act."""

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.starting_bonus import BonusCode


class EmptyActOneCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartingBonusSnapshot(BaseModel):
    roll: int
    code: BonusCode
    description: str
    available: bool


class ActOneSnapshot(BaseModel):
    session_id: UUID
    act_number: Literal[1] = 1
    status: Literal["act_one"] = "act_one"
    started_at: AwareDatetime
    balance: Decimal
    clients: int
    action_points: int
    hiring_level: int
    hiring_capacity: int
    hired_development_team_grades: list[str]
    experiments_per_development_team: int
    starting_bonus: StartingBonusSnapshot


NonnegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class PnlPreviewInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    average_price: NonnegativeDecimal
    orders_per_client: NonnegativeDecimal
    clients: Annotated[int, Field(strict=True, ge=0)]
    advertising_budget: NonnegativeDecimal
    marketing_payroll: NonnegativeDecimal
    development_payroll: NonnegativeDecimal
    support_payroll: NonnegativeDecimal


class PnlPreviewResult(BaseModel):
    model_config = ConfigDict(from_attributes=True, allow_inf_nan=False)

    arpc: Decimal
    gross_revenue: Decimal
    unit_cogs: Decimal
    total_cogs: Decimal
    business_costs: Decimal
    operating_costs: Decimal
    result: Decimal
    balance_after_result: Decimal
