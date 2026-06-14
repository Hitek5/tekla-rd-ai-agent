"""Tool and prompt policy layer.

Separation of duties:

* **policy** decides *whether a tool may run and whether it needs approval*,
  based on the declarative whitelist in ``configs/tools-policy.yaml``;
* **approval** (see :mod:`tekla_agent.approval`) decides *whether a presented
  approval token is cryptographically valid* for this exact call.

So ``check_tool_call`` takes an already-verified boolean ``approval_verified``
rather than a raw string. This keeps the trust boundary explicit: a token that
is merely *present* never counts as approval — it must have been verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tekla_agent.screening import compile_patterns, first_match


@dataclass(frozen=True)
class ToolDecision:
    allowed: bool
    decision: str
    reason: str
    requires_approval: bool
    mutates_model: bool


class ToolPolicy:
    def __init__(self, path: Path):
        self.path = path
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        self.defaults: dict[str, Any] = data.get("defaults", {})
        self.tools: dict[str, dict[str, Any]] = data.get("tools", {})
        raw_patterns = [str(item) for item in data.get("blocked_patterns", [])]
        self._compiled_patterns = compile_patterns(raw_patterns)

    def check_prompt(self, prompt: str) -> ToolDecision:
        matched = first_match(prompt, self._compiled_patterns)
        if matched is not None:
            return ToolDecision(
                allowed=False,
                decision="blocked_prompt_pattern",
                reason=f"Prompt contains blocked pattern: {matched}",
                requires_approval=False,
                mutates_model=False,
            )
        return ToolDecision(
            allowed=True,
            decision="prompt_allowed",
            reason="No blocked prompt pattern matched",
            requires_approval=False,
            mutates_model=False,
        )

    def describe_tool(self, tool: str) -> dict[str, Any] | None:
        return self.tools.get(tool)

    def check_tool_call(
        self,
        tool: str,
        *,
        dry_run: bool,
        approval_verified: bool,
        production_model: bool,
    ) -> ToolDecision:
        config = self.tools.get(tool)
        if config is None:
            return ToolDecision(
                allowed=False,
                decision="blocked_unknown_tool",
                reason=f"Tool is not whitelisted: {tool}",
                requires_approval=False,
                mutates_model=False,
            )

        mutates_model = bool(config.get("mutates_model", False))
        requires_approval = bool(config.get("requires_approval", False))
        allowed_in_production = bool(config.get("allowed_in_production", False))

        if production_model and mutates_model and not allowed_in_production:
            return ToolDecision(
                allowed=False,
                decision="blocked_production_write",
                reason="Mutating tools are disabled for production models",
                requires_approval=requires_approval,
                mutates_model=mutates_model,
            )

        if dry_run:
            return ToolDecision(
                allowed=True,
                decision="allowed_dry_run",
                reason="Dry-run mode does not mutate the model",
                requires_approval=requires_approval,
                mutates_model=mutates_model,
            )

        if requires_approval and not approval_verified:
            return ToolDecision(
                allowed=False,
                decision="blocked_requires_approval",
                reason="Tool requires a valid, scoped approval token",
                requires_approval=True,
                mutates_model=mutates_model,
            )

        return ToolDecision(
            allowed=True,
            decision="allowed_execute",
            reason="Tool call satisfies policy",
            requires_approval=requires_approval,
            mutates_model=mutates_model,
        )
