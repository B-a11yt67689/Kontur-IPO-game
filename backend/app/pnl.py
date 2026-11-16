"""Calculate P&L from the Excel sheets preserved in the IPO sources.

Sources: IPO-019/brief.md; «8 PnL» rows 5, 7–9, 14–16 and
«8 Итоги финансов после акта» row 2. No currency rounding is applied.
"""

from dataclasses import dataclass
from decimal import Decimal

_UNIT_COGS = tuple(
    Decimal(value) for value in ("1", "1.1", "1.2", "1.3", "1.35", "1.4", "1.45")
)
_BUSINESS_COSTS = tuple(
    Decimal(value) for value in ("30", "45", "60", "80", "100", "120", "140")
)


def _validate_decimal(name: str, value: Decimal, *, nonnegative: bool = True) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must be nonnegative")


@dataclass(frozen=True)
class PnlInputs:
    act_number: int
    average_price: Decimal
    orders_per_client: Decimal
    clients: int
    advertising_budget: Decimal
    marketing_payroll: Decimal
    development_payroll: Decimal
    support_payroll: Decimal
    balance_at_start: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.act_number, int) or isinstance(self.act_number, bool):
            raise TypeError("act_number must be an int")
        if not 1 <= self.act_number <= 7:
            raise ValueError("act_number must be between 1 and 7")
        if not isinstance(self.clients, int) or isinstance(self.clients, bool):
            raise TypeError("clients must be an int")
        if self.clients < 0:
            raise ValueError("clients must be nonnegative")
        for name in (
            "average_price",
            "orders_per_client",
            "advertising_budget",
            "marketing_payroll",
            "development_payroll",
            "support_payroll",
        ):
            _validate_decimal(name, getattr(self, name))
        _validate_decimal("balance_at_start", self.balance_at_start, nonnegative=False)


@dataclass(frozen=True)
class PnlResult:
    arpc: Decimal
    gross_revenue: Decimal
    unit_cogs: Decimal
    total_cogs: Decimal
    business_costs: Decimal
    operating_costs: Decimal
    result: Decimal
    balance_after_result: Decimal


def calculate_act_pnl(inputs: PnlInputs) -> PnlResult:
    """Return one act's P&L and balance before loans or repayments."""
    if not isinstance(inputs, PnlInputs):
        raise TypeError("inputs must be PnlInputs")
    unit_cogs = _UNIT_COGS[inputs.act_number - 1]
    business_costs = _BUSINESS_COSTS[inputs.act_number - 1]
    arpc = inputs.average_price * inputs.orders_per_client
    gross_revenue = arpc * inputs.clients
    total_cogs = inputs.orders_per_client * inputs.clients * unit_cogs
    operating_costs = (
        inputs.marketing_payroll
        + inputs.development_payroll
        + inputs.support_payroll
        + business_costs
    )
    result = gross_revenue - total_cogs - inputs.advertising_budget - operating_costs
    return PnlResult(
        arpc=arpc,
        gross_revenue=gross_revenue,
        unit_cogs=unit_cogs,
        total_cogs=total_cogs,
        business_costs=business_costs,
        operating_costs=operating_costs,
        result=result,
        balance_after_result=inputs.balance_at_start + result,
    )
