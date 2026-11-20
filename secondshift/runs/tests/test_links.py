"""The run reference in what SecondShift leaves on GitHub."""

import pytest
from django.test import override_settings

from secondshift.runs.links import attribution_footer, run_url
from secondshift.runs.models import Run


def run_at(pk: int) -> Run:
    return Run(pk=pk, commit_sha="a" * 40)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:5173",
        "http://app.localhost",
        "http://127.0.0.1:8000",
        "http://[::1]:8000",
        "http://0.0.0.0:8000",
    ],
)
def test_a_dashboard_on_this_machine_is_named_not_linked(url: str) -> None:
    with override_settings(FRONTEND_URL=url):
        footer = attribution_footer("Opened", run_at(12))

    # "#12" would link to issue 12 on GitHub.
    assert footer == f"Opened by SecondShift from run 12 at {'a' * 12}."


@override_settings(FRONTEND_URL="https://secondshift.example.com/")
def test_a_reachable_dashboard_links_the_run() -> None:
    assert run_url(12) == "https://secondshift.example.com/runs/12"
    assert attribution_footer("Handed back", run_at(12)) == (
        "Handed back by SecondShift from "
        "[run 12](https://secondshift.example.com/runs/12) at aaaaaaaaaaaa."
    )
