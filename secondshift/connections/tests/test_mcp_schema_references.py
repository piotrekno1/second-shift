import pytest
from jsonschema import Draft202012Validator, ValidationError

from secondshift.connections.mcp.base import MCPError
from secondshift.connections.mcp.client import validate_tool_schema


def cursor_schema() -> dict:
    return {
        "type": "object",
        "$defs": {
            "Cursor": {
                "type": "object",
                "properties": {"id": {"type": "integer", "minimum": 1}},
                "required": ["id"],
                "additionalProperties": False,
            }
        },
        "properties": {
            "cursor": {"anyOf": [{"$ref": "#/$defs/Cursor"}, {"type": "null"}]}
        },
    }


def test_local_cursor_reference_preserves_argument_validation() -> None:
    schema = cursor_schema()
    validate_tool_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate({"cursor": {"id": 1}})
    validator.validate({"cursor": None})
    with pytest.raises(ValidationError):
        validator.validate({"cursor": {"id": 0}})


@pytest.mark.parametrize(
    "reference", ["#/$defs/Cursor", "#/definitions/Cursor", "#/%24defs/Cursor"]
)
def test_local_json_pointers(reference: str) -> None:
    key = "definitions" if "definitions" in reference else "$defs"
    validate_tool_schema(
        {
            key: {"Cursor": {"type": "object"}},
            "properties": {"cursor": {"$ref": reference}},
        }
    )


def test_escaped_pointer_tokens() -> None:
    validate_tool_schema(
        {
            "$defs": {"a/b~c": {"type": "string"}},
            "properties": {"value": {"$ref": "#/$defs/a~1b~0c"}},
        }
    )


@pytest.mark.parametrize(
    "reference",
    [
        "https://example.com/schema",
        "http://169.254.169.254/",
        "file:///etc/passwd",
        "other.json#/$defs/Cursor",
        "#missing",
        "#/$defs/Missing",
        "#/$defs/a~2b",
        1,
    ],
)
def test_invalid_or_external_references_are_rejected(reference: object) -> None:
    with pytest.raises(MCPError, match="reference"):
        validate_tool_schema(
            {"type": "object", "properties": {"cursor": {"$ref": reference}}}
        )


@pytest.mark.parametrize("keyword", ["$dynamicRef", "$recursiveRef"])
def test_dynamic_references_remain_rejected(keyword: str) -> None:
    with pytest.raises(MCPError, match="reference"):
        validate_tool_schema({keyword: "#"})


def test_recursive_references_are_rejected() -> None:
    with pytest.raises(MCPError, match="recursive"):
        validate_tool_schema(
            {"$defs": {"Node": {"properties": {"next": {"$ref": "#/$defs/Node"}}}}}
        )


def test_mutually_recursive_references_are_rejected() -> None:
    with pytest.raises(MCPError, match="recursive"):
        validate_tool_schema(
            {"$defs": {"A": {"$ref": "#/$defs/B"}, "B": {"$ref": "#/$defs/A"}}}
        )


def test_nested_identifiers_cannot_change_reference_scope() -> None:
    schema = cursor_schema()
    schema["properties"]["cursor"]["$id"] = "https://example.com/other"
    with pytest.raises(MCPError, match="identifier"):
        validate_tool_schema(schema)


def test_reference_target_must_be_a_schema() -> None:
    with pytest.raises(MCPError, match="reference"):
        validate_tool_schema(
            {"title": "not a schema", "properties": {"cursor": {"$ref": "#/title"}}}
        )


def test_reference_depth_is_bounded() -> None:
    definitions = {f"s{i}": {"$ref": f"#/$defs/s{i + 1}"} for i in range(40)}
    definitions["s40"] = {"type": "string"}
    with pytest.raises(MCPError, match="deeply nested"):
        validate_tool_schema({"$defs": definitions})


def test_reference_expansion_work_is_bounded() -> None:
    definitions = {"s0": {"type": "string"}}
    for i in range(1, 8):
        definitions[f"s{i}"] = {
            "anyOf": [{"$ref": f"#/$defs/s{i - 1}"} for _ in range(4)]
        }
    with pytest.raises(MCPError, match="complex"):
        validate_tool_schema({"$defs": definitions})


def test_reference_sibling_constraints_are_preserved() -> None:
    schema = cursor_schema()
    schema["properties"]["cursor"] = {
        "$ref": "#/$defs/Cursor",
        "properties": {"id": {"maximum": 5}},
    }
    validate_tool_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate({"cursor": {"id": 5}})
    with pytest.raises(ValidationError):
        validator.validate({"cursor": {"id": 6}})


def test_pointer_to_boolean_schema_and_array_item() -> None:
    validate_tool_schema(
        {
            "$defs": {"Never": False},
            "allOf": [{"type": "string"}],
            "anyOf": [{"$ref": "#/$defs/Never"}, {"$ref": "#/allOf/0"}],
        }
    )


def test_external_reference_hidden_in_unused_definition_is_rejected() -> None:
    with pytest.raises(MCPError, match="reference"):
        validate_tool_schema(
            {
                "type": "object",
                "$defs": {"unused": {"$ref": "https://example.com/schema"}},
            }
        )
