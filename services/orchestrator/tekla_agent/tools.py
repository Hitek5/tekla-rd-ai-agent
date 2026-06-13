"""Typed tool contracts and structured tool-call extraction.

The MVP had two disconnected endpoints: ``/chat`` returned free text, and
``/tool-calls`` executed a tool you named yourself. Nothing turned the model's
*words* into a *validated, typed tool call*. That glue is what makes this an
agent rather than a chat box, so it is the highest-value functional addition.

Two responsibilities live here:

1. **Schemas** — Pydantic models mirroring ``TeklaAgent.Contracts`` (the C# DTOs).
   Validating arguments here, before the policy and before the workstation host,
   means a malformed ``CreateBeam`` is rejected with a clear error instead of
   throwing a ``NullReferenceException`` deep inside the C# host.

2. **Extraction** — pulling a single ``{"tool": ..., "args": {...}}`` object out
   of the model's reply. Small local models do not reliably emit clean JSON, so
   we tolerate fenced code blocks and surrounding prose, and fail closed (return
   ``None``) rather than guessing.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError


class Point3D(BaseModel):
    x: float
    y: float
    z: float


class CreateBeamArgs(BaseModel):
    start: Point3D
    end: Point3D
    profile: str
    material: str
    class_: str | None = None
    name: str | None = None


class CreateColumnArgs(BaseModel):
    base_point: Point3D
    height: float
    profile: str
    material: str
    class_: str | None = None
    name: str | None = None


class CreateRebarArgs(BaseModel):
    host_guid: str
    diameter_mm: float
    grade: str
    spacing_mm: float | None = None


class QueryObjectsArgs(BaseModel):
    object_type: str | None = None
    name: str | None = None
    profile: str | None = None
    material: str | None = None
    limit: int = 100


class ModifyObjectArgs(BaseModel):
    guid: str
    profile: str | None = None
    material: str | None = None
    class_: str | None = None


class DeleteObjectArgs(BaseModel):
    guid: str


class GenerateDrawingDraftArgs(BaseModel):
    object_guids: list[str]
    template: str | None = None


# Empty-arg tools still get a model so validation is uniform.
class NoArgs(BaseModel):
    pass


TOOL_SCHEMAS: dict[str, type[BaseModel]] = {
    "GetSelection": NoArgs,
    "QueryObjects": QueryObjectsArgs,
    "ValidateModel": NoArgs,
    "DryRun": NoArgs,
    "CreateBeam": CreateBeamArgs,
    "CreateColumn": CreateColumnArgs,
    "CreateRebar": CreateRebarArgs,
    "ModifyObject": ModifyObjectArgs,
    "DeleteObject": DeleteObjectArgs,
    "GenerateDrawingDraft": GenerateDrawingDraftArgs,
}


def known_tools() -> list[str]:
    return sorted(TOOL_SCHEMAS)


def validate_args(tool: str, args: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
    """Validate raw args against the tool schema.

    Returns ``(ok, reason, normalised_args)``. ``normalised_args`` is the
    schema's canonical dump so downstream hashing/auditing is deterministic.
    """
    schema = TOOL_SCHEMAS.get(tool)
    if schema is None:
        return False, f"unknown_tool: {tool}", None
    try:
        model = schema.model_validate(args)
    except ValidationError as exc:
        return False, f"invalid_args: {exc.error_count()} error(s)", None
    return True, "ok", model.model_dump(exclude_none=True)


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _candidate_json_blobs(text: str) -> list[str]:
    blobs = [m.group(1) for m in _JSON_BLOCK.finditer(text)]
    # Also try the first balanced {...} span as a fallback.
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                blobs.append(text[start : i + 1])
                start = -1
    return blobs


def extract_tool_call(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a ``{"tool", "args"}`` proposal from model text.

    Fails closed: returns ``None`` if no well-formed proposal is found, so the
    caller treats the reply as plain text rather than acting on a guess.
    """
    for blob in _candidate_json_blobs(text):
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("tool"), str):
            args = data.get("args")
            if args is None:
                args = {}
            if isinstance(args, dict):
                return {"tool": data["tool"], "args": args}
    return None
