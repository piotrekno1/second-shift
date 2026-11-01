"""Check that a trigger request came from someone who holds its secret.

A request passes with either:

- an Authorization header whose credentials are the secret, with any scheme,
  such as "Bearer <secret>". Grafana's webhook contact point sends this.
- a hex HMAC-SHA256 of the body, keyed with the secret, in a signature header.
  With a timestamp header, the signature covers "<timestamp>:<body>" and the
  timestamp must be recent. This is how Grafana signs webhooks.
"""

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass

SIGNATURE_HEADERS = ("X-Grafana-Alerting-Signature", "X-SecondShift-Signature")
TIMESTAMP_HEADERS = ("X-Grafana-Alerting-Timestamp", "X-SecondShift-Timestamp")
SIGNATURE_PREFIX = "sha256="


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""


MISSING = Verdict(False, "The request had no Authorization header or signature.")
WRONG = Verdict(False, "The request's credentials did not match the secret.")
STALE = Verdict(False, "The signed timestamp is missing, invalid or too old.")


def first_header(headers: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = headers.get(name)
        if value is not None:
            return value.strip()
    return None


def same(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())


def authorization_matches(value: str, secret: str) -> bool:
    parts = value.split(None, 1)
    credentials = parts[1] if len(parts) == 2 else ""
    return bool(credentials) and same(credentials.strip(), secret)


def signature(secret: str, body: bytes, timestamp: str | None) -> str:
    signed = f"{timestamp}:".encode() + body if timestamp is not None else body
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def fresh(timestamp: str, now: float, max_age_seconds: int) -> bool:
    try:
        sent = float(timestamp)
    except ValueError:
        return False
    return abs(now - sent) <= max_age_seconds


def verify(
    headers: Mapping[str, str],
    body: bytes,
    secret: str,
    *,
    now: float,
    max_age_seconds: int,
) -> Verdict:
    authorization = headers.get("Authorization")
    if authorization is not None:
        return Verdict(True) if authorization_matches(authorization, secret) else WRONG
    given = first_header(headers, SIGNATURE_HEADERS)
    if given is None:
        return MISSING
    given = given.removeprefix(SIGNATURE_PREFIX).lower()
    timestamp = first_header(headers, TIMESTAMP_HEADERS)
    if not same(given, signature(secret, body, timestamp)):
        return WRONG
    if timestamp is not None and not fresh(timestamp, now, max_age_seconds):
        return STALE
    return Verdict(True)
