from dataclasses import replace
from decimal import Decimal

from app.act_one_baseline import build_act_one_baseline


def test_build_act_one_baseline_matches_the_agreed_example():
    baseline = build_act_one_baseline()

    assert isinstance(baseline.balance, Decimal)
    assert baseline.balance == Decimal("675.00")
    assert baseline.clients == 60
    assert baseline.base_action_points == 130
    assert baseline.starting_action_point_additions == 0
    assert baseline.available_action_points == 130
    assert baseline.hired_development_team_grades == ("junior", "junior")
    assert baseline.hiring_level == 0
    assert baseline.hiring_capacity == 2
    assert baseline.experiments_per_development_team == 3


def test_build_act_one_baseline_is_deterministic():
    assert build_act_one_baseline() == build_act_one_baseline()


def test_hiring_capacity_is_independent_of_hired_teams():
    baseline = build_act_one_baseline()
    higher_capacity = replace(baseline, hiring_capacity=3)

    assert higher_capacity.hiring_capacity == 3
    assert higher_capacity.hired_development_team_grades == ("junior", "junior")
