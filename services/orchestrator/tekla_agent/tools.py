"""Typed tool contracts and structured tool-call extraction.

The MVP had two disconnected endpoints: ``/chat`` returned free text, and
``/tool-calls`` executed a tool you named yourself. Nothing turned the model's
*words* into a *validated, typed tool call*. That glue is what makes this an
agent rather than a chat box, so it is the highest-value functional addition.

Three responsibilities live here:

1. **Schemas** — Pydantic models mirroring ``TeklaAgent.Contracts`` (the C# DTOs).
   Validating arguments here, before the policy and before the workstation host,
   means a malformed ``CreateBeam`` is rejected with a clear error instead of
   throwing a ``NullReferenceException`` deep inside the C# host.

2. **Two serialisations** — internal (snake_case) for hashing/policy/approval
   binding, and *wire* (PascalCase via ``serialization_alias``) for forwarding to
   the C# host, whose Json.NET deserialiser expects ``BasePoint`` / ``Class`` /
   ``ObjectType`` and does not translate underscores. ``to_wire_args`` produces
   the wire form.

3. **Extraction** — pulling a single ``{"tool": ..., "args": {...}}`` object out
   of the model's reply. Small local models do not reliably emit clean JSON, so
   we tolerate fenced code blocks and surrounding prose, and fail closed (return
   ``None``) rather than guessing.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, ValidationError


class Point3D(BaseModel):
    x: float = Field(serialization_alias="X")
    y: float = Field(serialization_alias="Y")
    z: float = Field(serialization_alias="Z")


class CreateBeamArgs(BaseModel):
    start: Point3D = Field(serialization_alias="Start")
    end: Point3D = Field(serialization_alias="End")
    profile: str = Field(serialization_alias="Profile")
    material: str = Field(serialization_alias="Material")
    class_: str | None = Field(
        default=None,
        validation_alias=AliasChoices("class_", "class"),
        serialization_alias="Class",
    )
    name: str | None = Field(default=None, serialization_alias="Name")


class CreateColumnArgs(BaseModel):
    base_point: Point3D = Field(serialization_alias="BasePoint")
    height: float = Field(serialization_alias="Height")
    profile: str = Field(serialization_alias="Profile")
    material: str = Field(serialization_alias="Material")
    class_: str | None = Field(
        default=None,
        validation_alias=AliasChoices("class_", "class"),
        serialization_alias="Class",
    )
    name: str | None = Field(default=None, serialization_alias="Name")


class CreateRebarArgs(BaseModel):
    host_guid: str = Field(serialization_alias="HostGuid")
    diameter_mm: float = Field(serialization_alias="DiameterMm")
    grade: str = Field(serialization_alias="Grade")
    spacing_mm: float | None = Field(default=None, serialization_alias="SpacingMm")


class QueryObjectsArgs(BaseModel):
    object_type: str | None = Field(default=None, serialization_alias="ObjectType")
    name: str | None = Field(default=None, serialization_alias="Name")
    profile: str | None = Field(default=None, serialization_alias="Profile")
    material: str | None = Field(default=None, serialization_alias="Material")
    limit: int = Field(default=100, serialization_alias="Limit")


class ModifyObjectArgs(BaseModel):
    guid: str = Field(serialization_alias="Guid")
    profile: str | None = Field(default=None, serialization_alias="Profile")
    material: str | None = Field(default=None, serialization_alias="Material")
    class_: str | None = Field(
        default=None,
        validation_alias=AliasChoices("class_", "class"),
        serialization_alias="Class",
    )
    new_start: Point3D | None = Field(default=None, serialization_alias="NewStart")
    new_end: Point3D | None = Field(default=None, serialization_alias="NewEnd")


class DeleteObjectArgs(BaseModel):
    guid: str = Field(serialization_alias="Guid")


class GenerateDrawingDraftArgs(BaseModel):
    object_guids: list[str] = Field(serialization_alias="ObjectGuids")
    template: str | None = Field(default=None, serialization_alias="Template")


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

    Returns ``(ok, reason, normalised_args)``. ``normalised_args`` is the schema's
    canonical snake_case dump, so downstream hashing/auditing/approval binding is
    deterministic and language-neutral.
    """
    schema = TOOL_SCHEMAS.get(tool)
    if schema is None:
        return False, f"unknown_tool: {tool}", None
    try:
        model = schema.model_validate(args)
    except ValidationError as exc:
        return False, f"invalid_args: {exc.error_count()} error(s)", None
    return True, "ok", model.model_dump(exclude_none=True)


def to_wire_args(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Serialise validated args to the C# host wire names (PascalCase).

    Falls back to the input unchanged if the tool is unknown or the args do not
    validate — the caller has already enforced validity for executing calls.
    """
    schema = TOOL_SCHEMAS.get(tool)
    if schema is None:
        return args
    try:
        model = schema.model_validate(args)
    except ValidationError:
        return args
    return model.model_dump(by_alias=True, exclude_none=True)


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
