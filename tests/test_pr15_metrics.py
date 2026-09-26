"""PR-15 Prometheus /api/metrics：JSON / Prometheus text/plain; version=0.0.4 双格式 + Accept 头协商。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xyzw_auto_clicker import app as app_module
from xyzw_auto_clicker.adb import AdbClient
from xyzw_auto_clicker.matcher import ImageMatcher
from xyzw_auto_clicker.runner import TaskRunner

# PR-15 强约束：5 个指标全部带 HELP + TYPE 与文本取值
_REQUIRED_METRICS = {
    "lazy_fish_uptime_seconds": "gauge",
    "lazy_fish_process_resident_memory_bytes": "gauge",
    "lazy_fish_runner_status": "gauge",
    "lazy_fish_last_screenshot_mtime_seconds": "gauge",
    "lazy_fish_tasks_started_total": "counter",
}


class _StubAdb:
    async def devices(self):
        return []

    async def screenshot_png(self, device_id=None):
        return b""

    async def reconnect_all(self):
        return None

    def remote_status(self):
        return {}


@pytest.fixture
def metrics_client(tmp_path, monkeypatch):
    """挂上 LOG_JSON=1 并 stub 掉 AdbClient，构造可命中 /api/metrics 的 TestClient。"""
    monkeypatch.setenv("LOG_JSON", "1")
    monkeypatch.setattr(AdbClient, "__init__", lambda self: None)
    monkeypatch.setattr(app_module, "adb", _StubAdb())
    from xyzw_auto_clicker import plans as plans_module
    from xyzw_auto_clicker import settings as settings_module

    monkeypatch.setattr(settings_module, "TEMPLATE_DIR", tmp_path / "templates")
    monkeypatch.setattr(settings_module, "SHOT_DIR", tmp_path / "screenshots")
    monkeypatch.setattr(plans_module, "PLAN_DIR", tmp_path / "plans")
    monkeypatch.setattr(settings_module, "TRASH_DIR", tmp_path / "plans-trash")
    settings_module.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    settings_module.SHOT_DIR.mkdir(parents=True, exist_ok=True)
    plans_module.PLAN_DIR.mkdir(parents=True, exist_ok=True)
    settings_module.TRASH_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_module, "TEMPLATE_DIR", settings_module.TEMPLATE_DIR)
    monkeypatch.setattr(app_module, "SHOT_DIR", settings_module.SHOT_DIR)
    monkeypatch.setattr(app_module, "matcher", ImageMatcher(settings_module.TEMPLATE_DIR))
    monkeypatch.setattr(app_module, "runner", TaskRunner(app_module.adb, app_module.matcher))
    return TestClient(app_module.app)


def test_metrics_json_when_accept_application_json(metrics_client):
    """显式 Accept: application/json 必须走 JSON。"""
    response = metrics_client.get("/api/metrics", headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["runner_status"] == "idle"
    assert isinstance(body["uptime_seconds"], (int, float))
    assert isinstance(body["process_resident_memory_bytes"], int)
    """显式 Accept: application/json 必须走 JSON。"""
    response = metrics_client.get("/api/metrics", headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    json.loads(response.text)


def test_metrics_prometheus_when_accept_text_plain(metrics_client):
    """Accept: text/plain 走 Prometheus text/plain; version=0.0.4，且包含 HELP + TYPE。"""
    response = metrics_client.get(
        "/api/metrics",
        headers={"Accept": "text/plain; version=0.0.4"},
    )
    assert response.status_code == 200
    content_type = response.headers["content-type"]
    assert content_type.startswith("text/plain")
    assert "version=0.0.4" in content_type
    body = response.text
    for metric_name, metric_type in _REQUIRED_METRICS.items():
        assert f"# HELP {metric_name}" in body, f"缺少 HELP: {metric_name}"
        assert f"# TYPE {metric_name} {metric_type}" in body, (
            f"缺少 TYPE: {metric_name} {metric_type}"
        )
        # 指标名 + 数值行（非 0.0 行必须带值；runner_status 是字符串，按 Prometheus 规范走也行）
        assert metric_name in body


def test_metrics_prometheus_when_accept_wildcard(metrics_client):
    """Accept: */*（curl/wget 默认）必须走 Prometheus 文本格式。"""
    response = metrics_client.get("/api/metrics", headers={"Accept": "*/*"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# HELP lazy_fish_uptime_seconds" in response.text
    assert "# TYPE lazy_fish_tasks_started_total counter" in response.text


def test_metrics_prometheus_runner_status_uses_runner_value(metrics_client):
    """runner.state.status 变更必须如实反映到 Prometheus 文本里。"""
    app_module.runner.state.status = "running"
    response = metrics_client.get("/api/metrics", headers={"Accept": "text/plain"})
    assert response.status_code == 200
    assert "lazy_fish_runner_status running" in response.text


def test_metrics_json_when_no_accept_header(metrics_client):
    """完全不带 Accept 头时走 JSON：直接构造 ASGI scope 绕过 httpx 默认 */*。"""
    app_module.runner.state.status = "idle"
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/metrics",
        "raw_path": b"/api/metrics",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
    }
    sent: list[dict[str, object]] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    import anyio

    anyio.run(app_module.app, scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200
    headers = dict(start["headers"])
    assert headers[b"content-type"].startswith(b"application/json")


def test_metrics_404_when_disabled(tmp_path, monkeypatch):
    """LOG_JSON 不为真时 /api/metrics 必须 404，与既有契约一致。"""
    monkeypatch.delenv("LOG_JSON", raising=False)
    monkeypatch.setattr(AdbClient, "__init__", lambda self: None)
    monkeypatch.setattr(app_module, "adb", _StubAdb())
    from xyzw_auto_clicker import plans as plans_module
    from xyzw_auto_clicker import settings as settings_module

    monkeypatch.setattr(settings_module, "TEMPLATE_DIR", tmp_path / "templates")
    monkeypatch.setattr(settings_module, "SHOT_DIR", tmp_path / "screenshots")
    monkeypatch.setattr(plans_module, "PLAN_DIR", tmp_path / "plans")
    monkeypatch.setattr(settings_module, "TRASH_DIR", tmp_path / "plans-trash")
    settings_module.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    settings_module.SHOT_DIR.mkdir(parents=True, exist_ok=True)
    plans_module.PLAN_DIR.mkdir(parents=True, exist_ok=True)
    settings_module.TRASH_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_module, "TEMPLATE_DIR", settings_module.TEMPLATE_DIR)
    monkeypatch.setattr(app_module, "SHOT_DIR", settings_module.SHOT_DIR)
    monkeypatch.setattr(app_module, "matcher", ImageMatcher(settings_module.TEMPLATE_DIR))
    monkeypatch.setattr(app_module, "runner", TaskRunner(app_module.adb, app_module.matcher))

    with TestClient(app_module.app) as client:
        response = client.get("/api/metrics")
        assert response.status_code == 404