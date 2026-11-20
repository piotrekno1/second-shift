"""Links to a run's page, and the footer SecondShift leaves on GitHub."""

import ipaddress
from urllib.parse import urlsplit

from django.conf import settings

from secondshift.runs.models import Run


def run_url(run_id: int) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/runs/{run_id}"


def reachable_from_github() -> bool:
    """False when the dashboard runs on this machine only, like localhost."""
    host = urlsplit(settings.FRONTEND_URL).hostname or ""
    if host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (address.is_loopback or address.is_unspecified)


def run_reference(run: Run) -> str:
    """The run as GitHub Markdown. Never "#N", which GitHub links to an issue."""
    name = f"run {run.pk}"
    return f"[{name}]({run_url(run.pk)})" if reachable_from_github() else name


def attribution_footer(action: str, run: Run) -> str:
    """Such as "Opened by SecondShift from run 12 at 1a2b3c4d5e6f."."""
    return (
        f"{action} by SecondShift from {run_reference(run)} at {run.commit_sha[:12]}."
    )
