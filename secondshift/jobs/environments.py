"""The sandbox environments the runners offer.

Only a runner maps an environment's name to an image, and a run sends just the
name. The worker asks each runner what it offers every minute and keeps the
answer in the runner registry, so pages read the database and never wait on a
runner.
"""

from dataclasses import dataclass

from secondshift.runner_protocol.environments import RunnerEnvironments
from secondshift.runs.dao import runners_in_pool
from secondshift.runs.models import Runner
from secondshift.runs.runners import DEFAULT_POOL, offers


@dataclass(frozen=True)
class OfferedEnvironments:
    """What one runner offered when it last answered."""

    runner: str
    environments: RunnerEnvironments


def runner_environments() -> list[OfferedEnvironments]:
    """What each runner that takes runs offered. A runner that has not
    answered yet is left out.
    """
    return [
        OfferedEnvironments(
            runner.name,
            RunnerEnvironments(
                images=runner.images, default=runner.default_environment
            ),
        )
        for runner in runners_in_pool(DEFAULT_POOL)
        if runner.images is not None
    ]


@dataclass(frozen=True)
class EnvironmentOffer:
    """The runners that take runs, read once for a whole list of jobs."""

    runners: list[Runner]

    def missing(self, name: str) -> bool:
        """True when no runner that takes runs offers it.

        A runner that has not answered yet may offer anything, as placement
        assumes, and it refuses a session it can't run. With no runner
        registered nothing is known yet, so nothing is missing.
        """
        return bool(self.runners) and not any(
            offers(runner, name) for runner in self.runners
        )


def environment_offer() -> EnvironmentOffer:
    return EnvironmentOffer(runners_in_pool(DEFAULT_POOL))
