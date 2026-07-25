"""L1 deterministic checks.

Importing this package registers every check by name. Golden cases reference them
as strings, so adding a check is: write the function, decorate it with
@register("name"), import its module here.
"""

from tracegym.checks import (
    agentic,  # noqa: F401  tool_selection, trajectory
    budgets,  # noqa: F401  budget, latency
    grounding,  # noqa: F401  citation / context fidelity
    matchers,  # noqa: F401  contains, not_contains, regex, schema_valid
    safety,  # noqa: F401  pii, abstain
    sql_exec,  # noqa: F401  sql_exec_accuracy oracle
    sql_select_only,  # noqa: F401  sql_select_only
)
from tracegym.checks.base import CHECKS, CheckResult, register, run_checks

__all__ = ["CHECKS", "CheckResult", "register", "run_checks"]
