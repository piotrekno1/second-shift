import hashlib
import io
import os
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest
from django.test import Client

from secondshift.runner import sources
from secondshift.runner.protocol import RunnerProtocolError
from secondshift.runner.sources import (
    has_source,
    receive_source,
    source_path,
    sweep_sources,
    unpack_source,
)


def archive(*members: tuple[tarfile.TarInfo, bytes | None]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for info, content in members:
            if content is not None:
                info.size = len(content)
            tar.addfile(info, io.BytesIO(content) if content is not None else None)
    return output.getvalue()


def regular(name: str, content: bytes) -> tuple[tarfile.TarInfo, bytes]:
    return tarfile.TarInfo(name), content


def link(name: str, target: str, kind: bytes = tarfile.SYMTYPE) -> tuple:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    return info, None


def store(data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    receive_source(digest, io.BytesIO(data), len(data))
    return digest


@pytest.fixture(autouse=True)
def work_root(settings: Any, tmp_path: Path) -> Path:
    settings.RUNNER_WORK_ROOT = tmp_path
    return tmp_path


def test_a_received_source_unpacks_into_the_workspace(tmp_path: Path) -> None:
    digest = store(archive(regular("README.md", b"hello\n"), link("link", "README.md")))
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    unpack_source(digest, workspace)

    assert (workspace / "README.md").read_bytes() == b"hello\n"
    assert (workspace / "link").readlink() == Path("README.md")


def test_a_source_must_match_its_name() -> None:
    data = archive(regular("README.md", b"hello\n"))

    with pytest.raises(RunnerProtocolError, match="does not match"):
        receive_source("f" * 64, io.BytesIO(data), len(data))

    assert not source_path("f" * 64).exists()
    assert list(source_path("f" * 64).parent.iterdir()) == []


@pytest.mark.parametrize("name", ["../escape", "A" * 64, "f" * 63])
def test_a_source_name_cannot_choose_a_path(name: str) -> None:
    with pytest.raises(RunnerProtocolError, match="SHA-256"):
        receive_source(name, io.BytesIO(b""), 0)


def test_oversized_or_unsized_uploads_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RunnerProtocolError, match="Content-Length"):
        receive_source("f" * 64, io.BytesIO(b""), None)
    monkeypatch.setattr(sources, "MAX_SOURCE_SNAPSHOT_BYTES", 10)
    with pytest.raises(RunnerProtocolError, match="too large"):
        receive_source("f" * 64, io.BytesIO(b"x" * 11), 11)


def test_a_short_upload_is_refused() -> None:
    with pytest.raises(RunnerProtocolError, match="ended early"):
        receive_source("f" * 64, io.BytesIO(b"abc"), 10)


def test_a_missing_source_stops_the_session(tmp_path: Path) -> None:
    with pytest.raises(RunnerProtocolError, match="not uploaded"):
        unpack_source("f" * 64, tmp_path)


@pytest.mark.parametrize(
    "member",
    [
        link("escape", "../../etc/passwd"),
        link("absolute", "/etc/passwd"),
        regular("../outside", b"x"),
    ],
)
def test_entries_cannot_leave_the_workspace(tmp_path: Path, member: tuple) -> None:
    digest = store(archive(member))
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(RunnerProtocolError, match="unpack"):
        unpack_source(digest, workspace)

    assert not (tmp_path / "outside").exists()


def test_only_files_folders_and_links_are_allowed(tmp_path: Path) -> None:
    digest = store(
        archive(regular("README.md", b"x"), link("hard", "README.md", tarfile.LNKTYPE))
    )

    with pytest.raises(RunnerProtocolError, match="only hold"):
        unpack_source(digest, tmp_path)


def test_unpack_limits_stop_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = store(archive(regular("a", b"x" * 20), regular("b", b"y")))

    monkeypatch.setattr(sources, "MAX_SOURCE_UNPACKED_BYTES", 10)
    with pytest.raises(RunnerProtocolError, match="too large"):
        unpack_source(digest, tmp_path)
    monkeypatch.undo()
    monkeypatch.setattr(sources, "MAX_SOURCE_FILES", 1)
    with pytest.raises(RunnerProtocolError, match="too large"):
        unpack_source(digest, tmp_path)


def test_unused_sources_are_swept_after_an_hour() -> None:
    old = store(archive(regular("old", b"old")))
    kept = store(archive(regular("kept", b"kept")))
    hour_ago = time.time() - sources.SOURCE_MAX_AGE_SECONDS - 1
    for digest in (old, kept):
        os.utime(source_path(digest), (hour_ago, hour_ago))

    # Asking for a source keeps it another hour.
    assert has_source(kept)
    sweep_sources()

    assert not has_source(old)
    assert has_source(kept)


@pytest.mark.urls("secondshift.runner.urls")
def test_sources_api_streams_uploads_and_requires_auth(settings: Any) -> None:
    data = archive(regular("README.md", b"hello\n"))
    digest = hashlib.sha256(data).hexdigest()
    auth = {"HTTP_AUTHORIZATION": f"Bearer {settings.RUNNER_SERVICE_TOKEN}"}
    url = f"/v1/sources/{digest}"

    assert Client().put(url, data, content_type="application/gzip").status_code == 401
    assert Client().head(url, **auth).status_code == 404
    uploaded = Client().put(url, data, content_type="application/gzip", **auth)
    assert uploaded.status_code == 204
    assert Client().head(url, **auth).status_code == 204
    assert source_path(digest).read_bytes() == data
