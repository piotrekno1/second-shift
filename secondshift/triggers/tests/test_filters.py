import pytest

from secondshift.triggers.filters import (
    MISSING,
    field_value,
    parse_body,
    repeat_key,
    repeat_window_seconds,
    skip_reason,
)

BODY = {"status": "resolved", "labels": {"team": "web", "count": 2}}


def test_field_value_follows_dotted_paths() -> None:
    assert field_value(BODY, "labels.team") == "web"
    assert field_value(BODY, "labels.missing") is MISSING
    assert field_value(BODY, "status.deeper") is MISSING
    assert field_value(None, "status") is MISSING


@pytest.mark.parametrize(
    ("skip_when", "skipped"),
    [
        ({"status": "resolved"}, True),
        ({"status": "resolved", "labels.team": "web"}, True),
        ({"status": "resolved", "labels.team": "api"}, False),
        ({"labels.count": 2}, True),
        ({"labels.count": "2"}, False),
        ({}, False),
    ],
)
def test_skip_needs_every_field_to_match(skip_when: dict, skipped: bool) -> None:
    assert (skip_reason(BODY, skip_when) is not None) is skipped


def test_repeat_key_is_stable_and_absent_values_give_none() -> None:
    assert repeat_key(BODY, "labels") == repeat_key(
        {"labels": {"count": 2, "team": "web"}}, "labels"
    )
    assert repeat_key(BODY, "labels.team") != repeat_key(BODY, "status")
    assert repeat_key(BODY, "nothing") is None
    assert repeat_key(BODY, None) is None


def test_body_that_is_not_json_matches_nothing() -> None:
    data = parse_body(b"not json")

    assert data is None
    assert skip_reason(data, {"status": "resolved"}) is None
    assert repeat_key(data, "status") is None


def test_repeat_window_defaults_to_an_hour() -> None:
    assert repeat_window_seconds({}) == 3600
    assert repeat_window_seconds({"repeat_window": "30m"}) == 1800
