from pathlib import Path

from django.test import Client, override_settings


def test_dashboard_deep_links_serve_the_app(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<div id=app></div>")

    with override_settings(WEB_DIR=tmp_path):
        root = Client().get("/")
        deep_link = Client().get("/runs/8")

    assert root.status_code == deep_link.status_code == 200
    assert b"".join(deep_link.streaming_content) == b"<div id=app></div>"


def test_missing_dashboard_build_is_a_404(tmp_path: Path) -> None:
    with override_settings(WEB_DIR=tmp_path):
        assert Client().get("/runs/8").status_code == 404
