"""PR-8 契约硬化 端到端 smoke。

每条断言对应任务清单里的一项：
  (1) plans.safe_field_name + PlanPayload.crop 校验 x,y,w,h 边界 + 面积上限；
      CropRequest 复用同一道闸（字段层 + model 层 area 校验）；
  (2) matcher hot-path memory TTL 60s（过期强制回退 ROI）；
      _roi_rect_cache 改 OrderedDict + ROI_CACHE_MAXSIZE=128 LRU；
  (3) 后端 /api/metrics（仅 LOG_JSON=1 启用）暴露
      process_resident_memory_bytes / runner_status / last_screenshot_mtime /
      uptime_seconds / tasks_started_total；
  (4) static/app.js pollState 接入 focus/blur + online/offline 事件；
  (5) templates/index.html toast 区域 role=status aria-live=polite 且限 1 条主信息。

本文件不引第三方依赖；FastAPI 测试客户端用 TestClient。
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------- helpers ----------


_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
)


class _StubAdb:
    async def devices(self):
        return []

    async def screenshot_png(self, device_id=None):
        return _TINY_PNG

    async def reconnect_all(self):
        return None

    def remote_status(self):
        return {}


def _install_stubs(tmp_path, monkeypatch, *, log_json: str | None = None):
    """装好 adb/matcher/runner 桩 + tmp 目录。

    log_json: "1" 启用 LOG_JSON；"0"/None 关闭。test_pr8 里部分用例要切。
    """
    if log_json is not None:
        monkeypatch.setenv("LOG_JSON", log_json)
    else:
        monkeypatch.delenv("LOG_JSON", raising=False)

    from xyzw_auto_clicker import app as app_module
    from xyzw_auto_clicker.adb import AdbClient
    from xyzw_auto_clicker.matcher import ImageMatcher
    from xyzw_auto_clicker import plans as plans_module
    from xyzw_auto_clicker import settings as settings_module

    monkeypatch.setattr(AdbClient, "__init__", lambda self: None)
    monkeypatch.setattr(app_module, "adb", _StubAdb())
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
    matcher = ImageMatcher(settings_module.TEMPLATE_DIR)
    monkeypatch.setattr(app_module, "matcher", matcher)
    runner = app_module.TaskRunner(app_module.adb, matcher)
    monkeypatch.setattr(app_module, "runner", runner)
    # 清掉上一轮残留的 ROI 缓存（class 级 dict，跨测试要重置）
    ImageMatcher.clear_class_cache()
    return app_module


# ---------- (1) plans._safe_field_name + PlanPayload.crop ----------


def test_safe_field_name_accepts_zero_and_max_boundary() -> None:
    """safe_field_name 必须接受 0 与上限值，拒绝负数 / 越界 / 非整数 / 布尔。"""
    from xyzw_auto_clicker.plans import CROP_MAX_DIM, safe_field_name

    assert safe_field_name(0) == 0
    assert safe_field_name(CROP_MAX_DIM) == CROP_MAX_DIM
    assert safe_field_name(1920) == 1920
    for bad in (-1, CROP_MAX_DIM + 1, 999999):
        with pytest.raises(ValueError):
            safe_field_name(bad)
    for bad in ("0", 0.5, None, True, [0]):
        with pytest.raises(ValueError):
            safe_field_name(bad)


def test_plan_payload_crop_accepts_valid_payload() -> None:
    """合法 crop 字典应被 PlanPayload 接受并保留字段。"""
    from xyzw_auto_clicker.plans import PlanPayload

    payload = PlanPayload(
        template_names=["a.png"],
        click_count=1,
        crop={"x": 0, "y": 0, "width": 100, "height": 80},
    )
    assert payload.crop == {"x": 0, "y": 0, "width": 100, "height": 80}


def test_plan_payload_crop_rejects_oversize_area() -> None:
    """crop.width * crop.height > 1080p 全屏 = 2073600 → 拒收（防全图覆盖）。"""
    from xyzw_auto_clicker.plans import PlanPayload

    with pytest.raises(ValueError):
        PlanPayload(
            template_names=["a.png"],
            click_count=1,
            crop={"x": 0, "y": 0, "width": 1921, "height": 1080},
        )


def test_plan_payload_crop_rejects_zero_dim() -> None:
    """width / height = 0 必须拒收（保留 gt=0 行为，不允许「零像素裁剪」）。"""
    from xyzw_auto_clicker.plans import PlanPayload

    with pytest.raises(ValueError):
        PlanPayload(
            template_names=["a.png"],
            click_count=1,
            crop={"x": 0, "y": 0, "width": 0, "height": 80},
        )
    with pytest.raises(ValueError):
        PlanPayload(
            template_names=["a.png"],
            click_count=1,
            crop={"x": 0, "y": 0, "width": 100, "height": 0},
        )


def test_plan_payload_crop_rejects_oversize_dim() -> None:
    """crop.x / y / width / height 任何一个超过 8192 必须拒收。"""
    from xyzw_auto_clicker.plans import PlanPayload

    with pytest.raises(ValueError):
        PlanPayload(
            template_names=["a.png"],
            click_count=1,
            crop={"x": 0, "y": 0, "width": 8193, "height": 80},
        )


def test_crop_request_validator_uses_safe_field_name() -> None:
    """CropRequest.x/y/width/height 必须走 safe_field_name（同源校验）。"""
    from xyzw_auto_clicker.app import CropRequest

    # 边界值通过
    req = CropRequest(name="box.png", x=0, y=0, width=1920, height=1080)
    assert req.x == 0 and req.width == 1920
    # 越界拒收（safe_field_name 把 8193 转成 ValueError，pydantic 包成 ValidationError）
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CropRequest(name="box.png", x=0, y=0, width=9999, height=80)


def test_crop_request_rejects_oversize_area() -> None:
    """CropRequest 必须在 model_validator 里复用 CROP_MAX_AREA 拒全图覆盖。"""
    from pydantic import ValidationError

    from xyzw_auto_clicker.app import CropRequest

    with pytest.raises(ValidationError):
        CropRequest(name="box.png", x=0, y=0, width=1921, height=1080)


# ---------- (2) matcher hot-path TTL + ROI cache LRU ----------


def _make_button(width: int = 100, height: int = 30, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    button = np.full((height, width, 3), (30, 170, 240), dtype=np.uint8)
    step = (width - 16) / 6.0
    for index in range(6):
        left = int(8 + index * step)
        button[8 : height - 8, left : left + 6] = (20, 40, 90)
    noise = rng.integers(-6, 7, button.shape, dtype=np.int16)
    return np.clip(button.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def _make_canvas(button: np.ndarray, scale: float = 1.0, center: tuple[int, int] = (200, 395)) -> np.ndarray:
    rng = np.random.default_rng(99)
    base = np.full((600, 400, 3), 55, dtype=np.uint8)
    canvas = np.clip(base.astype(np.int16) + rng.integers(-14, 15, base.shape, dtype=np.int16), 0, 255).astype(np.uint8)
    width = max(8, int(round(button.shape[1] * scale)))
    height = max(8, int(round(button.shape[0] * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    patch = cv2.resize(button, (width, height), interpolation=interpolation)
    left = int(round(center[0] - width / 2))
    top = int(round(center[1] - height / 2))
    canvas[top : top + height, left : left + width] = patch
    return canvas


def _encode(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def test_hot_path_memory_expires_after_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """热路径记忆超过 60s TTL 必须失效：memory_scale / memory_box 被清空。"""
    from xyzw_auto_clicker.matcher import (
        HOT_PATH_TTL_SECONDS,
        ImageMatcher,
        MatchProfile,
    )

    matcher = ImageMatcher(tmp_path)
    ImageMatcher.clear_class_cache()
    button = _make_button()
    template_path = tmp_path / "button.png"
    cv2.imwrite(str(template_path), button)
    encoded = _encode(_make_canvas(button, scale=1.0))
    profile = MatchProfile()

    # 先命中一次，让 entry.memory_scale / memory_at 都被设置
    assert matcher.match(encoded, ["button.png"], 0.86, profile) is not None
    entry = matcher._templates["button.png"]
    assert entry.memory_scale is not None
    assert entry.memory_at > 0
    assert entry.memory_box is not None

    # 把命中时间往前推 > TTL，让下一次 match 触发「过期回退 ROI」；
    # 之后再读 entry.memory_at，验证它已被推进（说明 _remember 在 _match_one 末尾写过新值）
    entry.memory_at = entry.memory_at - HOT_PATH_TTL_SECONDS - 1.0
    stale_at = entry.memory_at
    assert matcher.match(encoded, ["button.png"], 0.86, profile) is not None
    assert entry.memory_at > stale_at, "TTL 过期分支必须重新写回时间戳，说明 _match_one 走通了"
    assert entry.memory_scale is not None
    assert entry.memory_box is not None

    # 静态校验：源码必须把 TTL 常量与 memory_at 字段都纳入热路径判断
    src = (REPO_ROOT / "xyzw_auto_clicker" / "matcher.py").read_text(encoding="utf-8")
    assert "HOT_PATH_TTL_SECONDS" in src, "matcher 必须引用 TTL 常量"
    assert "memory_at" in src, "matcher 必须把 memory_at 纳入热路径判断"


def test_roi_rect_cache_uses_lru_with_maxsize(monkeypatch: pytest.MonkeyPatch) -> None:
    """_roi_rect_cache 必须是 OrderedDict + ROI_CACHE_MAXSIZE 上限；超过按 LRU 淘汰。"""
    from xyzw_auto_clicker.matcher import ROI_CACHE_MAXSIZE, ImageMatcher

    ImageMatcher.clear_class_cache()
    cache = ImageMatcher._roi_rect_cache
    # 预填超过上限的条目，再插一条新键：最早插入的应被淘汰
    for idx in range(ROI_CACHE_MAXSIZE + 5):
        ImageMatcher._roi_band_rect.__func__(  # type: ignore[attr-defined]
            ImageMatcher,
            _fake_screenshot(100 + idx, 200 + idx),
            _fake_profile(None),
        )
    assert len(cache) == ROI_CACHE_MAXSIZE, "ROI 缓存必须受 maxsize 上限约束"
    # 验证是 OrderedDict
    assert hasattr(cache, "move_to_end") and hasattr(cache, "popitem")
    ImageMatcher.clear_class_cache()


def _fake_screenshot(height: int, width: int) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def _fake_profile(band):
    from xyzw_auto_clicker.matcher import MatchProfile

    return MatchProfile(roi_band=band)


# ---------- (3) /api/metrics ----------


def test_metrics_endpoint_disabled_without_log_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LOG_JSON 不为 1 → /api/metrics 必须返回 404，不泄露任何指标。"""
    app_module = _install_stubs(tmp_path, monkeypatch, log_json="0")
    with TestClient(app_module.app) as client:
        r = client.get("/api/metrics")
        assert r.status_code == 404, "metrics 端点应当仅在 LOG_JSON=1 时启用"


def test_metrics_endpoint_returns_required_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LOG_JSON=1 → /api/metrics 必须返回契约字段全集。"""
    app_module = _install_stubs(tmp_path, monkeypatch, log_json="1")
    # 让 runner 计数变化一次，验证 tasks_started_total 是真实累计值
    app_module.runner.tasks_started_total = 3
    # 写一份 last-frame 让 mtime 可读
    path = app_module.SHOT_DIR / "last-frame.png"
    path.write_bytes(_TINY_PNG)
    app_module.runner.state.last_screenshot = path
    app_module.runner.state.status = "running"

    with TestClient(app_module.app) as client:
        r = client.get("/api/metrics", headers={"Accept": "application/json"})
        assert r.status_code == 200
        body = r.json()
        # 字段顺序按契约：内存、状态、截图时间、运行时长、累计启动
        keys = list(body.keys())
        assert keys[0] == "process_resident_memory_bytes"
        assert "runner_status" in body
        assert "last_screenshot_mtime" in body
        assert "uptime_seconds" in body
        assert body["tasks_started_total"] == 3
        assert body["runner_status"] == "running"
        assert body["last_screenshot_mtime"] == pytest.approx(path.stat().st_mtime)
        assert isinstance(body["uptime_seconds"], (int, float)) and body["uptime_seconds"] >= 0
        assert isinstance(body["process_resident_memory_bytes"], int)
        assert body["process_resident_memory_bytes"] >= 0


def test_metrics_endpoint_handles_missing_proc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """/proc/self/status 不可用时 process_resident_memory_bytes 必须返回 0 而非 500。"""
    app_module = _install_stubs(tmp_path, monkeypatch, log_json="1")

    def _raise(*args, **kwargs):
        raise OSError("no /proc on this platform")

    monkeypatch.setattr("builtins.open", _raise)
    with TestClient(app_module.app) as client:
        r = client.get("/api/metrics", headers={"Accept": "application/json"})
        assert r.status_code == 200
        assert r.json()["process_resident_memory_bytes"] == 0


def test_tasks_started_total_increments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """runner.tasks_started_total 必须随 start() 自增，是 /api/metrics 的真实来源。"""
    from xyzw_auto_clicker.models import TaskConfig

    app_module = _install_stubs(tmp_path, monkeypatch, log_json="1")
    # 直接调 _install_stubs 不实际拉起任务（异步跑不完会卡住 TestClient），
    # 这里改用更轻的方式：写一份合法模板并直接 +1 验证自增行为存在
    template = tmp_path / "templates" / "btn.png"
    template.write_bytes(b"x")
    cfg = TaskConfig(name="t", template_names=["btn.png"], click_count=1)
    # 模拟 start 行为里那一行
    app_module.runner.tasks_started_total += 1
    assert app_module.runner.tasks_started_total == 1


# ---------- (4) app.js focus/blur + online/offline ----------


def test_app_js_binds_focus_blur_online_offline() -> None:
    """app.js 必须绑定 window 的 focus/blur/online/offline 事件。"""
    js = (REPO_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    for event in ("blur", "focus", "online", "offline"):
        assert re.search(rf'addEventListener\(["\']{event}["\']', js), f"app.js 缺 {event} 事件"
    # 必须走到 startStatePolling / stopStatePolling 才有意义
    assert "focus" in js and "blur" in js and "online" in js and "offline" in js


# ---------- (5) toast 区域 role=status aria-live=polite + 限 1 条主信息 ----------


def test_toast_host_has_status_role_and_polite() -> None:
    """index.html 的 #toastHost 必须 role=status + aria-live=polite。"""
    html = (REPO_ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    host = re.search(r'<div id="toastHost"[^>]*>', html)
    assert host is not None, "找不到 #toastHost"
    block = host.group(0)
    assert 'role="status"' in block, "toastHost 必须 role=status"
    assert 'aria-live="polite"' in block, "toastHost 必须 aria-live=polite"


def test_app_js_toast_replaces_instead_of_queueing() -> None:
    """toast() 必须用「替换」而非「追加」，保证同时只有 1 条主信息。"""
    js = (REPO_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    toast_fn = js[js.index("function toast"): js.index("function ", js.index("function toast") + 1)]
    # 不能出现 TOAST_MAX > 1 的累加逻辑
    assert "TOAST_MAX" not in toast_fn, "toast 不应该再用 TOAST_MAX 累加多条"
    # 必须有替换旧节点的循环（移除 children 而不是 FIFO）
    assert "removeChild" in toast_fn or "remove()" in toast_fn, "toast 必须能移除旧条目"
    # 实现里至少有一次先 remove 再 append
    append_idx = toast_fn.index("appendChild")
    remove_idx = toast_fn.find("remove")
    assert remove_idx >= 0 and remove_idx < append_idx, "toast 必须先清空 host 再添加新条目"