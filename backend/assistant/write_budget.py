"""Per-turn write budget enforcement.

A BudgetState is created fresh at the start of each assistant turn. It tracks
how many write tools have executed and rejects/terminates on excess, so a single
turn can never run away issuing unbounded CRM mutations. Ported verbatim from
Chatty's ``core/agents/security/write_budget.py``.

Budget applies to every tool with ``writes=True`` in the CRM tool defs.
"""

from dataclasses import dataclass
from enum import Enum

# 9 write tools exist today; 20/turn is generous headroom for a legitimate
# multi-step turn while still capping a runaway loop.
WRITE_BUDGET_PER_TURN = 20


class BudgetAction(Enum):
    ALLOW = "allow"
    REJECT = "reject"
    TERMINATE = "terminate"


@dataclass
class BudgetState:
    limit: int
    enabled: bool = True
    writes_used: int = 0
    rejected_once: bool = False

    def check_write(self, tool_name: str) -> BudgetAction:
        if not self.enabled or self.limit <= 0:
            return BudgetAction.ALLOW
        if self.writes_used < self.limit:
            self.writes_used += 1
            return BudgetAction.ALLOW
        if not self.rejected_once:
            self.rejected_once = True
            return BudgetAction.REJECT
        return BudgetAction.TERMINATE

    @property
    def remaining(self) -> int:
        if not self.enabled:
            return 999
        return max(0, self.limit - self.writes_used)
