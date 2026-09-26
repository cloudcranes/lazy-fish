"""PR-9 adb 长连接池：复用 / 涻出 / NOT_AVAILABLE。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from xyzw_auto_clicker.adb import AdbError, _ProcPool  # noqa: E402

_FAKE_ADB_SRC = (
    "import sys\n"
    "for line in sys.stdin:\n"
    "    if line.strip() == 'exit':\n"
    "        break\n"
)


@pytest.fixture
def fake_adb(tmp_path: Path) -> str:
    script = tmp_path / "fake_adb_shell.py"
    script.write_text(_FAKE_ADB_SRC, encoding="utf-8")
    return sys.executable


def test_pool_reuses_same_proc_across_calls(fake_adb: str) -> None:
    pool = _ProcPool()
    try:
        s1 = pool.get_or_create("dev-1", fake_adb)
        s2 = pool.get_or_create("dev-1", fake_adb)
        assert s1.proc is s2.proc
    finally:
        pool.close()


def test_pool_evicts_oldest_idle_when_over_max(fake_adb: str) -> None:
    pool = _ProcPool(max_size=2, idle_seconds=60.0)
    try:
        for idx in range(3):
            pool.get_or_create(f"dev-{idx}", fake_adb)
        assert len(pool._slots) == 3
        for key in ("dev-0", "dev-1"):
            pool._slots[key].last_used -= 120.0
        pool._evict_idle()
        assert "dev-0" not in pool._slots
        assert "dev-2" in pool._slots
        assert len(pool._slots) == 2
    finally:
        pool.close()


def test_get_or_create_raises_not_available_on_spawn_failure(tmp_path: Path) -> None:
    pool = _ProcPool()
    bad = str(tmp_path / "_definitely_missing_adb_xyz_")
    if os.name == "nt":
        bad += ".exe"
    with pytest.raises(AdbError) as exc_info:
        pool.get_or_create("dev-x", bad)
    assert exc_info.value.code == "NOT_AVAILABLE"
