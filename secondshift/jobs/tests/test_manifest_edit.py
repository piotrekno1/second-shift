import pytest

from secondshift.jobs.manifest import ManifestError, parse_manifest
from secondshift.jobs.manifest_edit import CANNOT_EDIT, add_job, job_key

SHA = "a" * 40
JOB = {
    "name": "Repository health",
    "skill": "skills/repository-health/SKILL.md",
    "skill_source": {"repository": "acme/skills", "ref": SHA},
    "schedule": "0 9 * * 1",
    "timezone": "Europe/Warsaw",
    "notify": [{"binding": "team-slack"}],
}

EXISTING = """\
# Jobs for acme/web. Ask Ada before adding one.
version: 1
jobs:
    docs:   # keeps README honest
        name: 'Docs check'
        skill: skills/docs/SKILL.md

        schedule: "0 6 * * *"

# Nothing below this line is a job.
"""


def test_a_missing_file_gets_a_new_one_with_only_the_job() -> None:
    created = add_job(None, "repository-health", JOB)

    manifest = parse_manifest(created)
    assert list(manifest.jobs) == ["repository-health"]
    assert created.startswith("version: 1\njobs:\n  repository-health:\n")
    # Lists sit under their key, as people write them.
    assert "    notify:\n      - binding: team-slack\n" in created


def test_the_job_is_added_after_the_others_and_the_rest_is_untouched() -> None:
    edited = add_job(EXISTING, "repository-health", JOB)

    head, _, tail = EXISTING.partition("\n# Nothing below")
    assert edited.startswith(head)
    assert edited.endswith("\n# Nothing below" + tail)
    # The new job follows the file's own indentation.
    assert "\n    repository-health:\n        name: Repository health\n" in edited
    assert list(parse_manifest(edited).jobs) == ["docs", "repository-health"]


def test_a_file_without_a_trailing_newline_still_gets_a_valid_job() -> None:
    text = "version: 1\njobs:\n  docs:\n    name: Docs\n    skill: a/SKILL.md"

    edited = add_job(text, "health", JOB)

    assert list(parse_manifest(edited).jobs) == ["docs", "health"]


def test_a_job_that_already_exists_is_never_overwritten() -> None:
    with pytest.raises(ManifestError, match="already has a job called docs"):
        add_job(EXISTING, "docs", JOB)


def test_jobs_in_flow_style_are_left_for_a_person() -> None:
    text = 'version: 1\njobs: {docs: {name: Docs, skill: "a/SKILL.md"}}\n'

    with pytest.raises(ManifestError) as raised:
        add_job(text, "health", JOB)
    assert str(raised.value) == CANNOT_EDIT


def test_an_invalid_file_says_so_instead_of_being_edited() -> None:
    with pytest.raises(ManifestError, match=r"Invalid \.secondshift\.yaml"):
        add_job("version: 1\njobs:\n  docs: [\n", "health", JOB)


def test_an_invalid_job_is_refused() -> None:
    with pytest.raises(ManifestError, match=r"Invalid \.secondshift\.yaml"):
        add_job(EXISTING, "health", {**JOB, "schedule": "whenever"})


def test_a_job_key_is_lowercase_words_joined_by_hyphens() -> None:
    assert job_key("Django: N+1 review!") == "django-n-1-review"
    assert job_key("¿¿") == "skill"
    assert len(job_key("x " * 100)) <= 60
    assert not job_key("x " * 100).endswith("-")
