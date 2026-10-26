import gzip
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from whitenoise.middleware import WhiteNoiseMiddleware

from secondshift.core.static_storage import StaticFilesStorage, immutable_static_file


def test_collect_preserves_vite_bytes_and_hashes_django_assets(tmp_path: Path) -> None:
    source = FileSystemStorage(location=tmp_path / "source")
    root = tmp_path / "collected"
    css = b'.icon { background: url("./mark-AbCd1234.svg"); }' * 40
    script = b'import "./other-XyZa5678.js";' * 40
    inputs = {
        "assets/app-AbCd1234.css": css,
        "assets/app-XyZa5678.js": script,
        "assets/mark-AbCd1234.svg": b"<svg></svg>",
        "admin/css/base.css": b"body { color: red; }" * 40,
    }
    with override_settings(
        STATIC_URL="/static/", STATIC_ROOT=root, WHITENOISE_USE_FINDERS=False
    ):
        storage = StaticFilesStorage(location=root)
        for name, content in inputs.items():
            source.save(name, ContentFile(content))
            storage.save(name, ContentFile(content))
        results = list(storage.post_process({name: (source, name) for name in inputs}))
        assert not any(isinstance(result[2], Exception) for result in results)
        for name in ("assets/app-AbCd1234.css", "assets/app-XyZa5678.js"):
            assert storage.url(name) == f"/static/{name}"
            assert (root / name).read_bytes() == inputs[name]
            assert gzip.decompress((root / f"{name}.gz").read_bytes()) == inputs[name]
        assert storage.url("admin/css/base.css") != "/static/admin/css/base.css"
        middleware = WhiteNoiseMiddleware(lambda request: HttpResponse(status=404))
        response = middleware(RequestFactory().get("/static/assets/app-AbCd1234.css"))
        assert response.status_code == 200
        assert "immutable" in response.headers["Cache-Control"]

        assert sorted(p.name for p in (root / "assets").iterdir()) == [
            "app-AbCd1234.css",
            "app-AbCd1234.css.gz",
            "app-XyZa5678.js",
            "app-XyZa5678.js.gz",
            "mark-AbCd1234.svg",
        ]


def test_dry_run_does_not_write_manifest(tmp_path: Path) -> None:
    storage = StaticFilesStorage(location=tmp_path)
    assert list(storage.post_process({}, dry_run=True)) == []
    assert not (tmp_path / "staticfiles.json").exists()


def test_immutable_urls_cover_vite_and_django_without_caching_plain_files() -> None:
    assert immutable_static_file("", "/static/assets/index-AbCd1234.js")
    assert immutable_static_file("", "/static/admin/css/base.123456abcdef.css")
    assert not immutable_static_file("", "/static/secondshift-mark.svg")
    assert not immutable_static_file("", "/static/assets/index.js")
