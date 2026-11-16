"""Owner rules that narrow the arguments of an approved tool.

Rules are stored on a grant as `{tool: {property: rule}}`. Each rule applies to
one top-level property of the tool's `inputSchema`:

- fixed: the broker always sends this value. The model never sees the property.
- allowed: the value must be one of these. The model must send it.
- maximum: the value must not be larger. The model must send it.
- forbidden: the model may not send the property at all.
"""

import copy
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from jsonschema import ValidationError, validate

from secondshift.connections.errors import ExternalToolError

ToolRules = dict[str, dict[str, dict[str, Any]]]


class RuleType(StrEnum):
    FIXED = "fixed"
    ALLOWED = "allowed"
    MAXIMUM = "maximum"
    FORBIDDEN = "forbidden"


NUMERIC_TYPES = {"integer", "number"}


class ToolRuleError(ValueError):
    """A rule the owner tried to save does not fit the tool."""


class ToolRuleViolation(ExternalToolError):
    """A call broke an owner rule. It never reaches the provider."""


def property_types(schema: Mapping[str, Any]) -> set[str]:
    declared = schema.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list):
        return {str(item) for item in declared}
    return set()


def fits(value: object, schema: Mapping[str, Any]) -> bool:
    try:
        validate(value, dict(schema))
    except ValidationError:
        return False
    return True


def value_problem(
    rule: Mapping[str, Any], name: str, prop: Mapping[str, Any]
) -> str | None:
    kind = rule.get("type")
    if kind == RuleType.FIXED:
        if "value" not in rule or not fits(rule["value"], prop):
            return f"The fixed value for `{name}` does not fit its type."
        return None
    values = rule.get("values")
    if not isinstance(values, list) or not values:
        return f"List at least one allowed value for `{name}`."
    if not all(fits(value, prop) for value in values):
        return f"An allowed value for `{name}` does not fit its type."
    return None


def maximum_problem(
    rule: Mapping[str, Any], name: str, prop: Mapping[str, Any]
) -> str | None:
    value = rule.get("value")
    if not property_types(prop) & NUMERIC_TYPES:
        return f"`{name}` is not a number, so it cannot have a maximum."
    if isinstance(value, bool) or not isinstance(value, int | float):
        return f"The maximum for `{name}` must be a number."
    if not fits(value, prop):
        return f"The maximum for `{name}` is outside what the tool accepts."
    return None


def rule_problem(
    rule: Mapping[str, Any], name: str, schema: Mapping[str, Any]
) -> str | None:
    """Why a rule does not fit the tool's input schema. None when it fits."""
    properties = schema.get("properties") or {}
    if name not in properties:
        return f"`{name}` is not an argument of this tool."
    kind = rule.get("type")
    if kind in (RuleType.FIXED, RuleType.ALLOWED):
        return value_problem(rule, name, properties[name])
    if kind == RuleType.MAXIMUM:
        return maximum_problem(rule, name, properties[name])
    if kind == RuleType.FORBIDDEN:
        if name in (schema.get("required") or []):
            return f"`{name}` is required by the tool, so it cannot be forbidden."
        return None
    return f"Unknown rule for `{name}`."


def validate_rules(
    rules: ToolRules, definitions: Mapping[str, Any], allowed_tools: list[str]
) -> None:
    """Raise ToolRuleError when any rule cannot apply to the approved tools."""
    for tool, properties in rules.items():
        if tool not in allowed_tools:
            raise ToolRuleError(f"`{tool}` is not an approved tool.")
        definition = definitions.get(tool) or {}
        schema = definition.get("inputSchema") or {}
        for name, rule in properties.items():
            if problem := rule_problem(rule, name, schema):
                raise ToolRuleError(f"{tool}: {problem}")


def broken_rules(rules: ToolRules, definitions: Mapping[str, Any]) -> list[str]:
    """Tools whose rules no longer fit, after the connection's schemas changed."""
    broken = []
    for tool, properties in sorted(rules.items()):
        schema = (definitions.get(tool) or {}).get("inputSchema") or {}
        if any(rule_problem(rule, name, schema) for name, rule in properties.items()):
            broken.append(tool)
    return broken


def kept_rules(rules: ToolRules, tools: set[str] | list[str]) -> ToolRules:
    """Only the rules for tools that are still approved or requested."""
    return {
        tool: rules[tool]
        for tool in sorted(rules)
        if tool in set(tools) and rules[tool]
    }


def apply_rules(
    tool: str, rules: Mapping[str, Mapping[str, Any]], arguments: Mapping[str, Any]
) -> dict[str, Any]:
    """Set fixed values, then reject anything that breaks a rule.

    The error names the property and the rule, never the value the model sent.
    """
    result = dict(arguments)
    for name, rule in rules.items():
        kind = rule["type"]
        if kind == RuleType.FIXED:
            result[name] = copy.deepcopy(rule["value"])
        elif kind == RuleType.FORBIDDEN:
            if name in result:
                raise ToolRuleViolation(f"{tool}: `{name}` is not allowed.")
        elif kind == RuleType.ALLOWED:
            if name not in result or result[name] not in rule["values"]:
                raise ToolRuleViolation(
                    f"{tool}: `{name}` must be one of the approved values."
                )
        elif kind == RuleType.MAXIMUM:
            value = result.get(name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or value > rule["value"]
            ):
                raise ToolRuleViolation(
                    f"{tool}: `{name}` must be at most {rule['value']}."
                )
    return result


def narrow_schema(
    schema: Mapping[str, Any], rules: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """The input schema the model sees, with the owner's rules applied."""
    if not rules:
        return dict(schema)
    narrowed = copy.deepcopy(dict(schema))
    properties = narrowed.setdefault("properties", {})
    required = list(narrowed.get("required") or [])
    for name, rule in rules.items():
        kind = rule["type"]
        if kind in (RuleType.FIXED, RuleType.FORBIDDEN):
            properties.pop(name, None)
            required = [item for item in required if item != name]
            continue
        prop = properties.get(name, {})
        if kind == RuleType.ALLOWED:
            properties[name] = {**prop, "enum": list(rule["values"])}
        elif kind == RuleType.MAXIMUM:
            ceiling = min(rule["value"], prop.get("maximum", rule["value"]))
            properties[name] = {**prop, "maximum": ceiling}
        if name not in required:
            required.append(name)
    if required:
        narrowed["required"] = required
    else:
        narrowed.pop("required", None)
    return narrowed
