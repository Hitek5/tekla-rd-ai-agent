from pathlib import Path

from tekla_agent.policy import ToolPolicy


POLICY_PATH = Path("configs/tools-policy.yaml")


def test_unknown_tool_is_blocked() -> None:
    policy = ToolPolicy(POLICY_PATH)
    decision = policy.check_tool_call(
        "RunArbitraryCSharp",
        dry_run=False,
        approval_token=None,
        production_model=False,
    )
    assert not decision.allowed
    assert decision.decision == "blocked_unknown_tool"


def test_mutating_tool_requires_approval_when_not_dry_run() -> None:
    policy = ToolPolicy(POLICY_PATH)
    decision = policy.check_tool_call(
        "CreateBeam",
        dry_run=False,
        approval_token=None,
        production_model=False,
    )
    assert not decision.allowed
    assert decision.decision == "blocked_requires_approval"


def test_read_tool_allowed_without_approval() -> None:
    policy = ToolPolicy(POLICY_PATH)
    decision = policy.check_tool_call(
        "GetSelection",
        dry_run=False,
        approval_token=None,
        production_model=True,
    )
    assert decision.allowed
    assert decision.decision == "allowed_execute"


def test_production_write_blocked_even_with_approval() -> None:
    policy = ToolPolicy(POLICY_PATH)
    decision = policy.check_tool_call(
        "ModifyObject",
        dry_run=False,
        approval_token="approved-by-engineer",
        production_model=True,
    )
    assert not decision.allowed
    assert decision.decision == "blocked_production_write"

