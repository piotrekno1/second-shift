"""Work the worker repeats on a timer, each with its own interval."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def always() -> bool:
    return True


@dataclass
class PeriodicTask:
    name: str
    # Seconds between runs. 0 runs it on every pass of the worker loop.
    every: float
    run: Callable[[], object]
    # Checked each pass, so a setting can turn the task off.
    enabled: Callable[[], bool] = always
    clock: Callable[[], float] = time.monotonic
    last_run: float | None = field(default=None, init=False)

    def due(self) -> bool:
        if not self.enabled():
            return False
        return self.last_run is None or self.clock() - self.last_run >= self.every

    def run_if_due(self) -> None:
        """Run when due. A failure is logged and the worker carries on."""
        if not self.due():
            return
        self.last_run = self.clock()
        try:
            self.run()
        except Exception:
            logger.exception("periodic task %r failed", self.name)
