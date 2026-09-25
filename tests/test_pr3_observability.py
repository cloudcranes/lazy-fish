"""PR-3 可观测性 端到端 smoke：logging 配置、/api/health、matcher 命中日志。"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xyzw_auto_clicker import app as app_module
from xyzw_auto_clicker.adb import AdbClient
from xyzw_auto_clicker.logging_setup import configure
from xyzw_auto_clicker.matcher import ImageMatcher


_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
)


class _StubAdb:
    async def devices(self):
        return []

    async def screenshot_png(self, device_id=None):
        return _TINY_PNG


def _install_stubs(tmp_path, monkeypatch):
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
    monkeypatch.setattr(app_module, "runner", app_module.TaskRunner(app_module.adb, app_module.matcher))


def test_logging_configure_plain_writes_text(tmp_path, monkeypatch, capsys):
    """默认 plain：handler 装到根 logger，输出走 stdout。"""
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    configure("INFO", fmt="plain")
    logging.getLogger("test.pr3").info("hello %s", "world")
    captured = capsys.readouterr()
    assert "hello world" in captured.out
    assert "INFO" in captured.out


def test_logging_configure_json_writes_single_line_json(tmp_path, monkeypatch, capsys):
    """fmt=json：每条记录一行 JSON，extra 字段平铺到顶层。"""
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    configure("INFO", fmt="json")
    logging.getLogger("test.pr3.json").info(
        "match",
        extra={"template_name": "btn.png", "confidence": 0.987, "scale": 1.05},
    )
    captured = capsys.readouterr().out.strip()
    assert captured, "json handler 写不出东西"
    line = captured.splitlines()[-1]
    payload = json.loads(line)
    assert payload["message"] == "match"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.pr3.json"
    assert payload["template_name"] == "btn.png"
    assert payload["confidence"] == 0.987
    assert payload["scale"] == 1.05


def test_logging_configure_rejects_unknown_fmt():
    with pytest.raises(ValueError, match="未知 fmt"):
        configure("INFO", fmt="yaml")


def test_health_endpoint_returns_required_fields(tmp_path, monkeypatch):
    """契约字段：status / last_screenshot_mtime / runner_status / uptime。"""
    _install_stubs(tmp_path, monkeypatch)
    from xyzw_auto_clicker import app as app_module_local

    # 无截图 → last_screenshot_mtime 必须是 None
    app_module_local.runner.state.last_screenshot = None
    with TestClient(app_module_local.app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["last_screenshot_mtime"] is None
        assert body["runner_status"] == "idle"
        assert isinstance(body["uptime"], (int, float))
        assert body["uptime"] >= 0


def test_health_reports_last_screenshot_mtime(tmp_path, monkeypatch):
    """写一份截图到 SHOT_DIR，/api/health 必须回报 mtime。"""
    _install_stubs(tmp_path, monkeypatch)
    from xyzw_auto_clicker import app as app_module_local

    path = app_module_local.SHOT_DIR / "last-frame.png"
    path.write_bytes(_TINY_PNG)
    app_module_local.runner.state.last_screenshot = path

    with TestClient(app_module_local.app) as client:
        body = client.get("/api/health").json()
        assert body["last_screenshot_mtime"] == pytest.approx(path.stat().st_mtime)
        assert body["runner_status"] == "idle"


def test_health_reports_runner_status(tmp_path, monkeypatch):
    """runner_state.status 变化时，/api/health 必须如实回放。"""
    _install_stubs(tmp_path, monkeypatch)
    from xyzw_auto_clicker import app as app_module_local

    app_module_local.runner.state.status = "running"
    with TestClient(app_module_local.app) as client:
        body = client.get("/api/health").json()
        assert body["runner_status"] == "running"


def _make_canvas(button: np.ndarray, scale: float = 1.0, center: tuple[int, int] = (200, 395)) -> np.ndarray:
    canvas = np.full((600, 400, 3), 55, dtype=np.uint8)
    width = max(8, int(round(button.shape[1] * scale)))
    height = max(8, int(round(button.shape[0] * scale)))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    patch = cv2.resize(button, (width, height), interpolation=interp)
    left = int(round(center[0] - width / 2))
    top = int(round(center[1] - height / 2))
    canvas[top : top + height, left : left + width] = patch
    return canvas


def _encode(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def _make_button() -> np.ndarray:
    button = np.full((30, 100, 3), (30, 170, 240), dtype=np.uint8)
    button[:, 8 : 100 - 8] = (20, 40, 90)
    return button


def test_matcher_logs_hit_with_template_confidence_scale(tmp_path, monkeypatch, capsys):
    """matcher 命中一次必须记一条 INFO，含 template_name / confidence / scale。"""
    _install_stubs(tmp_path, monkeypatch)
    from xyzw_auto_clicker import app as app_module_local
    from xyzw_auto_clicker.matcher import logger as matcher_logger

    button = _make_button()
    cv2.imwrite(str(tmp_path / "templates" / "button.png"), button)
    # 让 matcher 用上临时目录
    app_module_local.matcher = ImageMatcher(tmp_path / "templates")
    monkeypatch.setattr(matcher_logger, "level", logging.INFO)

    captured = _CaptureHandler()
    matcher_logger.addHandler(captured)
    try:
        result = app_module_local.matcher.match(_encode(_make_canvas(button)), ["button.png"], 0.86)
    finally:
        matcher_logger.removeHandler(captured)

    assert result is not None
    hits = [r for r in captured.records if r.levelno == logging.INFO and r.name == "xyzw_auto_clicker.matcher"]
    assert hits, "matcher 命中一次没有 INFO 日志"
    record = hits[-1]
    assert record.template_name == "button.png"
    assert record.confidence == pytest.approx(result.confidence, abs=1e-3)
    assert record.scale == pytest.approx(result.scale, abs=1e-3)
    assert "matcher hit" in record.getMessage()


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)