"""Read an OpenAPI 3 spec into operations a job can call.

The spec only describes operations. Where requests go is the base URL the owner
confirmed, which is saved with the connection and never read from the spec at
call time.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

import anyio
import httpx
import yaml
from asgiref.sync import async_to_sync
from django.conf import settings
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from secondshift.integrations.errors import ExternalRequestError, ExternalToolError
from secondshift.integrations.mcp.transport import MCPTransport
from secondshift.integrations.outbound import OutboundUrlError, validate_outbound_url

SPEC_MAX_BYTES = 5 * 1024 * 1024
MAX_OPERATIONS = 500
SCHEMA_MAX_DEPTH = 20
SCHEMA_MAX_BYTES = 64 * 1024
DESCRIPTION_MAX_CHARS = 500
METHODS = ("get", "post", "put", "patch", "delete")
OPERATION_NAME_PATTERN = r"[A-Za-z0-9_.-]{1,64}"
# Path and query only. Headers and cookies stay under SecondShift's control.
PARAMETER_LOCATIONS = {"path", "query"}
BODY = "body"


class APIError(ExternalToolError):
    pass


class APIRequestError(APIError, ExternalRequestError):
    """A remote failure the agent may report as unavailable evidence."""


@dataclass(frozen=True)
class Parameter:
    name: str
    location: str
    required: bool


@dataclass(frozen=True)
class Operation:
    name: str
    method: str
    path: str
    description: str
    parameters: tuple[Parameter, ...]
    has_body: bool
    input_schema: dict[str, Any]

    @property
    def read_only(self) -> bool:
        return self.method == "get"

    def definition(self) -> dict[str, Any]:
        """What is stored and frozen into run plans. No credentials."""
        return {
            "method": self.method,
            "path": self.path,
            "description": self.description,
            "parameters": [
                {"name": item.name, "in": item.location, "required": item.required}
                for item in self.parameters
            ],
            "body": self.has_body,
            "inputSchema": self.input_schema,
            "readOnly": self.read_only,
        }


@dataclass(frozen=True)
class Spec:
    title: str
    servers: tuple[str, ...]
    operations: dict[str, Operation] = field(default_factory=dict)


class NoAliasLoader(yaml.SafeLoader):
    """YAML without anchors and aliases, so a small file cannot expand to a huge one."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise APIError("Specs with YAML aliases are not supported.")
        return super().compose_node(parent, index)


def parse_document(text: str) -> dict[str, Any]:
    if len(text.encode()) > SPEC_MAX_BYTES:
        raise APIError("The spec is larger than 5 MB.")
    try:
        document = json.loads(text)
    except ValueError:
        try:
            document = yaml.load(text, Loader=NoAliasLoader)
        except yaml.YAMLError:
            raise APIError("The spec is not valid JSON or YAML.") from None
    if not isinstance(document, dict):
        raise APIError("The spec is not an OpenAPI document.")
    if not str(document.get("openapi", "")).startswith("3."):
        raise APIError("Only OpenAPI 3 specs are supported.")
    return document


async def fetch_text(url: str) -> str:
    try:
        with anyio.fail_after(settings.OUTBOUND_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                transport=MCPTransport(),
                headers={
                    "Accept": "application/json, application/yaml, text/yaml, */*",
                    "Accept-Encoding": "identity",
                },
                timeout=settings.OUTBOUND_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.get(url)
                body = await response.aread()
    except Exception:
        raise APIRequestError("The spec could not be downloaded.") from None
    if 300 <= response.status_code < 400:
        raise APIError("The spec URL redirects. Use the final address.")
    if response.status_code != 200:
        raise APIError("The spec could not be downloaded.")
    return body.decode("utf-8", errors="replace")


def checked_url(value: str, noun: str) -> str:
    try:
        return validate_outbound_url(value, noun, resolve=False)
    except OutboundUrlError as error:
        raise APIError(str(error)) from None


def load_spec(spec_url: str = "", spec_text: str = "") -> Spec:
    """From a URL, or from pasted text when the spec is not published."""
    if spec_url:
        spec_url = checked_url(spec_url, "spec URL")
        spec_text = async_to_sync(fetch_text)(spec_url)
    if not spec_text.strip():
        raise APIError("Enter a spec URL or paste a spec.")
    return read_spec(parse_document(spec_text), spec_url)


def read_spec(document: dict[str, Any], spec_url: str = "") -> Spec:
    info = document.get("info") if isinstance(document.get("info"), dict) else {}
    operations: dict[str, Operation] = {}
    paths = document.get("paths") if isinstance(document.get("paths"), dict) else {}
    for path, item in paths.items():
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or not isinstance(item, dict)
        ):
            continue
        shared = item.get("parameters") or []
        for method in METHODS:
            raw = item.get(method)
            if not isinstance(raw, dict):
                continue
            operation = read_operation(document, path, method, raw, shared)
            if operation is None or operation.name in operations:
                continue
            operations[operation.name] = operation
            if len(operations) > MAX_OPERATIONS:
                raise APIError(f"The spec has more than {MAX_OPERATIONS} operations.")
    if not operations:
        raise APIError("The spec lists no operations SecondShift can call.")
    return Spec(
        title=str(info.get("title", ""))[:200],
        servers=servers(document, spec_url),
        operations=operations,
    )


def servers(document: dict[str, Any], spec_url: str) -> tuple[str, ...]:
    """Absolute server URLs. Relative ones resolve against the spec URL.

    Variables use their defaults. A server that still has a variable, or that
    fails the outbound check, is left out for the owner to type instead.
    """
    found: list[str] = []
    for item in document.get("servers") or []:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        url = item["url"]
        for name, variable in (item.get("variables") or {}).items():
            if isinstance(variable, dict) and "default" in variable:
                url = url.replace("{" + str(name) + "}", str(variable["default"]))
        if "{" in url:
            continue
        if not urlsplit(url).scheme:
            if not spec_url:
                continue
            url = urljoin(spec_url, url)
        try:
            found.append(checked_url(url.rstrip("/"), "server URL"))
        except APIError:
            continue
    return tuple(dict.fromkeys(found))


def operation_name(raw: dict[str, Any], method: str, path: str) -> str:
    name = str(raw.get("operationId") or "")
    if re.fullmatch(OPERATION_NAME_PATTERN, name):
        return name
    slug = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")
    return f"{method}_{slug}"[:64] or method


def read_operation(
    document: dict[str, Any],
    path: str,
    method: str,
    raw: dict[str, Any],
    shared: list[Any],
) -> Operation | None:
    declared: dict[tuple[str, str], dict[str, Any]] = {}
    for item in [*shared, *(raw.get("parameters") or [])]:
        parameter = resolve(document, item)
        if not isinstance(parameter, dict):
            continue
        location = parameter.get("in")
        name = parameter.get("name")
        if location in PARAMETER_LOCATIONS and isinstance(name, str) and name != BODY:
            declared[(location, name)] = parameter
    properties: dict[str, Any] = {}
    required: list[str] = []
    parameters: list[Parameter] = []
    for (location, name), parameter in declared.items():
        if name in properties:
            # A path and query parameter share a name. Too ambiguous to call.
            return None
        is_required = location == "path" or bool(parameter.get("required"))
        schema = inline(document, parameter.get("schema") or {"type": "string"})
        if parameter.get("description"):
            schema = {**schema, "description": str(parameter["description"])[:300]}
        properties[name] = schema
        parameters.append(Parameter(name, location, is_required))
        if is_required:
            required.append(name)
    # Every {name} in the path must be a declared path parameter.
    template_names = set(re.findall(r"{([^}/]+)}", path))
    if template_names != {item.name for item in parameters if item.location == "path"}:
        return None
    has_body = False
    body = resolve(document, raw.get("requestBody"))
    if method != "get" and isinstance(body, dict):
        content = body.get("content") if isinstance(body.get("content"), dict) else {}
        media = content.get("application/json")
        if isinstance(media, dict):
            has_body = True
            properties[BODY] = inline(document, media.get("schema") or {})
            if body.get("required"):
                required.append(BODY)
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        input_schema["required"] = sorted(required)
    if len(json.dumps(input_schema).encode()) > SCHEMA_MAX_BYTES:
        return None
    try:
        Draft202012Validator.check_schema(input_schema)
    except SchemaError:
        return None
    description = str(raw.get("summary") or raw.get("description") or "")
    return Operation(
        name=operation_name(raw, method, path),
        method=method,
        path=path,
        description=f"{method.upper()} {path}. {description}"[:DESCRIPTION_MAX_CHARS],
        parameters=tuple(parameters),
        has_body=has_body,
        input_schema=input_schema,
    )


def resolve(document: dict[str, Any], value: Any, depth: int = 0) -> Any:
    """Follow a local $ref such as #/components/parameters/Limit."""
    while isinstance(value, dict) and "$ref" in value:
        if depth > SCHEMA_MAX_DEPTH:
            return {}
        reference = value["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            return {}
        target: Any = document
        for part in reference[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            target = target.get(part) if isinstance(target, dict) else None
        value = target
        depth += 1
    return value


def inline(document: dict[str, Any], schema: Any, depth: int = 0) -> dict[str, Any]:
    """A schema with every local $ref replaced. Deep or cyclic parts become {}."""
    if depth > SCHEMA_MAX_DEPTH:
        return {}
    schema = resolve(document, schema)
    if not isinstance(schema, dict):
        return {}
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key in {"$ref", "$dynamicRef", "$recursiveRef", "$id", "$schema"}:
            continue
        # OpenAPI 3.0 writes these as booleans. JSON Schema wants numbers.
        if key in {"exclusiveMinimum", "exclusiveMaximum"} and isinstance(value, bool):
            continue
        if isinstance(value, dict):
            result[key] = (
                {
                    name: inline(document, item, depth + 1)
                    for name, item in value.items()
                }
                if key in {"properties", "patternProperties", "$defs", "definitions"}
                else inline(document, value, depth + 1)
            )
        elif isinstance(value, list):
            result[key] = [
                inline(document, item, depth + 1) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value
    return result
