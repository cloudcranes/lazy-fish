"""PR-27 远程设备管理：connect/disconnect + 持久化注册表 + /api/devices 合并。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from xyzw_auto_clicker.adb import AdbClient, AdbError  # noqa: E402


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """隔离 data 目录 + stub 掉 adb 子进程。"""
    from xyzw_auto_clicker import app as app_module
    from xyzw_auto_clicker.matcher import ImageMatcher

    monkeypatch.setattr(AdbClient, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(app_module, "adb", _FakeAdb())
    monkeypatch.setattr(app_module, "matcher", ImageMatcher(tmp_path / "templates"))
    (tmp_path / "templates").mkdir(parents=True, exist_ok=True)

    with TestClient(app_module.app) as c:
        yield c


class _FakeAdb:
    """替代真实 adb：remote 注册表在内存，connect 由地址前缀决定成败。"""

    def __init__(self) -> None:
        self._remotes: dict[str, bool] = {}

    async def devices(self):
        return []

    async def screenshot_png(self, device_id=None):
        return b""

    async def reconnect_all(self):
        return None

    async def connect(self, host: str) -> bool:
        ok = host.startswith("ok")
        self._remotes[host] = ok
        return ok

    async def disconnect(self, host: str) -> bool:
        self._remotes.pop(host, None)
        return True

    def remote_status(self) -> dict[str, bool]:
        return dict(self._remotes)

    def remote_hosts(self):
        return sorted(self._remotes)


def test_connect_ok_and_persist(client):
    r = client.post("/api/devices/connect", json={"host": "ok-host:5555"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["remotes"]["ok-host:5555"] is True


def test_connect_failure_marks_offline(client):
    r = client.post("/api/devices/connect", json={"host": "bad-host:5555"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["remotes"]["bad-host:5555"] is False


def test_connect_invalid_host_rejected(client):
    for bad in ("noport", "a..b:1", "a_b:5555", "a:99999", "a:-1", "a:abc"):
        r = client.post("/api/devices/connect", json={"host": bad})
        assert r.status_code == 422, bad


def test_disconnect_removes(client):
    client.post("/api/devices/connect", json={"host": "ok-host:5555"})
    r = client.post("/api/devices/disconnect", json={"host": "ok-host:5555"})
    assert r.status_code == 200
    assert "ok-host:5555" not in r.json()["remotes"]


def test_devices_endpoint_includes_remotes(client):
    client.post("/api/devices/connect", json={"host": "ok-host:5555"})
    r = client.get("/api/devices")
    assert r.status_code == 200
    body = r.json()
    assert body["remotes"]["ok-host:5555"] is True
    assert body["devices"] == []


def test_adb_client_persistence(tmp_path):
    """真实 AdbClient 的注册表落盘/加载（不触碰 adb 子进程）。"""
    f = tmp_path / "devices.json"
    c = AdbClient(remote_file=f)
    c._remotes = {"192.168.1.10:5555": True, "192.168.1.11:5555": False}
    c._save_remotes()

    c2 = AdbClient(remote_file=f)
    assert c2._remotes == {"192.168.1.10:5555": True, "192.168.1.11:5555": False}
    assert c2.remote_hosts() == ["192.168.1.10:5555", "192.168.1.11:5555"]
