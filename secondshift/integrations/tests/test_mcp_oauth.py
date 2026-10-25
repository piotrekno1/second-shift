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

from secondshift.core.models import Workspace
from secondshift.core.services import create_workspace
from secondshift.integrations.external import external_access
from secondshift.integrations.mcp.base import MCPError, MCPRequestError
from secondshift.integrations.mcp.client import (
    create_mcp_connection,
    update_mcp_connection,
)
from secondshift.integrations.mcp.oauth import fresh_connection, redirect_uri
from secondshift.integrations.mcp.signin import (
    SignInError,
    complete_authorization,
    start_authorization,
)
from secondshift.integrations.models import Connection, OAuthAuthorization
from secondshift.repositories.models import Repository

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
                            "inputSchema": OBJECT_SCHEMA,
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
            f"secondshift.integrations.mcp.{module}.MCPTransport",
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


def test_extra_granted_scope_saves_nothing(
    fake: FakeServer, workspace: Workspace, owner: Any
) -> None:
    fake.grant_scope = "read write"
    state = begin(fake, workspace, owner)
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
