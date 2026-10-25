"""Smoke-test a built SecondShift wheel the way a new user would run it.

Usage:
    python scripts/release_smoke/run.py dist/secondshift-*.whl

The script installs the wheel into a clean virtual environment outside the
checkout and starts `secondshift up` with its own data folder. It then drives
the HTTP API like the dashboard does. A fake OpenAI-compatible model and a
webhook receiver run in this process, so no provider credentials are needed.

It checks two runs:
- `smoke-report` reads README.md in the sandbox and reports its marker. The
  report must reach the webhook.
- `smoke-failure` is rejected by the fake model with HTTP 401. The run must
  fail with a readable reason, and the failure must reach the webhook too.

Needs uv, git and a running Docker. Uses only the standard library, so it
never imports SecondShift from the checkout.
"""

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixture"
MODEL_ID = "smoke-model"
WEBHOOK_BINDING = "smoke-reports"
FAILURE_SKILL = "smoke-failure"
START_SECONDS = 240
RUN_SECONDS = 300
FINISHED = {"succeeded", "failed", "canceled"}


class SmokeFailure(Exception):
    pass


def check(condition: object, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def log(message: str) -> None:
    print(f"[smoke] {message}", flush=True)


# Fake services ---------------------------------------------------------------


@dataclass
class Recorder:
    """What the fake services received, shared with the test thread."""

    model_requests: list[dict[str, Any]] = field(default_factory=list)
    model_auth: list[str] = field(default_factory=list)
    webhooks: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def chat_reply(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """A scripted model. It asks for README.md once, then reports its marker."""
    messages = body.get("messages", [])
    text = json.dumps(messages)
    if FAILURE_SKILL in text:
        return 401, {"error": {"message": "Smoke model rejected this request."}}
    tool_results = [m for m in messages if m.get("role") == "tool"]
    usage = {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18}
    base = {
        "id": f"chatcmpl-{secrets.token_hex(4)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", MODEL_ID),
        "usage": usage,
    }
    if not body.get("tools") or tool_results:
        found = re.search(r"Marker: (smoke-[0-9a-f]+)", json.dumps(tool_results))
        content = (
            f"Smoke report. Marker: {found.group(1)}" if found else "Smoke check ok."
        )
        message = {"role": "assistant", "content": content}
        finish = "stop"
    else:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_readme",
                    "type": "function",
                    "function": {
                        "name": "workspace_read_file",
                        "arguments": json.dumps({"path": "README.md"}),
                    },
                }
            ],
        }
        finish = "tool_calls"
    return 200, {
        **base,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }


def stream_chunks(reply: dict[str, Any]) -> list[dict[str, Any]]:
    """The same reply as chat.completion.chunk events, for streamed requests."""
    choice = reply["choices"][0]
    message = choice["message"]
    delta: dict[str, Any] = {"role": "assistant"}
    if message.get("content"):
        delta["content"] = message["content"]
    if message.get("tool_calls"):
        delta["tool_calls"] = [
            {"index": index, **call} for index, call in enumerate(message["tool_calls"])
        ]
    head = {key: reply[key] for key in ("id", "created", "model")}
    head["object"] = "chat.completion.chunk"
    return [
        {**head, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {
            **head,
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}
            ],
        },
        {**head, "choices": [], "usage": reply["usage"]},
    ]


def handler_for(recorder: Recorder) -> type[BaseHTTPRequestHandler]:  # noqa: C901
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def send_json(self, status: int, payload: object) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self) -> None:
            if self.path.rstrip("/").endswith("/models"):
                self.send_json(200, {"data": [{"id": MODEL_ID, "object": "model"}]})
            else:
                self.send_json(404, {"error": {"message": "Not found."}})

        def do_POST(self) -> None:
            body = self.read_json()
            if self.path == "/hook":
                with recorder.lock:
                    recorder.webhooks.append(body)
                self.send_json(200, {"ok": True})
                return
            if not self.path.endswith("/chat/completions"):
                self.send_json(404, {"error": {"message": "Not found."}})
                return
            with recorder.lock:
                recorder.model_requests.append(body)
                recorder.model_auth.append(self.headers.get("Authorization", ""))
            status, reply = chat_reply(body)
            if status != 200 or not body.get("stream"):
                self.send_json(status, reply)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in stream_chunks(reply):
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

    return Handler


def start_fake_services(recorder: Recorder) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(recorder))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# The installed app -----------------------------------------------------------


def free_port_block(size: int = 3) -> int:
    """A port where `size` consecutive ports are free (web, gateway, runner)."""
    for _ in range(50):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            start = probe.getsockname()[1]
        if start + size > 65535:
            continue
        if all(port_is_free(start + offset) for offset in range(size)):
            return start
    raise SmokeFailure("Could not find free ports.")


def port_is_free(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def run(argv: list[str], **kwargs: Any) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise SmokeFailure(
            f"{' '.join(argv)} failed ({result.returncode}):\n"
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout


def install(wheel: Path, root: Path) -> Path:
    venv = root / "venv"
    run(["uv", "venv", str(venv), "--python", "3.13", "--quiet"])
    python = venv / "bin" / "python"
    run(["uv", "pip", "install", "--quiet", "--python", str(python), str(wheel)])
    return venv / "bin" / "secondshift"


def make_fixture(root: Path) -> tuple[Path, str]:
    repository = root / "fixture-repo"
    shutil.copytree(FIXTURE, repository)
    marker = f"smoke-{secrets.token_hex(6)}"
    readme = repository / "README.md"
    readme.write_text(readme.read_text().replace("SMOKE_MARKER_PLACEHOLDER", marker))
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Smoke",
        "GIT_AUTHOR_EMAIL": "smoke@example.invalid",
        "GIT_COMMITTER_NAME": "Smoke",
        "GIT_COMMITTER_EMAIL": "smoke@example.invalid",
    }
    for argv in (
        ["git", "init", "--quiet", "--initial-branch", "main"],
        ["git", "add", "--all"],
        ["git", "commit", "--quiet", "--no-gpg-sign", "-m", "Smoke fixture"],
    ):
        run(argv, cwd=repository, env=env)
    return repository, marker


def sandbox_containers() -> set[str]:
    output = run(
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            "name=secondshift-run-",
            "--format",
            "{{.Names}}",
        ]
    )
    return set(output.split())


class App:
    """`secondshift up`, started from outside the checkout."""

    def __init__(self, command: Path, root: Path) -> None:
        self.command = command
        self.root = root
        self.port = free_port_block()
        self.url = f"http://127.0.0.1:{self.port}"
        self.log_path = root / "secondshift-up.log"
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("DJANGO_", "SECONDSHIFT_", "DATABASE_"))
        }
        self.env["SECONDSHIFT_DATA_DIR"] = str(root / "data")
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        log_file = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [str(self.command), "up", "--no-browser", "--port", str(self.port)],
            cwd=self.root,
            env=self.env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + START_SECONDS
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise SmokeFailure("secondshift up exited before it was ready.")
            if "SecondShift is running at" in self.log_path.read_text(errors="replace"):
                return
            time.sleep(1)
        raise SmokeFailure(f"secondshift up was not ready in {START_SECONDS} s.")

    def sign_in_link(self) -> str:
        output = run(
            [str(self.command), "login", "--url", self.url],
            cwd=self.root,
            env=self.env,
        )
        links = re.findall(r"http://\S+/-/sign-in/\S+", output)
        check(links, f"secondshift login printed no link:\n{output}")
        return links[0]

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        os.killpg(self.process.pid, signal.SIGINT)
        try:
            self.process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=10)


class Client:
    """A signed-in browser session against the app's HTTP API."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )

    def csrf_token(self) -> str:
        return next(
            (
                cookie.value or ""
                for cookie in self.cookies
                if cookie.name == "csrftoken"
            ),
            "",
        )

    def raw(self, method: str, path: str, body: object = None) -> tuple[int, bytes]:
        url = path if path.startswith("http") else self.base_url + path
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if method != "GET":
            request.add_header("X-CSRFToken", self.csrf_token())
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def json(self, method: str, path: str, body: object = None) -> Any:
        status, content = self.raw(method, path, body)
        check(
            200 <= status < 300,
            f"{method} {path} answered HTTP {status}: {content[:500]!r}",
        )
        return json.loads(content)


# The checks --------------------------------------------------------------------


def check_dashboard(client: Client, link: str) -> None:
    status, page = client.raw("GET", link)
    check(status == 200, f"The sign-in link answered HTTP {status}.")
    html = page.decode()
    check('id="app"' in html, "The dashboard page is not the packaged index.html.")
    assets = re.findall(r'(?:src|href)="(/static/assets/[^"]+)"', html)
    check(assets, "The dashboard page references no packaged assets.")
    status, _ = client.raw("GET", assets[0])
    check(status == 200, f"The packaged asset {assets[0]} answered HTTP {status}.")
    check(client.csrf_token(), "Signing in set no CSRF cookie.")
    me = client.json("GET", "/api/me")
    log(f"Signed in as {me.get('email') or 'the owner'}. Dashboard assets load.")


def connect(client: Client, model_url: str, webhook_url: str, api_key: str) -> int:
    model = client.json(
        "POST",
        "/api/connections/model",
        {
            "provider": "openai_compatible",
            "display_name": "Smoke model",
            "api_key": api_key,
            "base_url": model_url,
            "default_model": MODEL_ID,
            "is_default": True,
        },
    )
    tested = client.json("POST", f"/api/connections/{model['id']}/test")
    check(tested.get("ok"), f"The model provider check failed: {tested}")
    webhook = client.json(
        "POST",
        "/api/connections/webhook",
        {"binding_key": WEBHOOK_BINDING, "display_name": "Smoke", "url": webhook_url},
    )
    log("Connected the fake model and the webhook receiver.")
    return int(webhook["id"])


def add_repository(client: Client, path: Path, webhook_id: int) -> dict[str, int]:
    client.json("POST", "/api/repositories/local", {"path": str(path)})
    jobs = {
        detail["key"]: detail
        for job in client.json("GET", "/api/jobs")
        if (detail := client.json("GET", f"/api/jobs/{job['id']}"))
    }
    check(
        {"smoke-report", "smoke-failure"} <= jobs.keys(),
        f"Repository discovery found {sorted(jobs)}.",
    )
    for key, job in jobs.items():
        for capability in job["capabilities"]:
            client.json(
                "POST",
                f"/api/jobs/{job['id']}/capabilities/{capability['id']}/grant",
                {"decision": "approved", "connection_id": webhook_id},
            )
        log(f"Discovered {key} and approved its notification.")
    return {key: int(job["id"]) for key, job in jobs.items()}


def run_job(client: Client, job_id: int) -> dict[str, Any]:
    run = client.json("POST", f"/api/jobs/{job_id}/run")
    deadline = time.monotonic() + RUN_SECONDS
    while time.monotonic() < deadline:
        detail = client.json("GET", f"/api/runs/{run['id']}")
        if detail["status"] in FINISHED:
            return detail
        time.sleep(2)
    raise SmokeFailure(f"Run {run['id']} did not finish in {RUN_SECONDS} s.")


def webhook_for(recorder: Recorder, run_id: int) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with recorder.lock:
            for payload in recorder.webhooks:
                if payload.get("run", {}).get("id") == run_id:
                    return payload
        time.sleep(1)
    raise SmokeFailure(f"No webhook arrived for run {run_id}.")


def check_success(
    client: Client, recorder: Recorder, job_id: int, marker: str
) -> dict[str, Any]:
    run = run_job(client, job_id)
    check(
        run["status"] == "succeeded",
        f"The smoke report failed: {run.get('failure') or run.get('summary')}",
    )
    status, output = client.raw("GET", f"/api/runs/{run['id']}/files/output")
    check(status == 200, f"The run output answered HTTP {status}.")
    check(marker in output.decode(), "The report does not contain the README marker.")
    status, input_file = client.raw("GET", f"/api/runs/{run['id']}/files/input")
    check(status == 200, f"The run input answered HTTP {status}.")
    check(
        "Read `README.md`" in input_file.decode(),
        "The stored input does not contain the skill text.",
    )
    payload = webhook_for(recorder, run["id"])
    check(payload["run"]["status"] == "succeeded", "The webhook status is wrong.")
    check(marker in payload["text"], "The webhook report has no marker.")
    log(f"Run {run['id']} succeeded. Report, input and webhook check out.")
    return run


def check_failure(client: Client, recorder: Recorder, job_id: int) -> dict[str, Any]:
    run = run_job(client, job_id)
    check(run["status"] == "failed", f"The failure run ended as {run['status']}.")
    failure = run.get("failure") or {}
    check(
        failure.get("label") == "Model provider" and "401" in failure.get("reason", ""),
        f"The failure reason is not readable: {failure}",
    )
    payload = webhook_for(recorder, run["id"])
    check(payload["run"]["status"] == "failed", "The failure webhook status is wrong.")
    log(f"Run {run['id']} failed as expected: {failure['reason']}")
    return run


def check_secret_kept(
    api_key: str, recorder: Recorder, runs: list[dict[str, Any]], data: Path
) -> None:
    with recorder.lock:
        auth = list(recorder.model_auth)
        webhooks = json.dumps(recorder.webhooks)
        prompts = json.dumps(recorder.model_requests)
    check(
        auth and all(value == f"Bearer {api_key}" for value in auth),
        "The gateway did not send the provider key to the provider.",
    )
    check(api_key not in webhooks, "The provider key leaked into a webhook.")
    check(api_key not in prompts, "The provider key leaked into a model prompt.")
    check(api_key not in json.dumps(runs), "The provider key leaked into run details.")
    for path in (data / "artifacts").rglob("*"):
        if path.is_file():
            check(
                api_key.encode() not in path.read_bytes(),
                f"The provider key leaked into {path.name}.",
            )
    log("The provider key reached only the provider.")


# Main --------------------------------------------------------------------------


def save_diagnostics(root: Path, target: Path, recorder: Recorder) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in ("secondshift-up.log",):
        if (root / name).exists():
            shutil.copy(root / name, target / name)
    with recorder.lock:
        (target / "fake-services.json").write_text(
            json.dumps(
                {
                    "model_requests": recorder.model_requests,
                    "webhooks": recorder.webhooks,
                },
                indent=2,
            )
        )
    containers = subprocess.run(
        ["docker", "ps", "--all"], capture_output=True, text=True, check=False
    )
    (target / "docker-ps.txt").write_text(containers.stdout + containers.stderr)
    log(f"Saved diagnostics to {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wheel", type=Path)
    parser.add_argument(
        "--diagnostics",
        type=Path,
        default=Path("smoke-diagnostics"),
        help="Where to save logs when the test fails.",
    )
    parser.add_argument("--keep", action="store_true", help="Keep the temp folder.")
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    check(wheel.is_file(), f"No wheel at {wheel}.")

    root = Path(tempfile.mkdtemp(prefix="secondshift-smoke-")).resolve()
    recorder = Recorder()
    server = start_fake_services(recorder)
    fake_url = f"http://127.0.0.1:{server.server_address[1]}"
    containers_before = sandbox_containers()
    app: App | None = None
    failed = True
    try:
        log(f"Installing {wheel.name} into {root}")
        command = install(wheel, root)
        repository, marker = make_fixture(root)
        app = App(command, root)
        log(f"Starting secondshift up on port {app.port}")
        app.start()
        client = Client(app.url)
        check_dashboard(client, app.sign_in_link())
        api_key = f"smoke-key-{secrets.token_hex(12)}"
        webhook_id = connect(client, f"{fake_url}/v1", f"{fake_url}/hook", api_key)
        jobs = add_repository(client, repository, webhook_id)
        runs = [
            check_success(client, recorder, jobs["smoke-report"], marker),
            check_failure(client, recorder, jobs["smoke-failure"]),
        ]
        check_secret_kept(api_key, recorder, runs, root / "data")
        failed = False
        log("Passed.")
        return 0
    except SmokeFailure as error:
        log(f"FAILED: {error}")
        return 1
    except Exception:
        log("FAILED with an unexpected error:")
        traceback.print_exc()
        return 1
    finally:
        if app is not None:
            app.stop()
        server.shutdown()
        for name in sandbox_containers() - containers_before:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True)
        if failed:
            save_diagnostics(root, args.diagnostics.resolve(), recorder)
            if app is not None and app.log_path.exists():
                tail = app.log_path.read_text(errors="replace").splitlines()[-60:]
                print("\n".join(["[smoke] Last lines of secondshift up:", *tail]))
        if failed or args.keep:
            log(f"Kept {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
