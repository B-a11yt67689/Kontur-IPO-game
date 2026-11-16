from dataclasses import FrozenInstanceError, astuple, replace
from decimal import Decimal

import pytest

from app.pnl import PnlInputs, PnlResult, calculate_act_pnl

DECIMAL_FIELDS = (
    "average_price",
    "orders_per_client",
    "advertising_budget",
    "marketing_payroll",
    "development_payroll",
    "support_payroll",
    "balance_at_start",
)


@pytest.fixture
def inputs() -> PnlInputs:
    return PnlInputs(
        act_number=1,
        average_price=Decimal("1.9"),
        orders_per_client=Decimal("1.3"),
        clients=42,
        advertising_budget=Decimal("0"),
        marketing_payroll=Decimal("10"),
        development_payroll=Decimal("90"),
        support_payroll=Decimal("60"),
        balance_at_start=Decimal("675"),
    )


@pytest.mark.parametrize(
    ("act", "clients", "advertising", "marketing", "balance", "expected"),
    [
        (1, 42, "0", "10", "675", ("103.74", "54.60", "190", "-140.86", "534.14")),
        (
            2,
            105,
            "97.50",
            "20",
            "534.14",
            ("259.35", "150.15", "215", "-203.30", "330.84"),
        ),
        (
            3,
            140,
            "105.60",
            "30",
            "330.84",
            ("345.80", "218.40", "240", "-218.20", "112.64"),
        ),
    ],
)
def test_excel_examples(
    inputs, act, clients, advertising, marketing, balance, expected
):
    pnl = calculate_act_pnl(
        replace(
            inputs,
            act_number=act,
            clients=clients,
            advertising_budget=Decimal(advertising),
            marketing_payroll=Decimal(marketing),
            balance_at_start=Decimal(balance),
        )
    )

    assert pnl.arpc == Decimal("2.47")
    assert (
        pnl.gross_revenue,
        pnl.total_cogs,
        pnl.operating_costs,
        pnl.result,
        pnl.balance_after_result,
    ) == tuple(Decimal(value) for value in expected)
    assert all(isinstance(value, Decimal) for value in astuple(pnl))


@pytest.mark.parametrize(
    ("act", "unit_cogs", "business_costs", "total_cogs", "result"),
    [
        (1, "1", "30", "54.60", "-140.86"),
        (2, "1.1", "45", "60.06", "-161.32"),
        (3, "1.2", "60", "65.52", "-181.78"),
        (4, "1.3", "80", "70.98", "-207.24"),
        (5, "1.35", "100", "73.71", "-229.97"),
        (6, "1.4", "120", "76.44", "-252.70"),
        (7, "1.45", "140", "79.17", "-275.43"),
    ],
)
def test_server_constants_for_every_act(
    inputs, act, unit_cogs, business_costs, total_cogs, result
):
    pnl = calculate_act_pnl(replace(inputs, act_number=act))

    assert pnl.unit_cogs == Decimal(unit_cogs)
    assert pnl.business_costs == Decimal(business_costs)
    assert pnl.total_cogs == Decimal(total_cogs)
    assert pnl.result == Decimal(result)


def test_zero_clients_still_incur_supplied_payroll_and_business_costs(inputs):
    pnl = calculate_act_pnl(replace(inputs, clients=0))

    assert pnl.arpc == Decimal("2.47")
    assert pnl.gross_revenue == pnl.total_cogs == Decimal("0")
    assert pnl.operating_costs == Decimal("190")
    assert pnl.result == Decimal("-190")
    assert pnl.balance_after_result == Decimal("485")


def test_advertising_is_deducted_exactly_once_and_outside_operating_costs(inputs):
    without_advertising = calculate_act_pnl(inputs)
    with_advertising = calculate_act_pnl(
        replace(inputs, advertising_budget=Decimal("12.345"))
    )

    assert with_advertising.operating_costs == without_advertising.operating_costs
    assert with_advertising.result == without_advertising.result - Decimal("12.345")
    assert with_advertising.balance_after_result == Decimal("521.795")


def test_fractional_values_are_not_rounded_to_cents(inputs):
    pnl = calculate_act_pnl(
        replace(
            inputs,
            act_number=5,
            average_price=Decimal("1.2345"),
            orders_per_client=Decimal("1.3456"),
            clients=3,
            advertising_budget=Decimal("0.0007"),
            marketing_payroll=Decimal("0.0002"),
            development_payroll=Decimal("0.0003"),
            support_payroll=Decimal("0.0004"),
            balance_at_start=Decimal("-0.0001"),
        )
    )

    assert pnl == PnlResult(
        arpc=Decimal("1.66114320"),
        gross_revenue=Decimal("4.98342960"),
        unit_cogs=Decimal("1.35"),
        total_cogs=Decimal("5.449680"),
        business_costs=Decimal("100"),
        operating_costs=Decimal("100.0009"),
        result=Decimal("-100.46785040"),
        balance_after_result=Decimal("-100.46795040"),
    )


def test_zero_prices_orders_and_payroll_are_valid(inputs):
    pnl = calculate_act_pnl(
        replace(inputs, **{name: Decimal("0") for name in DECIMAL_FIELDS})
    )

    assert pnl.arpc == pnl.gross_revenue == pnl.total_cogs == Decimal("0")
    assert pnl.operating_costs == Decimal("30")
    assert pnl.result == pnl.balance_after_result == Decimal("-30")


def test_positive_result_and_negative_starting_balance_are_valid(inputs):
    pnl = calculate_act_pnl(
        replace(inputs, average_price=Decimal("10"), balance_at_start=Decimal("-400"))
    )

    assert pnl.result == Decimal("301.40")
    assert pnl.balance_after_result == Decimal("-98.60")


def test_calculation_is_deterministic_and_dataclasses_are_frozen(inputs):
    original = replace(inputs)
    pnl = calculate_act_pnl(inputs)

    assert calculate_act_pnl(inputs) == pnl
    assert inputs == original
    with pytest.raises(FrozenInstanceError):
        inputs.clients = 100
    with pytest.raises(FrozenInstanceError):
        pnl.result = Decimal("0")


@pytest.mark.parametrize("field", DECIMAL_FIELDS)
@pytest.mark.parametrize("value", [None, "1", 1, 1.0, True, False])
def test_decimal_fields_reject_other_types(inputs, field, value):
    with pytest.raises(TypeError, match=field):
        replace(inputs, **{field: value})


@pytest.mark.parametrize("field", DECIMAL_FIELDS)
@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_decimal_fields_reject_nonfinite_values(inputs, field, value):
    with pytest.raises(ValueError, match=field):
        replace(inputs, **{field: Decimal(value)})


@pytest.mark.parametrize("field", DECIMAL_FIELDS[:-1])
def test_nonnegative_decimal_fields_reject_negative_values(inputs, field):
    with pytest.raises(ValueError, match=field):
        replace(inputs, **{field: Decimal("-0.0001")})


@pytest.mark.parametrize("field", ["act_number", "clients"])
@pytest.mark.parametrize("value", [None, "1", 1.0, Decimal("1"), True, False])
def test_integer_fields_reject_other_types_including_bool(inputs, field, value):
    with pytest.raises(TypeError, match=field):
        replace(inputs, **{field: value})


@pytest.mark.parametrize("act", [-1, 0, 8])
def test_act_number_must_be_in_range(inputs, act):
    with pytest.raises(ValueError, match="act_number"):
        replace(inputs, act_number=act)


def test_clients_cannot_be_negative(inputs):
    with pytest.raises(ValueError, match="clients"):
        replace(inputs, clients=-1)


@pytest.mark.parametrize("value", [None, {}, 1, True])
def test_calculation_requires_pnl_inputs(value):
    with pytest.raises(TypeError, match="inputs"):
        calculate_act_pnl(value)
