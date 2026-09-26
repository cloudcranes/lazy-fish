"""PR-14 STOP_FILE env 可配置 smoke。

覆盖任务清单：
  (1) `LAZY_FISH_STOP_FILE` 未设置时，STOP_FILE 走 BASE_DIR/STOP 默认；
  (2) `LAZY_FISH_STOP_FILE` 设到 tmp_path 后，STOP_FILE 立刻反映新路径；
  (3) monkeypatch 撤掉后 STOP_FILE 不被旧 env 残留污染。

不依赖 adb / FastAPI：只读 settings 模块。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# 让 tests/ 跑 pytest 时也能直接 `import xyzw_auto_clicker.settings`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xyzw_auto_clicker import settings as settings_module
from xyzw_auto_clicker.settings import BASE_DIR


def _reload_settings() -> None:
    """强制重载 settings，模拟「容器启动时读 env」语义。"""
    importlib.reload(settings_module)


def test_default_stop_file_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAZY_FISH_STOP_FILE", raising=False)
    _reload_settings()
    assert settings_module.STOP_FILE == BASE_DIR / "STOP"


def test_env_overrides_stop_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "STOP"
    monkeypatch.setenv("LAZY_FISH_STOP_FILE", str(target))
    _reload_settings()
    # 必须用模块属性读最新值；模块顶层 import 拿到的只是初次加载时的旧 Path。
    assert settings_module.STOP_FILE == target


def test_env_unset_after_override_clears(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 先注入一次，确认能切到 tmp_path
    monkeypatch.setenv("LAZY_FISH_STOP_FILE", str(tmp_path / "CUSTOM_STOP"))
    _reload_settings()
    assert settings_module.STOP_FILE == tmp_path / "CUSTOM_STOP"

    # 撤掉 env，应当回到 BASE_DIR/STOP（不残留旧路径）
    monkeypatch.delenv("LAZY_FISH_STOP_FILE", raising=False)
    _reload_settings()
    assert settings_module.STOP_FILE == BASE_DIR / "STOP"