from tekla_agent.tools import extract_tool_call, known_tools, validate_args


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
