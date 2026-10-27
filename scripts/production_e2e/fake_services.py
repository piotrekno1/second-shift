"""HTTPS fakes for GitHub, an OpenAI-compatible model and a webhook receiver.

The production stack reaches these through Docker DNS aliases (api.github.com,
github.com, model.e2e.test, hooks.e2e.test) on a network whose addresses count
as public, with a certificate signed by a test CA the app images trust.
"""

import base64
import os
import ssl
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, "/scripts/release_smoke")
import run as smoke

APP_ID = 424242
INSTALLATION_ID = 7001
REPOSITORY_ID = 9001
FULL_NAME = "acme/e2e-fixture"
USER_TOKEN = "gho_e2e_user_token"
INSTALLATION_TOKEN = "ghs_e2e_installation_token"
GIT_ROOT = Path("/srv/git")
BARE = GIT_ROOT / f"{FULL_NAME}.git"
WORK = Path("/srv/work")
FIXTURE = Path("/scripts/release_smoke/fixture")
CERTS = Path("/scripts/production_e2e/certs")
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "E2E",
    "GIT_AUTHOR_EMAIL": "e2e@example.invalid",
    "GIT_COMMITTER_NAME": "E2E",
    "GIT_COMMITTER_EMAIL": "e2e@example.invalid",
}

recorder = smoke.Recorder()
github_log: list[dict] = []
problems: list[str] = []


def git(*args: str, cwd: Path = WORK) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit_marker(marker: str) -> str:
    readme = WORK / "README.md"
    text = (FIXTURE / "README.md").read_text()
    readme.write_text(text.replace("SMOKE_MARKER_PLACEHOLDER", marker))
    git("add", "--all")
    git("commit", "--quiet", "--no-gpg-sign", "-m", f"Marker {marker}")
    git("push", "--quiet", str(BARE), "HEAD:main")
    return git("rev-parse", "HEAD")


def setup_repository() -> None:
    if BARE.exists():
        return
    subprocess.run(["cp", "-r", str(FIXTURE), str(WORK)], check=True)
    git("init", "--quiet", "--initial-branch", "main")
    BARE.parent.mkdir(parents=True, exist_ok=True)
    git("init", "--quiet", "--bare", str(BARE), cwd=GIT_ROOT)
    commit_marker("smoke-000000000000")


def repository_payload() -> dict:
    return {
        "id": REPOSITORY_ID,
        "full_name": FULL_NAME,
        "name": "e2e-fixture",
        "private": True,
        "default_branch": "main",
        "owner": {"login": "acme"},
    }


class Handler(smoke.FakeServicesHandler):
    recorder = recorder

    def host(self) -> str:
        return (self.headers.get("Host") or "").split(":")[0]

    def log_github(self) -> None:
        github_log.append(
            {
                "method": self.command,
                "host": self.host(),
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
                "time": time.time(),
            }
        )

    def require_installation_token(self) -> bool:
        if self.headers.get("Authorization") != f"Bearer {INSTALLATION_TOKEN}":
            problems.append(f"{self.command} {self.path} without installation token")
            self.send_json(401, {"message": "Bad credentials"})
            return False
        return True

    def do_GET(self) -> None:
        host = self.host()
        if host == "api.github.com":
            self.log_github()
            self.github_api_get()
        elif host == "github.com":
            self.log_github()
            self.git_backend()
        elif host == "hooks.e2e.test" and self.path == "/_e2e/state":
            with recorder.lock:
                state = {
                    "model_requests": recorder.model_requests,
                    "model_auth": recorder.model_auth,
                    "webhooks": recorder.webhooks,
                    "github": github_log,
                    "problems": problems,
                    "head": git("rev-parse", "HEAD"),
                }
            self.send_json(200, state)
        else:
            super().do_GET()

    def do_POST(self) -> None:
        host = self.host()
        if host == "api.github.com":
            self.log_github()
            self.github_api_post()
        elif host == "github.com" and self.path.startswith("/login/oauth/"):
            self.log_github()
            body = self.read_json()
            if body.get("code") != "e2e-code" or not body.get("client_secret"):
                problems.append("OAuth exchange without the expected code or secret")
                self.send_json(200, {"error": "bad_verification_code"})
                return
            self.send_json(200, {"access_token": USER_TOKEN, "token_type": "bearer"})
        elif host == "github.com":
            self.log_github()
            self.git_backend()
        elif host == "hooks.e2e.test" and self.path == "/_e2e/commit":
            marker = self.read_json()["marker"]
            self.send_json(200, {"sha": commit_marker(marker)})
        else:
            super().do_POST()

    def github_api_get(self) -> None:
        path = self.path.split("?")[0]
        if path == "/user/installations":
            if self.headers.get("Authorization") != f"Bearer {USER_TOKEN}":
                problems.append("installations listed without the user token")
            self.send_json(
                200,
                {
                    "total_count": 1,
                    "installations": [
                        {
                            "id": INSTALLATION_ID,
                            "app_id": APP_ID,
                            "account": {"login": "acme"},
                        }
                    ],
                },
            )
        elif not self.require_installation_token():
            return
        elif path == "/installation/repositories":
            self.send_json(
                200, {"total_count": 1, "repositories": [repository_payload()]}
            )
        elif path == f"/repositories/{REPOSITORY_ID}":
            self.send_json(200, repository_payload())
        elif path == f"/repos/{FULL_NAME}/branches":
            self.send_json(200, [{"name": "main"}])
        elif path == f"/repositories/{REPOSITORY_ID}/commits/main":
            self.send_json(200, {"sha": git("rev-parse", "HEAD")})
        else:
            self.send_json(404, {"message": "Not Found"})

    def github_api_post(self) -> None:
        path = self.path.split("?")[0]
        if path == f"/app/installations/{INSTALLATION_ID}/access_tokens":
            parts = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
            if parts.count(".") != 2:
                problems.append("installation token requested without an app JWT")
            body = self.read_json()
            if body.get("permissions") != {"contents": "read"}:
                problems.append(f"installation token permissions {body}")
            self.send_json(
                201, {"token": INSTALLATION_TOKEN, "expires_at": "2099-01-01T00:00:00Z"}
            )
        else:
            self.send_json(404, {"message": "Not Found"})

    def git_backend(self) -> None:
        """Serve Git smart HTTP through git http-backend (CGI)."""
        expected = base64.b64encode(f"x-access-token:{INSTALLATION_TOKEN}".encode())
        authorization = self.headers.get("Authorization", "")
        if authorization.lower() != f"basic {expected.decode()}".lower():
            problems.append(f"git {self.path} without the installation token")
        path, _, query = self.path.partition("?")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        environment = {
            "GIT_PROJECT_ROOT": str(GIT_ROOT),
            "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "REQUEST_METHOD": self.command,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "REMOTE_ADDR": self.client_address[0],
            "PATH": os.environ["PATH"],
        }
        if self.headers.get("Content-Encoding"):
            environment["HTTP_CONTENT_ENCODING"] = self.headers["Content-Encoding"]
        if self.headers.get("Git-Protocol"):
            environment["GIT_PROTOCOL"] = self.headers["Git-Protocol"]
        result = subprocess.run(
            ["git", "http-backend"], input=body, env=environment, capture_output=True
        )
        head, _, payload = result.stdout.partition(b"\r\n\r\n")
        status = 200
        headers = []
        for line in head.decode().split("\r\n"):
            name, _, value = line.partition(":")
            if name.lower() == "status":
                status = int(value.strip().split()[0])
            elif name:
                headers.append((name, value.strip()))
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    setup_repository()
    server = ThreadingHTTPServer(("0.0.0.0", 443), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERTS / "fake.pem", CERTS / "fake.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print("fake services ready", flush=True)
    threading.Thread(target=server.serve_forever).start()


if __name__ == "__main__":
    main()
