from tekla_agent.tools import (
    canonical_json,
    extract_tool_call,
    known_tools,
    to_wire_args,
    validate_args,
)


def test_known_tools_present() -> None:
    tools = known_tools()
    assert "CreateBeam" in tools
    assert "DeleteObject" in tools


def test_validate_create_beam_ok() -> None:
    ok, _reason, normalised = validate_args(
        "CreateBeam",
        {
            "start": {"x": 0, "y": 0, "z": 0},
            "end": {"x": 6000, "y": 0, "z": 0},
            "profile": "HEA300",
            "material": "S355",
        },
    )
    assert ok
    assert normalised["profile"] == "HEA300"


def test_validate_create_beam_missing_field() -> None:
    ok, reason, normalised = validate_args(
        "CreateBeam", {"start": {"x": 0, "y": 0, "z": 0}}
    )
    assert not ok
    assert "invalid_args" in reason
    assert normalised is None


def test_validate_unknown_tool() -> None:
    ok, reason, _ = validate_args("Nope", {})
    assert not ok
    assert "unknown_tool" in reason


def test_extract_from_fenced_block() -> None:
    text = (
        "Я предлагаю создать балку.\n"
        '```json\n{"tool": "CreateBeam", "args": {"profile": "HEA300"}}\n```\n'
        "Требуется согласование."
    )
    call = extract_tool_call(text)
    assert call is not None
    assert call["tool"] == "CreateBeam"
    assert call["args"]["profile"] == "HEA300"


def test_extract_from_inline_json() -> None:
    text = 'Сделаю так: {"tool": "GetSelection", "args": {}} — это безопасно.'
    call = extract_tool_call(text)
    assert call is not None
    assert call["tool"] == "GetSelection"


def test_extract_returns_none_for_plain_text() -> None:
    assert extract_tool_call("Просто текстовый ответ без инструмента.") is None


def test_validate_normalises_ints_to_floats() -> None:
    # Integer coords from a client must normalise the same way every time, so the
    # approval hash is stable (regression for the mint/verify args_mismatch bug).
    ok, _r, normalised = validate_args(
        "CreateBeam",
        {
            "start": {"x": 0, "y": 0, "z": 0},
            "end": {"x": 6000, "y": 0, "z": 0},
            "profile": "HEA300",
            "material": "S355",
        },
    )
    assert ok
    assert normalised["start"]["x"] == 0.0
    assert isinstance(normalised["start"]["x"], float)
    assert "class_" not in normalised  # None omitted


def test_to_wire_uses_csharp_names() -> None:
    wire = to_wire_args(
        "CreateColumn",
        {
            "base_point": {"x": 1, "y": 2, "z": 3},
            "height": 3000,
            "profile": "HEA300",
            "material": "S355",
            "class_": "3",
        },
    )
    assert wire["BasePoint"] == {"X": 1.0, "Y": 2.0, "Z": 3.0}
    assert wire["Class"] == "3"
    assert "base_point" not in wire


def test_canonical_json_keeps_cyrillic_utf8() -> None:
    # The host hashes the raw UTF-8 bytes, so canonical JSON must not escape
    # non-ASCII (a Cyrillic Name must round-trip byte-for-byte).
    s = canonical_json({"Name": "Балка", "b": 1, "a": 2})
    assert "Балка" in s
    assert "\\u" not in s
    assert s.index('"a"') < s.index('"b"')  # sorted keys


def test_to_wire_query_objects_object_type() -> None:
    wire = to_wire_args("QueryObjects", {"object_type": "Beam", "limit": 10})
    assert wire["ObjectType"] == "Beam"
    assert "object_type" not in wire


def test_class_alias_accepts_plain_class_key() -> None:
    ok, _r, normalised = validate_args(
        "CreateBeam",
        {
            "start": {"x": 0, "y": 0, "z": 0},
            "end": {"x": 1, "y": 0, "z": 0},
            "profile": "HEA300",
            "material": "S355",
            "class": "3",
        },
    )
    assert ok
    assert normalised["class_"] == "3"
