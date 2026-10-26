"""Preserve Vite's asset names; Django hashes its own admin/account assets."""

import re
from collections.abc import Iterator
from typing import Any

from django.core.files.storage import Storage
from whitenoise.storage import CompressedManifestStaticFilesStorage

VITE_ASSET_PREFIX = "assets/"


class StaticFilesStorage(CompressedManifestStaticFilesStorage):
    def post_process(
        self,
        paths: dict[str, tuple[Storage, str]],
        **options: Any,
    ) -> Iterator[tuple[str, str, bool | Exception]]:
        frontend = {
            name: source
            for name, source in paths.items()
            if name.startswith(VITE_ASSET_PREFIX)
        }
        django = {
            name: source for name, source in paths.items() if name not in frontend
        }
        yield from super().post_process(django, **options)
        if options.get("dry_run"):
            return
        # Vite already hashes filenames and rewrites URLs in its HTML/CSS/JS.
        # Register identity mappings without rewriting or duplicating those files.
        self.hashed_files.update({name: name for name in frontend})
        self.save_manifest()
        for name in frontend:
            yield name, name, False
        for name, compressed in self.compress_files(frontend):
            yield name, compressed, True


def immutable_static_file(path: str, url: str) -> bool:
    """Recognize Django's hashes and Vite's dash-separated content hashes."""
    return bool(
        re.search(r"\.[a-f0-9]{12}\.[^/]+$", url)
        or re.search(r"/assets/[^/]+-[A-Za-z0-9_-]{8,}\.[^/]+$", url)
    )
