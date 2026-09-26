"""PR-1 安全+契约 端到端 smoke：仅走 FastAPI TestClient，不碰 ADB。"""
from __future__ import annotations

import sys
from pathlib import Path

# 不引 adb：让 AdbClient 在 app 启动时报错前把方法替换掉
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from xyzw_auto_clicker import app as app_module
from xyzw_auto_clicker.adb import AdbClient
from xyzw_auto_clicker.matcher import ImageMatcher
from xyzw_auto_clicker.runner import RunnerState
from xyzw_auto_clicker.settings import SHOT_DIR, TEMPLATE_DIR


class _StubAdb:
    """足够 app 启动 + /api/devices + 手动截图（落盘）用。"""

    async def devices(self):
        return []

    async def screenshot_png(self, device_id=None):
        # 一张最小 PNG（1x1 透明），够触发落盘逻辑，不污染 git
        return _TINY_PNG

    async def reconnect_all(self):
        return None

    def remote_status(self):
        return {}


_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
)


def _install_stubs(tmp_path, monkeypatch):
    monkeypatch.setattr(AdbClient, "__init__", lambda self: None)
    monkeypatch.setattr(app_module, "adb", _StubAdb())
    # 让 TEMPLATE_DIR 在隔离目录，避免污染
    from xyzw_auto_clicker import settings as settings_module
    from xyzw_auto_clicker import plans as plans_module
    monkeypatch.setattr(settings_module, "TEMPLATE_DIR", tmp_path / "templates")
    monkeypatch.setattr(settings_module, "SHOT_DIR", tmp_path / "screenshots")
    monkeypatch.setattr(plans_module, "PLAN_DIR", tmp_path / "plans")
    monkeypatch.setattr(settings_module, "TRASH_DIR", tmp_path / "plans-trash")
    settings_module.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    settings_module.SHOT_DIR.mkdir(parents=True, exist_ok=True)
    plans_module.PLAN_DIR.mkdir(parents=True, exist_ok=True)
    settings_module.TRASH_DIR.mkdir(parents=True, exist_ok=True)
    # 替换模块级 globals：app 模块在 import 时就 import 了 settings 的常量
    monkeypatch.setattr(app_module, "TEMPLATE_DIR", settings_module.TEMPLATE_DIR)
    monkeypatch.setattr(app_module, "SHOT_DIR", settings_module.SHOT_DIR)
    monkeypatch.setattr(app_module, "matcher", ImageMatcher(settings_module.TEMPLATE_DIR))
    monkeypatch.setattr(app_module, "runner", app_module.TaskRunner(app_module.adb, app_module.matcher))


def test_state_last_screenshot_is_filename_string(tmp_path, monkeypatch):
    _install_stubs(tmp_path, monkeypatch)
    # 直接给 state 灌入 Path（模拟 runner 已经写完盘）
    from xyzw_auto_clicker import app as app_module_local
    path = app_module_local.SHOT_DIR / "last-frame.png"
    path.write_bytes(_TINY_PNG)
    app_module_local.runner.state.last_screenshot = path

    with TestClient(app_module_local.app) as client:
        r = client.get("/api/tasks/state")
        assert r.status_code == 200
        body = r.json()
        assert body["last_screenshot"] == "last-frame.png"
        assert isinstance(body["last_screenshot"], str)


def test_latest_screenshot_returns_png(tmp_path, monkeypatch):
    _install_stubs(tmp_path, monkeypatch)
    from xyzw_auto_clicker import app as app_module_local
    path = app_module_local.SHOT_DIR / "last-frame.png"
    path.write_bytes(_TINY_PNG)
    app_module_local.runner.state.last_screenshot = path

    with TestClient(app_module_local.app) as client:
        r = client.get("/api/screenshots/latest.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content == _TINY_PNG


def test_latest_screenshot_404_when_none(tmp_path, monkeypatch):
    _install_stubs(tmp_path, monkeypatch)
    from xyzw_auto_clicker import app as app_module_local
    app_module_local.runner.state.last_screenshot = None
    with TestClient(app_module_local.app) as client:
        r = client.get("/api/screenshots/latest.png")
        assert r.status_code == 404


@pytest.mark.parametrize(
    "name",
    [
        "../etc/passwd",
        "..",
        "../app.py",
        "/etc/passwd",
        "a/../../etc/passwd",
        "shot.png/../app.py",
        "foo.pngx",
        "",
        " ",
        "shot.txt",
    ],
)
def test_get_screenshot_rejects_traversal_and_wrong_ext(tmp_path, monkeypatch, name):
    _install_stubs(tmp_path, monkeypatch)
    from xyzw_auto_clicker import app as app_module_local
    with TestClient(app_module_local.app) as client:
        r = client.get(f"/api/screenshots/{name}")
        assert r.status_code in (400, 404), name


@pytest.mark.parametrize(
    "name",
    [
        "../app.py",
        "/etc/passwd",
        "..",
        "foo.json",
        "foo.pngx",
        "templates/../app.py",
    ],
)
def test_get_template_rejects_traversal_and_wrong_ext(tmp_path, monkeypatch, name):
    _install_stubs(tmp_path, monkeypatch)
    from xyzw_auto_clicker import app as app_module_local
    with TestClient(app_module_local.app) as client:
        r = client.get(f"/api/templates/{name}")
        assert r.status_code in (400, 404), name


@pytest.mark.parametrize(
    "name",
    [
        "../app.py",
        "/etc/passwd",
        "..",
        "foo.png",
        "foo.jsonx",
        "plans/../app.py",
    ],
)
def test_get_plan_rejects_traversal_and_wrong_ext(tmp_path, monkeypatch, name):
    _install_stubs(tmp_path, monkeypatch)
    from xyzw_auto_clicker import app as app_module_local
    with TestClient(app_module_local.app) as client:
        r = client.get(f"/api/plans/{name}")
        assert r.status_code in (400, 404), name


def test_crop_request_rejects_bad_device_id(tmp_path, monkeypatch):
    _install_stubs(tmp_path, monkeypatch)
    from xyzw_auto_clicker import app as app_module_local
    with TestClient(app_module_local.app) as client:
        r = client.post(
            "/api/templates/crop",
            json={
                "name": "ok.png",
                "device_id": "evil; rm -rf /",
                "x": 0,
                "y": 0,
                "width": 10,
                "height": 10,
            },
        )
        assert r.status_code == 422, r.text


def test_crop_request_accepts_clean_device_id(tmp_path, monkeypatch):
    _install_stubs(tmp_path, monkeypatch)
    from xyzw_auto_clicker import app as app_module_local
    # raise_server_exceptions=False: 这里 stub PNG 不是合法图片，crop_template 会因解码失败抛 500，
    # 那是后端实现问题，不是 device_id 校验的事；我们只想确认「合法 device_id 不被白名单误杀」
    with TestClient(app_module_local.app, raise_server_exceptions=False) as client:
        r = client.post(
            "/api/templates/crop",
            json={
                "name": "ok.png",
                "device_id": "emulator-5554",
                "x": 0,
                "y": 0,
                "width": 10,
                "height": 10,
            },
        )
        # 422 = pydantic 校验错；其他状态码都说明 device_id 没被前置校验拦下
        assert r.status_code != 422, r.text


def test_runner_state_snapshot_handles_path(tmp_path):
    """snapshot() 必须把 Path 序列化成 filename 字符串，不抛 TypeError。"""
    state = RunnerState(status="running", clicked=2, target=4)
    state.last_screenshot = tmp_path / "screenshots" / "last-frame.png"
    snap = state.snapshot()
    assert snap["last_screenshot"] == "last-frame.png"
