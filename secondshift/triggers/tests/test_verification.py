import hashlib
import hmac

import pytest

from secondshift.triggers.verification import MISSING, STALE, WRONG, verify

SECRET = "sst_secret"
BODY = b'{"status":"firing"}'
NOW = 1_800_000_000.0


def check(headers: dict[str, str], body: bytes = BODY) -> object:
    return verify(headers, body, SECRET, now=NOW, max_age_seconds=300)


def signed(body: bytes, timestamp: str | None = None, secret: str = SECRET) -> str:
    message = f"{timestamp}:".encode() + body if timestamp else body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


@pytest.mark.parametrize("scheme", ["Bearer", "Token", "SecondShift"])
def test_authorization_header_with_any_scheme(scheme: str) -> None:
    assert check({"Authorization": f"{scheme} {SECRET}"}).ok


@pytest.mark.parametrize(
    "value", ["Bearer wrong", f"Bearer {SECRET}x", SECRET, "Bearer ", ""]
)
def test_wrong_authorization_is_refused(value: str) -> None:
    assert check({"Authorization": value}) == WRONG


def test_request_without_credentials_is_refused() -> None:
    assert check({}) == MISSING


def test_grafana_signature_over_the_body() -> None:
    assert check({"X-Grafana-Alerting-Signature": signed(BODY)}).ok
    assert check({"X-SecondShift-Signature": "sha256=" + signed(BODY)}).ok


def test_signature_for_another_body_or_secret_is_refused() -> None:
    assert check({"X-Grafana-Alerting-Signature": signed(b"{}")}) == WRONG
    assert (
        check({"X-Grafana-Alerting-Signature": signed(BODY, secret="other")}) == WRONG
    )


def test_signature_with_a_recent_timestamp() -> None:
    timestamp = str(int(NOW) - 60)
    headers = {
        "X-Grafana-Alerting-Signature": signed(BODY, timestamp),
        "X-Grafana-Alerting-Timestamp": timestamp,
    }

    assert check(headers).ok


@pytest.mark.parametrize("timestamp", [str(int(NOW) - 301), str(int(NOW) + 301)])
def test_old_or_future_signed_timestamp_is_refused(timestamp: str) -> None:
    headers = {
        "X-Grafana-Alerting-Signature": signed(BODY, timestamp),
        "X-Grafana-Alerting-Timestamp": timestamp,
    }

    assert check(headers) == STALE


def test_dropping_the_timestamp_breaks_the_signature() -> None:
    timestamp = str(int(NOW))

    assert check({"X-Grafana-Alerting-Signature": signed(BODY, timestamp)}) == WRONG
