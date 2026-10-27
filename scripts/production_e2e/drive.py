"""Use a production deployment the way its first owner would.

Runs inside the deployment's network; host.sh starts it:
    python drive.py first INVITE_LINK
    python drive.py after-restart
"""

import json
import os
import re
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import boto3

BASE = "https://secondshift.e2e.test"
FAKE = "https://hooks.e2e.test"
STATE = Path("/state/state.json")
EMAIL = "owner@e2e.test"
PASSWORD = "e2e-Owner-password-" + "x" * 8
REPOSITORY_ID = 9001
FINISHED = {"succeeded", "failed", "canceled"}
results: list[tuple[str, bool, str]] = []


class Failure(Exception):
    pass


def check(name: str, ok: object, detail: object = "") -> None:
    results.append((name, bool(ok), str(detail)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {str(detail)[:200]!r}", flush=True)
    if not ok:
        raise Failure(name)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class Client:
    """A browser session. Caddy's internal CA is not trusted, so skip verify."""

    def __init__(self) -> None:
        self.cookies = CookieJar()
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        handlers = [
            urllib.request.HTTPCookieProcessor(self.cookies),
            urllib.request.HTTPSHandler(context=context),
        ]
        self.follow = urllib.request.build_opener(*handlers)
        self.stay = urllib.request.build_opener(*handlers, NoRedirect)

    def csrf(self) -> str:
        return next((c.value or "" for c in self.cookies if c.name == "csrftoken"), "")

    def raw(
        self,
        method: str,
        path: str,
        body: object = None,
        form: dict | None = None,
        follow: bool = True,
    ) -> tuple[int, bytes, dict]:
        url = path if path.startswith("http") else BASE + path
        data = None
        request = urllib.request.Request(url, method=method)
        if form is not None:
            data = urlencode(form).encode()
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        elif body is not None:
            data = json.dumps(body).encode()
            request.add_header("Content-Type", "application/json")
        request.data = data
        if method != "GET":
            request.add_header("X-CSRFToken", self.csrf())
            request.add_header("Referer", BASE + "/")
        opener = self.follow if follow else self.stay
        try:
            with opener.open(request, timeout=200) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)

    def json(self, method: str, path: str, body: object = None) -> Any:
        status, content, _ = self.raw(method, path, body)
        if not 200 <= status < 300:
            raise Failure(f"{method} {path} answered {status}: {content[:400]!r}")
        return json.loads(content) if content else None


def fake_state() -> dict:
    with urllib.request.urlopen(FAKE + "/_e2e/state", timeout=10) as response:
        return json.loads(response.read())


def s3() -> Any:
    return boto3.client(
        "s3",
        endpoint_url="http://rustfs:9000",
        aws_access_key_id=os.environ["S3_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET"],
        region_name="us-east-1",
    )


def bucket_objects() -> dict[str, bytes]:
    client = s3()
    bucket = os.environ["S3_BUCKET"]
    keys = [
        item["Key"]
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket)
        for item in page.get("Contents", [])
    ]
    return {
        key: client.get_object(Bucket=bucket, Key=key)["Body"].read() for key in keys
    }


def form_token(html: str) -> str:
    found = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    if not found:
        raise Failure("No CSRF token in the form.")
    return found.group(1)


def sign_up_owner(client: Client, link: str) -> None:
    link = BASE + urlsplit(link).path
    status, _, _ = client.raw("GET", link)
    check("invite link opens the sign-up page", status == 200, status)
    status, page, _ = client.raw("GET", "/accounts/signup/")
    status, page, headers = client.raw(
        "POST",
        "/accounts/signup/",
        form={
            "csrfmiddlewaretoken": form_token(page.decode()),
            "email": EMAIL,
            "password1": PASSWORD,
            "password2": PASSWORD,
        },
        follow=False,
    )
    check(
        "owner signs up with the invite",
        status == 302,
        f"{status} {headers.get('Location')}",
    )
    pending = client.json("GET", "/api/invites/pending")
    check(
        "invite waits for confirmation", pending and pending["role"] == "owner", pending
    )
    client.json("POST", "/api/invites/pending/accept")
    me = client.json("GET", "/api/me")
    check("owner is signed in", me.get("email") == EMAIL, me.get("email"))


def sign_in(client: Client) -> None:
    status, page, _ = client.raw("GET", "/accounts/login/")
    status, _, headers = client.raw(
        "POST",
        "/accounts/login/",
        form={
            "csrfmiddlewaretoken": form_token(page.decode()),
            "login": EMAIL,
            "password": PASSWORD,
        },
        follow=False,
    )
    check(
        "owner signs in with a password",
        status == 302,
        f"{status} {headers.get('Location')}",
    )
    me = client.json("GET", "/api/me")
    check("session belongs to the owner", me.get("email") == EMAIL, me.get("email"))


def connect_github(client: Client) -> int:
    status, _, headers = client.raw("GET", "/-/github/connect/", follow=False)
    location = headers.get("Location", "")
    check(
        "connect redirects to GitHub authorization",
        status == 302
        and location.startswith("https://github.com/login/oauth/authorize"),
        location,
    )
    state = parse_qs(urlsplit(location).query)["state"][0]
    status, _, headers = client.raw(
        "GET",
        f"/-/github/callback/?code=e2e-code&installation_id=7001&setup_action=install&state={state}",
        follow=False,
    )
    check(
        "callback links the installation",
        status == 302 and "github=connected" in headers.get("Location", ""),
        f"{status} {headers.get('Location')}",
    )
    available = client.json("GET", "/api/repositories/github/available")
    repositories = available.get("repositories") or available.get("available") or []
    match = [
        item for item in repositories if item.get("repository_id") == REPOSITORY_ID
    ]
    check("installation lists the repository", match, available)
    connection_id = match[0]["connection_id"]
    branches = client.json(
        "GET", f"/api/repositories/github/{connection_id}/{REPOSITORY_ID}/branches"
    )
    check("branches are listed", branches == ["main"], branches)
    return connection_id


def import_repository(client: Client, connection_id: int) -> dict:
    repository = client.json(
        "POST",
        "/api/repositories/github",
        {
            "connection_id": connection_id,
            "repository_id": REPOSITORY_ID,
            "branch": "main",
        },
    )
    check(
        "GitHub repository imports",
        repository["full_name"] == "acme/e2e-fixture" and repository["job_count"] == 2,
        repository,
    )
    return repository


def connect_services(client: Client, api_key: str) -> int:
    model = client.json(
        "POST",
        "/api/connections/model",
        {
            "provider": "openai_compatible",
            "display_name": "E2E model",
            "api_key": api_key,
            "base_url": "https://model.e2e.test/v1",
            "default_model": "smoke-model",
            "is_default": True,
        },
    )
    tested = client.json("POST", f"/api/connections/{model['id']}/test")
    check("model provider check passes over public HTTPS", tested.get("ok"), tested)
    webhook = client.json(
        "POST",
        "/api/connections/webhook",
        {
            "binding_key": "smoke-reports",
            "display_name": "E2E",
            "url": "https://hooks.e2e.test/hook",
        },
    )
    return int(webhook["id"])


def approve_jobs(client: Client, webhook_id: int) -> dict[str, int]:
    jobs = {}
    for job in client.json("GET", "/api/jobs"):
        detail = client.json("GET", f"/api/jobs/{job['id']}")
        for capability in detail["capabilities"]:
            client.json(
                "POST",
                f"/api/jobs/{job['id']}/capabilities/{capability['id']}/grant",
                {"decision": "approved", "connection_id": webhook_id},
            )
        jobs[detail["key"]] = int(job["id"])
    check(
        "both jobs discovered and approved",
        set(jobs) == {"smoke-report", "smoke-failure"},
        jobs,
    )
    return jobs


def run_job(client: Client, job_id: int) -> dict:
    run = client.json("POST", f"/api/jobs/{job_id}/run")
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        detail = client.json("GET", f"/api/runs/{run['id']}")
        if detail["status"] in FINISHED:
            return detail
        time.sleep(2)
    raise Failure(f"run {run['id']} did not finish")


def webhook_for(run_id: int) -> dict:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        for payload in fake_state()["webhooks"]:
            if payload.get("run", {}).get("id") == run_id:
                return payload
        time.sleep(1)
    raise Failure(f"no webhook for run {run_id}")


def check_report(client: Client, job_id: int, marker: str, commit: str) -> dict:
    run = run_job(client, job_id)
    events = [event["message"] for event in run.get("events", [])]
    check(
        "report run succeeds",
        run["status"] == "succeeded",
        run.get("failure") or run["status"],
    )
    check("run used the pinned commit", run["commit_sha"] == commit, run["commit_sha"])
    check(
        "run used the isolated runner sandbox",
        "Sandbox started without network access." in events,
        events,
    )
    status, output, _ = client.raw("GET", f"/api/runs/{run['id']}/files/output")
    check(
        "report reads the repository through gVisor",
        status == 200 and marker in output.decode(),
        output[:200],
    )
    status, stored, _ = client.raw("GET", f"/api/runs/{run['id']}/files/input")
    check("skill input is stored", status == 200 and b"README.md" in stored, status)
    payload = webhook_for(run["id"])
    check(
        "report reaches the webhook",
        marker in payload.get("text", ""),
        payload.get("text"),
    )
    return run


def first(link: str) -> None:
    client = Client()
    status, _, _ = client.raw("GET", "/-/health/")
    check("public HTTPS health check", status == 200, status)
    sign_up_owner(client, link)
    connection_id = connect_github(client)
    repository = import_repository(client, connection_id)
    head = fake_state()["head"]
    api_key = f"e2e-key-{secrets.token_hex(12)}"
    webhook_id = connect_services(client, api_key)
    jobs = approve_jobs(client, webhook_id)
    enabled = client.json(
        "POST", f"/api/repositories/{repository['id']}/enabled", {"enabled": True}
    )
    check("repository enabled", enabled["enabled"], enabled["enabled"])

    report = check_report(client, jobs["smoke-report"], "smoke-000000000000", head)
    failure = run_job(client, jobs["smoke-failure"])
    reason = (failure.get("failure") or {}).get("reason", "")
    check(
        "failing run reports a readable reason",
        failure["status"] == "failed" and "401" in reason,
        reason,
    )
    check(
        "failure reaches the webhook",
        webhook_for(failure["id"])["run"]["status"] == "failed",
    )

    marker = "smoke-" + secrets.token_hex(6)
    with urllib.request.urlopen(
        urllib.request.Request(
            FAKE + "/_e2e/commit",
            data=json.dumps({"marker": marker}).encode(),
            method="POST",
        ),
        timeout=30,
    ) as response:
        new_head = json.loads(response.read())["sha"]
    validation = client.json("POST", f"/api/repositories/{repository['id']}/validate")
    check(
        "new commit is detected",
        validation["needs_sync"] and validation["current_revision"] == new_head,
        validation,
    )
    client.json("POST", f"/api/repositories/{repository['id']}/resync")
    second = check_report(client, jobs["smoke-report"], marker, new_head)

    state = fake_state()
    check("GitHub fake saw no auth problems", not state["problems"], state["problems"])
    auth = state["model_auth"]
    check(
        "gateway sent the key only to the provider",
        auth and all(a == f"Bearer {api_key}" for a in auth),
        len(auth),
    )
    check(
        "key absent from webhooks and prompts",
        api_key not in json.dumps(state["webhooks"])
        and api_key not in json.dumps(state["model_requests"]),
    )
    objects = bucket_objects()
    check("run artifacts are stored in S3", len(objects) >= 3, sorted(objects))
    check(
        "key absent from stored artifacts",
        all(api_key.encode() not in value for value in objects.values()),
    )
    check(
        "GitHub tokens absent from stored artifacts",
        all(b"ghs_e2e" not in v and b"gho_e2e" not in v for v in objects.values()),
    )

    STATE.write_text(
        json.dumps(
            {
                "runs": [report["id"], failure["id"], second["id"]],
                "marker": marker,
                "head": new_head,
                "report_job": jobs["smoke-report"],
                "objects": sorted(objects),
                "api_key": api_key,
            }
        )
    )


def after_restart() -> None:
    saved = json.loads(STATE.read_text())
    client = Client()
    sign_in(client)
    for run_id in saved["runs"]:
        detail = client.json("GET", f"/api/runs/{run_id}")
        check(
            f"run {run_id} history survives restart",
            detail["status"] in FINISHED,
            detail["status"],
        )
    status, output, _ = client.raw("GET", f"/api/runs/{saved['runs'][2]}/files/output")
    check(
        "artifact readable after restart",
        status == 200 and saved["marker"] in output.decode(),
        status,
    )
    check("S3 objects survive restart", set(saved["objects"]) <= set(bucket_objects()))
    check_report(client, saved["report_job"], saved["marker"], saved["head"])
    auth = fake_state()["model_auth"]
    check(
        "saved provider key still decrypts and reaches the provider",
        auth and auth[-1] == f"Bearer {saved['api_key']}",
        len(auth),
    )


def main() -> int:
    try:
        if sys.argv[1] == "first":
            first(sys.argv[2])
        else:
            after_restart()
    except Failure as error:
        if not results or results[-1][1]:
            print(f"FAIL  {error}")
        return 1
    print(f"\n{len(results)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
