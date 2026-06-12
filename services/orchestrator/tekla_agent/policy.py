from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


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
        self.blocked_patterns = [str(item).lower() for item in data.get("blocked_patterns", [])]

    def check_prompt(self, prompt: str) -> ToolDecision:
        normalized = prompt.lower()
        for pattern in self.blocked_patterns:
            if pattern in normalized:
                return ToolDecision(
                    allowed=False,
                    decision="blocked_prompt_pattern",
                    reason=f"Prompt contains blocked pattern: {pattern}",
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

    def check_tool_call(
        self,
        tool: str,
        *,
        dry_run: bool,
        approval_token: str | None,
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

        if requires_approval and not approval_token:
            return ToolDecision(
                allowed=False,
                decision="blocked_requires_approval",
                reason="Tool requires approval token",
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

