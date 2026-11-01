import base64
import hashlib
import json
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from secondshift.connections.external import external_access
from secondshift.connections.mcp.base import MCPError, MCPRequestError
from secondshift.connections.mcp.client import (
    create_mcp_connection,
    update_mcp_connection,
)
from secondshift.connections.mcp.oauth import (
    fresh_connection,
    granted_scopes,
    redirect_uri,
)
from secondshift.connections.mcp.signin import (
    SignInError,
    complete_authorization,
    start_authorization,
)
from secondshift.connections.models import Connection, OAuthAuthorization
from secondshift.repositories.models import Repository
from secondshift.workspaces.models import Workspace
from secondshift.workspaces.services import create_workspace

SERVER = "https://mcp.example.com/mcp"
ISSUER = "https://auth.example.com"
OBJECT_SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}}


class FakeServer:
    """An MCP server behind OAuth, with a token endpoint that checks PKCE."""

    def __init__(self) -> None:
        self.challenge = ""
        self.access = "at-1"
        self.refresh = "rt-1"
        self.issued = 1
        self.grant_scope: str | None = None
        self.input_schema = OBJECT_SCHEMA
        self.static_token = "static-token"
        self.mcp_auth: list[str] = []
        self.register_body: dict[str, Any] = {"client_id": "client-1"}

    def tokens(self) -> dict[str, Any]:
        body = {
            "access_token": self.access,
            "refresh_token": self.refresh,
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        if self.grant_scope is not None:
            body["scope"] = self.grant_scope
        return body

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == f"{ISSUER}/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "registration_endpoint": f"{ISSUER}/register",
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if url == "https://mcp.example.com/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(
                200,
                json={
                    "resource": SERVER,
                    "authorization_servers": [ISSUER],
                    "scopes_supported": ["read", "write"],
                },
            )
        if url == f"{ISSUER}/register":
            return httpx.Response(201, json=self.register_body)
        if url == f"{ISSUER}/token":
            form = parse_qs(request.content.decode())
            grant = form["grant_type"][0]
            if grant == "authorization_code":
                verifier = form["code_verifier"][0]
                digest = hashlib.sha256(verifier.encode()).digest()
                expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
                if form["code"][0] != "good-code" or expected != self.challenge:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                assert form["redirect_uri"][0] == redirect_uri()
                return httpx.Response(200, json=self.tokens())
            if grant == "refresh_token" and form["refresh_token"][0] == self.refresh:
                self.issued += 1
                self.access, self.refresh = f"at-{self.issued}", f"rt-{self.issued}"
                return httpx.Response(200, json=self.tokens())
            return httpx.Response(400, json={"error": "invalid_grant"})
        if url == SERVER:
            return self.mcp(request)
        return httpx.Response(404)

    def mcp(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        if auth not in {f"Bearer {self.access}", f"Bearer {self.static_token}"}:
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"'
                },
            )
        self.mcp_auth.append(auth)
        if request.method != "POST":
            return httpx.Response(405)
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
        match message["method"]:
            case "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake", "version": "1"},
                }
            case "tools/list":
                result = {
                    "tools": [
                        {
                            "name": "read_metrics",
                            "inputSchema": self.input_schema,
                            "annotations": {"readOnlyHint": True},
                        },
                        {"name": "delete_dashboard", "inputSchema": OBJECT_SCHEMA},
                    ]
                }
            case _:
                raise AssertionError(message)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
        )


@pytest.fixture
def fake(monkeypatch) -> FakeServer:
    server = FakeServer()
    for module in ("oauth", "client"):
        monkeypatch.setattr(
            f"secondshift.connections.mcp.{module}.MCPTransport",
            lambda: httpx.MockTransport(server.handle),
        )
    return server


@pytest.fixture
def owner(db) -> Any:
    return get_user_model().objects.create_user(username="oauth-owner")


@pytest.fixture
def workspace(owner) -> Workspace:
    return create_workspace(name="OAuth", slug="oauth", owner=owner)


def begin(fake: FakeServer, workspace: Workspace, owner: Any, scopes=("read",)) -> str:
    url = start_authorization(
        workspace, owner, "mcp", "metrics", "", {"url": SERVER}, list(scopes)
    )
    query = parse_qs(urlsplit(url).query)
    fake.challenge = query["code_challenge"][0]
    return query["state"][0]


def test_full_sign_in_flow(fake: FakeServer, workspace: Workspace, owner: Any) -> None:
    url = start_authorization(
        workspace, owner, "mcp", "metrics", "", {"url": SERVER}, ["read"]
    )
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{ISSUER}/authorize"
    assert query["scope"] == ["read"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["client_id"] == ["client-1"]
    assert query["resource"] == [SERVER]
    assert query["redirect_uri"] == [redirect_uri()]
    fake.challenge = query["code_challenge"][0]

    connection = complete_authorization(owner, query["state"][0], "good-code", "")

    connection.refresh_from_db()
    assert connection.auth_strategy == Connection.AuthStrategy.OAUTH
    assert connection.access_token == "at-1"
    assert connection.refresh_token == "rt-1"
    assert connection.scopes == ["read"]
    assert connection.display_name == "Custom server"
    assert connection.metadata["target"] == "mcp.example.com"
    assert connection.metadata["oauth"]["client_id"] == "client-1"
    # Read-only tools start selected. Everything else waits for the owner.
    assert connection.available_tools == ["read_metrics"]
    assert set(connection.metadata["offered_tools"]) == {
        "read_metrics",
        "delete_dashboard",
    }
    assert fake.mcp_auth[-1] == "Bearer at-1"
    repository = Repository.objects.create(workspace=workspace, full_name="a/b")
    assert external_access(connection, repository).text == (
        "Allow these tools to use anything the credential can reach on mcp.example.com."
    )


def test_state_works_once_and_only_for_its_owner(
    fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    state = begin(fake, workspace, owner)
    stranger = get_user_model().objects.create_user(username="stranger")
    with pytest.raises(SignInError) as caught:
        complete_authorization(stranger, state, "good-code", "")
    assert caught.value.code == "invalid"
    complete_authorization(owner, state, "good-code", "")
    with pytest.raises(SignInError) as caught:
        complete_authorization(owner, state, "good-code", "")
    assert caught.value.code == "invalid"


def test_expired_and_refused_sign_ins(
    fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    state = begin(fake, workspace, owner)
    OAuthAuthorization.objects.filter(state=state).update(
        created_at=timezone.now() - timedelta(minutes=11)
    )
    with pytest.raises(SignInError) as caught:
        complete_authorization(owner, state, "good-code", "")
    assert caught.value.code == "expired"
    state = begin(fake, workspace, owner)
    with pytest.raises(SignInError) as caught:
        complete_authorization(owner, state, "", "access_denied")
    assert caught.value.code == "refused"
    assert not Connection.objects.filter(binding_key="metrics").exists()


def test_extra_granted_scopes_connect_and_remain_visible(
    client: Client, fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    fake.grant_scope = "read mcp:admin mcp:raw-api"
    connection = complete_authorization(
        owner, begin(fake, workspace, owner), "good-code", ""
    )
    connection.refresh_from_db()
    assert connection.scopes == ["mcp:admin", "mcp:raw-api", "read"]
    assert connection.metadata["oauth"]["requested_scopes"] == ["read"]
    assert connection.available_tools == ["read_metrics"]
    client.force_login(owner)
    payload = client.get("/api/connections").json()[0]
    assert payload["scopes"] == connection.scopes
    assert payload["requested_scopes"] == ["read"]
    assert "at-1" not in json.dumps(payload)
    assert "rt-1" not in json.dumps(payload)


@pytest.mark.parametrize("granted", ["read", "", "mcp:admin"])
def test_missing_required_scopes_save_nothing(
    granted: str, fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    fake.grant_scope = granted
    state = begin(fake, workspace, owner, scopes=("read", "write"))
    with pytest.raises(SignInError) as caught:
        complete_authorization(owner, state, "good-code", "")
    assert caught.value.code == "scope"
    assert not Connection.objects.filter(binding_key="metrics").exists()


def test_scopes_must_come_from_the_server_list(
    fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    with pytest.raises(MCPError, match="server's list"):
        begin(fake, workspace, owner, scopes=("admin",))


def test_client_secret_registration_is_refused(
    fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    fake.register_body = {"client_id": "c", "client_secret": "s"}
    with pytest.raises(MCPError, match="client secret"):
        begin(fake, workspace, owner)


def test_refresh_rotates_tokens_and_expires_on_refusal(
    fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    connection = complete_authorization(
        owner, begin(fake, workspace, owner), "good-code", ""
    )
    Connection.objects.filter(pk=connection.pk).update(expires_at=timezone.now())
    connection.refresh_from_db()

    fresh_connection(connection)
    assert connection.access_token == "at-2"
    saved = Connection.objects.get(pk=connection.pk)
    assert saved.refresh_token == "rt-2"
    assert saved.expires_at > timezone.now() + timedelta(minutes=50)

    fake.refresh = "rotated-elsewhere"
    Connection.objects.filter(pk=connection.pk).update(expires_at=timezone.now())
    connection.refresh_from_db()
    with pytest.raises(MCPRequestError, match="Reconnect"):
        fresh_connection(connection)
    assert Connection.objects.get(pk=connection.pk).status == Connection.Status.EXPIRED


def test_callback_redirects_with_fixed_codes(
    client: Client, fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    state = begin(fake, workspace, owner)
    response = client.get(
        "/api/connections/oauth/callback", {"state": state, "code": "good-code"}
    )
    assert response.status_code == 302
    assert response["Location"].endswith("/connections?oauth=invalid")

    client.force_login(owner)
    response = client.get(
        "/api/connections/oauth/callback", {"state": state, "code": "good-code"}
    )
    assert response["Location"].endswith(
        "/connections?oauth=connected&capability=metrics"
    )
    response = client.get(
        "/api/connections/oauth/callback", {"state": state, "code": "good-code"}
    )
    assert response["Location"].endswith("/connections?oauth=invalid")
    assert "at-1" not in response["Location"]


def test_inspect_and_start_api(
    client: Client, fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    client.force_login(owner)
    response = client.post(
        "/api/connections/mcp/inspect",
        data=json.dumps({"provider": "mcp", "settings": {"url": SERVER}}),
        content_type="application/json",
    )
    assert response.json() == {"oauth": True, "scopes": ["read", "write"]}
    response = client.post(
        "/api/connections/mcp/oauth/start",
        data=json.dumps(
            {
                "provider": "mcp",
                "binding_key": "metrics",
                "settings": {"url": SERVER},
                "scopes": ["read"],
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["authorize_url"].startswith(f"{ISSUER}/authorize?")
    stranger = get_user_model().objects.create_user(username="not-owner")
    client.force_login(stranger)
    response = client.post(
        "/api/connections/mcp/inspect",
        data=json.dumps({"provider": "mcp", "settings": {"url": SERVER}}),
        content_type="application/json",
    )
    assert response.status_code == 403


def test_custom_token_connection_and_tool_choice(
    fake: FakeServer, workspace: Workspace
) -> None:
    connection = create_mcp_connection(
        workspace, "mcp", "metrics", "", fake.static_token, {"url": SERVER}
    )
    assert connection.auth_strategy == Connection.AuthStrategy.API_KEY
    assert connection.available_tools == ["read_metrics"]
    assert fake.mcp_auth[-1] == f"Bearer {fake.static_token}"

    updated = update_mcp_connection(
        connection, None, None, None, ["read_metrics", "delete_dashboard"]
    )
    assert updated.available_tools == ["delete_dashboard", "read_metrics"]
    assert set(updated.metadata["tools"]) == {"delete_dashboard", "read_metrics"}
    with pytest.raises(MCPError, match="server's list"):
        update_mcp_connection(connection, None, None, None, ["drop_database"])


@pytest.mark.parametrize(
    "url", ["ftp://mcp.example.com/mcp", "https://user:pass@mcp.example.com/mcp"]
)
def test_custom_server_url_is_checked(workspace: Workspace, url: str) -> None:
    with pytest.raises(MCPError):
        create_mcp_connection(workspace, "mcp", "metrics", "", "t", {"url": url})


def test_built_in_tools_cannot_be_chosen(workspace: Workspace) -> None:
    connection = Connection(
        workspace=workspace,
        provider=Connection.Provider.GITHUB,
        kind=Connection.Kind.MCP,
        binding_key="github",
        auth_strategy=Connection.AuthStrategy.API_KEY,
        access_token="github_pat_x",
    )
    with pytest.raises(MCPError, match="fixed by SecondShift"):
        update_mcp_connection(connection, None, None, None, ["get_commit"])


def test_sign_in_keeps_the_approval_choice(
    fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    def sign_in(connection: Connection | None, require_approval: bool) -> Connection:
        url = start_authorization(
            workspace,
            owner,
            "mcp",
            "metrics",
            "",
            {"url": SERVER},
            ["read"],
            connection,
            require_approval,
        )
        query = parse_qs(urlsplit(url).query)
        fake.challenge = query["code_challenge"][0]
        return complete_authorization(owner, query["state"][0], "good-code", "")

    connection = sign_in(None, True)
    assert connection.require_approval is True

    # A reconnect keeps the connection's own setting.
    reconnected = sign_in(connection, False)
    reconnected.refresh_from_db()
    assert reconnected.require_approval is True


def test_sign_in_accepts_local_tool_schema_references(
    fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    fake.input_schema = {
        "type": "object",
        "$defs": {
            "Cursor": {"type": "object", "properties": {"id": {"type": "string"}}}
        },
        "properties": {
            "cursor": {"anyOf": [{"$ref": "#/$defs/Cursor"}, {"type": "null"}]}
        },
    }
    state = begin(fake, workspace, owner)
    connection = complete_authorization(owner, state, "good-code", "")
    connection.refresh_from_db()
    assert connection.status == Connection.Status.CONNECTED
    assert (
        connection.metadata["tools"]["read_metrics"]["inputSchema"] == fake.input_schema
    )


def test_refresh_saves_changed_grants_and_keeps_original_requirement(
    fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    connection = complete_authorization(
        owner, begin(fake, workspace, owner), "good-code", ""
    )
    for scope in ("read mcp:admin", "read mcp:raw-api", "read"):
        fake.grant_scope = scope
        Connection.objects.filter(pk=connection.pk).update(expires_at=timezone.now())
        connection.refresh_from_db()
        fresh_connection(connection)
        assert connection.scopes == sorted(scope.split())
        assert connection.metadata["oauth"]["requested_scopes"] == ["read"]
        saved = Connection.objects.get(pk=connection.pk)
        assert saved.scopes == connection.scopes
        assert saved.metadata["oauth"]["requested_scopes"] == ["read"]


def test_refresh_expires_when_required_scope_is_missing(
    fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    connection = complete_authorization(
        owner, begin(fake, workspace, owner), "good-code", ""
    )
    fake.grant_scope = "write"
    Connection.objects.filter(pk=connection.pk).update(expires_at=timezone.now())
    connection.refresh_from_db()
    with pytest.raises(MCPRequestError, match="required access"):
        fresh_connection(connection)
    saved = Connection.objects.get(pk=connection.pk)
    assert saved.status == Connection.Status.EXPIRED
    assert saved.access_token == "at-1"
    assert saved.scopes == ["read"]


def test_legacy_refresh_persists_its_existing_scope_baseline(
    fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    connection = complete_authorization(
        owner, begin(fake, workspace, owner), "good-code", ""
    )
    del connection.metadata["oauth"]["requested_scopes"]
    connection.expires_at = timezone.now()
    connection.save()
    fake.grant_scope = "read mcp:admin"
    fresh_connection(connection)
    assert connection.scopes == ["mcp:admin", "read"]
    assert connection.metadata["oauth"]["requested_scopes"] == ["read"]


def test_reconnect_requests_original_scopes_not_server_added_permissions(
    client: Client, fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    fake.grant_scope = "read mcp:admin"
    connection = complete_authorization(
        owner, begin(fake, workspace, owner), "good-code", ""
    )
    client.force_login(owner)
    response = client.post(
        "/api/connections/mcp/oauth/start",
        data=json.dumps({"provider": "mcp", "connection_id": connection.pk}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    query = parse_qs(urlsplit(response.json()["authorize_url"]).query)
    assert query["scope"] == ["read"]


@pytest.mark.parametrize(
    "scope",
    [
        ["read"],
        "read " + "x" * 101,
        " ".join(f"s{i}" for i in range(21)),
        "read bad\x00scope",
    ],
)
def test_invalid_granted_scope_response_is_rejected(scope: Any) -> None:
    with pytest.raises(MCPError, match="invalid OAuth scopes"):
        granted_scopes({"scope": scope}, ["read"])
