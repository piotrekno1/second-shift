"""How many tool calls each person can make per minute.

All of a person's tokens share one count, so a new token does not reset it.
The count lives in the shared cache, so every web process sees the same one.
"""

import math
import time

from django.conf import settings
from django.core.cache import cache

WINDOW_SECONDS = 60


def wait_seconds(user_id: int) -> int:
    """Count one call. Zero when it is allowed, else seconds until it would be."""
    now = time.time()
    window = int(now // WINDOW_SECONDS)
    key = f"secondshift:mcp-calls:{user_id}:{window}"
    # add() only sets a missing key, so the first call in a window starts at 0.
    cache.add(key, 0, timeout=WINDOW_SECONDS * 2)
    calls = cache.incr(key)
    if calls <= settings.API_RATE_LIMIT:
        return 0
    return max(math.ceil((window + 1) * WINDOW_SECONDS - now), 1)
