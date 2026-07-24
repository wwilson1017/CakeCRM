"""Per-turn write budget — the runaway-mutation backstop."""

from assistant.write_budget import BudgetAction, BudgetState


def test_allows_up_to_limit_then_rejects_then_terminates():
    b = BudgetState(limit=2)
    assert b.check_write("crm_create_contact") == BudgetAction.ALLOW  # 1
    assert b.check_write("crm_create_contact") == BudgetAction.ALLOW  # 2 (at limit)
    assert b.check_write("crm_create_contact") == BudgetAction.REJECT  # first excess
    assert b.check_write("crm_create_contact") == BudgetAction.TERMINATE  # subsequent excess
    assert b.writes_used == 2  # rejected writes don't count as used


def test_disabled_always_allows():
    b = BudgetState(limit=1, enabled=False)
    for _ in range(5):
        assert b.check_write("x") == BudgetAction.ALLOW
    assert b.remaining == 999


def test_zero_limit_always_allows():
    b = BudgetState(limit=0)
    assert b.check_write("x") == BudgetAction.ALLOW


def test_remaining_decrements():
    b = BudgetState(limit=3)
    assert b.remaining == 3
    b.check_write("x")
    assert b.remaining == 2
